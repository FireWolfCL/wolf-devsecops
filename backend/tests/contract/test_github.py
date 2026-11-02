"""Test de contrato del conector de GitHub (ADR-008).

Alimenta al conector con una respuesta-ejemplo GRABADA de la API de GitHub
(fixtures/github/runs.json) mediante un transporte simulado. Si GitHub cambia el
shape de su respuesta (nombres de campos como `workflow_runs`, `conclusion`,
`run_number`), este test deja de reflejar la realidad y debe actualizarse junto
con el conector — esa es justo la señal que queremos en CI.
"""

import json
import pathlib

import httpx
import pytest

from wolf_devsecops.connectors.github import GitHubConnector
from wolf_devsecops.core.config import GitHubProjectConfig, ProjectConfig, SourceConfig
from wolf_devsecops.models import CIStatus

_FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "github"


def _connector_with(fixture: dict, status_code: int = 200) -> GitHubConnector:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=fixture)

    return GitHubConnector(
        SourceConfig(url="https://api.github.com", token_env="GITHUB_TOKEN"),
        token="fake-token",
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="demo-app",
        name="Demo App",
        github=GitHubProjectConfig(repo="my-org/demo-app", ci_workflow="ci.yml"),
    )


@pytest.mark.asyncio
async def test_github_summary_mapea_run_exitoso():
    runs = json.loads((_FIXTURES / "runs.json").read_text(encoding="utf-8"))
    connector = _connector_with(runs)

    summary = await connector.fetch_summary(_project())

    assert summary.status == CIStatus.success
    assert summary.workflow == "ci.yml"
    assert summary.run_number == 42
    assert summary.branch == "main"
    assert summary.finished_at is not None


@pytest.mark.asyncio
async def test_github_sin_runs_devuelve_unknown():
    connector = _connector_with({"total_count": 0, "workflow_runs": []})

    summary = await connector.fetch_summary(_project())

    assert summary.status == CIStatus.unknown


@pytest.mark.asyncio
async def test_github_release_con_sbom():
    release = json.loads((_FIXTURES / "release.json").read_text(encoding="utf-8"))
    connector = _connector_with(release)

    result = await connector.fetch_release(_project())

    assert result.tag == "v0.2.0"
    assert result.sbom_url is not None
    assert str(result.sbom_url).endswith("sbom.cyclonedx.json")
