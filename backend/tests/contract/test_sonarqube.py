"""Test de contrato del conector de SonarQube (ADR-008)."""

import json
import pathlib

import httpx
import pytest

from wolf_devsecops.connectors.sonarqube import SonarQubeConnector
from wolf_devsecops.core.config import ProjectConfig, SonarProjectConfig, SourceConfig

_FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "sonarqube"


def _connector() -> SonarQubeConnector:
    gate = json.loads((_FIX / "gate.json").read_text(encoding="utf-8"))
    measures = json.loads((_FIX / "measures.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/qualitygates/project_status":
            return httpx.Response(200, json=gate)
        if request.url.path == "/api/measures/component":
            return httpx.Response(200, json=measures)
        return httpx.Response(404, json={})

    return SonarQubeConnector(
        SourceConfig(url="https://sonar.local", token_env="SONAR_TOKEN"),
        token="fake",
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="demo-app",
        name="Demo App",
        sonarqube=SonarProjectConfig(project_key="demo-app"),
    )


@pytest.mark.asyncio
async def test_sonarqube_gate_y_metricas():
    q = await _connector().fetch_summary(_project())
    assert q.quality_gate == "ERROR"
    assert q.bugs == 3
    assert q.vulnerabilities == 1
    assert q.code_smells == 42
    assert q.coverage == 71.4
