"""ADR-023 — postura NETA gobernada.

Cubre: modelo (`counts_net`), posture pura, `_cve_severities_from_report` de Trivy,
`DbGovernance` (SQLite) y `Aggregator._accepted_container_counts` (fail-closed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from wolf_devsecops.connectors.base import ConnectorUnavailable
from wolf_devsecops.connectors.trivy import TrivyConnector
from wolf_devsecops.core import aggregator as agg_mod
from wolf_devsecops.core.aggregator import Aggregator
from wolf_devsecops.core.cache import TTLCache
from wolf_devsecops.core.config import AppConfig, ProjectConfig, SourceConfig, TrivyProjectConfig
from wolf_devsecops.core.posture import score_project
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.models import (
    CIStatus,
    CISummary,
    FindingsSummary,
    ProjectSummary,
    QualitySummary,
    ReleaseSummary,
    SeverityCount,
    SourceResult,
    SourceStatus,
)
from wolf_devsecops.models_db.exceptions import SourceException
from wolf_devsecops.services.governance import DbGovernance, GovernanceProvider, NullGovernance
from wolf_devsecops.settings import get_settings


# ─── helpers de postura pura ─────────────────────────────────────────────────
def _ok(data):
    return SourceResult(status=SourceStatus.ok, data=data)


def _nc():
    return SourceResult(status=SourceStatus.not_configured)


def _fs(critical=0, high=0, acc_critical=0, acc_high=0, **extra):
    return FindingsSummary(
        counts=SeverityCount(critical=critical, high=high, **extra),
        total_active=critical + high,
        accepted=SeverityCount(critical=acc_critical, high=acc_high),
    )


def _summary(**blocks) -> ProjectSummary:
    base = dict(ci=_nc(), findings=_nc(), quality=_nc(), release=_nc(), containers=_nc())
    base.update(blocks)
    return ProjectSummary(key="k", name="K", generated_at=datetime.now(UTC), **base)


def _todo_pass(**over):
    b = dict(
        ci=_ok(CISummary(status=CIStatus.success, workflow="ci.yml")),
        findings=_ok(_fs()),
        containers=_ok(_fs()),
        quality=_ok(QualitySummary(quality_gate="OK", vulnerabilities=0, coverage=85.0)),
        release=_ok(ReleaseSummary(tag="v1.0.0", sbom_url="https://e/sbom.json")),
    )
    b.update(over)
    return _summary(**b)


def _check(p, cid):
    return next(c for c in p.checks if c.id == cid)


# ─── modelo: counts_net ──────────────────────────────────────────────────────
def test_counts_net_resta_por_severidad():
    f = FindingsSummary(
        counts=SeverityCount(critical=3, high=2, medium=5, low=1, info=4), total_active=15,
        accepted=SeverityCount(critical=1, high=2, medium=1, low=0, info=4),
    )
    n = f.counts_net
    assert (n.critical, n.high, n.medium, n.low, n.info) == (2, 0, 4, 1, 0)


def test_counts_net_piso_en_cero_nunca_negativo():
    f = FindingsSummary(
        counts=SeverityCount(critical=1, high=0, medium=2), total_active=3,
        accepted=SeverityCount(critical=5, high=3, medium=9, low=2, info=1),
    )
    n = f.counts_net
    assert (n.critical, n.high, n.medium, n.low, n.info) == (0, 0, 0, 0, 0)


def test_counts_net_default_igual_a_crudo_y_crudo_intacto():
    f = FindingsSummary(counts=SeverityCount(critical=2, high=1), total_active=3)
    assert f.accepted.critical == 0 and f.accepted.total == 0
    assert f.counts_net == f.counts
    g = _fs(critical=1, acc_critical=1)
    _ = g.counts_net
    assert g.counts.critical == 1  # la propiedad no muta el crudo


# ─── posture pura ────────────────────────────────────────────────────────────
def test_critico_aceptado_no_capa_y_check_pasa_con_accepted_risk():
    p = score_project(_todo_pass(containers=_ok(_fs(critical=1, acc_critical=1))))
    c = _check(p, "containers-no-critical")
    assert c.result == "pass" and c.reason_code == "accepted_risk"
    assert p.band != "en_riesgo" and p.capped_by != "critical"
    assert p.band == "solido" and p.score == 100


def test_dos_criticos_uno_aceptado_sigue_capando():
    p = score_project(_todo_pass(containers=_ok(_fs(critical=2, acc_critical=1))))
    c = _check(p, "containers-no-critical")
    assert c.result == "fail" and c.reason_code == "threshold_not_met"
    assert p.band == "en_riesgo" and p.capped_by == "critical"


def test_fail_closed_accepted_cero_identico_a_antes():
    p = score_project(_todo_pass(containers=_ok(_fs(critical=1))))
    c = _check(p, "containers-no-critical")
    assert c.result == "fail" and c.reason_code == "threshold_not_met"
    assert p.band == "en_riesgo" and p.capped_by == "critical"


def test_sin_riesgo_y_sin_aceptados_reason_ok_no_accepted_risk():
    p = score_project(_todo_pass())
    assert _check(p, "containers-no-critical").reason_code == "ok"


def test_high_aceptado_pasa_con_accepted_risk_y_high_no_capa():
    p = score_project(_todo_pass(containers=_ok(_fs(high=2, acc_high=2))))
    c = _check(p, "containers-no-high")
    assert c.result == "pass" and c.reason_code == "accepted_risk"


def test_accepted_mayor_que_counts_no_crea_credito_y_no_rompe():
    # Piso en 0: más aceptados que crudos no puede dar neto negativo ni pasar de más.
    p = score_project(_todo_pass(containers=_ok(_fs(critical=1, acc_critical=7))))
    c = _check(p, "containers-no-critical")
    assert c.result == "pass" and c.reason_code == "accepted_risk"
    assert p.score == 100


def test_critico_aceptado_en_findings_tampoco_capa():
    p = score_project(_todo_pass(findings=_ok(_fs(critical=1, acc_critical=1))))
    assert p.capped_by != "critical" and p.band != "en_riesgo"


def test_reconciliacion_covered_by_containers_sobre_neto_en_ambos_lados():
    # DD 1 crítico (accepted=0) vs Contenedores 2 crit con 1 aceptado ⇒ neto cont=1.
    # f(1) <= c(1) ⇒ cubierto.
    p = score_project(_todo_pass(
        findings=_ok(_fs(critical=1)),
        containers=_ok(_fs(critical=2, acc_critical=1)),
    ))
    f = _check(p, "findings-no-critical")
    assert f.result == "na" and f.reason_code == "covered_by_containers"


def test_reconciliacion_aceptados_bajan_contenedores_y_exceso_de_app_penaliza():
    # DD 1 crítico; Contenedores 1 crítico aceptado ⇒ neto cont=0 ⇒ f(1) > c(0): el
    # exceso se evalúa y penaliza (no hay doble conteo que lo cubra).
    p = score_project(_todo_pass(
        findings=_ok(_fs(critical=1)),
        containers=_ok(_fs(critical=1, acc_critical=1)),
    ))
    f = _check(p, "findings-no-critical")
    assert f.result == "fail" and f.reason_code == "threshold_not_met"
    assert _check(p, "containers-no-critical").reason_code == "accepted_risk"
    assert p.capped_by == "critical"  # el crítico de DD sin cobertura sí capa


def test_reconciliacion_dd_sin_aceptados_no_cambia():
    p = score_project(_todo_pass(
        findings=_ok(_fs(critical=1)), containers=_ok(_fs(critical=1))))
    f = _check(p, "findings-no-critical")
    assert f.result == "na" and f.reason_code == "covered_by_containers"


def test_exceso_de_app_sigue_penalizando_con_aceptados_en_contenedores():
    p = score_project(_todo_pass(
        findings=_ok(_fs(critical=3)),
        containers=_ok(_fs(critical=2, acc_critical=1)),
    ))
    assert _check(p, "findings-no-critical").result == "fail"


def test_sin_pilar_contenedores_findings_se_evalua_normal():
    p = score_project(_todo_pass(containers=_nc(), findings=_ok(_fs(critical=1))))
    assert _check(p, "findings-no-critical").result == "fail"


# ─── trivy: _cve_severities_from_report ──────────────────────────────────────
def _report(*vulns_per_result):
    return {"Results": [{"Vulnerabilities": list(v)} for v in vulns_per_result]}


def _v(cid, sev):
    return {"VulnerabilityID": cid, "Severity": sev}


def test_trivy_mapea_cve_a_severidad_normalizada_mayusculas():
    r = _report([_v("cve-2024-0001", "CRITICAL"), _v("CVE-2024-0002", "High"),
                 _v("CVE-2024-0003", "UNKNOWN"), _v("CVE-2024-0004", "weird")])
    out = TrivyConnector._cve_severities_from_report(r, set())
    assert out == {"CVE-2024-0001": "critical", "CVE-2024-0002": "high",
                   "CVE-2024-0003": "info", "CVE-2024-0004": "info"}


def test_trivy_respeta_ignored():
    r = _report([_v("CVE-1", "CRITICAL"), _v("CVE-2", "HIGH")])
    out = TrivyConnector._cve_severities_from_report(r, {"CVE-1"})
    assert "CVE-1" not in out and out["CVE-2"] == "high"


def test_trivy_ignored_none_equivale_a_vacio():
    r = _report([_v("CVE-1", "LOW")])
    assert TrivyConnector._cve_severities_from_report(r, None) == {"CVE-1": "low"}


def test_trivy_cve_repetido_conserva_la_mas_alta_sin_importar_orden():
    a = _report([_v("CVE-9", "HIGH")], [_v("CVE-9", "CRITICAL")])
    b = _report([_v("CVE-9", "CRITICAL")], [_v("CVE-9", "HIGH"), _v("CVE-9", "LOW")])
    assert TrivyConnector._cve_severities_from_report(a, set())["CVE-9"] == "critical"
    assert TrivyConnector._cve_severities_from_report(b, set())["CVE-9"] == "critical"


def test_trivy_reporte_hostil_malformado_no_revienta():
    r = {"Results": [None, "x", {"Vulnerabilities": None}, {"Vulnerabilities": [None, 3, {}, {"VulnerabilityID": ""},
                                                                              {"VulnerabilityID": "CVE-OK"}]}]}
    assert TrivyConnector._cve_severities_from_report(r, set()) == {"CVE-OK": "info"}
    assert TrivyConnector._cve_severities_from_report({}, set()) == {}
    assert TrivyConnector._cve_severities_from_report({"Results": None}, set()) == {}


# ─── DbGovernance (SQLite) ───────────────────────────────────────────────────
@pytest.fixture
async def session(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/gov.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        yield s
    await db_engine.dispose_engine()


def _row(project="p1", cve="CVE-2024-1", status="applied", kind="risk_accepted",
         expires=None, ref=None):
    return SourceException(
        project_key=project, source="defectdojo", finding_ref=ref or f"f-{cve}-{status}-{kind}",
        kind=kind, justification="test", status=status, cve_id=cve, expires_at=expires,
    )


async def test_db_governance_solo_applied_risk_accepted_vigentes(session):
    now = datetime.now(UTC)
    session.add_all([
        _row(cve="cve-2024-0001"),                                          # ok (minúsculas → MAYÚS)
        _row(cve="CVE-2024-0002", expires=now + timedelta(days=5)),         # ok, vigente
        _row(cve="CVE-2024-0003", status="proposed"),
        _row(cve="CVE-2024-0004", status="write_failed"),
        _row(cve="CVE-2024-0005", status="expired"),
        _row(cve="CVE-2024-0006", status="expire_failed"),
        _row(cve="CVE-2024-0007", expires=now - timedelta(days=1)),         # vencida por fecha
        _row(cve=None, ref="sin-cve"),                                      # cve_id NULL
        _row(cve="CVE-2024-0008", kind="false_positive"),
        _row(cve="CVE-2024-0009", kind="out_of_scope"),
        _row(cve="CVE-2024-0010", kind="mitigated"),
        _row(project="otro", cve="CVE-2024-0011"),                          # otro proyecto
    ])
    await session.commit()
    gov = DbGovernance(ttl_seconds=30)
    assert await gov.accepted_cves("p1") == {"CVE-2024-0001", "CVE-2024-0002"}
    assert await gov.accepted_cves("otro") == {"CVE-2024-0011"}
    assert await gov.accepted_cves("inexistente") == set()


async def test_db_governance_expira_en_el_limite(session):
    # expires_at == ahora-ε ⇒ excluida; el borde es exclusivo (> now).
    session.add(_row(cve="CVE-B", expires=datetime.now(UTC) - timedelta(seconds=1)))
    await session.commit()
    assert await DbGovernance().accepted_cves("p1") == set()


async def test_db_governance_cache_e_invalidate(session):
    gov = DbGovernance(ttl_seconds=300)
    assert await gov.accepted_cves("p1") == set()
    session.add(_row(cve="CVE-C"))
    await session.commit()
    assert await gov.accepted_cves("p1") == set()          # cacheado (incluido el vacío)
    await gov.invalidate("p1")
    assert await gov.accepted_cves("p1") == {"CVE-C"}      # refetch


async def test_db_governance_invalidate_es_por_proyecto_y_global(session):
    session.add_all([_row(project="a", cve="CVE-A"), _row(project="b", cve="CVE-B")])
    await session.commit()
    gov = DbGovernance(ttl_seconds=300)
    assert await gov.accepted_cves("a") == {"CVE-A"}
    assert await gov.accepted_cves("b") == {"CVE-B"}
    # Cambia la BD; invalidar solo "a" no debe tocar "b".
    for r in (await session.execute(select(SourceException))).scalars():
        r.status = "expired"
    await session.commit()
    await gov.invalidate("a")
    assert await gov.accepted_cves("a") == set()
    assert await gov.accepted_cves("b") == {"CVE-B"}       # sigue cacheado
    await gov.invalidate()                                  # global
    assert await gov.accepted_cves("b") == set()


def test_protocol_y_null_governance():
    assert isinstance(NullGovernance(), GovernanceProvider)
    assert isinstance(DbGovernance(), GovernanceProvider)


async def test_null_governance_neto_igual_crudo():
    g = NullGovernance()
    assert await g.accepted_cves("x") == set()
    assert await g.invalidate("x") is None


# ─── Aggregator._accepted_container_counts ───────────────────────────────────
class _Gov:
    def __init__(self, cves, boom=False):
        self.cves, self.boom, self.invalidated = set(cves), boom, []

    async def accepted_cves(self, key):
        if self.boom:
            raise RuntimeError("gov caído")
        return set(self.cves)

    async def invalidate(self, project_key=None):
        self.invalidated.append(project_key)


class _Trivy:
    def __init__(self, sevs=None, exc=None):
        self.sevs, self.exc, self.calls = sevs or {}, exc, 0

    async def cve_severities(self, project):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.sevs


def _agg(gov, trivy, *, configured=True, project_uses=True):
    cfg = AppConfig(
        sources={"trivy": SourceConfig(url="http://t", token_env="X")} if configured else {},
        projects=[ProjectConfig(
            key="p1", name="P1",
            trivy=TrivyProjectConfig(report_path="r.json") if project_uses else None)],
    )
    a = Aggregator(cfg, TTLCache(60), governance=gov)
    a._build_connector = lambda name: trivy if name == "trivy" else None  # type: ignore[method-assign]
    return a, cfg.projects[0]


async def test_agg_default_governance_es_null():
    a, p = _agg(None, _Trivy({"CVE-1": "critical"}))
    assert isinstance(a._governance, NullGovernance)
    assert await a._accepted_container_counts(p) == SeverityCount()


async def test_agg_cuenta_por_severidad():
    sevs = {"CVE-1": "critical", "CVE-2": "critical", "CVE-3": "high", "CVE-4": "low", "CVE-5": "critical"}
    a, p = _agg(_Gov({"CVE-1", "CVE-3", "CVE-4", "CVE-2"}), _Trivy(sevs))
    c = await a._accepted_container_counts(p)
    assert (c.critical, c.high, c.medium, c.low, c.info) == (2, 1, 0, 1, 0)


async def test_agg_cve_aceptado_que_trivy_no_reporta_no_se_resta():
    # Harbor lo reporta, Trivy no (o está en wolf-ignore ⇒ ausente del mapa).
    a, p = _agg(_Gov({"CVE-HARBOR-ONLY", "CVE-1"}), _Trivy({"CVE-1": "high"}))
    c = await a._accepted_container_counts(p)
    assert c.total == 1 and c.high == 1


async def test_agg_trivy_caido_fail_closed():
    a, p = _agg(_Gov({"CVE-1"}), _Trivy(exc=ConnectorUnavailable("down")))
    assert (await a._accepted_container_counts(p)).total == 0


async def test_agg_trivy_no_configurado_fail_closed_y_no_consulta_gobierno():
    for kw in ({"configured": False}, {"project_uses": False}):
        gov, trivy = _Gov({"CVE-1"}), _Trivy({"CVE-1": "critical"})
        a, p = _agg(gov, trivy, **kw)
        assert (await a._accepted_container_counts(p)).total == 0
        assert trivy.calls == 0


async def test_agg_sin_aceptados_no_pega_a_trivy():
    trivy = _Trivy({"CVE-1": "critical"})
    a, p = _agg(_Gov(set()), trivy)
    assert (await a._accepted_container_counts(p)).total == 0
    assert trivy.calls == 0


async def test_agg_connector_none_fail_closed():
    a, p = _agg(_Gov({"CVE-1"}), None)
    assert (await a._accepted_container_counts(p)).total == 0


async def test_agg_invalidate_governance_delega():
    gov = _Gov(set())
    a, _ = _agg(gov, _Trivy())
    await a.invalidate_governance("p1")
    await a.invalidate_governance()
    assert gov.invalidated == ["p1", None]
