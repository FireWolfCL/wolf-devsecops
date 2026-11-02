"""Features read-only: inventario de dependencias (Trivy) y mapa de módulos (GitHub).

Conector con httpx.MockTransport (sin red), agregador con conector inyectado y rutas
con agregador stub + RBAC real. ADR-001: solo lectura; ADR-002: token por env var.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from wolf_devsecops.api.deps import get_aggregator, get_config
from wolf_devsecops.auth import security
from wolf_devsecops.connectors.github import GitHubConnector
from wolf_devsecops.connectors.trivy import TrivyConnector
from wolf_devsecops.core.aggregator import Aggregator
from wolf_devsecops.core.cache import TTLCache
from wolf_devsecops.core.config import (
    AppConfig,
    GitHubProjectConfig,
    ProjectConfig,
    SourceConfig,
    TrivyProjectConfig,
)
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models import (
    DependencyInventory,
    ModuleNode,
    ModuleTree,
    SourceResult,
    SourceStatus,
)
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.settings import get_settings

FULL = {
    "Results": [{
        "Target": "package-lock.json", "Type": "npm",
        "Packages": [
            {"Name": "lodash", "Version": "4.17.20"},
            {"Name": "left-pad", "Version": "1.3.0"},
        ],
        "Vulnerabilities": [
            {"VulnerabilityID": "CVE-1", "PkgName": "lodash", "InstalledVersion": "4.17.20", "FixedVersion": "4.17.21"},
            {"VulnerabilityID": "CVE-2", "PkgName": "lodash", "InstalledVersion": "4.17.20"},
        ],
    }]
}
ONLY_VULNS = {
    "Results": [{
        "Target": "debian 12", "Type": "debian",
        "Vulnerabilities": [{"VulnerabilityID": "CVE-9", "PkgName": "openssl", "InstalledVersion": "3.0.1"}],
    }]
}


def _trivy(report: dict) -> TrivyConnector:
    return TrivyConnector(
        SourceConfig(url="https://art.local", token_env="T"), token=None,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=report)),
    )


def _tproj() -> ProjectConfig:
    return ProjectConfig(key="p", name="P", trivy=TrivyProjectConfig(report_path="r.json"))


async def test_packages_con_list_all_pkgs():
    pkgs, degraded = await _trivy(FULL).inventory(_tproj())
    assert degraded is False
    by = {p["name"]: p for p in pkgs}
    assert set(by) == {"lodash", "left-pad"}  # incluye el paquete sin CVE
    assert by["lodash"]["vulnerable"] and by["lodash"]["cve_ids"] == ["CVE-1", "CVE-2"]
    assert by["lodash"]["fixed_version"] == "4.17.21"
    assert by["left-pad"]["vulnerable"] is False and by["left-pad"]["ecosystem"] == "npm"
    assert pkgs[0]["name"] == "lodash"  # vulnerables primero
    assert len(await _trivy(FULL).packages(_tproj())) == 2


async def test_packages_degrada_sin_packages():
    pkgs, degraded = await _trivy(ONLY_VULNS).inventory(_tproj())
    assert degraded is True
    assert [p["name"] for p in pkgs] == ["openssl"] and pkgs[0]["fixed_version"] is None


def _tree_handler(tree: list[dict], truncated: bool = False):
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main"})
        assert req.url.path == "/repos/o/r/git/trees/main" and req.url.params["recursive"] == "1"
        return httpx.Response(200, json={"tree": tree, "truncated": truncated})
    return h


def _gh(handler) -> GitHubConnector:
    return GitHubConnector(SourceConfig(url="https://api.github.com", token_env="X"), token=None,
                           transport=httpx.MockTransport(handler))


def _gproj() -> ProjectConfig:
    return ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))


TREE = [
    {"path": "src", "type": "tree"},
    {"path": "src/a.py", "type": "blob", "size": 10},
    {"path": "src/deep/x/y/z/w/v.py", "type": "blob", "size": 1},
    {"path": "vendor", "type": "commit"},
]


async def test_fetch_tree_ok_y_acotado():
    t = await _gh(_tree_handler(TREE)).fetch_tree(_gproj(), max_nodes=100, max_depth=3)
    assert [(n.path, n.type, n.size) for n in t.nodes] == [("src", "dir", None), ("src/a.py", "file", 10)]
    assert t.truncated is True  # el archivo profundo se recortó
    t2 = await _gh(_tree_handler(TREE[:2])).fetch_tree(_gproj(), max_nodes=1)
    assert len(t2.nodes) == 1 and t2.truncated is True
    t3 = await _gh(_tree_handler(TREE[:2], truncated=True)).fetch_tree(_gproj())
    assert t3.truncated is True


def _agg(monkeypatch, connector, source):
    cfg = AppConfig(sources={source: SourceConfig(url="https://x", token_env="X")},
                    projects=[ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"),
                                            trivy=TrivyProjectConfig(report_path="r.json"))])
    agg = Aggregator(cfg, TTLCache(300))
    monkeypatch.setattr(agg, "_build_connector", lambda name: connector)
    return agg, cfg.projects[0]


async def test_aggregator_dependencies_cachea(monkeypatch):
    calls = {"n": 0}

    class C(TrivyConnector):
        async def _report(self, project):
            calls["n"] += 1
            return FULL
    agg, p = _agg(monkeypatch, C(SourceConfig(url="https://x", token_env="X"), None), "trivy")
    r1 = await agg.get_dependencies(p)
    r2 = await agg.get_dependencies(p)
    assert r1.status == SourceStatus.ok and r1.data.source == "trivy" and len(r1.data.packages) == 2
    assert calls["n"] == 1 and r2.data.project_key == "p"


async def test_aggregator_codemap_degrada_y_no_configurado(monkeypatch):
    agg, p = _agg(monkeypatch, _gh(lambda r: httpx.Response(500)), "github")
    assert (await agg.get_codemap(p)).status == SourceStatus.unavailable
    bare = Aggregator(AppConfig(projects=[ProjectConfig(key="q", name="Q")]), TTLCache(300))
    q = bare._config.projects[0]
    assert (await bare.get_codemap(q)).status == SourceStatus.not_configured
    assert (await bare.get_dependencies(q)).status == SourceStatus.not_configured


class _Stub:
    mode = "ok"

    async def get_dependencies(self, project):
        return self._r(DependencyInventory(project_key=project.key, generated_at=datetime.now(UTC)))

    async def get_codemap(self, project):
        return self._r(ModuleTree(project_key=project.key, generated_at=datetime.now(UTC),
                                  nodes=[ModuleNode(path="a", type="file", size=1)]))

    def _r(self, data):
        if self.mode == "nc":
            return SourceResult(status=SourceStatus.not_configured)
        if self.mode == "down":
            return SourceResult(status=SourceStatus.unavailable, error="caída")
        return SourceResult(status=SourceStatus.ok, data=data)


@pytest.fixture
async def env(tmp_path):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/dc.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    headers = {}
    async with db_engine.get_sessionmaker()() as s:
        for role in Role:
            u = User(username=f"u-{role.value}", role=role, is_active=True, token_version=0,
                     mfa_enabled=(role == Role.admin))
            s.add(u)
            await s.flush()
            tok = security.create_access_token(subject=u.id, role=role.value, token_version=0)
            headers[role.value] = {"Authorization": f"Bearer {tok}"}
        await s.commit()
    app = create_app()
    stub = _Stub()
    app.dependency_overrides[get_aggregator] = lambda: stub
    app.dependency_overrides[get_config] = lambda: AppConfig(projects=[ProjectConfig(key="p", name="P")])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, headers, stub
    await db_engine.dispose_engine()


@pytest.mark.parametrize("path", ["dependencies", "codemap"])
async def test_rutas_rbac_y_estados(env, path):
    c, h, stub = env
    url = f"/api/v1/projects/p/{path}"
    assert (await c.get(url)).status_code == 401
    for role in Role:  # todos los roles leen
        assert (await c.get(url, headers=h[role.value])).status_code == 200
    body = (await c.get(url, headers=h["viewer"])).json()
    assert body["project_key"] == "p" and "generated_at" in body
    assert (await c.get(f"/api/v1/projects/zz/{path}", headers=h["viewer"])).status_code == 404
    stub.mode = "nc"
    assert (await c.get(url, headers=h["viewer"])).status_code == 404
    stub.mode = "down"
    assert (await c.get(url, headers=h["viewer"])).status_code == 503
