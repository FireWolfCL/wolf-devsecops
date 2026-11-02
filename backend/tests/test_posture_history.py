"""ADR-019 — tendencia histórica de postura: modelo, CLI/servicio y endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from wolf_devsecops.api.deps import get_config
from wolf_devsecops.auth import security
from wolf_devsecops.core.config import AppConfig, GitHubProjectConfig, ProjectConfig, SourceConfig
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models import (
    CIStatus,
    CISummary,
    FindingsSummary,
    ProjectSummary,
    SeverityCount,
    SourceResult,
    SourceStatus,
)
from wolf_devsecops.models_db.posture import PostureSnapshot
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import posture_history_service as svc
from wolf_devsecops.settings import get_settings

CFG = AppConfig(
    sources={"github": SourceConfig(url="https://api.github.com", token_env="X")},
    projects=[
        ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r")),
        ProjectConfig(key="empty", name="E", github=GitHubProjectConfig(repo="o/e")),
    ],
)


def _ok(d):
    return SourceResult(status=SourceStatus.ok, data=d)


def _nc():
    return SourceResult(status=SourceStatus.not_configured)


def _summary(key="p", with_data=True) -> ProjectSummary:
    return ProjectSummary(
        key=key, name=key, generated_at=datetime.now(UTC),
        ci=_ok(CISummary(status=CIStatus.success, workflow="ci.yml")) if with_data else _nc(),
        findings=_ok(FindingsSummary(
            counts=SeverityCount(critical=1, high=2, medium=3, low=4), total_active=10
        )) if with_data else _nc(),
        quality=_nc(), release=_nc(), containers=_nc(),
    )


class _StubAgg:
    async def get_summary(self, project):
        return _summary(project.key, with_data=project.key == "p")


@pytest.fixture
async def session(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/hist.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        yield s
    await db_engine.dispose_engine()


async def test_modelo_roundtrip(session):
    session.add(PostureSnapshot(project_key="p", compliance_pct=80.0, band="aceptable",
                                crit=0, high=1, med=2, low=3, coverage_pct=None))
    await session.commit()
    row = (await session.execute(select(PostureSnapshot))).scalar_one()
    assert row.id and row.captured_at is not None and row.coverage_pct is None


async def test_capture_escribe_una_fila_por_proyecto_con_datos(session):
    written, skipped = await svc.capture_snapshots(session, _StubAgg(), CFG)
    assert (written, skipped) == (1, ["empty"])  # sin_datos no genera punto
    row = (await session.execute(select(PostureSnapshot))).scalar_one()
    assert row.project_key == "p" and row.crit == 1 and row.high == 2
    assert (row.med, row.low) == (3, 4)
    assert row.compliance_pct is not None and row.band and row.coverage_pct is not None
    await svc.capture_snapshots(session, _StubAgg(), CFG)  # segunda corrida = fila nueva
    assert len((await session.execute(select(PostureSnapshot))).scalars().all()) == 2


def test_cli_registrado():
    from typer.testing import CliRunner

    from wolf_devsecops.cli.main import app

    out = CliRunner().invoke(app, ["snapshot-posture", "--help"])
    assert out.exit_code == 0


@pytest.fixture
async def env(session):
    headers = {}
    for role in Role:
        u = User(username=f"u-{role.value}", role=role, is_active=True, token_version=0)
        session.add(u)
        await session.flush()
        tok = security.create_access_token(subject=u.id, role=u.role.value, token_version=0)
        headers[role.value] = {"Authorization": f"Bearer {tok}"}
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in (2, 0, 1):  # insertadas desordenadas
        session.add(PostureSnapshot(project_key="p", captured_at=base + timedelta(days=i),
                                    compliance_pct=50.0 + i, band="en_riesgo",
                                    crit=i, high=0, med=0, low=0, coverage_pct=90.0))
    await session.commit()
    app = create_app()
    app.dependency_overrides[get_config] = lambda: CFG
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, headers


async def test_sin_token_401(env):
    c, _ = env
    assert (await c.get("/api/v1/projects/p/history")).status_code == 401


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
async def test_todos_los_roles_leen(env, role):
    c, h = env
    r = await c.get("/api/v1/projects/p/history", headers=h[role])
    assert r.status_code == 200


async def test_proyecto_desconocido_404(env):
    c, h = env
    assert (await c.get("/api/v1/projects/nope/history", headers=h["viewer"])).status_code == 404


async def test_serie_ordenada_y_contrato(env):
    c, h = env
    body = (await c.get("/api/v1/projects/p/history", headers=h["viewer"])).json()
    assert body["project_key"] == "p"
    pcts = [s["compliance_pct"] for s in body["snapshots"]]
    assert pcts == [50.0, 51.0, 52.0]
    assert set(body["snapshots"][0]) == {
        "captured_at", "compliance_pct", "band", "crit", "high", "med", "low", "coverage_pct"}


async def test_limit_devuelve_las_mas_recientes_ascendentes(env):
    c, h = env
    body = (await c.get("/api/v1/projects/p/history?limit=2", headers=h["viewer"])).json()
    assert [s["compliance_pct"] for s in body["snapshots"]] == [51.0, 52.0]
    assert (await c.get("/api/v1/projects/p/history?limit=0", headers=h["viewer"])).status_code == 422


async def test_proyecto_sin_snapshots_lista_vacia(env):
    c, h = env
    body = (await c.get("/api/v1/projects/empty/history", headers=h["viewer"])).json()
    assert body["snapshots"] == []
