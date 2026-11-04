"""A5 — Centro de Update v1 (ADR-016): versión, degradación, migraciones, pipeline."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from alembic import command
from sqlalchemy import select

from wolf_devsecops import __version__
from wolf_devsecops.auth import security
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.main import create_app
from wolf_devsecops.models_db.audit import AuditLog
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import migration_reversibility as mr
from wolf_devsecops.services import update_service as us
from wolf_devsecops.services import user_service
from wolf_devsecops.settings import get_settings

# Última migración de alembic (actualizar al agregar una nueva revisión).
HEAD = "0009_posture_ci_gate"
_MIGRATION_COUNT = 9  # nº total de revisiones (pendientes en una BD virgen)


def _bearer(user):
    tok = security.create_access_token(
        subject=user.id, role=user.role.value, token_version=user.token_version or 0)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
async def env(tmp_path):
    s = get_settings()
    old = (s.database_url, s.environment, s.update_release_repo)
    s.database_url = f"sqlite+aiosqlite:///{tmp_path}/upd.db"
    s.environment = "development"
    s.update_release_repo = ""
    await db_engine.dispose_engine()
    # Esquema vía la propia migración (realista): deja alembic_version en HEAD.
    await asyncio.to_thread(command.upgrade, us.build_alembic_config(), "head")
    async with db_engine.get_sessionmaker()() as sess:
        admin = await user_service.create_user(sess, username="admin", password="admin-password-1", role=Role.admin)
        admin.mfa_enabled = True  # MFA obligatorio para admin (T-AUTH-04)
        await sess.commit()
        op = await user_service.create_user(sess, username="op", password="op-password-123", role=Role.operator)
        hdr = {"admin": _bearer(admin), "operator": _bearer(op)}
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, hdr, s
    await db_engine.dispose_engine()
    s.database_url, s.environment, s.update_release_repo = old


# ─── versión ─────────────────────────────────────────────────────────────────
def test_is_newer_tristate():
    assert us._is_newer("v1.10.0", "1.9.0") is True  # numérico, no string
    assert us._is_newer("0.0.9", "0.1.0") is False
    assert us._is_newer("1.2.3", "v1.2.3") is False
    assert us._is_newer("1.2.4-rc1", "1.2.3") is True  # sufijo ignorado
    for bad in ("", "latest", "1.2", "1.2.3; rm -rf /", None):
        assert us._is_newer(bad, "1.0.0") is None
        assert us._is_newer("1.0.0", bad) is None


def test_latest_release_filters_hostile_and_unpublished():
    rel = [
        {"tag_name": "v9.9.9", "draft": True},
        {"tag_name": "v8.0.0", "prerelease": True},
        {"tag_name": "v7.0.0-rc1"},
        {"tag_name": "v1.2.3; rm -rf /"},
        {"tag_name": 5},
        "garbage",
        {"tag_name": "v1.9.0", "name": "n"},
        {"tag_name": "v1.10.0", "name": "ten", "body": "x" * 9000, "html_url": "u"},
    ]
    out = us._latest_release(rel)
    assert out["available"] and out["version"] == "1.10.0" and out["tag"] == "v1.10.0"
    assert len(out["notes"]) == 4000
    assert us._latest_release([{"tag_name": "v1.0.0", "draft": True}]) == {
        "available": False, "reason": "no_matching_release"}


async def test_degradation_no_repo_and_status_still_works(env):
    c, h, _ = env
    r = await c.get("/api/v1/updates/status", headers=h["admin"])
    assert r.status_code == 200
    b = r.json()
    assert b["server"]["latest"] == {**b["server"]["latest"], "available": False, "reason": "no_repo"}
    assert b["server"]["update_available"] is None  # nunca "al día" sin poder leer
    assert b["server"]["current_version"] == __version__
    assert b["update_command"] == get_settings().update_command and b["environment"] == "development"
    # Guard anti-fuga de credenciales: se excluye el bloque `migrations`, cuyos slugs
    # (p. ej. "0006_source_token_cipher") contienen "token" de forma legítima.
    non_migration = json.dumps({k: v for k, v in b.items() if k != "migrations"}).lower()
    assert "token" not in non_migration


@pytest.mark.parametrize("mode,reason", [("net", "network_error"), (404, "http_404"),
                                         ("notjson", "invalid_response"), ("dict", "invalid_response")])
async def test_fetch_never_raises(env, monkeypatch, mode, reason):
    _, _, s = env
    s.update_release_repo = "acme/app"

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, *a, **k):
            if mode == "net":
                raise httpx.ConnectError("boom")
            if mode == "notjson":
                return httpx.Response(200, content=b"<html>")
            if mode == "dict":
                return httpx.Response(200, json={"a": 1})
            return httpx.Response(mode, json=[])
    monkeypatch.setattr(us.httpx, "AsyncClient", FakeClient)
    assert (await us._fetch_releases()) == (None, reason)
    assert (await us.check_latest()) == {"available": False, "reason": reason}


async def test_status_update_available_and_invalid_repo(env, monkeypatch):
    _, _, s = env
    s.update_release_repo = "../evil"
    assert (await us._fetch_releases()) == (None, "invalid_repo")

    async def fake():
        return [{"tag_name": "v9.0.0", "name": "big"}, {"tag_name": "v0.0.1"}], None
    monkeypatch.setattr(us, "_fetch_releases", fake)
    st = await us.status()
    assert st["server"]["update_available"] is True and st["server"]["latest"]["tag"] == "v9.0.0"


# ─── RBAC ────────────────────────────────────────────────────────────────────
async def test_rbac(env):
    c, h, _ = env
    assert (await c.get("/api/v1/updates/version")).status_code == 401
    r = await c.get("/api/v1/updates/version", headers=h["operator"])
    assert r.status_code == 200 and r.json() == {"current_version": __version__}
    for m, u in (("GET", "/api/v1/updates/status"), ("POST", "/api/v1/updates/migrate")):
        assert (await c.request(m, u, json={})).status_code == 401
        assert (await c.request(m, u, headers=h["operator"], json={})).status_code == 403


# ─── reversibilidad ──────────────────────────────────────────────────────────
def test_has_real_downgrade(tmp_path):
    def w(name, body):
        p = tmp_path / name
        p.write_text(body)
        return p
    assert not mr.has_real_downgrade(w("a.py", "def downgrade():\n    pass\n"))
    assert not mr.has_real_downgrade(w("b.py", 'def downgrade():\n    """doc"""\n'))
    assert not mr.has_real_downgrade(w("c.py", 'def downgrade():\n    """doc"""\n    pass\n'))
    assert not mr.has_real_downgrade(w("d.py", "def upgrade():\n    pass\n"))
    assert not mr.has_real_downgrade(w("e.py", "def downgrade(:\n"))
    assert not mr.has_real_downgrade(tmp_path / "nope.py")
    assert mr.has_real_downgrade(w("f.py", "def downgrade():\n    op.drop_table('t')\n"))
    assert mr.has_real_downgrade(__import__("pathlib").Path("migrations/versions/0001_initial.py"))
    with pytest.raises(mr.IrreversibleMigrationError, match="x1"):
        mr.assert_reversible([mr.RevisionCheck("ok", True), mr.RevisionCheck("x1", False)])
    mr.assert_reversible([])


async def test_empty_downgrade_rejected_400_and_db_untouched(env, monkeypatch):
    c, h, _ = env
    called = []
    monkeypatch.setattr(us.command, "upgrade", lambda *a, **k: called.append(1))
    monkeypatch.setattr(us, "pending_reversibility",
                        lambda cur: [mr.RevisionCheck("0002_dummy", False)])
    r = await c.post("/api/v1/updates/migrate", headers=h["admin"], json={"confirm_backup": True})
    assert r.status_code == 400 and "0002_dummy" in r.json()["detail"]
    assert called == []


# ─── gate de backup ──────────────────────────────────────────────────────────
async def test_backup_gate_in_production(env, monkeypatch):
    c, h, s = env
    s.environment = "production"
    called = []
    monkeypatch.setattr(us.command, "upgrade", lambda *a, **k: called.append(1))
    for body in ({}, {"confirm_backup": False}):
        r = await c.post("/api/v1/updates/migrate", headers=h["admin"], json=body)
        assert r.status_code == 400 and "respaldo" in r.json()["detail"]
    assert called == []


# ─── estado y camino feliz ───────────────────────────────────────────────────
async def test_migration_status_pending_then_apply_on_fresh_db(env, tmp_path):
    s = get_settings()
    s.database_url = f"sqlite+aiosqlite:///{tmp_path}/fresh.db"  # BD virgen
    await db_engine.dispose_engine()
    assert await us.migration_status() == {
        "current": None, "head": HEAD, "pending": _MIGRATION_COUNT, "up_to_date": False}
    out = await us.apply_migrations(environment="development", confirm_backup=False)
    assert out == {"current": HEAD, "head": HEAD, "pending": 0, "up_to_date": True}
    # production CON confirmación pasa el gate
    out = await us.apply_migrations(environment="production", confirm_backup=True)
    assert out["up_to_date"] is True


async def test_http_migrate_idempotent_and_audited(env):
    c, h, _ = env
    st = (await c.get("/api/v1/updates/status", headers=h["admin"])).json()["migrations"]
    assert st == {"current": HEAD, "head": HEAD, "pending": 0, "up_to_date": True}
    r = await c.post("/api/v1/updates/migrate", headers=h["admin"], json={})
    assert r.status_code == 200, r.text
    assert r.json()["migrations"]["up_to_date"] is True
    async with db_engine.get_sessionmaker()() as sess:
        rows = (await sess.execute(select(AuditLog).where(
            AuditLog.action == "update.migrations_applied"))).scalars().all()
    assert rows and rows[0].detail == {"current": HEAD, "head": HEAD}
    assert rows[0].actor_username == "admin" and rows[0].result == "success"


async def test_failed_upgrade_auto_rollback_and_500(env, monkeypatch):
    c, h, _ = env
    calls = []
    monkeypatch.setattr(us.command, "upgrade", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom")))
    monkeypatch.setattr(us.command, "downgrade", lambda cfg, t: calls.append(t))
    r = await c.post("/api/v1/updates/migrate", headers=h["admin"], json={})
    assert r.status_code == 500 and "revirtió" in r.json()["detail"] and calls == [HEAD]

    def bad_down(cfg, t):
        raise RuntimeError("rb-fail")
    monkeypatch.setattr(us.command, "downgrade", bad_down)
    r = await c.post("/api/v1/updates/migrate", headers=h["admin"], json={})
    # T-SEC-01: el 500 NO filtra el texto crudo de la causa (kaboom/rb-fail van al log);
    # el cliente ve un mensaje controlado que pide intervención manual.
    detail = r.json()["detail"]
    assert r.status_code == 500 and "intervención manual" in detail
    assert "kaboom" not in detail and "rb-fail" not in detail
