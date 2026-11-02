"""Orquestación de la propuesta de fix con código (Feature B, ADR-018).

Resuelve SERVER-SIDE el endpoint/token/allow-list del proveedor elegido por-petición
(ADR-002: el cliente nunca manda base_url/token), lee la ventana de código acotada
(`code_context`), redacta posibles secretos antes de cualquier egreso hosted, llama
al modelo (reusa `openai_compat.chat`, temperature=0) y valida la salida contra el
esquema con cotas de longitud, degradando a "sin propuesta" sin 500.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import AppConfig
from ..core.credentials import resolve_token
from ..models import AICodeFixResult
from ..settings import Settings
from .base import AIProviderError
from .code_context import CodeContextError, fetch_code_window, scan_and_redact_secrets
from .openai_compat import CODE_FIX_SYSTEM, chat, code_fix_user, parse_code_fix

_DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"


class CodeFixError(Exception):
    """Error de validación/configuración del flujo de code-fix (saneado al usuario)."""


@dataclass
class _Runtime:
    base_url: str | None
    token: str | None
    allowed_models: frozenset[str]


def resolve_runtime(provider_type: str, config: AppConfig, settings: Settings) -> _Runtime:
    """Resuelve endpoint/token/allow-list del proveedor SIN que el cliente aporte nada.

    - local: base_url del bloque `ai` si está configurado como local, si no el
      default de Ollama; sin token (sin egreso). Allow-list = modelo del bloque `ai`
      (si es local) + `ai_local_model_allowlist`.
    - hosted: base_url/token_env del bloque `ai` si está configurado como hosted, si
      no los de `ai_hosted_*`. Allow-list = `ai_hosted_model_allowlist`.
    """
    ai = config.ai
    if provider_type == "local":
        if ai is not None and ai.provider == "local":
            base_url = ai.base_url or _DEFAULT_LOCAL_BASE_URL
        else:
            base_url = _DEFAULT_LOCAL_BASE_URL
        allowed: set[str] = set(settings.ai_local_model_allowlist_set)
        if ai is not None and ai.provider == "local" and ai.model:
            allowed.add(ai.model)
        return _Runtime(base_url=base_url, token=None, allowed_models=frozenset(allowed))

    # hosted
    if ai is not None and ai.provider == "hosted":
        base_url = ai.base_url or settings.ai_hosted_base_url or None
        token_env = ai.token_env or settings.ai_hosted_token_env or None
    else:
        base_url = settings.ai_hosted_base_url or None
        token_env = settings.ai_hosted_token_env or None
    token = resolve_token(token_env)
    return _Runtime(
        base_url=base_url, token=token, allowed_models=settings.ai_hosted_model_allowlist_set
    )


async def propose_code_fix(
    *,
    config: AppConfig,
    settings: Settings,
    provider_type: str,
    model: str,
    github_api_base: str,
    read_token: str | None,
    repo: str,
    file_path: str,
    line: int | None,
    finding_title: str,
    ref: str | None = None,
    github_transport=None,
) -> tuple[AICodeFixResult, int]:
    """Devuelve (resultado, nº_secretos_redactados). `read_token` es el token de
    LECTURA de la fuente github (ADR-002) — distinto del token del proveedor de IA.
    """
    runtime = resolve_runtime(provider_type, config, settings)

    # Allow-list de modelo por proveedor (fail-closed): fuera de la lista → rechazo.
    if model not in runtime.allowed_models:
        raise CodeFixError(
            f"Modelo '{model}' no permitido para el proveedor '{provider_type}'."
        )
    if provider_type == "hosted" and (not runtime.base_url or not runtime.token):
        raise CodeFixError(
            "El proveedor hosted no está configurado server-side (falta base_url o la "
            "variable de entorno con el token)."
        )

    # Lectura ACOTADA de código (±N líneas, un archivo, sin clonar). La ruta viene
    # del finding pero se valida server-side dentro de fetch_code_window.
    try:
        window = await fetch_code_window(
            api_base=github_api_base,
            repo=repo,
            file_path=file_path,
            line=line,
            token=read_token,
            window_lines=settings.code_window_lines,
            ref=ref,
            transport=github_transport,
        )
    except CodeContextError as exc:
        raise CodeFixError(str(exc)) from exc

    original = window.text
    egress = provider_type == "hosted"
    redacted_count = 0
    to_send = original
    if egress:
        # CON EGRESO: no entregamos credenciales a un tercero si podemos evitarlo.
        to_send, redacted_count = scan_and_redact_secrets(original)

    base_url = runtime.base_url or _DEFAULT_LOCAL_BASE_URL
    try:
        raw = await chat(
            base_url,
            runtime.token,
            model,
            user=code_fix_user(finding_title, window.path, to_send),
            system=CODE_FIX_SYSTEM,
        )
    except AIProviderError as exc:
        raise CodeFixError(str(exc)) from exc

    result = parse_code_fix(
        raw,
        "hosted" if egress else "local",
        model,
        egress=egress,
        original_snippet=original,
        max_chars=settings.code_fix_max_output_chars,
    )
    return result, redacted_count
