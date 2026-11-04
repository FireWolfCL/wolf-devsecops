"""Detección de cambios de postura → eventos (campana + reporte de avance).

Compara el snapshot NUEVO de un proyecto contra el ANTERIOR y emite eventos:
aparición/resolución de críticos y altos, y mejora/empeora de banda o de % de
cumplimiento. No guarda texto humano (el frontend lo arma en el idioma del
usuario). Es la base de la campana y del reporte "qué vamos parchando".

SOLO métricas agregadas (ADR-019): nunca findings crudos, nunca secretos.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models_db.events import EventRead, ProjectEvent
from ..models_db.posture import PostureSnapshot as Row

# Ranking de bandas: mayor = mejor. Un salto hacia arriba es mejora.
_BAND_RANK: dict[str, int] = {
    "critico": 0,
    "en_riesgo": 1,
    "evidencia_insuficiente": 2,
    "sin_datos": 3,
    "aceptable": 4,
    "solido": 5,
}
_COMPLIANCE_DELTA = 5.0  # pp mínimos para un evento de % (sin cambio de banda)

# Taxonomía de kinds para el reporte.
GOOD_KINDS = {"improved", "resolved_critical", "resolved_high"}
BAD_KINDS = {"worsened", "new_critical", "new_high", "ci_failed", "gate_error",
             "exception_expired", "exception_expire_failed"}


def _ev(
    new: Row,
    kind: str,
    severity: str,
    *,
    prev: Row | None = None,
    crit_delta: int = 0,
    high_delta: int = 0,
) -> ProjectEvent:
    return ProjectEvent(
        project_key=new.project_key,
        kind=kind,
        severity=severity,
        band_from=prev.band if prev else None,
        band_to=new.band,
        compliance_from=prev.compliance_pct if prev else None,
        compliance_to=new.compliance_pct,
        crit_delta=crit_delta,
        high_delta=high_delta,
    )


def detect(prev: Row | None, new: Row) -> list[ProjectEvent]:
    """Eventos entre dos snapshots de un mismo proyecto. Sin `prev` (primer punto)
    no hay eventos: es la línea base."""
    if prev is None:
        return []
    events: list[ProjectEvent] = []

    dc = new.crit - prev.crit
    if dc > 0:
        events.append(_ev(new, "new_critical", "danger", crit_delta=dc))
    elif dc < 0:
        events.append(_ev(new, "resolved_critical", "good", crit_delta=dc))

    dh = new.high - prev.high
    if dh > 0:
        events.append(_ev(new, "new_high", "warn", high_delta=dh))
    elif dh < 0:
        events.append(_ev(new, "resolved_high", "good", high_delta=dh))

    pr, nr = _BAND_RANK.get(prev.band, 9), _BAND_RANK.get(new.band, 9)
    if nr > pr:
        events.append(_ev(new, "improved", "good", prev=prev))
    elif nr < pr:
        events.append(_ev(new, "worsened", "danger" if new.band == "critico" else "warn", prev=prev))
    elif abs(new.compliance_pct - prev.compliance_pct) >= _COMPLIANCE_DELTA:
        up = new.compliance_pct > prev.compliance_pct
        events.append(_ev(new, "improved" if up else "worsened", "good" if up else "warn", prev=prev))

    # CI pasa a rojo y quality gate pasa a ERROR (2.5): solo en la TRANSICIÓN, no
    # mientras siga igual (no repetir en cada snapshot).
    if new.ci_status == "failure" and prev.ci_status != "failure":
        events.append(_ev(new, "ci_failed", "danger", prev=prev))
    if new.quality_gate == "ERROR" and prev.quality_gate != "ERROR":
        events.append(_ev(new, "gate_error", "warn", prev=prev))

    return events


async def latest_before(session: AsyncSession, project_key: str) -> Row | None:
    """El snapshot más reciente YA existente de un proyecto (antes de la corrida en curso)."""
    q = (
        select(Row)
        .where(Row.project_key == project_key)
        .order_by(Row.captured_at.desc())
        .limit(1)
    )
    return (await session.execute(q)).scalars().first()


async def list_events(session: AsyncSession, limit: int = 50) -> list[ProjectEvent]:
    q = select(ProjectEvent).order_by(ProjectEvent.created_at.desc()).limit(limit)
    return list((await session.execute(q)).scalars())


async def unread_count(session: AsyncSession, user_id: str) -> int:
    marker = await session.get(EventRead, user_id)
    q = select(func.count(ProjectEvent.id))
    if marker is not None:
        q = q.where(ProjectEvent.created_at > marker.last_read_at)
    return int((await session.execute(q)).scalar() or 0)


async def mark_read(session: AsyncSession, user_id: str) -> None:
    marker = await session.get(EventRead, user_id)
    now = datetime.now(UTC)
    if marker is None:
        session.add(EventRead(user_id=user_id, last_read_at=now))
    else:
        marker.last_read_at = now
    await session.commit()


async def report(session: AsyncSession, days: int) -> dict:
    """Agrega los eventos de los últimos `days` días: totales por kind y por proyecto.
    Es el 'conteo de lo que vamos parchando y mejorando'."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = list(
        (
            await session.execute(
                select(ProjectEvent).where(ProjectEvent.created_at >= since)
            )
        ).scalars()
    )
    totals: dict[str, int] = {}
    by_project: dict[str, dict[str, int]] = {}
    for r in rows:
        totals[r.kind] = totals.get(r.kind, 0) + 1
        p = by_project.setdefault(
            r.project_key,
            {"improved": 0, "worsened": 0, "new_critical": 0, "resolved_critical": 0},
        )
        if r.kind in p:
            p[r.kind] += 1
    return {
        "since": since,
        "days": days,
        "totals": totals,
        "by_project": [{"project_key": k, **v} for k, v in sorted(by_project.items())],
    }
