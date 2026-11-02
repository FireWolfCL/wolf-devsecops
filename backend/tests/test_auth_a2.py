"""A2 — identidad + login/MFA (ADR-013/014, §8/§10).

Cubre el flujo end-to-end por HTTP (login, mfa/verify, me, self-service MFA) y la
lógica de dominio que A2 deja lista para A3/A4 (guard anti-autobloqueo, bootstrap).

Patrón de BD: SQLite temporal + Base.metadata.create_all (igual que
test_persistence_a0.py); NO corre Alembic aquí (eso es gate de CI). La app se
ejercita con httpx.ASGITransport, que NO dispara el lifespan — así el bootstrap de
config YAML→BD no interfiere con estos tests de auth.
"""

from __future__ import annotations

import httpx
import pyotp
import pytest

from wolf_devsecops.cli import create_admin as cli_create_admin
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import auth_service, user_service
from wolf_devsecops.settings import get_settings


@pytest.fixture
async def app_env(tmp_path):
    """BD SQLite temporal con el esquema creado + app FastAPI lista para ASGI."""
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/auth_a2.db"
    settings.login_max_attempts = 5
    settings.login_lockout_minutes = 15
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_app()
    await db_engine.dispose_engine()


@pytest.fixture
async def client(app_env):
    transport = httpx.ASGITransport(app=app_env)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _session():
    return db_engine.get_sessionmaker()()


async def _make_user(
    *, username="alice", password="s3cret-password", role=Role.viewer, is_active=True, email=None
):
    async with _session() as s:
        user = await user_service.create_user(
            s, username=username, password=password, role=role, email=email
        )
        if not is_active:
            user.is_active = False
            await s.commit()
        return user.id


async def _login(client, username, password):
    return await client.post(
        "/api/v1/auth/login", data={"username": username, "password": password}
    )


# ─── Login: OK / password mala / inactivo ─────────────────────────────────────
async def test_login_ok_sin_mfa(client):
    await _make_user(username="bob", password="correct-horse-battery")
    resp = await _login(client, "bob", "correct-horse-battery")
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["mfa_required"] is False
    assert body["must_enroll_mfa"] is False  # bob es viewer


async def test_login_password_mala_401(client):
    await _make_user(username="bob", password="right-one")
    resp = await _login(client, "bob", "wrong-one")
    assert resp.status_code == 401
    assert "access_token" not in resp.json() or resp.json().get("access_token") is None


async def test_login_usuario_inexistente_401_uniforme(client):
    # Mismo 401 que password mala: sin oráculo de enumeración.
    resp = await _login(client, "ghost", "whatever")
    assert resp.status_code == 401


async def test_login_usuario_inactivo_401(client):
    await _make_user(username="frozen", password="pw-frozen-123", is_active=False)
    resp = await _login(client, "frozen", "pw-frozen-123")
    assert resp.status_code == 401


# ─── Lockout → 423 ────────────────────────────────────────────────────────────
async def test_lockout_tras_umbral_devuelve_423(client):
    await _make_user(username="locky", password="good-pass-123")
    maxn = get_settings().login_max_attempts
    for _ in range(maxn):
        r = await _login(client, "locky", "bad")
        assert r.status_code == 401
    # Ya bloqueada: incluso con la password correcta responde 423, no 200.
    r = await _login(client, "locky", "good-pass-123")
    assert r.status_code == 423


# ─── MFA: mfa_required + verify con TOTP y con recovery code ───────────────────
async def _enable_mfa(user_id) -> tuple[str, list[str]]:
    """Activa MFA para un usuario y devuelve (secret_totp, recovery_codes)."""
    async with _session() as s:
        user = await s.get(user_service.User, user_id)
        secret, _uri, _svg = await user_service.start_mfa_setup(s, user)
        code = pyotp.TOTP(secret).now()
        recovery = await user_service.enable_mfa(s, user, code)
        return secret, recovery


async def test_login_con_mfa_pide_segundo_factor(client):
    uid = await _make_user(username="mfauser", password="mfa-pass-123")
    await _enable_mfa(uid)
    resp = await _login(client, "mfauser", "mfa-pass-123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mfa_required"] is True
    assert body["mfa_token"]
    assert body["access_token"] is None  # aún no hay sesión


