"""Endpoints de operación (sin versionar): salud y metadatos del despliegue."""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..core.config import AppConfig, ConfigError
from ..services import config_service
from .deps import get_config

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    """Vivo. No depende de la config, así que responde aunque falte projects.yaml."""
    return {"status": "ok", "version": __version__}


@router.get("/api/meta")
def meta() -> dict:
    """Metadatos: cuántos proyectos y qué fuentes están configuradas.

    Expone `ai_enabled` para que el frontend muestre con honestidad el estado del
    gate del reviewer IA (ADR-007) sin tener que provocar un 503. No revela nada
    sensible: solo si la feature está activa, no el proveedor ni el modelo.
    """
    try:
        config: AppConfig = get_config()
    except ConfigError as exc:
        return {
            "project_count": 0,
            "sources_configured": [],
            "ai_enabled": False,  # sin config no hay IA efectiva (fail-closed)
            "config_error": str(exc),
        }
    # IA EFECTIVA (ADR-015): techo del entorno AND toggle en BD AND bloque válido.
    ai_enabled = config_service.ai_effectively_enabled(config)
    return {
        "project_count": len(config.projects),
        "sources_configured": sorted(config.sources.keys()),
        "ai_enabled": ai_enabled,
    }
