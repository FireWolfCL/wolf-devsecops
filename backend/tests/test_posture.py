"""Scoring de postura (docs/POSTURE-RUBRIC.md v1.0). Funciones puras."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wolf_devsecops.core.posture import PostureConfig, score_global, score_project
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


def _ok(data):
    return SourceResult(status=SourceStatus.ok, data=data)


def _nc():
    return SourceResult(status=SourceStatus.not_configured)


def _unavail():
    return SourceResult(status=SourceStatus.unavailable, error="caída")


def _summary(**blocks) -> ProjectSummary:
    base = dict(ci=_nc(), findings=_nc(), quality=_nc(), release=_nc(), containers=_nc())
    base.update(blocks)
    return ProjectSummary(key="k", name="K", generated_at=datetime.now(UTC), **base)


def _findings(critical=0, high=0):
    return FindingsSummary(counts=SeverityCount(critical=critical, high=high), total_active=critical + high)


def _ci(status):
    return CISummary(status=status, workflow="ci.yml")


# ─── casos de la rúbrica ──────────────────────────────────────────────────────
def test_todo_na_es_sin_datos():
    p = score_project(_summary())
    assert p.score is None and p.band == "sin_datos" and p.coverage == 0


def test_solo_ci_verde_evidencia_insuficiente():
    # 100% del poco peso medido, pero cobertura 10 < 40 ⇒ no es "Sólido".
    p = score_project(_summary(ci=_ok(_ci(CIStatus.success))))
    assert p.score == 100 and p.coverage == 10
    assert p.band == "evidencia_insuficiente" and p.capped_by == "coverage"


def test_ci_running_es_na_no_verdict():
    p = score_project(_summary(ci=_ok(_ci(CIStatus.running))))
    ci = next(c for c in p.checks if c.id == "ci-green")
    assert ci.result == "na" and ci.reason_code == "no_verdict"
    assert p.score is None  # nada más medido


def _todo_pass_blocks():
    return dict(
        ci=_ok(_ci(CIStatus.success)),
        findings=_ok(_findings(0, 0)),
        containers=_ok(_findings(0, 0)),
        quality=_ok(QualitySummary(quality_gate="OK", vulnerabilities=0, coverage=85.0)),
        release=_ok(ReleaseSummary(tag="v1.0.0", sbom_url="https://e/sbom.json")),
    )


def test_todo_pass_es_solido_100():
    # Con Contenedores activo, los checks de Hallazgos-CVE son NA (cubiertos): la
    # cobertura baja a 70 (findings-crit 20 + findings-high 10 salen del cálculo),
    # pero el score sigue 100 (todo lo medido cumple) ⇒ sólido sin tope.
    p = score_project(_summary(**_todo_pass_blocks()))
    assert p.score == 100 and p.coverage == 70 and p.band == "solido" and p.capped_by is None


def test_findings_cve_cubierto_por_contenedores_no_doble_cuenta():
    # Crítico que aparece en Hallazgos Y en Contenedores: NO penaliza dos veces.
    blocks = _todo_pass_blocks()
    blocks["findings"] = _ok(_findings(critical=1))
    blocks["containers"] = _ok(_findings(critical=1))  # el mismo CVE, fuente autoritativa
    p = score_project(_summary(**blocks))
    fnc = next(c for c in p.checks if c.id == "findings-no-critical")
    cnc = next(c for c in p.checks if c.id == "containers-no-critical")
    assert fnc.result == "na" and fnc.reason_code == "covered_by_containers"
    assert cnc.result == "fail"  # el pilar de contenedores sí lo cuenta (una vez)
    assert p.band == "en_riesgo" and p.capped_by == "critical"


def test_findings_exceso_app_si_penaliza():
    # Hallazgos con MÁS críticos que Contenedores ⇒ el exceso (app/SAST) sí penaliza.
    blocks = _todo_pass_blocks()
    blocks["findings"] = _ok(_findings(critical=2))
    blocks["containers"] = _ok(_findings(critical=1))
    p = score_project(_summary(**blocks))
    fnc = next(c for c in p.checks if c.id == "findings-no-critical")
    assert fnc.result == "fail" and fnc.reason_code == "threshold_not_met"


def test_quality_warn_es_parcial():
    p = score_project(_summary(quality=_ok(QualitySummary(quality_gate="WARN"))))
    gate = next(c for c in p.checks if c.id == "quality-gate")
    assert gate.result == "partial" and gate.earned == pytest.approx(5.0)  # 0.5 * 10


def test_unavailable_vs_not_configured_mismo_score_distinta_marca():
    # Mismos checks medidos (solo CI), uno con findings caído, otro sin configurar.
    a = score_project(_summary(ci=_ok(_ci(CIStatus.success)), findings=_unavail()))
    b = score_project(_summary(ci=_ok(_ci(CIStatus.success)), findings=_nc()))
    assert a.score == b.score  # la caída NO cambia el score
    assert a.degraded is True and b.degraded is False


def test_coverage_none_es_na():
    p = score_project(_summary(quality=_ok(QualitySummary(quality_gate="OK", coverage=None))))
    cov = next(c for c in p.checks if c.id == "quality-coverage")
    assert cov.result == "na" and cov.reason_code == "missing_data"


def test_release_sin_tag_es_na():
    p = score_project(_summary(release=_ok(ReleaseSummary(tag=None))))
    sbom = next(c for c in p.checks if c.id == "release-sbom")
    assert sbom.result == "na" and sbom.reason_code == "missing_data"


def test_coverage_min_configurable():
    q = _ok(QualitySummary(quality_gate="OK", coverage=55.0))
    assert next(c for c in score_project(_summary(quality=q)).checks if c.id == "quality-coverage").result == "fail"
    p = score_project(_summary(quality=q), PostureConfig(coverage_min=50.0))
    assert next(c for c in p.checks if c.id == "quality-coverage").result == "pass"


# ─── global ───────────────────────────────────────────────────────────────────
def test_global_pondera_por_evidencia_y_excluye_sin_datos():
    completo = _summary(**_todo_pass_blocks())           # score 100, peso_e 70 (CVE-Hallazgos NA)
    sin_datos = _summary()                               # score None, excluido del cálculo
    g = score_global([completo, sin_datos])
    assert g.global_score == 100 and g.project_count == 2
    assert g.distribution.get("solido") == 1 and g.distribution.get("sin_datos") == 1
    assert g.coverage == 35  # 70 de 200 peso posible (CVE-Hallazgos cubiertos por Contenedores)


def test_global_critico_topa_aceptable():
    blocks = _todo_pass_blocks()
    blocks["containers"] = _ok(_findings(critical=2))
    g = score_global([_summary(**blocks)])
    assert "en_riesgo" in g.distribution  # el proyecto quedó en riesgo por el crítico


# ─── only_checks: comparación por rama (ADR-024) ──────────────────────────────
def test_only_checks_ci_green_excluye_el_resto():
    # ci verde + findings con crítico: normalmente el crítico tapa la banda, pero con
    # only_checks={ci-green} SOLO cuenta ci-green ⇒ score 100, cobertura 10.
    from wolf_devsecops.core.posture import BRANCH_COMPARABLE_CHECKS

    blocks = _todo_pass_blocks()
    blocks["findings"] = _ok(_findings(critical=3))
    p = score_project(_summary(**blocks), only_checks=BRANCH_COMPARABLE_CHECKS)
    assert p.score == 100 and p.coverage == 10
    ci = next(c for c in p.checks if c.id == "ci-green")
    assert ci.result == "pass"
    # todo lo no comparable queda na con la razón explícita
    no_comp = [c for c in p.checks if c.id != "ci-green"]
    assert no_comp and all(c.result == "na" and c.reason_code == "not_branch_comparable" for c in no_comp)


def test_only_checks_ci_rojo_da_cero():
    p = score_project(_summary(ci=_ok(_ci(CIStatus.failure))), only_checks=frozenset({"ci-green"}))
    assert p.score == 0 and p.coverage == 10
    assert next(c for c in p.checks if c.id == "ci-green").result == "fail"


def test_only_checks_sin_run_es_sin_datos():
    # rama sin verdicto de CI (unknown) ⇒ ci-green na ⇒ nada medido ⇒ sin_datos (honesto).
    p = score_project(_summary(ci=_ok(_ci(CIStatus.unknown))), only_checks=frozenset({"ci-green"}))
    assert p.score is None and p.band == "sin_datos"
