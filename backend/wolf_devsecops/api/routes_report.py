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

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import require_role
from ..core import ignore as ign
from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..db.session import get_db
from ..edition import FEATURE_REPORTS_COMPLIANCE, current_edition, require_feature
from ..models import (
    PostureSnapshot,
    ProjectReport,
    ProjectSummary,
    ReportSection,
    SourceResult,
    SourceStatus,
)
from ..rbac.roles import ALL_ROLES
from ..services import posture_history_service, report_pdf
from ..settings import get_settings
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


@router.get(
    "/projects/{key}/report.pdf",
    dependencies=[Depends(require_feature(FEATURE_REPORTS_COMPLIANCE))],  # feature Pro (402)
)
async def project_report_pdf(
    key: str,
    lang: str = Query("es"),
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Reporte de cumplimiento en PDF (2.4, Pro): score+tendencia, checks, mapeo
    ISO/SSDF/DSOMM y riesgos aceptados vigentes. Evidencia para auditoría."""
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")

    summary = await aggregator.get_summary(project)
    rows = await posture_history_service.list_history(db, key, days=90)
    history = [
        PostureSnapshot(
            captured_at=r.captured_at, compliance_pct=r.compliance_pct, band=r.band,
            crit=r.crit, high=r.high, med=r.med, low=r.low, coverage_pct=r.coverage_pct,
        )
        for r in rows
    ]
    entries = ign.load_ignore(get_settings().ignore_path).entries_for(key)
    accepted = [e for e in entries if not e.is_expired(date.today())]

    pdf = report_pdf.build_pdf(
        summary, history, accepted, lang=lang, edition=current_edition()
    )
    filename = f"wolf-cumplimiento-{key}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
