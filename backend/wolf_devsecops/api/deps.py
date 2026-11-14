"""Inyección de dependencias de la API (ARCHITECTURE.md §2).

Singletons perezosos: la config se carga una vez; el agregador comparte una caché.
"""

from __future__ import annotations

from ..ai.base import AIProvider, AIProviderError
from ..ai.registry import AI_PROVIDER_REGISTRY
from ..core.aggregator import Aggregator
from ..core.cache import TTLCache
from ..core.config import AppConfig
from ..core.credentials import resolve_token
from ..services import config_service
from ..services.governance import DbGovernance, GovernanceProvider
from ..settings import get_settings


def get_config() -> AppConfig:
    """AppConfig actual. Ahora se ensambla desde la BD (ADR-015), pero el TIPO es el
    mismo contrato Pydantic de siempre: connectors/aggregator/ai no se enteran."""
    return config_service.get_cached_config()


_aggregator: Aggregator | None = None
# Overlay de gobierno (ADR-023): SINGLETON de módulo, vive aparte de la config.
# Sobrevive al rebuild del Aggregator por cambio de config (el gobierno no es config),
# así que una aceptación aprobada no se "des-invalida" al recargar el panel admin.
_governance: GovernanceProvider | None = None


def get_governance() -> GovernanceProvider:
    global _governance
    if _governance is None:
        _governance = DbGovernance()
    return _governance


def get_aggregator() -> Aggregator:
    global _aggregator
    # Rebuild si la config cambió (hot-reload del panel admin, ADR-015): el
    # Aggregator es inmutable respecto a su config, así que se reconstruye cuando
    # get_config() devuelve un AppConfig distinto al capturado. El overlay de gobierno
    # es el MISMO singleton entre rebuilds (no se pierde el estado de aceptaciones).
    current = get_config()
    if _aggregator is None or _aggregator._config is not current:
        settings = get_settings()
        _aggregator = Aggregator(current, TTLCache(settings.cache_ttl_seconds), get_governance())
    return _aggregator


def reset_aggregator() -> None:
    """Fuerza reconstrucción del agregador (p. ej. tras invalidar config)."""
    global _aggregator
    _aggregator = None


def get_ai_provider() -> AIProvider:
    """Construye el proveedor de IA desde la config. Lanza si no está configurado."""
    ai = get_config().ai
    if ai is None:
        raise AIProviderError("No hay bloque 'ai' en la configuración.")
    cls = AI_PROVIDER_REGISTRY.get(ai.provider)
    if cls is None:
        raise AIProviderError(f"Proveedor de IA desconocido: {ai.provider}")
    return cls(model=ai.model, base_url=ai.base_url, token=resolve_token(ai.token_env))
