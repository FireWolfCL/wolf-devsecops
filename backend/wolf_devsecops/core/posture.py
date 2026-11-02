"""Scoring de postura ("% de cumplimiento") — docs/POSTURE-RUBRIC.md (v1.0).

Funciones PURAS y deterministas: mismo ProjectSummary + misma config ⇒ mismo
resultado, sin reloj/I/O/aleatoriedad. El score NO se persiste (ADR-012): se
calcula al vuelo desde `ProjectSummary`. Sin fuga: cada check expone solo
{id, result, weight, earned, reason_code}; jamás rutas, código ni URLs internas.

Cada check mapea a OWASP DSOMM + NIST SSDF (ver la rúbrica); aquí solo la lógica.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..models import (
    CheckResult,
    CIStatus,
    GlobalPosture,
    PostureScore,
    ProjectSummary,
    SourceResult,
    SourceStatus,
)

RUBRIC_VERSION = "1.0"


@dataclass(frozen=True)
class PostureConfig:
    coverage_min: float = 60.0  # umbral del check de cobertura (no secreto)


_DEFAULT_CFG = PostureConfig()


def _half_up(value: float) -> int:
    """Redondeo HALF_UP explícito (no el banker's rounding de round())."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ─── Elegibilidad de fuente (§1 de la rúbrica) ───────────────────────────────
@dataclass
class _Elig:
    ok: bool
    reason: str
    stale: bool
    degraded: bool


def _eligible(s: SourceResult) -> _Elig:
    if s.status == SourceStatus.not_configured:
        return _Elig(False, "source_not_configured", False, False)
    if s.status == SourceStatus.unavailable or s.data is None:
        return _Elig(False, "source_unavailable", False, True)
    return _Elig(True, "ok", s.status == SourceStatus.stale, False)


# ─── Un check: (result, earned_fraction 0..1, reason, stale, degraded) ────────
@dataclass
class _Outcome:
    result: str  # pass | partial | fail | na
    frac: float  # 0 | 0.5 | 1  (na ⇒ ignorado)
    reason: str
    stale: bool
    degraded: bool


def _na(e: _Elig) -> _Outcome:
    return _Outcome("na", 0.0, e.reason, e.stale, e.degraded)


def _verdict(e: _Elig, ok: bool) -> _Outcome:
    return (
        _Outcome("pass", 1.0, "ok", e.stale, e.degraded)
        if ok
        else _Outcome("fail", 0.0, "threshold_not_met", e.stale, e.degraded)
    )


def _c_ci(s: SourceResult) -> _Outcome:
    e = _eligible(s)
    if not e.ok:
        return _na(e)
    st = s.data.status
    if st == CIStatus.success:
        return _Outcome("pass", 1.0, "ok", e.stale, e.degraded)
    if st == CIStatus.failure:
        return _Outcome("fail", 0.0, "threshold_not_met", e.stale, e.degraded)
    return _Outcome("na", 0.0, "no_verdict", e.stale, e.degraded)  # running/unknown


def _c_count_zero(s: SourceResult, attr: str) -> _Outcome:
    e = _eligible(s)
    if not e.ok:
        return _na(e)
    return _verdict(e, getattr(s.data.counts, attr) == 0)


def _c_findings_cve(summary: ProjectSummary, attr: str) -> _Outcome:
    """Check de severidad de Hallazgos (DefectDojo) que NO cuenta dos veces los
    CVEs de contenedor (reconciliación, opción A de la rúbrica).

    El pilar de Contenedores (Trivy/Harbor) es la fuente autoritativa de los CVEs
    de imagen/filesystem. Cuando está activo, la severidad de Hallazgos que ya
    está cubierta por Contenedores (`findings<=containers`) pasa a `na`
    (`covered_by_containers`): ni penaliza ni infla cobertura. Solo si DefectDojo
    reporta MÁS que Contenedores (exceso = hallazgos de aplicación, p. ej. SAST),
    ese exceso sí se evalúa y puede penalizar. Sin pilar de contenedores, el check
    se evalúa normal sobre Hallazgos.
    """
    ef = _eligible(summary.findings)
    if not ef.ok:
        return _na(ef)
    ec = _eligible(summary.containers)
    f = getattr(summary.findings.data.counts, attr)
    if ec.ok:
        c = getattr(summary.containers.data.counts, attr)
        if f <= c:  # todo lo de esta severidad ya lo representa Contenedores
            return _Outcome("na", 0.0, "covered_by_containers", ef.stale, ef.degraded)
        return _verdict(ef, False)  # exceso de hallazgos de app por encima de los CVEs
    return _verdict(ef, f == 0)


def _c_gate(s: SourceResult) -> _Outcome:
    e = _eligible(s)
    if not e.ok:
        return _na(e)
    g = s.data.quality_gate
    if g == "OK":
        return _Outcome("pass", 1.0, "ok", e.stale, e.degraded)
    if g == "WARN":
        return _Outcome("partial", 0.5, "ok", e.stale, e.degraded)
    if g == "ERROR":
        return _Outcome("fail", 0.0, "threshold_not_met", e.stale, e.degraded)
    return _Outcome("na", 0.0, "no_verdict", e.stale, e.degraded)  # NONE


def _c_quality_vulns(s: SourceResult) -> _Outcome:
    e = _eligible(s)
    if not e.ok:
        return _na(e)
    return _verdict(e, s.data.vulnerabilities == 0)


def _c_coverage(s: SourceResult, cov_min: float) -> _Outcome:
    e = _eligible(s)
    if not e.ok:
        return _na(e)
    cov = s.data.coverage
    if cov is None:
        return _Outcome("na", 0.0, "missing_data", e.stale, e.degraded)
    return _verdict(e, cov >= cov_min)


def _c_sbom(s: SourceResult) -> _Outcome:
    e = _eligible(s)
    if not e.ok:
        return _na(e)
    if s.data.tag is None:
        return _Outcome("na", 0.0, "missing_data", e.stale, e.degraded)
    return _verdict(e, s.data.sbom_url is not None)


# ─── Evaluación cruda de un proyecto (compartida por proyecto y global) ───────
@dataclass
class _Raw:
    checks: list[CheckResult]
    ganado: float
    peso_e: int
    stale: bool
    degraded: bool
    critical_open: bool


def _critical_open(summary: ProjectSummary) -> bool:
    """Crítico abierto en una fuente elegible de hallazgos o de contenedores."""
    for block in (summary.findings, summary.containers):
        e = _eligible(block)
        if e.ok and block.data.counts.critical >= 1:
            return True
    return False


def _evaluate(summary: ProjectSummary, cfg: PostureConfig) -> _Raw:
    # (id, peso, outcome) en el orden de la rúbrica.
    specs: list[tuple[str, int, _Outcome]] = [
        ("ci-green", 10, _c_ci(summary.ci)),
        ("findings-no-critical", 20, _c_findings_cve(summary, "critical")),
        ("findings-no-high", 10, _c_findings_cve(summary, "high")),
        ("containers-no-critical", 20, _c_count_zero(summary.containers, "critical")),
        ("containers-no-high", 10, _c_count_zero(summary.containers, "high")),
        ("quality-gate", 10, _c_gate(summary.quality)),
        ("quality-no-vulns", 5, _c_quality_vulns(summary.quality)),
        ("quality-coverage", 5, _c_coverage(summary.quality, cfg.coverage_min)),
        ("release-sbom", 10, _c_sbom(summary.release)),
    ]
    checks: list[CheckResult] = []
    ganado = 0.0
    peso_e = 0
    stale = False
    degraded = False
    for cid, weight, o in specs:
        earned = weight * o.frac
        checks.append(
            CheckResult(id=cid, result=o.result, weight=weight, earned=earned, reason_code=o.reason)
        )
        if o.result != "na":
            peso_e += weight
            ganado += earned
        stale = stale or o.stale
        degraded = degraded or (o.reason == "source_unavailable")
    return _Raw(checks, ganado, peso_e, stale, degraded, _critical_open(summary))


# ─── Banda (§3–§4) ────────────────────────────────────────────────────────────
def _band(score: int, coverage: int, critical_open: bool) -> tuple[str, str | None]:
    if coverage < 40:
        return "evidencia_insuficiente", "coverage"
    if score >= 90:
        band = "solido"
    elif score >= 70:
        band = "aceptable"
    elif score >= 50:
        band = "en_riesgo"
    else:
        band = "critico"
    capped: str | None = None
    if coverage < 70 and band == "solido":
        band, capped = "aceptable", "coverage"
    if critical_open and band in ("solido", "aceptable"):
        band, capped = "en_riesgo", "critical"
    return band, capped


# ─── API pública ──────────────────────────────────────────────────────────────
def score_project(summary: ProjectSummary, cfg: PostureConfig = _DEFAULT_CFG) -> PostureScore:
    r = _evaluate(summary, cfg)
    if r.peso_e == 0:
        return PostureScore(
            rubric_version=RUBRIC_VERSION, score=None, coverage=0, band="sin_datos",
            stale=r.stale, degraded=r.degraded, checks=r.checks,
        )
    score = _half_up(100 * r.ganado / r.peso_e)
    coverage = _half_up(r.peso_e)  # peso total = 100 ⇒ cobertura == peso_e
    band, capped = _band(score, coverage, r.critical_open)
    return PostureScore(
        rubric_version=RUBRIC_VERSION, score=score, coverage=coverage, band=band,
        stale=r.stale, degraded=r.degraded, capped_by=capped, checks=r.checks,
    )


def score_global(summaries: list[ProjectSummary], cfg: PostureConfig = _DEFAULT_CFG) -> GlobalPosture:
    total_ganado = 0.0
    total_peso = 0
    any_critical = False
    distribution: dict[str, int] = {}
    at_risk: list[str] = []
    critical: list[str] = []
    for s in summaries:
        r = _evaluate(s, cfg)
        ps = score_project(s, cfg)
        distribution[ps.band] = distribution.get(ps.band, 0) + 1
        if ps.band == "en_riesgo":
            at_risk.append(s.key)
        elif ps.band == "critico":
            critical.append(s.key)
        if r.peso_e > 0:
            total_ganado += r.ganado
            total_peso += r.peso_e
        any_critical = any_critical or r.critical_open

    n = len(summaries)
    if total_peso == 0:
        return GlobalPosture(
            global_score=None, coverage=0, band="sin_datos",
            distribution=distribution, at_risk=at_risk, critical=critical, project_count=n,
        )
    gscore = _half_up(100 * total_ganado / total_peso)
    gcov = _half_up(100 * total_peso / (100 * n)) if n else 0
    band, _ = _band(gscore, gcov, False)
    # Regla de crítico global (§5): si algún proyecto tiene crítico abierto, tope Aceptable.
    if any_critical and band == "solido":
        band = "aceptable"
    return GlobalPosture(
        global_score=gscore, coverage=gcov, band=band,
        distribution=distribution, at_risk=at_risk, critical=critical, project_count=n,
    )
