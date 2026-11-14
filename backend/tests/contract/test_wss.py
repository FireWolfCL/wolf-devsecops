"""Test de contrato del conector de WSS (ADR-008 / ADR-011).

⚠️ Valida el shape SUPUESTO de la API de Wolf Security Suite. Si el contrato real
difiere, este test (y el fixture) deben actualizarse junto con el conector.
"""

import json
import pathlib

import httpx
import pytest

from wolf_devsecops.connectors.wss import WSSConnector
from wolf_devsecops.core.config import ProjectConfig, SourceConfig, WSSProjectConfig

_FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "wss"


def _connector() -> WSSConnector:
    data = json.loads((_FIX / "vulnerabilities.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/vulnerabilities"
        return httpx.Response(200, json=data)

    return WSSConnector(
        SourceConfig(url="https://wss.local", token_env="WSS_TOKEN"),
        token="fake",
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="wolf-cloud",
        name="Wolf-Cloud",
        wss=WSSProjectConfig(product="Wolf-Cloud"),
    )


@pytest.mark.asyncio
async def test_wss_vulns_por_severidad():
    summary = await _connector().fetch_summary(_project())
    assert summary.total_active == 7
    assert summary.counts.critical == 1
    assert summary.counts.high == 2
    assert summary.counts.medium == 3
