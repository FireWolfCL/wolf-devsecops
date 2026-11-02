"""Interfaz común `AIProvider` (ARCHITECTURE.md §7, ADR-007).

Seam paralelo al de `Connector`: añadir un proveedor de IA es una clase + su alta
en el registro. `local` no egresa fuera de la red; `hosted` usa token por env y
egresa a un tercero (decisión explícita del operador).

INVARIANTE: el contexto que recibe un proveedor lleva SOLO texto de findings,
nunca código ni diffs (en el MVP). La salida del modelo es input NO confiable:
el llamador la valida contra el esquema AIReviewResult antes de usarla.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..models import AIReviewContext, AIReviewResult


class AIProviderError(Exception):
    """Error del proveedor de IA (saneado antes de salir al usuario)."""


class AIProvider(ABC):
    provider_type: ClassVar[str]  # "local" | "hosted"
    egress: ClassVar[bool]  # ¿el dato sale de la red del operador?

    def __init__(self, model: str, base_url: str | None, token: str | None) -> None:
        self.model = model
        self.base_url = base_url
        self.token = token

    @abstractmethod
    async def review(self, context: AIReviewContext) -> AIReviewResult:
        """Explica y prioriza los findings. Nunca recibe código (invariante)."""
