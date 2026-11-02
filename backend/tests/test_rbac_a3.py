"""A3 — enforcement RBAC (ADR-014, docs/ARCHITECTURE-ADMIN.md §5) por HTTP.

Un usuario por rol en SQLite temporal (create_all, como test_persistence_a0.py) y
Bearer armado con `create_access_token`. httpx.ASGITransport no dispara el lifespan.
Se prueba la DENEGACIÓN (401/403), no solo el happy path.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from wolf_devsecops.api.deps import get_aggregator, get_config
from wolf_devsecops.auth import dependencies, security
from wolf_devsecops.core.config import (
    AppConfig,
    GitHubProjectConfig,
    ProjectConfig,
    SourceConfig,
)
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import CAPABILITY_ROLES, Role, role_can, roles_for
from wolf_devsecops.settings import get_settings


class _StubAggregator:
    async def list_projects(self):
        return []


@pytest.fixture
async def env(tmp_path):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/rbac_a3.db"
    settings.ai_enabled = False
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    headers: dict[str, dict[str, str]] = {}
    async with db_engine.get_sessionmaker()() as s:
        for role in Role:
            u = User(username=f"u-{role.value}", role=role, is_active=True, token_version=0)
            s.add(u)
            await s.flush()
            tok = security.create_access_token(
                subject=u.id, role=u.role.value, token_version=u.token_version
            )
            headers[role.value] = {"Authorization": f"Bearer {tok}"}
        await s.commit()
    app = create_app()
    # Sin red ni config en BD: el agregador/config son stubs; aquí se prueba el guard.
    app.dependency_overrides[get_aggregator] = lambda: _StubAggregator()
    app.dependency_overrides[get_config] = lambda: AppConfig(
        sources={"github": SourceConfig(url="https://api.github.com", token_env="X")},
        projects=[ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, headers
    await db_engine.dispose_engine()


AI = "/api/v1/projects/p/ai/review"
PROJECTS = "/api/v1/projects"
SOURCE = "/api/v1/projects/p/sources/bogus"  # 400 de negocio: pasó el guard, sin red


@pytest.mark.parametrize("method,url", [("GET", PROJECTS), ("GET", SOURCE), ("POST", AI)])
async def test_no_token_is_401(env, method, url):
    client, _ = env
    assert (await client.request(method, url)).status_code == 401


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
@pytest.mark.parametrize("url", [PROJECTS, SOURCE])
async def test_any_authenticated_role_passes_read_guard(env, role, url):
    client, h = env
    r = await client.get(url, headers=h[role])
    assert r.status_code not in (401, 403)


async def test_projects_200_for_viewer_and_admin(env):
    client, h = env
    for role in ("viewer", "admin"):
        assert (await client.get(PROJECTS, headers=h[role])).status_code == 200


async def test_viewer_forbidden_on_ai_review_even_when_ai_off(env):
    client, h = env
    assert get_settings().ai_enabled is False
    r = await client.post(AI, headers=h["viewer"])
    assert r.status_code == 403  # el guard de rol corre ANTES del gate 503


@pytest.mark.parametrize("role", ["operator", "admin"])
async def test_operator_admin_pass_guard_then_hit_ai_gate_503(env, role):
    client, h = env
    r = await client.post(AI, headers=h[role])
    assert r.status_code == 503  # pasó el guard; frena el gate ai_enabled


async def test_ai_gate_order_unauthenticated_is_401_not_503(env):
    client, _ = env
    assert (await client.post(AI)).status_code == 401


async def test_health_stays_public(env):
    client, _ = env
    assert (await client.get("/api/health")).status_code == 200


async def test_users_me_needs_auth_only(env):
    client, h = env
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=h["viewer"])).status_code == 200


# --- require_role: fail-closed (unitario, sin HTTP) ------------------------------

class _U:
    def __init__(self, role):
        self.role = role


async def test_require_role_fail_closed():
    guard = dependencies.require_role(Role.admin)
    assert (await guard(_U(Role.admin))).role == Role.admin
    for bad in (Role.viewer, Role.operator, "root", "", None, "ADMIN"):
        with pytest.raises(HTTPException) as e:
            await guard(_U(bad))
        assert e.value.status_code == 403


async def test_require_role_without_roles_denies_everyone():
    guard = dependencies.require_role()
    for r in Role:
        with pytest.raises(HTTPException) as e:
            await guard(_U(r))
        assert e.value.status_code == 403


# --- matriz ejecutable (§5) ------------------------------------------------------

def test_matrix_matches_design_section_5():
    expected = {
        "projects.read": {"viewer", "operator", "admin"},
        "system.version.read": {"viewer", "operator", "admin"},
        "profile.self": {"viewer", "operator", "admin"},
        "ai.review": {"operator", "admin"},
        "ai.code_fix": {"operator", "admin"},
        "remediation.write": {"operator", "admin"},
        "settings.manage": {"admin"},
        "users.manage": {"admin"},
        "updates.manage": {"admin"},
    }
    assert {k: {r.value for r in v} for k, v in CAPABILITY_ROLES.items()} == expected


def test_role_can_default_deny():
    assert role_can(Role.admin, "users.manage")
    assert not role_can(Role.viewer, "ai.review")
    assert not role_can(Role.admin, "capability.that.does.not.exist")
    assert not role_can("root", "projects.read")
    assert roles_for("nope") == ()
