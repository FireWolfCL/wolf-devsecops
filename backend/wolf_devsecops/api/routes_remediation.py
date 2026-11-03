"""Remediación asistida (Feature D, ADR-017) — POST /projects/{key}/remediation/branch.

Crea un branch aislado (prefijo reservado wolf-sec/) + un PR en estado DRAFT con la
propuesta de remediación. NUNCA mergea, nunca toca main/default/otros branches, nunca
force-push (lo garantizan GitHubWriteClient y remediation_service).

Orden fail-closed: 401 (token) -> 403 (rol operator/admin; viewer jamás; MFA si admin,
vía require_admin_action) -> 503 (gate WOLF_REMEDIATION_WRITE_ENABLED) -> 400 (confirm)
-> validación de config -> escritura. El RBAC corre como dependencia ANTES del cuerpo.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import require_admin_action
from ..connectors.github import GitHubWriteClient
from ..core.credentials import resolve_token
from ..db.session import get_db
from ..edition import FEATURE_REMEDIATION_PR, require_feature
from ..models import RemediationBranchRequest, RemediationBranchResult
from ..models_db.user import User
from ..rbac.roles import roles_for
from ..services import remediation_service
from ..services.remediation_service import RemediationError
from ..settings import get_settings
from .deps import get_config

router = APIRouter(prefix="/api/v1")

_audit = logging.getLogger("wolf_devsecops.remediation.audit")


def _build_write_client(gh_url: str, token: str) -> GitHubWriteClient:
    """Fábrica aislada (seam de test): construye el cliente de escritura con el PAT ya
    resuelto. Los tests la sustituyen por un cliente simulado."""
    return GitHubWriteClient(api_base=gh_url, token=token)


@router.post(
    "/projects/{key}/remediation/branch",
    response_model=RemediationBranchResult,
    # Feature Pro (edición): el PR-draft / branch de remediación es de pago.
    dependencies=[Depends(require_feature(FEATURE_REMEDIATION_PR))],
)
async def remediation_branch(
    key: str,
    body: RemediationBranchRequest,
    user: User = Depends(require_admin_action(*roles_for("remediation.write"))),
    db: AsyncSession = Depends(get_db),
) -> RemediationBranchResult:
    settings = get_settings()
    # Techo fail-closed (ADR-017): sin el flag, nada se escribe, aunque rol y confirm
    # sean correctos.
    if not settings.remediation_write_enabled:
        raise HTTPException(
            status_code=503,
            detail="Remediación por escritura deshabilitada "
            "(requiere WOLF_REMEDIATION_WRITE_ENABLED=true).",
        )
    # Confirmación explícita por acción: nada se escribe sin confirm=true.
    if body.confirm is not True:
        raise HTTPException(
            status_code=400,
            detail="Se requiere confirm=true explícito para escribir en el repositorio.",
        )

    config = get_config()
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    if project.github is None:
        raise HTTPException(status_code=422, detail="El proyecto no define un repositorio github.")
    gh_source = config.sources.get("github")
    if gh_source is None:
        raise HTTPException(status_code=422, detail="La fuente 'github' no está configurada.")

    # PAT de ESCRITURA separado del de lectura (ADR-002, ADR-017). El valor vive SOLO
    # en el entorno; aquí solo se resuelve por NOMBRE de variable, nunca se devuelve.
    write_token = resolve_token(settings.github_write_token_env)
    if not write_token:
        raise HTTPException(
            status_code=503,
            detail="Falta el PAT de escritura en el entorno "
            f"(variable {settings.github_write_token_env}).",
        )
    client = _build_write_client(gh_source.url, write_token)

    try:
        result = await remediation_service.create_remediation_branch(
            db,
            project=project,
            picked_findings=body.picked_findings,
            write_client=client,
            actor_user_id=user.id,
            actor_username=user.username,
        )
    except RemediationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _audit.info(
        "remediation_branch project=%s branch=%s applied=%s already_existed=%s",
        key, result.branch, result.applied, result.already_existed,
    )
    return result
