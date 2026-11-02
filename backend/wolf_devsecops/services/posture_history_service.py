"""Tendencia histórica de postura (ADR-019). SOLO métricas agregadas, nunca findings.

La captura es una ACCIÓN explícita (CLI `snapshot-posture`), no un efecto de GET.
Reusa `core.posture.score_project` (lo mismo que alimenta el panel).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..core.posture import score_project
from ..models import ProjectSummary
from ..models_db.posture import PostureSnapshot as PostureSnapshotRow
from . import event_service

MAX_LIMIT = 1000


def _severity_totals(summary: ProjectSummary) -> tuple[int, int, int, int]:
    """crit/high/med/low del panel. Findings (DefectDojo) y Contenedores (Trivy/Harbor)
    se solapan (rúbrica, reconciliación opción A): se toma el MAYOR por severidad
    para no contar dos veces el mismo CVE."""
    totals = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for block in (summary.findings, summary.containers):
        if block.data is None:
            continue
        for sev in totals:
            totals[sev] = max(totals[sev], getattr(block.data.counts, sev))
    return totals["critical"], totals["high"], totals["medium"], totals["low"]


def row_from_summary(summary: ProjectSummary, *, now: datetime | None = None) -> PostureSnapshotRow | None:
    """Fila agregada desde un ProjectSummary, o None si no hay score (sin_datos)."""
    posture = summary.posture or score_project(summary)
    if posture.score is None:
        return None  # sin datos: no hay punto que graficar
    crit, high, med, low = _severity_totals(summary)
    return PostureSnapshotRow(
        project_key=summary.key,
        captured_at=now or datetime.now(UTC),
        compliance_pct=float(posture.score),
        band=str(posture.band),
        crit=crit,
        high=high,
        med=med,
        low=low,
        coverage_pct=float(posture.coverage),
    )


async def capture_snapshots(
    session: AsyncSession, aggregator: Aggregator, config: AppConfig
) -> tuple[int, list[str]]:
    """Una fila por proyecto con datos, con un mismo `captured_at`. Devuelve
    (filas escritas, keys omitidas por sin_datos / error de agregación)."""
    now = datetime.now(UTC)
    written = 0
    skipped: list[str] = []
    for project in config.projects:
        try:
            summary = await aggregator.get_summary(project)
        except Exception:  # noqa: BLE001 — un proyecto roto no aborta la corrida
            skipped.append(project.key)
            continue
        row = row_from_summary(summary, now=now)
        if row is None:
            skipped.append(project.key)
            continue
        # Antes de añadir el punto nuevo, trae el anterior y detecta los cambios.
        prev = await event_service.latest_before(session, project.key)
        session.add(row)
        for ev in event_service.detect(prev, row):
            session.add(ev)
        written += 1
    await session.commit()
    return written, skipped


async def list_history(
    session: AsyncSession, project_key: str, limit: int | None = None
) -> list[PostureSnapshotRow]:
    """Serie ascendente por captured_at. Con `limit`, las N más RECIENTES (aun así ascendentes)."""
    q = select(PostureSnapshotRow).where(PostureSnapshotRow.project_key == project_key)
    if limit is not None:
        q = q.order_by(PostureSnapshotRow.captured_at.desc()).limit(limit)
        rows = list((await session.execute(q)).scalars())
        rows.reverse()
        return rows
    q = q.order_by(PostureSnapshotRow.captured_at.asc())
    return list((await session.execute(q)).scalars())
