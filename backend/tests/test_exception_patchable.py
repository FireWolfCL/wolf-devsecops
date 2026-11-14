"""ADR-021 — seguimiento de parche de riesgos aceptados (migración 0012).

Un riesgo aceptado porque no había parche debe NOTIFICAR cuando Trivy publica
`FixedVersion`: re-chequeo del scheduler → fila marcada + evento `exception_patchable`
(una sola vez), visible en campana/reporte y despachable por los canales externos.
Solo LEE el reporte (ADR-001); no escribe a la fuente.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text

from wolf_devsecops.connectors.defectdojo import DefectDojoConnector
from wolf_devsecops.connectors.trivy import TrivyConnector
from wolf_devsecops.core.cve import extract_cve
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.models import ExceptionIn
from wolf_devsecops.models_db.events import ProjectEvent
from wolf_devsecops.services import event_service, notification_service
from wolf_devsecops.services import exceptions_service as svc
from wolf_devsecops.services import update_service as us
from wolf_devsecops.settings import get_settings


class _StubDD:
    async def set_finding_state(self, fid, **kw):
        return None


class _StubAgg:
    def __init__(self, fixes=None):
        self.fixes = fixes  # {project: {CVE: fixed|None}} | None = fuente no disponible
        self.reads = []
        self.dd = _StubDD()

    def connector_for(self, source):
        return {"defectdojo": self.dd}.get(source)

    async def cve_fixes(self, project_key):
        self.reads.append(project_key)
        return None if self.fixes is None else self.fixes.get(project_key)

    async def invalidate_governance(self, project_key=None):  # overlay neto (ADR-023)
        return None


@pytest.fixture
async def session(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/pt.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        yield s
    await db_engine.dispose_engine()


async def _accept(session, agg, *, title="CVE-2024-1111 libfoo - 1.0", cve=None, ref="7", project="p"):
    """Riesgo aceptado vigente (propuesta → aprobación admin)."""
    row = await svc.create(
        session, agg, project,
        ExceptionIn(source="defectdojo", finding_ref=ref, kind="risk_accepted",
                    justification="sin parche del proveedor", finding_title=title, cve_id=cve),
        user_id="u1", username="oper")
    return await svc.approve(session, agg, row.id, user_id="a1", username="admin")


async def _events(session):
    return list((await session.execute(select(ProjectEvent))).scalars())


# ─── alta: qué se guarda ──────────────────────────────────────────────────────

async def test_create_deriva_cve_del_titulo_y_queda_sin_parche(session):
    row = await _accept(session, _StubAgg())
    assert row.cve_id == "CVE-2024-1111" and row.fixed_version is None and row.patchable_since is None


async def test_cve_explicito_gana_y_se_normaliza(session):
    row = await _accept(session, _StubAgg(), title="otro texto", cve="cve-2023-9999")
    assert row.cve_id == "CVE-2023-9999"


async def test_sin_cve_no_se_rastrea(session):
    row = await _accept(session, _StubAgg(), title="Hardcoded password in config.py")
    assert row.cve_id is None


def test_cve_id_malformado_se_rechaza():
    with pytest.raises(ValidationError):
        ExceptionIn(source="defectdojo", finding_ref="1", kind="risk_accepted",
                    justification="motivo ok", cve_id="CVE-2024; DROP TABLE")


def test_extract_cve_estricto():
    assert extract_cve(None, [{"vulnerability_id": "cve-2021-44228"}]) == "CVE-2021-44228"
    assert extract_cve("GHSA-xxxx-yyyy-zzzz", "texto sin id") is None
    assert extract_cve("CVE-2024-12") is None  # < 4 dígitos: no es un CVE válido


# ─── re-chequeo ───────────────────────────────────────────────────────────────

async def test_cve_sin_fix_no_dispara(session):
    agg = _StubAgg({"p": {"CVE-2024-1111": None}})
    row = await _accept(session, agg)
    assert await svc.check_patchable(session, agg) == 0
    await session.refresh(row)
    assert row.fixed_version is None and await _events(session) == []


async def test_cve_con_fix_marca_fila_y_emite_evento(session):
    agg = _StubAgg({"p": {"CVE-2024-1111": "1.2.3"}})
    row = await _accept(session, agg)
    assert await svc.check_patchable(session, agg) == 1
    await session.refresh(row)
    assert row.fixed_version == "1.2.3" and row.patchable_since is not None
    assert row.status == "applied"  # no se revoca solo: remediar lo decide un humano
    (ev,) = await _events(session)
    assert (ev.kind, ev.severity, ev.project_key) == ("exception_patchable", "warn", "p")
    assert ev.detail == {"cve": "CVE-2024-1111", "fixed_version": "1.2.3", "source": "defectdojo"}


async def test_dedup_no_reenvia_en_ciclos_siguientes(session):
    agg = _StubAgg({"p": {"CVE-2024-1111": "1.2.3"}})
    await _accept(session, agg)
    assert await svc.check_patchable(session, agg) == 1
    assert await svc.check_patchable(session, agg) == 0
    assert await svc.check_patchable(session, agg) == 0
    assert len(await _events(session)) == 1


async def test_sin_cve_es_noop_y_no_lee_trivy(session):
    agg = _StubAgg({"p": {}})
    await _accept(session, agg, title="Hardcoded password")
    assert await svc.check_patchable(session, agg) == 0
    assert agg.reads == []  # ni siquiera es candidata


async def test_fuente_caida_no_infiere_nada(session):
    agg = _StubAgg(None)  # Trivy no disponible
    row = await _accept(session, agg)
    assert await svc.check_patchable(session, agg) == 0
    await session.refresh(row)
    assert row.fixed_version is None and await _events(session) == []


async def test_solo_candidatas_vigentes_de_riesgo_aceptado(session):
    agg = _StubAgg({"p": {"CVE-2024-1111": "1.2.3", "CVE-2024-2222": "9.9"}})
    revoked = await _accept(session, agg, ref="1")
    await svc.revoke(session, agg, revoked.id, user_id="u1", username="oper")
    fp = await svc.create(  # falso positivo con CVE: no es "riesgo aceptado"
        session, agg, "p",
        ExceptionIn(source="defectdojo", finding_ref="2", kind="false_positive",
                    justification="no aplica", finding_title="CVE-2024-2222 libbar"),
        user_id="u1", username="oper")
    assert fp.kind == "false_positive"
    assert await svc.check_patchable(session, agg) == 0
    assert await _events(session) == []


async def test_una_lectura_de_trivy_por_proyecto(session):
    agg = _StubAgg({"p": {"CVE-2024-1111": None, "CVE-2024-3333": None}})
    await _accept(session, agg, ref="1")
    await _accept(session, agg, ref="2", title="CVE-2024-3333 libbaz")
    await svc.check_patchable(session, agg)
    assert agg.reads == ["p"]


# ─── taxonomía + notificación ─────────────────────────────────────────────────

def test_evento_en_taxonomia_y_no_en_good_ni_bad():
    assert "exception_patchable" in event_service.ACTION_KINDS
    assert "exception_patchable" not in event_service.GOOD_KINDS | event_service.BAD_KINDS


def test_mensaje_externo_accionable_y_escapado():
    ev = ProjectEvent(project_key="p", kind="exception_patchable", severity="warn",
                      crit_delta=0, high_delta=0,
                      detail={"cve": "CVE-2024-1111", "fixed_version": "1.2.3<script>"})
    es = notification_service._line(ev, "es")
    en = notification_service._line(ev, "en")
    assert "CVE-2024-1111" in es and "v1.2.3" in es and "remediar" in es
    assert "now has a patch" in en
    assert "<script>" not in es and "&lt;script&gt;" in es  # detail viene de un reporte externo
    assert notification_service._ICON["exception_patchable"] in es


def test_severidad_warn_supera_el_umbral_por_defecto():
    sev = notification_service._SEV_RANK
    assert sev["warn"] >= sev[get_settings().notify_min_severity]


async def test_se_despacha_una_vez_por_el_pipeline(session, monkeypatch):
    agg = _StubAgg({"p": {"CVE-2024-1111": "1.2.3"}})
    await _accept(session, agg)
    await svc.check_patchable(session, agg)
    sent = []

    async def fake_broadcast(settings, subject, text_):
        sent.append(text_)
        return ["telegram"]

    monkeypatch.setattr(notification_service.channels, "broadcast", fake_broadcast)
    monkeypatch.setattr(notification_service, "_has_channel", lambda s: True)
    out = await notification_service.dispatch_pending(session, get_settings())
    assert out["sent"] >= 1 and "CVE-2024-1111" in sent[0]
    again = await notification_service.dispatch_pending(session, get_settings())
    assert again.get("sent", 0) == 0 and len(sent) == 1  # no se repite


# ─── fuentes (solo lectura) ───────────────────────────────────────────────────

def test_trivy_cve_fixes_ve_el_reporte_completo():
    report = {"Results": [
        {"Vulnerabilities": [
            {"VulnerabilityID": "CVE-2024-1111", "FixedVersion": ""},
            {"VulnerabilityID": "cve-2024-2222", "FixedVersion": "2.0"},
        ]},
        {"Vulnerabilities": [{"VulnerabilityID": "CVE-2024-1111", "FixedVersion": "1.2.3"}]},
    ]}
    assert TrivyConnector._cve_fixes_from_report(report) == {
        "CVE-2024-1111": "1.2.3", "CVE-2024-2222": "2.0"}


def test_defectdojo_expone_cve_canonico():
    out = DefectDojoConnector._findings_list([
        {"id": 5, "title": "CVE-2024-1111 libfoo - 1.0", "severity": "High", "vulnerability_ids": []},
        {"id": 6, "title": "Hardcoded secret", "severity": "High", "cve": None},
        {"id": 7, "title": "x", "severity": "Low", "vulnerability_ids": [{"vulnerability_id": "CVE-2023-0001"}]},
    ])
    by = {f["ref"]: f["cve"] for f in out}
    assert by == {"5": "CVE-2024-1111", "6": None, "7": "CVE-2023-0001"}


# ─── migración 0012 ───────────────────────────────────────────────────────────

def test_migracion_0012_reversible_y_conserva_datos(tmp_path):
    s = get_settings()
    old = (s.database_url, s.environment)
    path = tmp_path / "m.db"
    s.database_url = f"sqlite+aiosqlite:///{path}"
    s.environment = "development"
    asyncio.run(db_engine.dispose_engine())
    cfg = us.build_alembic_config()
    sync = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(cfg, "0011_multitenancy_f0")
        with sync.begin() as c:
            c.execute(text(
                "INSERT INTO source_exceptions (id, project_key, source, finding_ref, kind, justification, status) "
                "VALUES ('e1', 'p', 'defectdojo', '1', 'risk_accepted', 'motivo', 'applied')"))
        command.upgrade(cfg, "0012_exception_patchable")  # pin a 0012 (no head): el downgrade -1 revierte 0012
        with sync.connect() as c:
            cols = {r[1] for r in c.execute(text("PRAGMA table_info(source_exceptions)"))}
            assert {"cve_id", "fixed_version", "patchable_since", "tenant_id"} <= cols
            row = c.execute(text("SELECT cve_id, fixed_version, tenant_id FROM source_exceptions")).one()
            assert row.cve_id is None and row.fixed_version is None
            assert row.tenant_id == "00000000-0000-0000-0000-000000000001"
        command.downgrade(cfg, "-1")
        with sync.connect() as c:
            cols = {r[1] for r in c.execute(text("PRAGMA table_info(source_exceptions)"))}
            assert not ({"cve_id", "fixed_version", "patchable_since"} & cols) and "tenant_id" in cols
            assert c.execute(text("SELECT status FROM source_exceptions")).scalar() == "applied"
        command.upgrade(cfg, "head")
    finally:
        sync.dispose()
        asyncio.run(db_engine.dispose_engine())
        s.database_url, s.environment = old
