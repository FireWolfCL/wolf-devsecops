"""Endpoints de proyectos (versionados bajo /api/v1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import require_role
from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..models import PostureOverview, ProjectListItem, ProjectSummary, ToolDashboard
from ..rbac.roles import ALL_ROLES
from .deps import get_aggregator, get_config

# ADR-014: cualquier usuario autenticado (viewer/operator/admin).
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_role(*ALL_ROLES))])


@router.get("/projects", response_model=list[ProjectListItem])
async def list_projects(aggregator: Aggregator = Depends(get_aggregator)) -> list[ProjectListItem]:
    return await aggregator.list_projects()


@router.get("/posture", response_model=PostureOverview)
async def global_posture(aggregator: Aggregator = Depends(get_aggregator)) -> PostureOverview:
    """% de cumplimiento POR organización (docs/POSTURE-RUBRIC.md). Wolf separado."""
    return await aggregator.global_posture()


_TOOL_DASHBOARDS = {"github", "sonarqube", "defectdojo", "containers"}


@router.get("/tools/{tool}", response_model=ToolDashboard)
async def tool_dashboard(
    tool: str, aggregator: Aggregator = Depends(get_aggregator)
) -> ToolDashboard:
    """Vista por herramienta: una fuente, todos los proyectos que la usan."""
    if tool not in _TOOL_DASHBOARDS:
        raise HTTPException(status_code=400, detail=f"Herramienta desconocida: {tool}")
    return await aggregator.get_tool_dashboard(tool)


@router.get("/projects/{key}", response_model=ProjectSummary)
async def get_project(
    key: str,
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
) -> ProjectSummary:
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    return await aggregator.get_summary(project)
