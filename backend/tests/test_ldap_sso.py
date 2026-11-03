"""SSO/LDAP (E2, Pro) — agregación de failover (unit) + login federado por la app.

Sin directorio real: la lógica de 3 estados se prueba monkeypatcheando `_try_host`
(puro, sin ldap3), y el camino de login monkeypatchea `ldap_service.authenticate_ldap`
para devolver cada resultado y verificar 200 / 401 / 503, break-glass local y gate Pro.
"""

from __future__ import annotations

import httpx
import pytest

from wolf_devsecops.api import deps
from wolf_devsecops.auth import security
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models_db.config import LDAPConfigRow
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import config_service, ldap_service
from wolf_devsecops.settings import get_settings

R = ldap_service.LdapAuthResult
CFG = ldap_service.LdapConfig(hosts=["ldaps://a:636", "ldaps://b:636"], base_dn="dc=x")


# ─── Agregación de failover (unit, sin ldap3) ────────────────────────────────
def test_authenticated_shortcircuits(monkeypatch):
    calls: list[str] = []

    def fake(host, cfg, u, p):
        calls.append(host)
        return R.AUTHENTICATED

    monkeypatch.setattr(ldap_service, "_try_host", fake)
    assert ldap_service.authenticate_ldap(CFG, "u", "pw") is R.AUTHENTICATED
    assert calls == ["ldaps://a:636"]  # cortó en la primera réplica que validó


def test_replication_lag_retries_other_replica(monkeypatch):
    seq = iter([R.INVALID_CREDENTIALS, R.AUTHENTICATED])  # A desactualizada, B al día
    monkeypatch.setattr(ldap_service, "_try_host", lambda h, c, u, p: next(seq))
    assert ldap_service.authenticate_ldap(CFG, "u", "pw") is R.AUTHENTICATED


def test_all_invalid_rejects(monkeypatch):
    monkeypatch.setattr(ldap_service, "_try_host", lambda h, c, u, p: R.INVALID_CREDENTIALS)
    assert ldap_service.authenticate_ldap(CFG, "u", "pw") is R.INVALID_CREDENTIALS


def test_all_unreachable_is_unreachable(monkeypatch):
    monkeypatch.setattr(ldap_service, "_try_host", lambda h, c, u, p: R.UNREACHABLE)
    assert ldap_service.authenticate_ldap(CFG, "u", "pw") is R.UNREACHABLE


def test_empty_password_rejected_without_bind(monkeypatch):
    monkeypatch.setattr(ldap_service, "_try_host", lambda h, c, u, p: R.AUTHENTICATED)
    assert ldap_service.authenticate_ldap(CFG, "u", "") is R.INVALID_CREDENTIALS


def test_no_hosts_is_unreachable():
    cfg = ldap_service.LdapConfig(hosts=[], base_dn="dc=x")
    assert ldap_service.authenticate_ldap(cfg, "u", "pw") is R.UNREACHABLE


# ─── Login federado por la app ───────────────────────────────────────────────
@pytest.fixture
async def app_env(tmp_path):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/ldap.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        # Usuario LDAP (viewer, sin MFA ni password local) y config LDAP habilitada.
        s.add(User(username="ldapuser", role=Role.viewer, is_active=True,
                   auth_source="ldap", hashed_password=None, mfa_enabled=False, token_version=0))
        s.add(LDAPConfigRow(id=1, enabled=True, hosts=["ldaps://dc:636"], base_dn="dc=corp",
                            user_dn_template="uid={username},dc=corp"))
        # Admin LOCAL de break-glass (con password) para probar que su login no toca LDAP.
        from wolf_devsecops.auth.security import hash_password
        s.add(User(username="localadmin", role=Role.viewer, is_active=True, auth_source="local",
                   hashed_password=hash_password("local-pass-123"), mfa_enabled=False,
                   token_version=0))
        await s.commit()
    await config_service.refresh_config()
    deps.reset_aggregator()
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c
    config_service.invalidate_config()
    deps.reset_aggregator()
    await db_engine.dispose_engine()


async def _login(c, username, password):
    return await c.post("/api/v1/auth/login", data={"username": username, "password": password})


