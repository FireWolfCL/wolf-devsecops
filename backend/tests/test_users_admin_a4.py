"""A4 — CRUD admin de /api/v1/users (ADR-013/014): conflictos, guards, revocación.

Un guard por rol se prueba por DENEGACIÓN (401/403). Las reglas de dominio
(anti-autobloqueo, último-admin) se prueban por HTTP y directo contra el servicio.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from wolf_devsecops.auth import security
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models_db.audit import AuditLog
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import user_service
from wolf_devsecops.settings import get_settings

U = "/api/v1/users"


def _bearer(user: User) -> dict[str, str]:
    tok = security.create_access_token(
        subject=user.id, role=user.role.value, token_version=user.token_version or 0
    )
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
async def env(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/users_a4.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        admin = await user_service.create_user(
            s, username="admin", password="admin-password-1", role=Role.admin)
        admin.mfa_enabled = True  # MFA obligatorio para admin (T-AUTH-04)
        await s.commit()
        operator = await user_service.create_user(
            s, username="op", password="op-password-123", role=Role.operator)
        ids = {"admin": admin.id, "operator": operator.id}
        hdr = {"admin": _bearer(admin), "operator": _bearer(operator)}
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, hdr, ids
    await db_engine.dispose_engine()


async def _audit_actions():
    async with db_engine.get_sessionmaker()() as s:
        return [r.action for r in (await s.execute(select(AuditLog))).scalars().all()]


async def test_operator_and_anonymous_denied_everywhere(env):
    c, h, ids = env
    calls = [("GET", U), ("POST", U), ("PATCH", f"{U}/{ids['admin']}"),
             ("POST", f"{U}/{ids['admin']}/reset-password"),
             ("POST", f"{U}/{ids['admin']}/unlock")]
    for m, u in calls:
        assert (await c.request(m, u, json={})).status_code == 401
        assert (await c.request(m, u, headers=h["operator"], json={})).status_code == 403
    # escalada: el operator no puede promoverse ni a sí mismo
    r = await c.patch(f"{U}/{ids['operator']}", headers=h["operator"], json={"role": "admin"})
    assert r.status_code == 403
    async with db_engine.get_sessionmaker()() as s:
        assert (await s.get(User, ids["operator"])).role == Role.operator


async def test_create_list_duplicate_and_no_sensitive_fields(env):
    c, h, _ = env
    a = h["admin"]
    r = await c.post(U, headers=a, json={"username": "bob", "password": "bob-password-1",
                                          "role": "viewer", "full_name": "Bob"})
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "viewer" and body["must_change_password"] is True
    for forbidden in ("hashed_password", "password", "mfa_secret", "mfa_recovery_codes"):
        assert forbidden not in body
    assert (await c.post(U, headers=a, json={"username": "bob",
                                              "password": "other-password-1"})).status_code == 409
    assert (await c.post(U, headers=a, json={"username": "x", "password": "short"})
            ).status_code == 422
    assert (await c.post(U, headers=a, json={"username": "eve", "password": "eve-password-1",
                                              "is_admin": True})).status_code == 422
    r = await c.get(U, headers=a)
    assert r.status_code == 200 and r.json()["total"] == 3
    assert {u["username"] for u in r.json()["items"]} == {"admin", "op", "bob"}
    assert "hashed_password" not in r.text
    # el usuario creado puede iniciar sesión con la password dada
    login = await c.post("/api/v1/auth/login",
                         data={"username": "bob", "password": "bob-password-1"})
    assert login.status_code == 200


async def test_role_change_bumps_token_version_and_kills_old_token(env):
    c, h, ids = env
    old = h["operator"]
    assert (await c.get("/api/v1/auth/me", headers=old)).status_code == 200
    r = await c.patch(f"{U}/{ids['operator']}", headers=h["admin"], json={"role": "viewer"})
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    assert (await c.get("/api/v1/auth/me", headers=old)).status_code == 401  # token viejo cae
    async with db_engine.get_sessionmaker()() as s:
        u = await s.get(User, ids["operator"])
        assert u.token_version == 1
        new = _bearer(u)
    assert (await c.get("/api/v1/auth/me", headers=new)).status_code == 200

    # is_active=false también sube la versión; un PATCH sin cambio efectivo, no.
    await c.patch(f"{U}/{ids['operator']}", headers=h["admin"], json={"full_name": "Zed"})
    async with db_engine.get_sessionmaker()() as s:
        assert (await s.get(User, ids["operator"])).token_version == 1
    await c.patch(f"{U}/{ids['operator']}", headers=h["admin"], json={"is_active": False})
    async with db_engine.get_sessionmaker()() as s:
        u = await s.get(User, ids["operator"])
        assert u.token_version == 2 and u.is_active is False
    assert (await c.get("/api/v1/auth/me", headers=new)).status_code == 401


async def test_self_lockout_and_last_admin_guards(env):
    c, h, ids = env
    a = h["admin"]
    # único admin: no puede degradarse ni desactivarse (autobloqueo -> 400)
    assert (await c.patch(f"{U}/{ids['admin']}", headers=a,
                          json={"role": "viewer"})).status_code == 400
    assert (await c.patch(f"{U}/{ids['admin']}", headers=a,
                          json={"is_active": False})).status_code == 400
    # segundo admin: ahora uno puede degradar al otro (hay otro activo)...
    r = await c.post(U, headers=a, json={"username": "admin2", "password": "admin2-password",
                                          "role": "admin"})
    admin2 = r.json()["id"]
    assert (await c.patch(f"{U}/{admin2}", headers=a, json={"role": "operator"})
            ).status_code == 200
    # ...y el que queda es de nuevo el último: sigue protegido.
    assert (await c.patch(f"{U}/{ids['admin']}", headers=a,
                          json={"role": "operator"})).status_code == 400
    async with db_engine.get_sessionmaker()() as s:
        assert (await s.get(User, ids["admin"])).role == Role.admin
    assert (await c.patch(f"{U}/no-existe", headers=a, json={"full_name": "x"})
            ).status_code == 404


async def test_last_admin_guard_holds_even_if_actor_is_not_the_target(env):
    """Un actor distinto del afectado (p. ej. admin ya desactivado en paralelo) NO
    puede saltarse el guard: el servicio protege al último admin activo por sí mismo."""
    _, _, ids = env
    async with db_engine.get_sessionmaker()() as s:
        for kwargs in ({"role": Role.viewer}, {"is_active": False}):
            with pytest.raises(user_service.LastAdminError):
                await user_service.update_user(
                    s, ids["admin"], acting_user_id=ids["operator"], **kwargs)
        assert (await s.get(User, ids["admin"])).role == Role.admin
        assert (await s.get(User, ids["admin"])).is_active is True
        # No admin objetivo: sin guard (operator puede desactivarse aquí vía servicio).
        u = await user_service.update_user(
            s, ids["operator"], acting_user_id=ids["admin"], is_active=False)
        assert u is not None and u.is_active is False


async def test_reset_password_and_unlock(env):
    c, h, ids = env
    a = h["admin"]
    old = h["operator"]
    r = await c.post(f"{U}/{ids['operator']}/reset-password", headers=a)
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert len(temp) >= 12
    assert (await c.get("/api/v1/auth/me", headers=old)).status_code == 401  # sesiones caen
    login = await c.post("/api/v1/auth/login", data={"username": "op", "password": temp})
    assert login.status_code == 200 and login.json()["must_change_password"] is True
    assert (await c.post(f"{U}/nope/reset-password", headers=a)).status_code == 404

    async with db_engine.get_sessionmaker()() as s:
        u = await s.get(User, ids["operator"])
        u.failed_login_attempts = 5
        await s.commit()
    assert (await c.post(f"{U}/{ids['operator']}/unlock", headers=a)).status_code == 204
    async with db_engine.get_sessionmaker()() as s:
        assert (await s.get(User, ids["operator"])).failed_login_attempts == 0
    assert (await c.post(f"{U}/nope/unlock", headers=a)).status_code == 404


async def test_audit_events_without_secrets(env):
    c, h, ids = env
    a = h["admin"]
    await c.post(U, headers=a, json={"username": "zed", "password": "zed-password-12"})
    await c.patch(f"{U}/{ids['operator']}", headers=a, json={"role": "viewer"})
    r = await c.post(f"{U}/{ids['operator']}/reset-password", headers=a)
    temp = r.json()["temporary_password"]
    actions = await _audit_actions()
    for expected in ("user.created", "user.updated", "user.password_reset"):
        assert expected in actions
    async with db_engine.get_sessionmaker()() as s:
        blob = " ".join(f"{r.detail}" for r in (await s.execute(select(AuditLog))).scalars())
        assert temp not in blob and "zed-password-12" not in blob


async def test_self_service_me_routes_untouched(env):
    c, h, _ = env
    r = await c.post("/api/v1/users/me/change-password", headers=h["operator"],
                     json={"current_password": "wrong-password", "new_password": "new-password-1"})
    assert r.status_code == 400  # llega al handler self-service (no lo captura /{id})
