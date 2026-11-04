"""Historial de postura por proyecto (ADR-019). Solo lectura de métricas agregadas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import require_role
from ..core.config import AppConfig
from ..db.session import get_db
from ..models import PostureHistory, PostureSnapshot, PostureSparklines
from ..rbac.roles import ALL_ROLES
from ..services import posture_history_service as svc
from .deps import get_config

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])

MAX_DAYS = 3650  # tope de seguridad del parámetro; la edición recorta aparte


@router.get("/projects/history/spark", response_model=PostureSparklines)
async def posture_spark(
    days: int | None = Query(None, ge=1, le=MAX_DAYS),
    db: AsyncSession = Depends(get_db),
) -> PostureSparklines:
    """Series compactas de % de cumplimiento por proyecto para los sparklines del
    dashboard. Una sola consulta a BD (no por proyecto, nunca a las fuentes)."""
    window, series = await svc.list_spark(db, days)
    return PostureSparklines(window_days=window, series=series)


@router.get("/projects/{key}/history", response_model=PostureHistory)
async def project_history(
    key: str,
    limit: int | None = Query(None, ge=1, le=svc.MAX_LIMIT),
    days: int | None = Query(None, ge=1, le=MAX_DAYS),
    config: AppConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> PostureHistory:
    if config.project(key) is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    rows = await svc.list_history(db, key, limit, days)
    return PostureHistory(
        project_key=key,
        window_days=svc.effective_window_days(days),
        snapshots=[
            PostureSnapshot(
                captured_at=r.captured_at, compliance_pct=r.compliance_pct, band=r.band,
                crit=r.crit, high=r.high, med=r.med, low=r.low, coverage_pct=r.coverage_pct,
                rubric_version=r.rubric_version,
            )
            for r in rows
        ],
    )
