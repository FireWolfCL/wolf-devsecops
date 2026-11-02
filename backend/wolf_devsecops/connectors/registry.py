"""Registro de conectores: nombre de fuente → clase (ARCHITECTURE.md §2).

Punto ÚNICO de alta de fuentes. Añadir una fuente nueva (DefectDojo, SonarQube,
WSS, GitLab…) es implementar `Connector` y registrarla aquí. Nada más cambia.
"""

from __future__ import annotations

from .base import Connector
from .defectdojo import DefectDojoConnector
from .github import GitHubConnector
from .harbor import HarborConnector
from .sonarqube import SonarQubeConnector
from .trivy import TrivyConnector
from .wss import WSSConnector

CONNECTOR_REGISTRY: dict[str, type[Connector]] = {
    GitHubConnector.source_name: GitHubConnector,
    DefectDojoConnector.source_name: DefectDojoConnector,
    SonarQubeConnector.source_name: SonarQubeConnector,
    WSSConnector.source_name: WSSConnector,
    HarborConnector.source_name: HarborConnector,  # imágenes de contenedor (ADR-006)
    TrivyConnector.source_name: TrivyConnector,  # reportes Trivy JSON
}
