"""Regresión de los must-fix del modelo de amenazas (docs/THREAT-MODEL-ADMIN.md):
T-AUTH-01 (rate-limit login), T-AUTH-04 (MFA obligatorio admin), T-UPD-01 (lock migrate).
"""

from __future__ import annotations

import httpx
import pytest

from wolf_devsecops.auth import security
from wolf_devsecops.auth.ratelimit import login_limiter
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import config_service, update_service
from wolf_devsecops.settings import get_settings


@pytest.fixture
async def env(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/hard.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    users = {}
    async with db_engine.get_sessionmaker()() as s:
        # admin SIN MFA (debe quedar bloqueado de lo admin) y admin CON MFA.
        for key, role, mfa in [("admin_no_mfa", Role.admin, False),
                               ("admin_mfa", Role.admin, True)]:
            u = User(username=key, role=role, is_active=True, token_version=0, mfa_enabled=mfa)
            s.add(u)
            await s.flush()
            tok = security.create_access_token(subject=u.id, role=role.value, token_version=0)
            users[key] = {"id": u.id, "hdr": {"Authorization": f"Bearer {tok}"}}
        await s.commit()
    await config_service.refresh_config()
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, users
    config_service.invalidate_config()
    await db_engine.dispose_engine()


async def test_mfa_obligatorio_admin_bloquea_admin_sin_mfa(env):
    c, u = env
    # Admin sin MFA: 403 en superficie admin…
    assert (await c.get("/api/v1/settings/sources", headers=u["admin_no_mfa"]["hdr"])).status_code == 403
    assert (await c.get("/api/v1/users", headers=u["admin_no_mfa"]["hdr"])).status_code == 403
    # …pero SÍ puede ver su perfil y enrolar MFA (camino de salida).
    assert (await c.get("/api/v1/auth/me", headers=u["admin_no_mfa"]["hdr"])).status_code == 200
    assert (await c.post("/api/v1/users/me/mfa/setup", headers=u["admin_no_mfa"]["hdr"])).status_code == 200
    # Admin con MFA: entra.
    assert (await c.get("/api/v1/settings/sources", headers=u["admin_mfa"]["hdr"])).status_code == 200


async def test_rate_limit_login_429(env):
    c, _ = env
    login_limiter._hits.clear()  # estado limpio para el test
    codes = []
    for _ in range(14):
        r = await c.post("/api/v1/auth/login", data={"username": "nadie", "password": "x"})
        codes.append(r.status_code)
    assert 429 in codes  # tras el umbral (10/min) aparece el 429
    assert codes[:10] == [401] * 10  # los primeros 10 son credenciales inválidas


async def test_migrate_lock_409_concurrente():
    # Con el lock tomado, una segunda aplicación de migraciones se rechaza (T-UPD-01).
    async with update_service._apply_lock:
        with pytest.raises(update_service.MigrationInProgressError):
            await update_service.apply_migrations(environment="development", confirm_backup=False)
