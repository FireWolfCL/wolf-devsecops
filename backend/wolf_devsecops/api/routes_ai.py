"""Reviewer IA (ADR-007, F6) — POST /api/v1/projects/{key}/ai/review.

GATE: deshabilitado por defecto (WOLF_AI_ENABLED=false) hasta el visto bueno de
wolf-ai-safety-agent. Cuando se habilite, envía SOLO el texto de los findings
(nunca código) al proveedor configurado (local sin egreso, o hosted por API).
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.base import AIProvider, AIProviderError
from ..ai.code_fix import CodeFixError, propose_code_fix
from ..ai.projection import project_findings
from ..auth.dependencies import require_role
from ..core.config import ConfigError
from ..core.credentials import resolve_token
from ..db.session import get_db
from ..models import AICodeFixRequest, AICodeFixResult, AIReviewContext, AIReviewResult
from ..models_db.user import User
from ..rbac.roles import roles_for
from ..services import audit_service, config_service
from ..settings import get_settings
from .deps import get_aggregator, get_ai_provider, get_config

router = APIRouter(prefix="/api/v1")

# Traza de auditoría del subsistema de IA (P0-5): proyecto/proveedor/modelo/egreso.
# NUNCA registra tokens, el prompt ni el texto de los findings.
_audit = logging.getLogger("wolf_devsecops.ai.audit")

# Línea embebida en un target tipo "app/foo.py:123" (DefectDojo no trae un campo
# `line` fiable en la forma normalizada; si no hay, se lee la ventana desde el inicio).
_TARGET_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+)$")


def _split_target(target: str) -> tuple[str, int | None]:
    m = _TARGET_LINE.match(target.strip())
    if m:
        return m.group("path"), int(m.group("line"))
    return target.strip(), None


# ADR-014: orden de evaluación = get_current_user (401) -> require_role (403) -> gate
# ai_enabled (503, dentro del handler) -> lógica. Un viewer recibe 403 aunque la IA esté off.
@router.post(
    "/projects/{key}/ai/review",
    response_model=AIReviewResult,
    dependencies=[Depends(require_role(*roles_for("ai.review")))],
)
async def review_project(key: str) -> AIReviewResult:
    # GATE (ADR-007): se evalúa ANTES de tocar la config, para que el 503 salga
    # aunque no haya projects.yaml. Bloqueado hasta visto bueno de wolf-ai-safety-agent.
    # A4/ADR-015: IA efectiva = techo del entorno (WOLF_AI_ENABLED) AND toggle en BD
    # AND bloque `ai` válido. El entorno sigue siendo fail-closed.
    try:
        ai_on = config_service.ai_effectively_enabled(get_config())
    except ConfigError:
        ai_on = False
    if not ai_on:
        raise HTTPException(
            status_code=503,
            detail="Reviewer IA deshabilitado (requiere WOLF_AI_ENABLED=true en el entorno "
            "—pendiente del visto bueno de wolf-ai-safety-agent— y activarlo en el panel "
            "de administración con un proveedor/modelo configurado).",
        )

    config = get_config()
    aggregator = get_aggregator()
    project = config.project(key)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")

    # Frontera de datos (P0-1): los findings crudos de DefectDojo pasan por una
    # proyección con allow-list (title/severity/description saneada). NUNCA sale
    # file_path, line, código ni ubicaciones de secretos.
    detail = await aggregator.get_source_detail(project, "defectdojo")
    raw = detail.payload.get("findings", []) if detail.payload else []
    # Tope de hallazgos (crítico primero) para acotar latencia del modelo local.
    from ..settings import get_settings
    context = AIReviewContext(
        project_key=key,
        findings=project_findings(raw, limit=get_settings().ai_max_findings),
    )

    try:
        provider: AIProvider = get_ai_provider()
        result = await provider.review(context)
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _audit.info(
        "ai_review project=%s provider=%s model=%s egress=%s findings=%d",
        key, result.provider_type, result.model, result.egress, len(context.findings),
    )
    return result


# ─── Feature B: propuesta de fix con CÓDIGO (ADR-018, assistive) ─────────────
# Mismo orden de evaluación que /ai/review: 401 (token) -> 403 (rol operator/admin,
# viewer jamás) -> 503 (gate ai_enabled) -> lógica. El cliente NUNCA manda
# base_url/token/egress: se resuelven server-side por provider (ADR-002).
@router.post(
    "/projects/{key}/ai/code-fix",
    response_model=AICodeFixResult,
)
async def code_fix_project(
    key: str,
    body: AICodeFixRequest,
    user: User = Depends(require_role(*roles_for("ai.code_fix"))),
    db: AsyncSession = Depends(get_db),
) -> AICodeFixResult:
    try:
        ai_on = config_service.ai_effectively_enabled(get_config())
    except ConfigError:
        ai_on = False
    if not ai_on:
        raise HTTPException(
            status_code=503,
            detail="Subsistema de IA deshabilitado (requiere WOLF_AI_ENABLED=true en el "
            "entorno y activarlo en el panel con un proveedor/modelo configurado).",
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

    # Resuelve el finding agregado (DefectDojo) → file_path(+line). La ruta viene del
    # finding, pero se valida server-side dentro de code_context (anti-traversal).
    aggregator = get_aggregator()
    detail = await aggregator.get_source_detail(project, "defectdojo")
    findings = detail.payload.get("findings", []) if detail.payload else []
    match = next(
        (
            f
            for f in findings
            if str(f.get("id")) == body.finding_ref or str(f.get("title")) == body.finding_ref
        ),
        None,
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Finding no encontrado para ese finding_ref.")
    target = match.get("target")
    if not target:
        raise HTTPException(
            status_code=422,
            detail="El finding no tiene una ubicación de archivo; no hay código que proponer.",
        )
    file_path, line = _split_target(str(target))

    settings = get_settings()
    read_token = resolve_token(gh_source.token_env)  # token de LECTURA, nunca se devuelve
    try:
        result, redacted = await propose_code_fix(
            config=config,
            settings=settings,
            provider_type=body.provider,
            model=body.model,
            github_api_base=gh_source.url,
            read_token=read_token,
            repo=project.github.repo,
            file_path=file_path,
            line=line,
            finding_title=str(match.get("title") or body.finding_ref),
        )
    except CodeFixError as exc:
        # Config/validación (modelo no permitido, hosted sin configurar, archivo que no
        # se pudo leer): 422. No se filtra token ni ruta interna.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Audit trail (ADR-018): queda traza de proveedor/modelo/egreso. detail SANEADO:
    # jamás el código, el prompt ni el token (audit_service además filtra claves sensibles).
    await audit_service.record(
        db,
        action="ai.code_fix",
        actor_user_id=user.id,
        actor_username=user.username,
        target_type="project",
        target_id=key,
        detail={
            "provider": result.provider_type,
            "model": result.model,
            "egress": result.egress,
            "finding_ref": body.finding_ref,
            "secrets_redacted": redacted,
        },
    )
    _audit.info(
        "ai_code_fix project=%s provider=%s model=%s egress=%s redacted=%d",
        key, result.provider_type, result.model, result.egress, redacted,
    )
    return result
