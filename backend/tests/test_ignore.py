"""MEJORAS 6.3.c — excepciones versionables (config/wolf-ignore.yaml).

Solo lectura hacia fuentes (ADR-001): excluye del score ids de escáneres sin estado
nativo (Trivy suelto) y respeta el vencimiento (una excepción vencida vuelve a contar).
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import httpx
import pytest

from wolf_devsecops.connectors.trivy import TrivyConnector
from wolf_devsecops.core import ignore as ign
from wolf_devsecops.core.config import ProjectConfig, SourceConfig, TrivyProjectConfig
from wolf_devsecops.settings import get_settings

_FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "trivy"

_YAML = """
version: 1
global:
  - {{ cve: CVE-GLOBAL, motivo: FP, aprobado_por: x, fecha: 2026-01-01, vence: {gv} }}
projects:
  wolf-cloud:
    - {{ cve: CVE-0001, motivo: "riesgo aceptado", aprobado_por: x, fecha: 2026-01-01, vence: {v} }}
    - {{ rule: DS002, motivo: "root requerido", aprobado_por: x, fecha: 2026-01-01, vence: {v} }}
    - {{ cve: CVE-VENCIDA, motivo: FP, aprobado_por: x, fecha: 2026-01-01, vence: 2000-01-01 }}
"""


def _write(tmp_path, v="2999-01-01", gv="2999-01-01"):
    p = tmp_path / "wolf-ignore.yaml"
    p.write_text(_YAML.format(v=v, gv=gv), encoding="utf-8")
    return p


def test_load_merge_global_y_proyecto(tmp_path):
    f = ign.load_ignore(_write(tmp_path))
    ids = f.active_ids("wolf-cloud", today=date(2026, 11, 4))
    assert ids == {"CVE-0001", "DS002", "CVE-GLOBAL"}  # suyos + global, en MAYÚSCULAS
    assert f.active_ids("otro", today=date(2026, 11, 4)) == {"CVE-GLOBAL"}  # solo global


def test_vencida_no_suprime_y_sale_en_expired(tmp_path):
    f = ign.load_ignore(_write(tmp_path))
    today = date(2026, 11, 4)
    assert "CVE-VENCIDA" not in f.active_ids("wolf-cloud", today=today)
    assert "CVE-VENCIDA" in {e.key for e in f.expired("wolf-cloud", today=today)}


def test_archivo_ausente_no_suprime_nada(tmp_path):
    f = ign.load_ignore(tmp_path / "no-existe.yaml")
    assert f.active_ids("wolf-cloud") == set()


def _connector() -> TrivyConnector:
    data = json.loads((_FIX / "report.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=data)

    return TrivyConnector(
        SourceConfig(url="https://artifacts.local", token_env="T"),
        token=None,
        transport=httpx.MockTransport(handler),
    )


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="wolf-cloud", name="Wolf-Cloud",
        trivy=TrivyProjectConfig(report_path="reports/wolf-cloud.json"),
    )


@pytest.mark.asyncio
async def test_trivy_excluye_del_score_lo_ignorado(tmp_path, monkeypatch):
    # Sin ignore: 7 (1 crit, 2 high, 2 med, 1 low, 1 info).
    base = await _connector().fetch_summary(_project())
    assert base.total_active == 7 and base.counts.critical == 1

    # Con CVE-0001 (crítico) y DS002 (misconfig medium) ignorados y vigentes.
    monkeypatch.setattr(get_settings(), "ignore_path", str(_write(tmp_path)))
    s = await _connector().fetch_summary(_project())
    assert s.counts.critical == 0  # CVE-0001 ya no cuenta
    assert s.counts.medium == 1  # DS002 fuera (quedaba 1 vuln medium)
    assert s.total_active == 5
    # El detalle también los omite (cuadra con el score).
    detail = await _connector().fetch_detail(_project())
    ids = {f["id"] for f in detail.payload["findings"]}
    assert "CVE-0001" not in ids and "DS002" not in ids
    assert "CVE-0002" in ids  # uno no ignorado sigue
