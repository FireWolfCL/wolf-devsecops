"""Cliente compartido para endpoints compatibles con OpenAI (Ollama, OpenAI…).

Lo usan LocalAIProvider y HostedAIProvider. La salida del modelo es input NO
confiable: `parse_review` la valida contra el esquema y degrada con elegancia si
el modelo no devolvió JSON válido.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

import httpx

from ..models import AIReviewContext, AIReviewResult, PrioritizedFinding
from .base import AIProviderError

_SYSTEM = (
    "Eres un analista de seguridad de aplicaciones. Recibes una lista de HALLAZGOS "
    "ya detectados (nunca código fuente). Devuelve SOLO un JSON con la forma "
    '{"summary": "<2-3 frases>", "prioritized": [{"title","severity",'
    '"rationale","recommended_action"}]}. severity ∈ critical|high|medium|low|info. '
    "No inventes hallazgos que no estén en la entrada."
)


async def chat(base_url: str, token: str | None, model: str, user: str, system: str = _SYSTEM) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "model": model,
        "temperature": 0,
        # Modo JSON: obliga al modelo a devolver JSON válido (clave para modelos
        # locales pequeños que si no responden en prosa). Soportado por Ollama y
        # por los endpoints hosted compatibles con OpenAI; si alguno lo ignora,
        # `parse_review`/`parse_code_fix` degradan igual.
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # Timeout configurable: un modelo LOCAL en CPU (sin GPU) puede tardar bastante
    # con un prompt de muchos hallazgos; un endpoint hosted con GPU es rápido.
    from ..settings import get_settings
    timeout = get_settings().ai_timeout_seconds
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise AIProviderError("No se pudo contactar el proveedor de IA.") from exc
    if resp.status_code != 200:
        raise AIProviderError(f"El proveedor de IA respondió HTTP {resp.status_code}.")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("Respuesta del proveedor de IA sin el formato esperado.") from exc


def _extract_json(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("sin JSON")
    return json.loads(raw[start : end + 1])


def parse_review(
    raw: str,
    provider_type: Literal["local", "hosted"],
    model: str,
    egress: bool,
    context: AIReviewContext,
) -> AIReviewResult:
    """Valida la salida del modelo (input no confiable) contra el esquema."""
    summary = ""
    prioritized: list[PrioritizedFinding] = []
    try:
        obj = _extract_json(raw)
        summary = str(obj.get("summary", ""))[:1000]
        for item in obj.get("prioritized", [])[:50]:  # cota de salida (P0-2)
            try:
                prioritized.append(PrioritizedFinding(**item))
            except (TypeError, ValueError):
                continue  # descarta un item mal formado, no toda la respuesta
    except (ValueError, json.JSONDecodeError):
        summary = raw.strip()[:500] or "El modelo no devolvió un análisis estructurado."
    return AIReviewResult(
        provider_type=provider_type,
        model=model,
        egress=egress,
        generated_at=datetime.now(UTC),
        summary=summary,
        prioritized=prioritized,
    )


def context_to_user(context: AIReviewContext) -> str:
    return json.dumps(
        {
            "project": context.project_key,
            "findings": [f.model_dump(mode="json") for f in context.findings],
        }
    )


# ─── Feature B: propuesta de fix con código (ADR-018) ────────────────────────
CODE_FIX_SYSTEM = (
    "Eres un ingeniero de seguridad de aplicaciones. Recibes el título de un "
    "hallazgo y una VENTANA de código (fragmento). Propón una corrección mínima y "
    "segura de ESE fragmento. Devuelve SOLO un JSON con la forma "
    '{"proposed_snippet": "<código corregido o diff, como texto>", '
    '"explanation": "<por qué, 2-4 frases>"}. No inventes código fuera del '
    "fragmento. No incluyas secretos. La salida es texto plano, nunca HTML."
)


def code_fix_user(finding_title: str, file_path: str, snippet: str) -> str:
    return json.dumps(
        {"finding": finding_title[:300], "file": file_path, "code_window": snippet}
    )


def parse_code_fix(
    raw: str,
    provider_type: Literal["local", "hosted"],
    model: str,
    egress: bool,
    original_snippet: str,
    max_chars: int,
):
    """Valida la salida del modelo (input NO confiable) contra cotas de longitud.

    Degrada a "sin propuesta" (sin 500) si el modelo no devolvió JSON parseable.
    Devuelve un AICodeFixResult. El import es local para no crear un ciclo models↔ai.
    """
    from ..models import AICodeFixResult

    proposed = ""
    explanation = ""
    try:
        obj = _extract_json(raw)
        proposed = str(obj.get("proposed_snippet", ""))[:max_chars]
        explanation = str(obj.get("explanation", ""))[:max_chars]
    except (ValueError, json.JSONDecodeError):
        proposed = ""
        explanation = "El modelo no devolvió una propuesta estructurada."
    if not proposed and not explanation:
        explanation = "El modelo no devolvió una propuesta."
    return AICodeFixResult(
        provider_type=provider_type,
        model=model,
        egress=egress,
        original_snippet=original_snippet[:max_chars],
        proposed_snippet=proposed,
        explanation=explanation,
    )
