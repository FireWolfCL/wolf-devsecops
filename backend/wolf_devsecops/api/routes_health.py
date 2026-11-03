"""Endpoints de operación (sin versionar): salud y metadatos del despliegue."""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__, edition, licensing
from ..core.config import AppConfig, ConfigError
from ..services import config_service
from ..settings import get_settings
from .deps import get_config

router = APIRouter()


def _edition_block() -> dict:
    """Edición efectiva + features/límites + estado de licencia, para que el frontend
    muestre/oculte y ponga los 'Upgrade'. Es informativo; el BACKEND es la autoridad."""
    ed = edition.current_edition()
    return {
        "edition": ed,
        "features": sorted(edition.features_for(ed)),
        "limits": edition.limits_for(ed),
        "license": licensing.status(get_settings()),
    }


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
            **_edition_block(),
        }
    # IA EFECTIVA (ADR-015): techo del entorno AND toggle en BD AND bloque válido.
    ai_enabled = config_service.ai_effectively_enabled(config)
    return {
        "project_count": len(config.projects),
        "sources_configured": sorted(config.sources.keys()),
        "ai_enabled": ai_enabled,
        **_edition_block(),
    }
