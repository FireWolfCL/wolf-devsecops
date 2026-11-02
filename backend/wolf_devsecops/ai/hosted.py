"""HostedAIProvider — proveedor por API con token (Claude / OpenAI).

CON egreso: el texto de los findings sale a un tercero. Es una decisión EXPLÍCITA
del operador (ADR-007); la salida registra egress=True para dejar traza.
"""

from __future__ import annotations

from ..models import AIReviewContext, AIReviewResult
from .base import AIProvider, AIProviderError
from .openai_compat import chat, context_to_user, parse_review


class HostedAIProvider(AIProvider):
    provider_type = "hosted"
    egress = True

    async def review(self, context: AIReviewContext) -> AIReviewResult:
        if not self.base_url:
            raise AIProviderError("El proveedor hosted necesita base_url (endpoint de la API).")
        if not self.token:
            raise AIProviderError("El proveedor hosted necesita un token (por variable de entorno).")
        raw = await chat(self.base_url, self.token, self.model, context_to_user(context))
        return parse_review(raw, "hosted", self.model, egress=True, context=context)
