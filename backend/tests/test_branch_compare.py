"""Comparación de postura entre ramas (ADR-024): lógica del agregador + validación.

MVP: forge GitHub y check ci-green (conscientes de rama). Read-only y al vuelo
(ADR-001/012). El score por rama se apoya SOLO en lo que varía por rama; el resto
queda `na` (not_branch_comparable). NO es histórico (eso es ADR-019).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from wolf_devsecops.api.routes_projects import _parse_refs
from wolf_devsecops.core.aggregator import Aggregator, BranchCompareUnsupported
from wolf_devsecops.core.cache import TTLCache
from wolf_devsecops.core.config import AppConfig, GitHubProjectConfig, ProjectConfig, SourceConfig
from wolf_devsecops.models import CIStatus, CISummary


class _FakeGH:
    """Conector GitHub de prueba: devuelve el estado de CI por rama sin tocar la red."""

    def __init__(self, by_ref: dict[str, CIStatus]) -> None:
        self._by_ref = by_ref

    async def fetch_summary_for_ref(self, project, ref: str) -> CISummary:
        return CISummary(status=self._by_ref.get(ref, CIStatus.unknown), workflow="ci.yml", branch=ref)


def _agg(project: ProjectConfig) -> Aggregator:
    cfg = AppConfig(
        sources={"github": SourceConfig(url="https://api.github.com", token_env="X")},
        projects=[project],
    )
    return Aggregator(cfg, TTLCache(300))


def _gh_project() -> ProjectConfig:
    return ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))


# ─── lógica de compare_refs ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ranking_deltas_y_regresiones(monkeypatch):
    project = _gh_project()
    a = _agg(project)
    fake = _FakeGH({"main": CIStatus.success, "sec": CIStatus.success, "dev": CIStatus.failure})
    monkeypatch.setattr(a, "_build_connector", lambda name: fake)

    cmp = await a.compare_refs(project, ["main", "sec", "dev"], "main")

    assert cmp.comparable_checks == ["ci-green"]
    assert cmp.project_key == "p" and cmp.baseline == "main"
    by = {r.ref: r for r in cmp.refs}
    assert by["main"].posture.score == 100 and by["sec"].posture.score == 100
    assert by["dev"].posture.score == 0
    # honestidad: la rama que GitHub reporta se refleja
    assert by["dev"].ci_branch == "dev"
    # deltas vs baseline
    assert by["main"].delta_vs_baseline == 0
    assert by["sec"].delta_vs_baseline == 0
    assert by["dev"].delta_vs_baseline == -100
    # dev regresa en ci-green respecto a main; sec no cambia nada
    assert by["dev"].new_fails == ["ci-green"] and by["dev"].resolved_fails == []
    assert by["sec"].new_fails == [] and by["sec"].resolved_fails == []
    # ranking: 100 antes que 0; empate main/sec respeta el orden pedido
    assert cmp.ranking == ["main", "sec", "dev"]
    assert cmp.note and "ci-green" in cmp.note


@pytest.mark.asyncio
async def test_resolved_fails_cuando_baseline_esta_rojo(monkeypatch):
    project = _gh_project()
    a = _agg(project)
    fake = _FakeGH({"dev": CIStatus.failure, "sec": CIStatus.success})
    monkeypatch.setattr(a, "_build_connector", lambda name: fake)

    cmp = await a.compare_refs(project, ["dev", "sec"], "dev")

    by = {r.ref: r for r in cmp.refs}
    assert by["sec"].resolved_fails == ["ci-green"] and by["sec"].new_fails == []
    assert by["sec"].delta_vs_baseline == 100
    assert cmp.ranking == ["sec", "dev"]  # sec (100) mejor que dev (0)


@pytest.mark.asyncio
async def test_rama_sin_run_queda_ultima_en_ranking(monkeypatch):
    project = _gh_project()
    a = _agg(project)
    fake = _FakeGH({"main": CIStatus.success, "wip": CIStatus.unknown})
    monkeypatch.setattr(a, "_build_connector", lambda name: fake)

    cmp = await a.compare_refs(project, ["main", "wip"], "main")

    by = {r.ref: r for r in cmp.refs}
    assert by["wip"].posture.score is None  # sin verdicto ⇒ sin_datos
    assert by["wip"].delta_vs_baseline is None  # no se inventa un delta contra sin_datos
    assert cmp.ranking == ["main", "wip"]  # sin_datos al final


@pytest.mark.asyncio
async def test_cache_por_rama_no_colisiona(monkeypatch):
    # dos ramas con estados distintos deben puntuar distinto: si la clave de caché no
    # incluyera el ref, una pisaría a la otra (regresión que ADR-024 señala como crítica).
    project = _gh_project()
    a = _agg(project)
    fake = _FakeGH({"a": CIStatus.success, "b": CIStatus.failure})
    monkeypatch.setattr(a, "_build_connector", lambda name: fake)

    cmp = await a.compare_refs(project, ["a", "b"], "a")
    by = {r.ref: r for r in cmp.refs}
    assert by["a"].posture.score == 100 and by["b"].posture.score == 0


@pytest.mark.asyncio
async def test_forge_no_github_es_no_soportado(monkeypatch):
    project = ProjectConfig(key="p", name="P")  # sin bloque github ⇒ sin forge
    a = _agg(project)
    with pytest.raises(BranchCompareUnsupported):
        await a.compare_refs(project, ["main", "dev"], "main")


# ─── validación de la query (_parse_refs) ─────────────────────────────────────
def test_parse_refs_normaliza_y_deduplica():
    assert _parse_refs("main, sec ,dev,main") == ["main", "sec", "dev"]


def test_parse_refs_exige_al_menos_dos():
    with pytest.raises(HTTPException) as e:
        _parse_refs("main")
    assert e.value.status_code == 400


def test_parse_refs_rechaza_espacios_internos():
    with pytest.raises(HTTPException):
        _parse_refs("ma in,dev")


def test_parse_refs_cota_maximo():
    with pytest.raises(HTTPException):
        _parse_refs("a,b,c,d,e,f,g")