async def test_mfa_verify_con_totp(client):
    uid = await _make_user(username="mfauser", password="mfa-pass-123")
    secret, _ = await _enable_mfa(uid)
    mfa_token = (await _login(client, "mfauser", "mfa-pass-123")).json()["mfa_token"]

    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": code})
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_mfa_verify_con_recovery_code_un_solo_uso(client):
    uid = await _make_user(username="mfauser", password="mfa-pass-123")
    _secret, recovery = await _enable_mfa(uid)
    one_code = recovery[0]

    # Primer uso del recovery code: OK.
    mfa_token = (await _login(client, "mfauser", "mfa-pass-123")).json()["mfa_token"]
    r1 = await client.post("/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": one_code})
    assert r1.status_code == 200

    # Segundo uso del MISMO recovery code: ya consumido → 401.
    mfa_token2 = (await _login(client, "mfauser", "mfa-pass-123")).json()["mfa_token"]
    r2 = await client.post("/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token2, "code": one_code})
    assert r2.status_code == 401


# ─── /auth/me e invalidación por token_version ─────────────────────────────────
async def test_me_devuelve_identidad(client):
    await _make_user(username="meuser", password="me-pass-123", email="me@x.io")
    token = (await _login(client, "meuser", "me-pass-123")).json()["access_token"]
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "meuser"
    assert body["role"] == "viewer"
    assert body["email"] == "me@x.io"


async def test_bump_token_version_invalida_token_viejo(client):
    uid = await _make_user(username="revoke", password="revoke-pass-123")
    token = (await _login(client, "revoke", "revoke-pass-123")).json()["access_token"]
    ok = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200

    async with _session() as s:
        user = await s.get(user_service.User, uid)
        await auth_service.bump_token_version(s, user)

    stale = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert stale.status_code == 401


async def test_me_sin_token_401(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401


# ─── MFA admin obligatorio (flag must_enroll_mfa) ──────────────────────────────
async def test_admin_sin_mfa_entra_pero_marca_must_enroll(client):
    await _make_user(username="root", password="root-pass-123", role=Role.admin)
    resp = await _login(client, "root", "root-pass-123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]  # el admin SÍ puede entrar (verificación dura es A6)
    assert body["must_enroll_mfa"] is True

    token = body["access_token"]
    me = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})).json()
    assert me["must_enroll_mfa"] is True


# ─── Enrolamiento MFA self-service: setup → enable → disable ───────────────────
async def test_enrolamiento_mfa_self_service(client):
    await _make_user(username="enrol", password="enrol-pass-123")
    token = (await _login(client, "enrol", "enrol-pass-123")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    setup = await client.post("/api/v1/users/me/mfa/setup", headers=headers)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert "<svg" in setup.json()["qr_code_svg"]

    code = pyotp.TOTP(secret).now()
    enable = await client.post("/api/v1/users/me/mfa/enable", json={"code": code}, headers=headers)
    assert enable.status_code == 200
    assert len(enable.json()["recovery_codes"]) == 10

    # Disable exige password: una mala → 401, la correcta → 204.
    bad = await client.post("/api/v1/users/me/mfa/disable", json={"password": "nope"}, headers=headers)
    assert bad.status_code == 401
    good = await client.post(
        "/api/v1/users/me/mfa/disable", json={"password": "enrol-pass-123"}, headers=headers
    )
    assert good.status_code == 204


async def test_change_password_self_service(client):
    await _make_user(username="chg", password="old-pass-123")
    token = (await _login(client, "chg", "old-pass-123")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    bad = await client.post(
        "/api/v1/users/me/change-password",
        json={"current_password": "wrong", "new_password": "new-pass-456"},
        headers=headers,
    )
    assert bad.status_code == 400

    ok = await client.post(
        "/api/v1/users/me/change-password",
        json={"current_password": "old-pass-123", "new_password": "new-pass-456"},
        headers=headers,
    )
    assert ok.status_code == 204
    # La nueva password funciona para un login fresco.
    assert (await _login(client, "chg", "new-pass-456")).status_code == 200


# ─── Guard anti-autobloqueo (lógica de user_service, cableada por A4) ──────────
async def test_guard_no_te_quites_tu_propio_rol(app_env):
    uid = await _make_user(username="adm", password="adm-pass-123", role=Role.admin)
    async with _session() as s:
        with pytest.raises(user_service.SelfLockoutError):
            await user_service.update_user(s, uid, acting_user_id=uid, role=Role.viewer)
        with pytest.raises(user_service.SelfLockoutError):
            await user_service.update_user(s, uid, acting_user_id=uid, is_active=False)


async def test_update_user_valido_funciona(app_env):
    uid = await _make_user(username="u1", password="u1-pass-123", role=Role.viewer)
    other = await _make_user(username="u2", password="u2-pass-123", role=Role.admin)
    async with _session() as s:
        updated = await user_service.update_user(s, uid, acting_user_id=other, role=Role.operator)
        assert updated is not None and updated.role == Role.operator


# ─── Bootstrap del primer admin (create-admin) ─────────────────────────────────
async def test_create_admin_bootstrap(app_env):
    async with _session() as s:
        user = await cli_create_admin.bootstrap_admin(
            s, username="firstadmin", password="bootstrap-pass-123"
        )
        assert user.role == Role.admin
        assert user.must_change_password is True
        assert await user_service.count_users(s) == 1


async def test_create_admin_rechaza_segundo_sin_flag(app_env):
    async with _session() as s:
        await cli_create_admin.bootstrap_admin(s, username="a1", password="pass-a1-123")
        with pytest.raises(RuntimeError):
            await cli_create_admin.bootstrap_admin(s, username="a2", password="pass-a2-123")
        # Con allow_existing sí crea un segundo.
        u2 = await cli_create_admin.bootstrap_admin(
            s, username="a2", password="pass-a2-123", allow_existing=True
        )
        assert u2.username == "a2"


async def test_bootstrap_admin_puede_loguear(client):
    async with _session() as s:
        await cli_create_admin.bootstrap_admin(s, username="theadmin", password="the-pass-123")
    resp = await _login(client, "theadmin", "the-pass-123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["must_change_password"] is True  # forzado por el bootstrap
    assert body["must_enroll_mfa"] is True  # admin sin MFA
