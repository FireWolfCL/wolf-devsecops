"""Notificaciones del panel (campana + reporte de avance).

Solo lectura de métricas agregadas (ADR-019). Los eventos los genera
`snapshot-posture` al detectar cambios entre snapshots; aquí se listan, se cuentan
los no-leídos (marcador por usuario) y se agregan para el reporte.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user, require_role
from ..db.session import get_db
from ..models import EventItem, EventList, EventReport, UnreadCount
from ..models_db.user import User
from ..rbac.roles import ALL_ROLES
from ..services import event_service as svc

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])


def _item(r) -> EventItem:
    return EventItem(
        id=r.id,
        created_at=r.created_at,
        project_key=r.project_key,
        kind=r.kind,
        severity=r.severity,
        band_from=r.band_from,
        band_to=r.band_to,
        compliance_from=r.compliance_from,
        compliance_to=r.compliance_to,
        crit_delta=r.crit_delta,
        high_delta=r.high_delta,
    )


@router.get("/events", response_model=EventList)
async def list_events(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventList:
    rows = await svc.list_events(db, limit)
    unread = await svc.unread_count(db, user.id)
    return EventList(events=[_item(r) for r in rows], unread=unread)


@router.get("/events/unread_count", response_model=UnreadCount)
async def unread_count(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> UnreadCount:
    return UnreadCount(count=await svc.unread_count(db, user.id))


@router.post("/events/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    await svc.mark_read(db, user.id)


@router.get("/events/report", response_model=EventReport)
async def events_report(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> EventReport:
    return EventReport(**await svc.report(db, days))
