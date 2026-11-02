"""F10 — POST /api/v1/settings/sources/{name}/test (probar conexión).

Verifica: admin corre probe() sobre una fuente configurada (url muerta → reachable
False, sin romper); fuente desconocida → 404; RBAC (anon 401, operator 403).
"""

from __future__ import annotations

import httpx
import pytest

from wolf_devsecops.api import deps
from wolf_devsecops.auth import security
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models_db.config import SourceConfigRow
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import config_service
from wolf_devsecops.settings import get_settings

DEAD = "http://127.0.0.1:9"  # conexión rechazada rápido (no sale a la red)


@pytest.fixture
async def env(tmp_path):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/srctest.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    headers = {}
    async with db_engine.get_sessionmaker()() as s:
        for role in (Role.admin, Role.operator):
            u = User(username=f"u-{role.value}", role=role, is_active=True, token_version=0,
                     mfa_enabled=(role == Role.admin))  # MFA obligatorio para admin (T-AUTH-04)
            s.add(u)
            await s.flush()
            tok = security.create_access_token(subject=u.id, role=role.value, token_version=0)
            headers[role.value] = {"Authorization": f"Bearer {tok}"}
        # Fuente trivy configurada con URL muerta.
        s.add(SourceConfigRow(name="trivy", url=DEAD, token_env="NOPE_TOKEN", enabled=True))
        await s.commit()
    await config_service.refresh_config()
    deps.reset_aggregator()
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, headers
    config_service.invalidate_config()
    deps.reset_aggregator()
    await db_engine.dispose_engine()


async def test_probe_fuente_configurada_url_muerta(env):
    c, h = env
    r = await c.post("/api/v1/settings/sources/trivy/test", headers=h["admin"])
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "trivy"
    assert body["reachable"] is False  # url muerta, pero no rompe


async def test_probe_fuente_desconocida_404(env):
    c, h = env
    assert (await c.post("/api/v1/settings/sources/nope/test", headers=h["admin"])).status_code == 404


async def test_probe_rbac(env):
    c, h = env
    assert (await c.post("/api/v1/settings/sources/trivy/test")).status_code == 401
    assert (await c.post("/api/v1/settings/sources/trivy/test", headers=h["operator"])).status_code == 403
