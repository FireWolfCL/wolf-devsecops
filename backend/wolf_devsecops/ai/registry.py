"""Registro de proveedores de IA: tipo → clase (ADR-007). Alta única."""

from __future__ import annotations

from .base import AIProvider
from .hosted import HostedAIProvider
from .local import LocalAIProvider

AI_PROVIDER_REGISTRY: dict[str, type[AIProvider]] = {
    LocalAIProvider.provider_type: LocalAIProvider,
    HostedAIProvider.provider_type: HostedAIProvider,
}
