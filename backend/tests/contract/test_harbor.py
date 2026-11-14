"""Test de contrato del conector Harbor (ADR-008). Valida el mapeo del scan_overview
de un artefacto a FindingsSummary. Si Harbor cambia el shape, se actualiza aquí."""

import json
import pathlib

import httpx
import pytest

from wolf_devsecops.connectors.harbor import HarborConnector
from wolf_devsecops.core.config import HarborProjectConfig, ProjectConfig, SourceConfig

_FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "harbor"


def _connector() -> HarborConnector:
    data = json.loads((_FIX / "artifacts.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2.0/projects/library/repositories/app/artifacts"
        assert request.url.params.get("with_scan_overview") == "true"
        return httpx.Response(200, json=data)

    return HarborConnector(
        SourceConfig(url="https://harbor.local", token_env="HARBOR_TOKEN"),
        token="fake",
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="wolf-cloud",
        name="Wolf-Cloud",
        harbor=HarborProjectConfig(project="library", repository="app"),
    )


@pytest.mark.asyncio
async def test_harbor_scan_overview_por_severidad():
    summary = await _connector().fetch_summary(_project())
    assert summary.counts.critical == 1
    assert summary.counts.high == 2
    assert summary.counts.medium == 3
    assert summary.counts.low == 4
    assert summary.counts.info == 1  # Unknown → info
    assert summary.total_active == 11


@pytest.mark.asyncio
async def test_harbor_detail_trae_payload():
    detail = await _connector().fetch_detail(_project())
    assert detail.source == "harbor"
    assert detail.payload["total"] == 11
    assert detail.payload["harbor_project"] == "library"
