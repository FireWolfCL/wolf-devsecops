"""Historial de postura por proyecto (ADR-019). Solo lectura de métricas agregadas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import require_role
from ..core.config import AppConfig
from ..db.session import get_db
from ..models import PostureHistory, PostureSnapshot
from ..rbac.roles import ALL_ROLES
from ..services import posture_history_service as svc
from .deps import get_config

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])


@router.get("/projects/{key}/history", response_model=PostureHistory)
async def project_history(
    key: str,
    limit: int | None = Query(None, ge=1, le=svc.MAX_LIMIT),
    config: AppConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> PostureHistory:
    if config.project(key) is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    rows = await svc.list_history(db, key, limit)
    return PostureHistory(
        project_key=key,
        snapshots=[
            PostureSnapshot(
                captured_at=r.captured_at, compliance_pct=r.compliance_pct, band=r.band,
                crit=r.crit, high=r.high, med=r.med, low=r.low, coverage_pct=r.coverage_pct,
            )
            for r in rows
        ],
    )
