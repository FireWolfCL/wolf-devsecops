"""Test de contrato del conector de DefectDojo (ADR-008)."""

import json
import pathlib

import httpx
import pytest

from wolf_devsecops.connectors.defectdojo import DefectDojoConnector
from wolf_devsecops.core.config import DefectDojoProjectConfig, ProjectConfig, SourceConfig

_FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "defectdojo"


def _connector() -> DefectDojoConnector:
    findings = json.loads((_FIX / "findings.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/findings/"
        return httpx.Response(200, json=findings)

    return DefectDojoConnector(
        SourceConfig(url="https://dd.local", token_env="DEFECTDOJO_TOKEN"),
        token="fake",
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="wolf-cloud",
        name="Wolf-Cloud",
        defectdojo=DefectDojoProjectConfig(product="Wolf-Cloud"),
    )


@pytest.mark.asyncio
async def test_defectdojo_cuenta_por_severidad():
    summary = await _connector().fetch_summary(_project())
    assert summary.total_active == 4
    assert summary.counts.critical == 1
    assert summary.counts.high == 1
    assert summary.counts.medium == 1
    assert summary.counts.low == 1
    assert summary.counts.total == 4


@pytest.mark.asyncio
async def test_defectdojo_categoriza_por_tipo_de_escaneo():
    # 6.2: el test_type de DefectDojo (related_fields) se mapea a categoría estable.
    summary = await _connector().fetch_summary(_project())
    assert summary.categories == {"secrets": 1, "sast": 1, "sca": 1, "dast": 1}


@pytest.mark.asyncio
async def test_defectdojo_detalle_lleva_categoria():
    # 6.2: cada finding del detalle lleva su categoría para el filtro del frontend.
    detail = await _connector().fetch_detail(_project())
    findings = detail.payload["findings"]
    assert all("category" in f for f in findings)
    assert {f["category"] for f in findings} == {"secrets", "sast", "sca", "dast"}


@pytest.mark.asyncio
async def test_defectdojo_detalle_lleva_estado_de_gestion():
    # 2.3: cada finding lleva su estado de gestión (para marcar «en triage» en
    # Contenedores) y el CVE queda como id para cruzar con Trivy.
    detail = await _connector().fetch_detail(_project())
    by_title = {f["title"]: f for f in detail.payload["findings"]}
    assert all("status" in f for f in detail.payload["findings"])
    assert by_title["Hardcoded AWS secret key"]["status"] == "under_review"
    assert by_title["Outdated dependency (CVE-2024-1234)"]["status"] == "verified"
    assert by_title["Reflected XSS"]["status"] == "active"
    assert by_title["SQL injection"]["status"] == "active"
    # El CVE queda como id → es lo que cruza con el CVE de Trivy en la vista.
    assert by_title["Outdated dependency (CVE-2024-1234)"]["id"] == "CVE-2024-1234"


@pytest.mark.asyncio
async def test_defectdojo_no_cuenta_gestionados_fuera():
    # 6.3.a: un finding risk-accepted (o FP / out-of-scope / mitigado) devuelto por
    # el API NO debe pesar en el score ni aparecer en el drill-down activo, aunque
    # venga con active=true. El guardia del conector lo excluye (defensa en profundidad).
    conn, project = _connector(), _project()
    summary = await conn.fetch_summary(project)
    # El fixture trae 5 resultados (count=5) con uno risk-accepted → 4 cuentan.
    assert summary.total_active == 4
    assert summary.counts.total == 4
    assert summary.counts.critical == 1  # el 2º crítico es risk-accepted: no suma
    detail = await conn.fetch_detail(project)
    titles = {f["title"] for f in detail.payload["findings"]}
    assert "Risk-accepted old CVE" not in titles
    assert "CVE-2023-9999" not in {f.get("id") for f in detail.payload["findings"]}
