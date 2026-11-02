"""Inventario de dependencias — GET /api/v1/projects/{key}/dependencies (Trivy).

Solo lectura (ADR-001); cualquier usuario autenticado (viewer/operator/admin).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import require_role
from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..models import DependencyInventory, SourceStatus
from ..rbac.roles import ALL_ROLES
from .deps import get_aggregator, get_config

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])


@router.get("/projects/{key}/dependencies", response_model=DependencyInventory)
async def project_dependencies(
    key: str,
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
) -> DependencyInventory:
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    result = await aggregator.get_dependencies(project)
    if result.status == SourceStatus.not_configured:
        raise HTTPException(status_code=404, detail="El proyecto no tiene Trivy configurado.")
    if result.data is None:  # unavailable sin dato previo (mensaje ya saneado)
        raise HTTPException(status_code=503, detail=result.error or "Fuente no disponible.")
    return result.data
