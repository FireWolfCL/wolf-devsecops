"""Test de contrato del conector Jenkins (ADR-008, MEJORAS 1.4).

Alimenta al conector con una respuesta-ejemplo GRABADA de /lastBuild/api/json vía un
transporte simulado. Si Jenkins cambia el shape (result, building, number, timestamp),
el test deja de reflejar la realidad y debe actualizarse con el conector.
"""

import json
import pathlib

import httpx
import pytest

from wolf_devsecops.connectors.jenkins import JenkinsConnector
from wolf_devsecops.core.config import JenkinsProjectConfig, ProjectConfig, SourceConfig
from wolf_devsecops.models import CIStatus

_FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "jenkins"


def _connector(build: dict | None = None, status_code: int = 200, headers: dict | None = None):
    if build is None:
        build = json.loads((_FIX / "last_build.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/json") and "/job/" not in request.url.path:
            return httpx.Response(200, json={"mode": "NORMAL"}, headers={"X-Jenkins": "2.426.3"})
        return httpx.Response(status_code, json=build, headers=headers or {})

    return JenkinsConnector(
        SourceConfig(url="https://jenkins.example.com", token_env="JENKINS_TOKEN"),
        token="user:apitoken",
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(key="wolf-cloud", name="Wolf-Cloud",
                         jenkins=JenkinsProjectConfig(job="wolf-cloud"))


@pytest.mark.asyncio
async def test_jenkins_build_exitoso():
    summary = await _connector().fetch_summary(_project())
    assert summary.status == CIStatus.success
    assert summary.run_number == 128
    assert summary.workflow == "wolf-cloud"
    assert summary.finished_at is not None


@pytest.mark.asyncio
async def test_jenkins_building_es_running():
    summary = await _connector({"number": 9, "result": None, "building": True}).fetch_summary(_project())
    assert summary.status == CIStatus.running
    assert summary.finished_at is None  # aún corriendo: sin fecha de fin


@pytest.mark.asyncio
async def test_jenkins_failure_y_unstable_mapean_a_failure():
    assert (await _connector({"number": 1, "result": "FAILURE", "building": False})
            .fetch_summary(_project())).status == CIStatus.failure
    assert (await _connector({"number": 2, "result": "UNSTABLE", "building": False})
            .fetch_summary(_project())).status == CIStatus.failure


@pytest.mark.asyncio
async def test_jenkins_sin_build_404_unknown():
    summary = await _connector(build={}, status_code=404).fetch_summary(_project())
    assert summary.status == CIStatus.unknown


@pytest.mark.asyncio
async def test_jenkins_probe_version():
    probe = await _connector().probe()
    assert probe.reachable and probe.authenticated and probe.version == "2.426.3"


@pytest.mark.asyncio
async def test_jenkins_detalle_trae_build():
    detail = await _connector().fetch_detail(_project())
    assert detail.source == "jenkins"
    assert detail.payload["last_build"]["number"] == 128
