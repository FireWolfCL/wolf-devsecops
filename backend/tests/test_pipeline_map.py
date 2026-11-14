"""Mapa de pipeline "usted está aquí": tabla de escenarios del pin + recent_runs.

Solo lectura (ADR-001). Estados honestos: fuente ausente => "none", nunca verde falso.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from wolf_devsecops.api.deps import get_aggregator, get_config
from wolf_devsecops.auth import security
from wolf_devsecops.connectors.github import GitHubConnector
from wolf_devsecops.core.config import AppConfig, GitHubProjectConfig, ProjectConfig, SourceConfig
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models import (
    CIStatus,
    CISummary,
    FindingsSummary,
    ProjectSummary,
    QualitySummary,
    RegistryImage,
    ReleaseSummary,
    RunInfo,
    SeverityCount,
    SourceResult,
    SourceStatus,
)
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services.pipeline_service import build_pipeline_map
from wolf_devsecops.settings import get_settings

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
KEYS = ["commit", "ci", "quality", "security", "registry", "release"]


def ok(data):
    return SourceResult(status=SourceStatus.ok, data=data, fetched_at=NOW)


NC = SourceResult(status=SourceStatus.not_configured)


def ci(status=CIStatus.success):
    return ok(CISummary(status=status, workflow="ci.yml", run_number=7, html_url="https://gh/run/7"))


def quality(gate="OK"):
    return ok(QualitySummary(quality_gate=gate))


def findings(crit=0, high=0):
    return ok(FindingsSummary(counts=SeverityCount(critical=crit, high=high), total_active=crit + high))


def release(tag="v1.0.0"):
    return ok(ReleaseSummary(tag=tag, name=tag, sbom_url="https://gh/sbom.json") if tag else ReleaseSummary())


def image(present=True):
    return ok(RegistryImage(present=present, tag="1.0.0" if present else None,
                            pushed_at=NOW if present else None, artifacts=1 if present else 0))


def run(state=CIStatus.success, n=7):
    return RunInfo(actor="felipe", sha_short="abc1234", message="fix: algo", branch="main",
                   event="push", ci_state=state, run_number=n, html_url="https://gh/run/7", created_at=NOW)


def summary(**over) -> ProjectSummary:
    base = dict(
        key="p", name="P", ci=ci(), findings=findings(), quality=quality(),
        release=release(), containers=NC, generated_at=NOW, links={},
    )
    base.update(over)
    return ProjectSummary(**base)


def states(m):
    return {s.key: s.state for s in m.stages}


def build(s=None, runs=None, registry=None, gh=True):
    return build_pipeline_map(
        s or summary(), [run()] if runs is None else runs, registry or image(), github_configured=gh
    )


def test_todo_verde_pin_en_release():
    m = build()
    assert [s.key for s in m.stages] == KEYS
    assert set(states(m).values()) == {"ok"}
    assert m.current_index == KEYS.index("release")
    assert m.stages[5].detail_code == "released" and m.stages[5].meta["sbom"] is True
    assert m.last_push.sha_short == "abc1234" and len(m.recent_pushes) == 1


def test_ci_rojo_pin_en_ci():
    m = build(summary(ci=ci(CIStatus.failure)))
    assert m.stages[1].state == "fail" and m.stages[1].detail_code == "ci_failing"
    assert m.stages[1].meta["run_number"] == 7
    assert m.current_index == 1


def test_ci_corriendo_es_pending():
    m = build(summary(ci=ci(CIStatus.running)))
    assert m.stages[1].state == "pending" and m.stages[1].detail_code == "awaiting_ci"
    assert m.current_index == 1


def test_gate_error_pin_en_calidad():
    m = build(summary(quality=quality("ERROR")))
    assert m.stages[2].state == "fail" and m.stages[2].detail_code == "gate_error"
    assert m.current_index == 2


def test_gate_warn_es_warn():
    assert build(summary(quality=quality("WARN"))).stages[2].state == "warn"


def test_criticos_pin_en_seguridad():
    m = build(summary(findings=findings(crit=2, high=1)))
    assert m.stages[3].state == "fail" and m.stages[3].detail_code == "crit_unmanaged"
    assert m.stages[3].meta == {"critical": 2, "high": 1}
    assert m.current_index == 3


def test_criticos_de_contenedor_cuentan():
    m = build(summary(containers=findings(crit=1)))
    assert m.stages[3].state == "fail"


def test_altos_sin_criticos_es_warn():
    m = build(summary(findings=findings(high=3)))
    assert m.stages[3].state == "warn" and m.stages[3].detail_code == "high_present"
    assert m.current_index == 3


def test_sin_harbor_registro_gris_y_no_captura_pin():
    m = build(registry=NC)
    assert m.stages[4].state == "none" and m.stages[4].detail_code == "no_source"
    assert m.current_index == 5  # salta el gris, queda en release (ok)


def test_harbor_sin_artefacto_pending_captura_pin():
    m = build(registry=image(present=False))
    assert m.stages[4].state == "pending" and m.stages[4].detail_code == "awaiting_image"
    assert m.current_index == 4


def test_sin_release_con_github_pending():
    m = build(summary(release=release(None)))
    assert m.stages[5].state == "pending" and m.stages[5].detail_code == "awaiting_release"
    assert m.current_index == 5


def test_repo_sin_github_commit_y_ci_none():
    s = summary(ci=NC, release=NC)
    m = build(s, runs=[], gh=False)
    assert states(m)["commit"] == "none" and m.stages[0].detail_code == "no_source"
    assert states(m)["ci"] == "none"
    assert m.last_push is None and m.recent_pushes == []
    # solo quedan ok quality/security/registry: el pin es la última ok (registry)
    assert m.current_index == 4


def test_nada_configurado_sin_pin_y_sin_verde_falso():
    s = summary(ci=NC, findings=NC, quality=NC, release=NC, containers=NC)
    m = build(s, runs=[], registry=NC, gh=False)
    assert set(states(m).values()) == {"none"}
    assert m.current_index == -1


def test_fuente_caida_sin_dato_es_none_no_verde():
    down = SourceResult(status=SourceStatus.unavailable, error="caída")
    m = build(summary(quality=down, findings=down))
    assert m.stages[2].state == "none" and m.stages[2].detail_code == "source_unavailable"
    assert m.stages[3].state == "none"  # no se puede afirmar "limpio"


def test_seguridad_limpia_solo_con_todas_las_fuentes_con_dato():
    down = SourceResult(status=SourceStatus.unavailable, error="x")
    assert build(summary(findings=findings(), containers=down)).stages[3].state == "none"
    assert build(summary(findings=findings(), containers=findings())).stages[3].detail_code == "clean"


def test_primera_bloqueante_gana_sobre_posteriores():
    m = build(summary(ci=ci(CIStatus.failure), quality=quality("ERROR")))
    assert m.current_index == 1


# ─── GitHubConnector.recent_runs ─────────────────────────────────────────────

RUNS = {"workflow_runs": [
    {"run_number": 9, "status": "completed", "conclusion": "failure", "head_branch": "main",
     "head_sha": "0123456789abcdef", "event": "push", "html_url": "https://gh/r/9",
     "created_at": "2026-11-01T10:00:00Z",
     "actor": {"login": "ana"}, "triggering_actor": {"login": "bot"},
     "head_commit": {"message": "feat: x\n\ncuerpo largo"}},
    {"run_number": 8, "status": "in_progress", "conclusion": None, "head_branch": "dev",
     "head_sha": "fedcba9876543210", "event": "pull_request", "html_url": "https://gh/r/8",
     "created_at": "2026-11-01T09:00:00Z", "triggering_actor": {"login": "bot"}},
    {"run_number": 7, "status": "completed", "conclusion": "success"},
]}


def _gh(handler) -> GitHubConnector:
    return GitHubConnector(SourceConfig(url="https://api.github.com", token_env="T"), token="x",
                           transport=httpx.MockTransport(handler))


def _proj() -> ProjectConfig:
    return ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))


async def test_recent_runs_mapea_y_limita():
    seen = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["per_page"] = req.url.params.get("per_page")
        return httpx.Response(200, json=RUNS)

    out = await _gh(h).recent_runs(_proj(), limit=2)
    assert seen["per_page"] == "2" and len(out) == 2
    a, b = out
    assert (a.actor, a.sha_short, a.message, a.branch, a.event) == ("ana", "0123456", "feat: x", "main", "push")
    assert a.ci_state == CIStatus.failure and a.run_number == 9 and a.created_at is not None
    assert b.actor == "bot"  # cae a triggering_actor
    assert b.ci_state == CIStatus.running and b.message is None


async def test_recent_runs_tolerante():
    assert await _gh(lambda r: httpx.Response(500)).recent_runs(_proj()) == []
    assert await _gh(lambda r: httpx.Response(200, json=RUNS)).recent_runs(ProjectConfig(key="q", name="Q")) == []

    def boom(req):
        raise httpx.ConnectError("x")

    assert await _gh(boom).recent_runs(_proj()) == []
    # campos ausentes no revientan
    out = await _gh(lambda r: httpx.Response(200, json=RUNS)).recent_runs(_proj(), limit=5)
    assert out[2].actor is None and out[2].sha_short is None and out[2].ci_state == CIStatus.success


# ─── Ruta ────────────────────────────────────────────────────────────────────


class _Stub:
    async def get_summary(self, project):
        return summary(ci=ci(CIStatus.failure))

    async def get_recent_runs(self, project, limit=5):
        return [run(CIStatus.failure)]

    async def get_registry_image(self, project):
        return NC


@pytest.fixture
async def env(tmp_path):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/pm.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    headers = {}
    async with db_engine.get_sessionmaker()() as s:
        for role in Role:
            u = User(username=f"u-{role.value}", role=role, is_active=True, token_version=0,
                     mfa_enabled=(role == Role.admin))
            s.add(u)
            await s.flush()
            tok = security.create_access_token(subject=u.id, role=role.value, token_version=0)
            headers[role.value] = {"Authorization": f"Bearer {tok}"}
        await s.commit()
    app = create_app()
    app.dependency_overrides[get_aggregator] = lambda: _Stub()
    app.dependency_overrides[get_config] = lambda: AppConfig(
        projects=[ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, headers
    await db_engine.dispose_engine()


async def test_ruta_pipeline_auth_y_sin_gate(env):
    c, h = env
    url = "/api/v1/projects/p/pipeline"
    assert (await c.get(url)).status_code == 401
    assert (await c.get("/api/v1/projects/zz/pipeline", headers=h["viewer"])).status_code == 404
    settings = get_settings()
    prev = settings.edition
    settings.edition = "community"  # sin feature-gate: disponible en todas las ediciones
    try:
        for role in Role:
            assert (await c.get(url, headers=h[role.value])).status_code == 200
    finally:
        settings.edition = prev
    body = (await c.get(url, headers=h["viewer"])).json()
    assert [s["key"] for s in body["stages"]] == KEYS
    assert body["current_index"] == 1
    assert body["stages"][1]["detail_code"] == "ci_failing"
    assert body["last_push"]["sha_short"] == "abc1234"
    assert set(body["stages"][0]) == {"key", "state", "detail_code", "url", "meta"}
