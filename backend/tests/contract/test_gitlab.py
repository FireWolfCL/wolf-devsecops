"""Test de contrato del conector GitLab (ADR-008, MEJORAS 1.3).

Alimenta al conector con respuestas-ejemplo GRABADAS de la API v4 de GitLab vía un
transporte simulado que enruta por path (/version, /pipelines, /releases). Si GitLab
cambia el shape (status del pipeline, tag_name, assets.links), el test deja de reflejar
la realidad y debe actualizarse con el conector — esa es la señal que queremos en CI.
"""

import json
import pathlib

import httpx
import pytest

from wolf_devsecops.connectors.gitlab import GitLabConnector
from wolf_devsecops.core.config import GitLabProjectConfig, ProjectConfig, SourceConfig
from wolf_devsecops.models import CIStatus

_FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "gitlab"


def _load(name: str):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


def _connector(status_pipelines: int = 200) -> GitLabConnector:
    pipelines = _load("pipelines.json")
    releases = _load("releases.json")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/version"):
            return httpx.Response(200, json={"version": "16.7.0"})
        if path.endswith("/pipelines"):
            return httpx.Response(status_pipelines, json=pipelines)
        if path.endswith("/releases"):
            return httpx.Response(200, json=releases)
        return httpx.Response(404, json={})

    return GitLabConnector(
        SourceConfig(url="https://gitlab.example.com", token_env="GITLAB_TOKEN"),
        token="fake-token",
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="wolf-cloud", name="Wolf-Cloud",
        gitlab=GitLabProjectConfig(project="grupo/wolf-cloud", ref="main"),
    )


@pytest.mark.asyncio
async def test_gitlab_pipeline_exitoso_mapea_success():
    summary = await _connector().fetch_summary(_project())
    assert summary.status == CIStatus.success
    assert summary.branch == "main"
    assert summary.run_number == 1234
    assert str(summary.html_url).endswith("/pipelines/1234")
    assert summary.finished_at is not None


@pytest.mark.asyncio
async def test_gitlab_sin_pipelines_devuelve_unknown():
    conn = _connector()
    # Respuesta vacía → unknown (no revienta).
    conn._transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    summary = await conn.fetch_summary(_project())
    assert summary.status == CIStatus.unknown


@pytest.mark.asyncio
async def test_gitlab_release_con_sbom():
    rel = await _connector().fetch_release(_project())
    assert rel.tag == "v2.4.0"
    assert rel.name == "Wolf Cloud 2.4.0"
    assert rel.published_at is not None
    assert str(rel.sbom_url).endswith("/package_files/99/download")  # detecta el asset SBOM


@pytest.mark.asyncio
async def test_gitlab_probe_version_y_auth():
    probe = await _connector().probe()
    assert probe.reachable and probe.authenticated
    assert probe.version == "16.7.0" and probe.compatible


@pytest.mark.asyncio
async def test_gitlab_detalle_trae_pipeline():
    detail = await _connector().fetch_detail(_project())
    assert detail.source == "gitlab"
    assert detail.payload["latest_pipeline"]["id"] == 1234
