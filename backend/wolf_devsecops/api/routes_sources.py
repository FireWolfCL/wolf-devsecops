"""Detalle por fuente (versionado bajo /api/v1) — F5."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import require_role
from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..models import SourceDetail
from ..rbac.roles import ALL_ROLES
from .deps import get_aggregator, get_config

# ADR-014: cualquier usuario autenticado (viewer/operator/admin).
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])

_VALID_SOURCES = {"github", "defectdojo", "sonarqube", "wss", "harbor", "trivy"}


@router.get("/projects/{key}/sources/{source}", response_model=SourceDetail)
async def get_source_detail(
    key: str,
    source: str,
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
) -> SourceDetail:
    if source not in _VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"Fuente desconocida: {source}")
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    return await aggregator.get_source_detail(project, source)
