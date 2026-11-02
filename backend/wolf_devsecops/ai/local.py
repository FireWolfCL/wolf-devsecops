"""LocalAIProvider — endpoint compatible OpenAI en la red del operador (Ollama).

SIN egreso: el texto de los findings no sale de la infraestructura del equipo.
Es el modo por defecto seguro (ADR-007).
"""

from __future__ import annotations

from ..models import AIReviewContext, AIReviewResult
from .base import AIProvider
from .openai_compat import chat, context_to_user, parse_review


class LocalAIProvider(AIProvider):
    provider_type = "local"
    egress = False

    async def review(self, context: AIReviewContext) -> AIReviewResult:
        base_url = self.base_url or "http://localhost:11434/v1"
        raw = await chat(base_url, token=None, model=self.model, user=context_to_user(context))
        return parse_review(raw, "local", self.model, egress=False, context=context)
