"""Test de contrato del conector Trivy (ADR-008). Valida el parseo de un reporte
Trivy JSON (SchemaVersion 2): vulnerabilidades + misconfiguraciones → FindingsSummary."""

import json
import pathlib

import httpx
import pytest

from wolf_devsecops.connectors.trivy import TrivyConnector
from wolf_devsecops.core.config import ProjectConfig, SourceConfig, TrivyProjectConfig

_FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "trivy"


def _connector() -> TrivyConnector:
    data = json.loads((_FIX / "report.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/reports/demo-app.json"
        return httpx.Response(200, json=data)

    return TrivyConnector(
        SourceConfig(url="https://artifacts.local", token_env="TRIVY_ARTIFACTS_TOKEN"),
        token=None,
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="demo-app",
        name="Demo App",
        trivy=TrivyProjectConfig(report_path="reports/demo-app.json"),
    )


@pytest.mark.asyncio
async def test_trivy_vulns_y_misconfigs_por_severidad():
    summary = await _connector().fetch_summary(_project())
    assert summary.counts.critical == 1
    assert summary.counts.high == 2
    assert summary.counts.medium == 2  # 1 vuln + 1 misconfig
    assert summary.counts.low == 1  # misconfig
    assert summary.counts.info == 1  # UNKNOWN → info
    assert summary.total_active == 7


@pytest.mark.asyncio
async def test_trivy_detail_schema_version():
    detail = await _connector().fetch_detail(_project())
    assert detail.source == "trivy"
    assert detail.payload["schema_version"] == 2
    assert detail.payload["total"] == 7
