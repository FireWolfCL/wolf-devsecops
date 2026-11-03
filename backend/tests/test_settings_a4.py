"""A4 — /api/v1/settings/* (ADR-015): hot-reload, secretos, techo de IA, auditoría.

SQLite temporal + create_all; admin/operator en BD con Bearer de `create_access_token`.
Las URLs de fuentes apuntan a 127.0.0.1:9 (conexión rechazada): el agregador degrada
a `unavailable` sin salir a la red.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from wolf_devsecops.api import deps
from wolf_devsecops.auth import security
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models_db.audit import AuditLog
from wolf_devsecops.models_db.config import ProjectRow, ProjectSourceRow, SourceConfigRow
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import config_service
from wolf_devsecops.settings import get_settings

S = "/api/v1/settings"
DEAD = "http://127.0.0.1:9"


@pytest.fixture
async def env(tmp_path, monkeypatch):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/settings_a4.db"
    settings.ai_enabled = True  # techo abierto; los tests lo cierran cuando lo necesitan
    monkeypatch.setenv("A4_PRESENT_TOKEN", "super-secret-value-do-not-leak")
    monkeypatch.delenv("A4_ABSENT_TOKEN", raising=False)
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
        await s.commit()
    await config_service.refresh_config()  # config vacía pero cargada
    deps.reset_aggregator()
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, headers
    config_service.invalidate_config()
    deps.reset_aggregator()
    settings.ai_enabled = False
    await db_engine.dispose_engine()


async def _audit_actions():
    async with db_engine.get_sessionmaker()() as s:
        return [r.action for r in (await s.execute(select(AuditLog))).scalars().all()]


async def test_operator_and_anonymous_cannot_reach_settings(env):
    c, h = env
    for m, u in [("GET", f"{S}/sources"), ("GET", f"{S}/ai"), ("GET", f"{S}/projects"),
                 ("PUT", f"{S}/sources/github"), ("POST", f"{S}/projects")]:
        assert (await c.request(m, u)).status_code == 401
        assert (await c.request(m, u, headers=h["operator"], json={})).status_code == 403


async def test_edit_source_reflects_in_dashboard_without_restart(env):
    c, h = env
    a = h["admin"]
    r = await c.put(f"{S}/sources/github", headers=a,
                    json={"url": DEAD, "token_env": "A4_PRESENT_TOKEN", "enabled": True})
    assert r.status_code == 200 and r.json()["token_present"] is True
    r = await c.post(f"{S}/projects", headers=a, json={
        "key": "p1", "name": "Proyecto 1",
        "sources": {"github": {"repo": "o/r"}}})
    assert r.status_code == 201

    # El dashboard (get_config/aggregator/GET /projects) ve el cambio sin reiniciar.
    meta = (await c.get("/api/meta")).json()
    assert meta["project_count"] == 1 and meta["sources_configured"] == ["github"]
    listed = await c.get("/api/v1/projects", headers=a)
    assert listed.status_code == 200 and [p["key"] for p in listed.json()] == ["p1"]
    assert deps.get_aggregator()._config.sources["github"].url == DEAD

    # Edita la fuente: nueva URL -> el agregador se reconstruye con la config nueva.
    await c.put(f"{S}/sources/github", headers=a,
                json={"url": DEAD + "/v2", "token_env": "A4_PRESENT_TOKEN", "enabled": True})
    assert deps.get_aggregator()._config.sources["github"].url == DEAD + "/v2"

    # Deshabilitarla la saca del AppConfig que ve el dashboard.
    await c.put(f"{S}/sources/github", headers=a,
                json={"url": DEAD, "token_env": "A4_PRESENT_TOKEN", "enabled": False})
    assert (await c.get("/api/meta")).json()["sources_configured"] == []

    # Update y delete de proyecto también refrescan.
    r = await c.put(f"{S}/projects/p1", headers=a, json={"name": "Renombrado", "sources": {}})
    assert r.status_code == 200
    assert deps.get_config().project("p1").name == "Renombrado"
    assert (await c.delete(f"{S}/projects/p1", headers=a)).status_code == 204
    assert (await c.get("/api/meta")).json()["project_count"] == 0
    assert (await c.delete(f"{S}/projects/p1", headers=a)).status_code == 404
    assert (await c.get(f"{S}/projects/p1", headers=a)).status_code == 404
    assert (await c.put(f"{S}/projects/nope", headers=a, json={"name": "x"})).status_code == 404


async def test_project_create_conflict_and_validation(env):
    c, h = env
    a = h["admin"]
    body = {"key": "p2", "name": "P2", "sources": {"sonarqube": {"project_key": "k"}}}
    assert (await c.post(f"{S}/projects", headers=a, json=body)).status_code == 201
    assert (await c.post(f"{S}/projects", headers=a, json=body)).status_code == 409
    bad = {"key": "p3", "name": "P3", "sources": {"nosuch": {}}}
    assert (await c.post(f"{S}/projects", headers=a, json=bad)).status_code == 400
    bad = {"key": "p3", "name": "P3", "sources": {"github": {}}}  # falta repo
    assert (await c.post(f"{S}/projects", headers=a, json=bad)).status_code == 400
    assert (await c.post(f"{S}/projects", headers=a, json={"name": "sin key"})).status_code == 400
    assert (await c.post(f"{S}/projects", headers=a,
                         json={"key": "../x", "name": "n"})).status_code == 422


async def test_project_org_roundtrip_and_multiorg_gate(env):
    """`org` viaja en create/update y el límite de 1 org en Community devuelve 402."""
    c, h = env
    a = h["admin"]
    settings = get_settings()
    # Crear con org explícita: se persiste y vuelve en el GET.
    r = await c.post(f"{S}/projects", headers=a,
                     json={"key": "o1", "name": "O1", "org": "Acme", "sources": {}})
    assert r.status_code == 201
    assert r.json()["org"] == "Acme"
    assert (await c.get(f"{S}/projects/o1", headers=a)).json()["org"] == "Acme"
    # Sin org → default "Wolf" (segunda org, pero enterprise lo permite).
    r = await c.post(f"{S}/projects", headers=a, json={"key": "o2", "name": "O2", "sources": {}})
    assert r.status_code == 201 and r.json()["org"] == "Wolf"
    # Re-asignar org existente nunca penaliza, ni siquiera en Community.
    prev = settings.edition
    settings.edition = "community"
    try:
        r = await c.put(f"{S}/projects/o2", headers=a,
                        json={"name": "O2", "org": "Acme", "sources": {}})
        assert r.status_code == 200 and r.json()["org"] == "Acme"
        # Una TERCERA org nueva en Community → 402 (función Pro).
        r = await c.post(f"{S}/projects", headers=a,
                         json={"key": "o3", "name": "O3", "org": "Globex", "sources": {}})
        assert r.status_code == 402
    finally:
        settings.edition = prev


async def test_token_value_never_leaves_only_token_present(env):
    c, h = env
    a = h["admin"]
    await c.put(f"{S}/sources/github", headers=a,
                json={"url": DEAD, "token_env": "A4_PRESENT_TOKEN"})
    await c.put(f"{S}/sources/sonarqube", headers=a,
                json={"url": DEAD, "token_env": "A4_ABSENT_TOKEN"})
    r = await c.get(f"{S}/sources", headers=a)
    assert r.status_code == 200
    rows = {x["name"]: x for x in r.json()}
    assert rows["github"]["token_present"] is True
    assert rows["sonarqube"]["token_present"] is False
    assert rows["github"]["token_env"] == "A4_PRESENT_TOKEN"
    assert "super-secret-value-do-not-leak" not in r.text
    assert "token" not in {k for x in rows.values() for k in x} - {"token_env", "token_present"}


@pytest.mark.parametrize("literal", [
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "glpat-xxxxxxxxxxxxxxxxxxxx",
    "AKIAIOSFODNN7EXAMPLE",
    "0123456789abcdef0123456789abcdef01234567",
    "my token with spaces",
    "lower_case_name",
])
async def test_literal_secret_rejected_400_and_never_persisted(env, literal):
    c, h = env
    r = await c.put(f"{S}/sources/github", headers=h["admin"],
                    json={"url": DEAD, "token_env": literal})
    assert r.status_code == 400
    assert literal not in r.text
    async with db_engine.get_sessionmaker()() as s:
        assert (await s.execute(select(SourceConfigRow))).scalars().all() == []
    # campo `token` inesperado: rechazado por el esquema (extra=forbid), jamás procesado
    r = await c.put(f"{S}/sources/github", headers=h["admin"],
                    json={"url": DEAD, "token_env": "GITHUB_TOKEN", "token": literal})
    assert r.status_code == 422


async def test_unknown_source_and_bad_url_400(env):
    c, h = env
    r = await c.put(f"{S}/sources/gitlab", headers=h["admin"],
                    json={"url": DEAD, "token_env": "GL_TOKEN"})
    assert r.status_code == 400
    r = await c.put(f"{S}/sources/github", headers=h["admin"],
                    json={"url": "ftp://x", "token_env": "GITHUB_TOKEN"})
    assert r.status_code == 400


async def test_ai_env_ceiling_409_and_effective_toggle(env):
    c, h = env
    a = h["admin"]
    settings = get_settings()
    body = {"provider": "local", "model": "llama3", "base_url": "http://127.0.0.1:11434",
            "token_env": None, "enabled": True}

    # Techo cerrado: no se puede activar (409) y el toggle efectivo queda off.
    settings.ai_enabled = False
    r = await c.put(f"{S}/ai", headers=a, json=body)
    assert r.status_code == 409
    g = (await c.get(f"{S}/ai", headers=a)).json()
    assert g["enabled"] is False and g["env_allows_ai"] is False
    assert g["effective_enabled"] is False
    assert (await c.get("/api/meta")).json()["ai_enabled"] is False
    # Desactivar / guardar con enabled=false sí se permite bajo techo cerrado.
    r = await c.put(f"{S}/ai", headers=a, json={**body, "enabled": False})
    assert r.status_code == 200 and r.json()["effective_enabled"] is False

    # Techo abierto: activar es efectivo -> meta lo refleja y el gate 503 se levanta.
    settings.ai_enabled = True
    r = await c.put(f"{S}/ai", headers=a, json=body)
    assert r.status_code == 200 and r.json()["effective_enabled"] is True
    await c.post(f"{S}/projects", headers=a, json={"key": "p", "name": "P"})
    assert (await c.get("/api/meta")).json()["ai_enabled"] is True
    r = await c.post("/api/v1/projects/p/ai/review", headers=a)
    assert r.status_code != 503  # pasó el gate (falla después por red/datos, no por gate)

    # Cerrar el techo con la fila enabled=true en BD => efectivo off (env gana).
    settings.ai_enabled = False
    assert (await c.get("/api/meta")).json()["ai_enabled"] is False
    assert (await c.post("/api/v1/projects/p/ai/review", headers=a)).status_code == 503

    # Apagar desde el panel con techo abierto => 503 otra vez.
    settings.ai_enabled = True
    await c.put(f"{S}/ai", headers=a, json={**body, "enabled": False})
    assert (await c.post("/api/v1/projects/p/ai/review", headers=a)).status_code == 503

    # Validaciones.
    assert (await c.put(f"{S}/ai", headers=a,
                        json={**body, "provider": "nope"})).status_code == 400
    assert (await c.put(f"{S}/ai", headers=a,
                        json={**body, "token_env": "ghp_" + "x" * 30})).status_code == 400
    assert (await c.put(f"{S}/ai", headers=a, json={**body, "model": ""})).status_code == 400


async def test_audit_events_written_without_secrets(env):
    c, h = env
    a = h["admin"]
    await c.put(f"{S}/sources/github", headers=a,
                json={"url": DEAD, "token_env": "A4_PRESENT_TOKEN"})
    await c.post(f"{S}/projects", headers=a, json={"key": "pa", "name": "PA"})
    await c.put(f"{S}/ai", headers=a, json={"provider": "local", "model": "m", "enabled": True})
    get_settings().ai_enabled = False
    await c.put(f"{S}/ai", headers=a, json={"provider": "local", "model": "m", "enabled": True})
    actions = await _audit_actions()
    for expected in ("config.source_updated", "config.project_updated", "config.ai_toggled"):
        assert expected in actions
    async with db_engine.get_sessionmaker()() as s:
        rows = (await s.execute(select(AuditLog))).scalars().all()
        blob = " ".join(str(r.detail) for r in rows)
        assert "super-secret-value-do-not-leak" not in blob
        denied = [r for r in rows if r.result == "denied"]
        assert len(denied) == 1 and denied[0].action == "config.ai_toggled"
        assert all(r.actor_username == "u-admin" for r in rows)


async def test_delete_project_removes_substructure(env):
    c, h = env
    await c.post(f"{S}/projects", headers=h["admin"],
                 json={"key": "px", "name": "X", "sources": {"wss": {"product": "p"}}})
    await c.delete(f"{S}/projects/px", headers=h["admin"])
    async with db_engine.get_sessionmaker()() as s:
        assert (await s.execute(select(ProjectRow))).scalars().all() == []
        assert (await s.execute(select(ProjectSourceRow))).scalars().all() == []
