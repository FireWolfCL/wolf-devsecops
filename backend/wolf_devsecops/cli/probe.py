"""Prueba de fuentes para el wizard (ADR-010, F8).

Reusa `Connector.probe()` (no duplica I/O): salud + autenticación + versión
soportada (ADR-008). No lanza: devuelve ProbeResult.
"""

from __future__ import annotations

from ..connectors.registry import CONNECTOR_REGISTRY
from ..core.config import SourceConfig
from ..core.credentials import resolve_token
from ..models import ProbeResult


async def probe_source(source_name: str, url: str, token_env: str | None = None) -> ProbeResult:
    cls = CONNECTOR_REGISTRY.get(source_name)
    if cls is None:
        return ProbeResult(
            source=source_name,
            reachable=False,
            authenticated=False,
            detail=f"No hay conector para '{source_name}'.",
        )
    connector = cls(SourceConfig(url=url, token_env=token_env or ""), resolve_token(token_env))
    return await connector.probe()
