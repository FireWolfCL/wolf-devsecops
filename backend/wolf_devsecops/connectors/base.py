"""Interfaz común `Connector` (ARCHITECTURE.md §2, ADR-006/008).

Añadir una fuente nueva = una clase que implementa esta interfaz + una línea en
registry.py. El core, la API y el frontend no se tocan.

Resiliencia (ADR-008): cada conector declara `supported_versions` y expone
`probe()`, que NO lanza — devuelve un ProbeResult tipado. El parseo de respuestas
externas es tolerante a campos desconocidos (los modelos usan extra="ignore").
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel

from ..core.config import ProjectConfig, SourceConfig
from ..models import ProbeResult, SourceDetail


class ConnectorError(Exception):
    """Error genérico de conector (saneado antes de salir al usuario)."""


class ConnectorIncompatible(ConnectorError):
    """La versión de la fuente no está en supported_versions (ADR-008)."""


class ConnectorUnavailable(ConnectorError):
    """La fuente está configurada pero no respondió / falló la llamada."""


class Connector(ABC):
    source_name: ClassVar[str]
    supported_versions: ClassVar[str]  # rango semver, p. ej. ">=2.0,<3"

    def __init__(self, source_config: SourceConfig, token: str | None) -> None:
        self.source_config = source_config
        self.token = token

    @abstractmethod
    async def probe(self) -> ProbeResult:
        """Salud + autenticación + versión de la fuente. No lanza."""

    @abstractmethod
    async def fetch_summary(self, project: ProjectConfig) -> BaseModel:
        """Devuelve el bloque normalizado de esta fuente para el proyecto.

        Lanza ConnectorUnavailable si la fuente falla; el agregador lo traduce a
        un SourceResult(status=unavailable) sin romper el resto del proyecto.
        """

    @abstractmethod
    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        """Detalle crudo-normalizado de esta fuente para el proyecto."""
