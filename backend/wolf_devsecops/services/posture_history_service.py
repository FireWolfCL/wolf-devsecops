"""Tendencia histórica de postura (ADR-019). SOLO métricas agregadas, nunca findings.

La captura es una ACCIÓN explícita (CLI `snapshot-posture`), no un efecto de GET.
Reusa `core.posture.score_project` (lo mismo que alimenta el panel).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import edition
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
    # Estado de CI y gate para los triggers de notificación (2.5). None si la fuente
    # no está configurada (no se dispara alerta por algo que no se mide).
    ci_status = summary.ci.data.status.value if summary.ci.data is not None else None
    quality_gate = summary.quality.data.quality_gate if summary.quality.data is not None else None
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
        rubric_version=posture.rubric_version,
        ci_status=ci_status,
        quality_gate=quality_gate,
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


def effective_window_days(days: int | None) -> int | None:
    """Ventana efectiva: el `days` pedido acotado por el tope de la edición.

    Community topa en `COMMUNITY_HISTORY_DAYS` (p.ej. 30): pedir 90 se recorta a 30.
    Pro/Enterprise (`history.unlimited`) no tienen tope, así que respetan el `days`
    pedido tal cual, y sin `days` devuelven todo (None).
    """
    cap = edition.history_window_days()
    if cap is None:
        return days  # Pro+: sin tope; None = ilimitado
    return cap if days is None else min(days, cap)


async def list_history(
    session: AsyncSession,
    project_key: str,
    limit: int | None = None,
    days: int | None = None,
) -> list[PostureSnapshotRow]:
    """Serie ascendente por captured_at. Con `limit`, las N más RECIENTES (aun así ascendentes).

    Ventana por edición (feature Pro `history.unlimited`): en Community solo se
    devuelven los snapshots de los últimos N días; Pro/Enterprise = ilimitado.
    `days` acota adicionalmente la ventana (p.ej. 7/30/90 del selector del panel).
    """
    q = select(PostureSnapshotRow).where(PostureSnapshotRow.project_key == project_key)
    window_days = effective_window_days(days)
    if window_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        q = q.where(PostureSnapshotRow.captured_at >= cutoff)
    if limit is not None:
        q = q.order_by(PostureSnapshotRow.captured_at.desc()).limit(limit)
        rows = list((await session.execute(q)).scalars())
        rows.reverse()
        return rows
    q = q.order_by(PostureSnapshotRow.captured_at.asc())
    return list((await session.execute(q)).scalars())


async def list_spark(
    session: AsyncSession, days: int | None = None
) -> tuple[int | None, dict[str, list[float]]]:
    """Series compactas de % de cumplimiento por proyecto (una sola consulta).

    Devuelve (ventana_efectiva, {key: [compliance ascendente]}). Alimenta los
    sparklines del dashboard sin N llamadas ni golpear las fuentes (ADR-012).
    """
    window_days = effective_window_days(days)
    q = select(
        PostureSnapshotRow.project_key,
        PostureSnapshotRow.compliance_pct,
    )
    if window_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        q = q.where(PostureSnapshotRow.captured_at >= cutoff)
    q = q.order_by(PostureSnapshotRow.project_key.asc(), PostureSnapshotRow.captured_at.asc())
    series: dict[str, list[float]] = {}
    for key, pct in (await session.execute(q)).all():
        series.setdefault(key, []).append(float(pct))
    return window_days, series