async def test_ldap_login_authenticated_returns_token(app_env, monkeypatch):
    monkeypatch.setattr(ldap_service, "authenticate_ldap", lambda cfg, u, p: R.AUTHENTICATED)
    r = await _login(app_env, "ldapuser", "whatever")
    assert r.status_code == 200 and r.json().get("access_token")


async def test_ldap_login_invalid_is_401(app_env, monkeypatch):
    monkeypatch.setattr(ldap_service, "authenticate_ldap", lambda cfg, u, p: R.INVALID_CREDENTIALS)
    assert (await _login(app_env, "ldapuser", "bad")).status_code == 401


async def test_ldap_login_directory_down_is_503_not_lockout(app_env, monkeypatch):
    monkeypatch.setattr(ldap_service, "authenticate_ldap", lambda cfg, u, p: R.UNREACHABLE)
    assert (await _login(app_env, "ldapuser", "whatever")).status_code == 503


async def test_breakglass_local_admin_unaffected_by_directory(app_env, monkeypatch):
    # Aunque el directorio esté caído, el admin local entra (su bind es local).
    monkeypatch.setattr(ldap_service, "authenticate_ldap", lambda cfg, u, p: R.UNREACHABLE)
    r = await _login(app_env, "localadmin", "local-pass-123")
    assert r.status_code == 200 and r.json().get("access_token")


# ─── Hardening de seguridad (revisiones crypto + threat) ─────────────────────
def test_tls_object_failsafe_validates_on_unknown_value():
    import ssl
    # Valor inesperado → CERT_REQUIRED (fail-safe), no CERT_NONE.
    cfg = ldap_service.LdapConfig(hosts=["ldaps://x"], base_dn="d", tls_verify="garbage")
    assert ldap_service._tls_object(cfg).validate == ssl.CERT_REQUIRED
    relaxed = ldap_service.LdapConfig(hosts=["ldaps://x"], base_dn="d", tls_verify="relaxed")
    assert ldap_service._tls_object(relaxed).validate == ssl.CERT_NONE


def test_service_bind_without_password_is_unreachable(monkeypatch):
    # search+bind sin bind password = unauthenticated bind → UNREACHABLE, sin bindear.
    cfg = ldap_service.LdapConfig(hosts=["ldaps://dc"], base_dn="dc=x",
                                  bind_dn="cn=svc,dc=x", bind_password=None)
    monkeypatch.setattr(ldap_service, "_build_server", lambda h, c: object())

    def _must_not_bind(*a, **k):
        raise AssertionError("no debe bindear sin bind password")

    monkeypatch.setattr(ldap_service, "_bind", _must_not_bind)
    assert ldap_service.authenticate_ldap(cfg, "u", "pw") is R.UNREACHABLE


@pytest.fixture
async def ldap_db(tmp_path):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/ldapcfg.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await config_service.refresh_config()
    async with db_engine.get_sessionmaker()() as s:
        yield s
    config_service.invalidate_config()
    await db_engine.dispose_engine()


async def test_enable_requires_encrypted_transport(ldap_db):
    base = dict(hosts=["ldaps://dc"], base_dn="dc=x", user_dn_template="uid={username},dc=x")
    # Activar sin cifrado (ni LDAPS ni StartTLS) → rechazado.
    with pytest.raises(config_service.ConfigWriteError):
        await config_service.set_ldap_settings(
            ldap_db, enabled=True, use_ssl=False, start_tls=False, **base
        )
    # Con LDAPS → aceptado.
    row = await config_service.set_ldap_settings(ldap_db, enabled=True, use_ssl=True, **base)
    assert row.enabled and row.use_ssl


async def test_ldap_config_write_requires_pro(app_env):
    # En Community, PUT /settings/ldap responde 402 (require_feature(FEATURE_SSO)).
    settings = get_settings()
    prev = settings.edition
    settings.edition = "community"
    try:
        async with db_engine.get_sessionmaker()() as s:
            admin = User(username="adm", role=Role.admin, is_active=True, auth_source="local",
                         mfa_enabled=True, token_version=0)
            s.add(admin)
            await s.flush()
            tok = security.create_access_token(subject=admin.id, role="admin", token_version=0)
            await s.commit()
        r = await app_env.put("/api/v1/settings/ldap",
                              headers={"Authorization": f"Bearer {tok}"},
                              json={"enabled": False, "hosts": [], "base_dn": ""})
        assert r.status_code == 402
    finally:
        settings.edition = prev
