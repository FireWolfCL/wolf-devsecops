"""Reporte documental por proyecto — GET /api/v1/projects/{key}/report.

Ensambla en UN solo contrato lo que el frontend (o un export a Markdown/PDF)
necesita para entregar un reporte: postura (score/banda/checks) y, por cada
herramienta, QUÉ está afectado y CÓMO remediarlo. Reutiliza el agregador y los
detalles normalizados de Trivy/DefectDojo; no vuelve a hablar con las fuentes más
de lo que ya hace un resumen + sus detalles accionables.

Solo lectura: cualquier usuario autenticado (viewer/operator/admin).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import require_role
from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..models import (
    FindingsSummary,
    ProjectReport,
    ProjectSummary,
    QualitySummary,
    ReportSection,
    SourceResult,
    SourceStatus,
)
from ..rbac.roles import ALL_ROLES
from .deps import get_aggregator, get_config

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])

_LIVE = (SourceStatus.ok, SourceStatus.stale)

_BAND_LABEL = {
    "solido": "Sólido", "aceptable": "Aceptable", "en_riesgo": "En riesgo",
    "critico": "Crítico", "evidencia_insuficiente": "Evidencia insuficiente",
    "sin_datos": "Sin datos",
}


def _findings_headline(block: SourceResult[FindingsSummary]) -> str:
    if block.status == SourceStatus.not_configured:
        return "Sin conectar."
    if block.status not in _LIVE or block.data is None:
        return "Fuente no disponible."
    c = block.data.counts
    return (
        f"{block.data.total_active} activos · {c.critical} críticos · "
        f"{c.high} altos · {c.medium} medios · {c.low} bajos"
    )


def _quality_headline(block: SourceResult[QualitySummary]) -> tuple[str, str | None]:
    if block.status == SourceStatus.not_configured:
        return "Sin conectar.", None
    if block.status not in _LIVE or block.data is None:
        return "Fuente no disponible.", None
    q = block.data
    cov = f"{q.coverage:.0f}%" if q.coverage is not None else "—"
    note = f"Gate {q.quality_gate} · {q.bugs} bugs · {q.vulnerabilities} vulns · cobertura {cov}"
    return note, note


async def _section_with_findings(
    aggregator: Aggregator, project, summary_block: SourceResult, source: str,
    label: str, link: str | None,
) -> ReportSection:
    """Sección con lista accionable (contenedores=trivy, hallazgos=defectdojo)."""
    findings: list[dict] = []
    if summary_block.status in _LIVE and summary_block.data is not None and summary_block.data.total_active > 0:
        detail = await aggregator.get_source_detail(project, source)
        findings = (detail.payload or {}).get("findings", []) if detail else []
    return ReportSection(
        source="containers" if source == "trivy" else source,
        tool_label=label,
        status=summary_block.status,
        headline=_findings_headline(summary_block),
        findings=findings,
        link=link,
    )


@router.get("/projects/{key}/report", response_model=ProjectReport)
async def project_report(
    key: str,
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
) -> ProjectReport:
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")

    s: ProjectSummary = await aggregator.get_summary(project)
    links = s.links

    sections: list[ReportSection] = []
    # Contenedores (Trivy/Harbor) — con detalle accionable.
    sections.append(await _section_with_findings(
        aggregator, project, s.containers, "trivy", "Contenedores (Trivy/Harbor)", links.get("harbor")))
    # Hallazgos (DefectDojo) — con detalle accionable.
    sections.append(await _section_with_findings(
        aggregator, project, s.findings, "defectdojo", "Hallazgos (DefectDojo)", links.get("defectdojo")))
    # Calidad (SonarQube) — nota, sin lista.
    q_head, q_note = _quality_headline(s.quality)
    sections.append(ReportSection(
        source="sonarqube", tool_label="Calidad de código (SonarQube)", status=s.quality.status,
        headline=q_head, note=q_note, link=links.get("sonarqube")))
    # CI / Release (GitHub) — nota.
    ci = s.ci
    if ci.status in _LIVE and ci.data is not None:
        ci_head = f"CI: {ci.data.status} (workflow {ci.data.workflow})"
    elif ci.status == SourceStatus.not_configured:
        ci_head = "Sin conectar."
    else:
        ci_head = "Fuente no disponible."
    rel = s.release
    rel_note = None
    if rel.status in _LIVE and rel.data is not None:
        rel_note = f"Release {rel.data.tag or '—'} · SBOM {'adjunto' if rel.data.sbom_url else 'no adjunto'}"
    sections.append(ReportSection(
        source="github", tool_label="CI / Release (GitHub)", status=ci.status,
        headline=ci_head, note=rel_note, link=links.get("github")))

    # Línea humana de postura.
    posture_summary = "Sin datos de postura."
    if s.posture is not None and s.posture.score is not None:
        band = _BAND_LABEL.get(s.posture.band, s.posture.band)
        posture_summary = (
            f"{s.posture.score}% de cumplimiento · {band} · cobertura {s.posture.coverage}% "
            f"(rúbrica v{s.posture.rubric_version})"
        )

    return ProjectReport(
        key=s.key, name=s.name, org=s.org, generated_at=s.generated_at,
        posture=s.posture, posture_summary=posture_summary, sections=sections,
    )
