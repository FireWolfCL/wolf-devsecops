"""Mapa de módulos — GET /api/v1/projects/{key}/codemap (GitHub Trees API, sin IA).

Solo lectura (ADR-001); cualquier usuario autenticado (viewer/operator/admin).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import require_role
from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..models import ModuleTree, SourceStatus
from ..rbac.roles import ALL_ROLES
from .deps import get_aggregator, get_config

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])


@router.get("/projects/{key}/codemap", response_model=ModuleTree)
async def project_codemap(
    key: str,
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
) -> ModuleTree:
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    result = await aggregator.get_codemap(project)
    if result.status == SourceStatus.not_configured:
        raise HTTPException(status_code=404, detail="El proyecto no tiene bloque 'github' configurado.")
    if result.data is None:  # unavailable sin dato previo (mensaje ya saneado)
        raise HTTPException(status_code=503, detail=result.error or "Fuente no disponible.")
    return result.data
