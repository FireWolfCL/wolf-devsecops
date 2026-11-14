"""Endpoints de proyectos (versionados bajo /api/v1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import require_role
from ..core.aggregator import Aggregator, BranchCompareUnsupported
from ..core.config import AppConfig
from ..models import (
    PipelineMap,
    PostureComparison,
    PostureOverview,
    ProjectListItem,
    ProjectSummary,
    ToolDashboard,
)
from ..rbac.roles import ALL_ROLES, Role
from ..services.pipeline_service import get_pipeline_map
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


_MAX_COMPARE_REFS = 6  # cota superior de ramas por comparación (acota llamadas upstream)


def _parse_refs(refs: str) -> list[str]:
    """Normaliza la lista de refs de la query: separa por coma, recorta, deduplica
    (preserva orden). Rechaza refs con espacios/control o vacíos, y fuera de rango."""
    out: list[str] = []
    for raw in refs.split(","):
        ref = raw.strip()
        if not ref:
            continue
        if any(ch.isspace() for ch in ref) or len(ref) > 255:
            raise HTTPException(status_code=400, detail=f"Rama inválida: {ref!r}")
        if ref not in out:
            out.append(ref)
    if len(out) < 2:
        raise HTTPException(
            status_code=400, detail="Se requieren al menos 2 ramas distintas para comparar."
        )
    if len(out) > _MAX_COMPARE_REFS:
        raise HTTPException(
            status_code=400, detail=f"Máximo {_MAX_COMPARE_REFS} ramas por comparación."
        )
    return out


@router.get("/projects/{key}/compare", response_model=PostureComparison)
async def compare_project_refs(
    key: str,
    refs: str = Query(..., description="Ramas a comparar, separadas por coma (ej. main,sec,dev)."),
    baseline: str | None = Query(None, description="Rama base del delta; por defecto la primera."),
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
) -> PostureComparison:
    """Compara la postura de varias ramas del proyecto y dice cuál está mejor (ADR-024).

    Read-only y al vuelo: NO persiste nada y NO escanea (ADR-001/012). MVP: solo el
    check ci-green (conscientes de rama); el resto queda `na`. Es el estado AL HEAD de
    cada rama, no una línea de tiempo histórica (eso es la tendencia, ADR-019)."""
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    ref_list = _parse_refs(refs)
    base = baseline.strip() if baseline else ref_list[0]
    if base not in ref_list:
        raise HTTPException(
            status_code=400, detail=f"El baseline {base!r} debe estar entre las ramas comparadas."
        )
    try:
        return await aggregator.compare_refs(project, ref_list, base)
    except BranchCompareUnsupported as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{key}/pipeline", response_model=PipelineMap)
async def get_project_pipeline(
    key: str,
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
) -> PipelineMap:
    """Mapa "usted está aquí" (6 etapas). Solo lectura (ADR-001), sin feature-gate:
    disponible en todas las ediciones. Misma auth que el detalle de proyecto."""
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    return await get_pipeline_map(aggregator, project)


@router.post("/refresh", dependencies=[Depends(require_role(Role.operator, Role.admin))])
async def refresh_all(aggregator: Aggregator = Depends(get_aggregator)) -> dict:
    """Invalida TODA la caché de agregación: la próxima lectura trae datos frescos de la
    fuente, sin esperar el TTL. Para ver un cambio recién hecho (operator/admin)."""
    return {"invalidated": await aggregator.invalidate()}


@router.post("/projects/{key}/refresh", dependencies=[Depends(require_role(Role.operator, Role.admin))])
async def refresh_project(
    key: str,
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
) -> dict:
    """Invalida la caché de UN proyecto: su próxima lectura refetchea (operator/admin)."""
    if config.project(key) is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    return {"invalidated": await aggregator.invalidate(key)}
