"""Reporte documental por proyecto — GET /api/v1/projects/{key}/report.

Ensambla en UN solo contrato lo que el frontend (o un export a Markdown/PDF)
necesita para entregar un reporte: postura (score/banda/checks) y, por cada
herramienta, QUÉ está afectado y CÓMO remediarlo. Reutiliza el agregador y los
detalles normalizados de Trivy/DefectDojo/Sonar; no vuelve a hablar con las
fuentes más de lo que ya hace un resumen + sus detalles accionables.

El contrato es DATO ESTRUCTURADO: el idioma del reporte lo decide el frontend
(antes el backend redactaba frases en español y se mezclaban los idiomas).

Solo lectura: cualquier usuario autenticado (viewer/operator/admin).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import require_role
from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..models import (
    ProjectReport,
    ProjectSummary,
    ReportSection,
    SourceResult,
    SourceStatus,
)
from ..rbac.roles import ALL_ROLES
from .deps import get_aggregator, get_config

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])

_LIVE = (SourceStatus.ok, SourceStatus.stale)


async def _findings_section(
    aggregator: Aggregator, project, summary_block: SourceResult, source: str,
    detail_source: str, link: str | None,
) -> ReportSection:
    """Sección con lista accionable. `detail_source` es la fuente del drill-down
    (trivy para contenedores, defectdojo, sonarqube)."""
    findings: list[dict] = []
    counts = None
    total_active = None
    if summary_block.status in _LIVE and summary_block.data is not None:
        counts = summary_block.data.counts
        total_active = summary_block.data.total_active
        if summary_block.data.total_active > 0:
            detail = await aggregator.get_source_detail(project, detail_source)
            findings = (detail.payload or {}).get("findings", []) if detail else []
    return ReportSection(
        source=source,
        kind="findings",
        status=summary_block.status,
        counts=counts,
        total_active=total_active,
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
    sections.append(await _findings_section(
        aggregator, project, s.containers, "containers", "trivy", links.get("harbor")))
    # Hallazgos (DefectDojo) — con detalle accionable.
    sections.append(await _findings_section(
        aggregator, project, s.findings, "defectdojo", "defectdojo", links.get("defectdojo")))

    # Calidad (SonarQube) — resumen + issues con archivo:línea, cómo arreglar y
    # evidencia (los trae fetch_detail del conector).
    sonar_findings: list[dict] = []
    if s.quality.status in _LIVE and s.quality.data is not None:
        detail = await aggregator.get_source_detail(project, "sonarqube")
        sonar_findings = (detail.payload or {}).get("findings", []) if detail else []
    sections.append(ReportSection(
        source="sonarqube",
        kind="quality",
        status=s.quality.status,
        quality=s.quality.data if s.quality.status in _LIVE else None,
        findings=sonar_findings,
        link=links.get("sonarqube"),
    ))

    # CI / Release (GitHub).
    sections.append(ReportSection(
        source="github",
        kind="ci",
        status=s.ci.status,
        ci=s.ci.data if s.ci.status in _LIVE else None,
        release=s.release.data if s.release.status in _LIVE else None,
        link=links.get("github"),
    ))

    return ProjectReport(
        key=s.key, name=s.name, org=s.org, generated_at=s.generated_at,
        posture=s.posture, sections=sections,
    )
