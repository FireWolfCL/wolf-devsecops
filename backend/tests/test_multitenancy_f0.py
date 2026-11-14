"""Multi-tenancy F0 (ADR-022): tenants/organizations + tenant_id con tenant default.

Invariante: cero cambio de comportamiento con un solo tenant. Se prueba contra la
migración real (SQLite temporal): seed del default, backfill de filas previas,
reconciliación `org` → `organizations`, server_default en INSERT nuevos vía servicios
existentes, y reversibilidad upgrade → downgrade -1 → upgrade.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.models_db import (
    AuditLog,
    EventRead,
    Organization,
    ProjectRow,
    SourceConfigRow,
    Tenant,
    User,
)
from wolf_devsecops.models_db._shared import DEFAULT_TENANT_ID
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import audit_service, config_service, event_service, user_service
from wolf_devsecops.services import update_service as us
from wolf_devsecops.settings import get_settings

BEFORE = "0010_source_exceptions"


@pytest.fixture
def mig(tmp_path):
    """BD SQLite temporal + config Alembic; restaura settings y engine al terminar."""
    s = get_settings()
    old = (s.database_url, s.environment)
    path = tmp_path / "mt.db"
    s.database_url = f"sqlite+aiosqlite:///{path}"
    s.environment = "development"
    asyncio.run(db_engine.dispose_engine())
    cfg = us.build_alembic_config()
    sync = create_engine(f"sqlite:///{path}")
    yield cfg, sync
    sync.dispose()
    asyncio.run(db_engine.dispose_engine())
    s.database_url, s.environment = old


def _seed_legacy(sync) -> None:
    """Datos 'de antes' (esquema 0010, sin tenant_id)."""
    with sync.begin() as c:
        c.execute(text("INSERT INTO users (id, username, email, role) VALUES "
                       "('u1', 'alice', 'a@x.io', 'admin')"))
        c.execute(text("INSERT INTO source_config (name, url, token_env) VALUES "
                       "('github', 'https://api.github.com', 'GH_TOKEN')"))
        c.execute(text("INSERT INTO projects (key, name, org) VALUES "
                       "('p1', 'P1', 'Wolf'), ('p2', 'P2', 'Wolf'), ('p3', 'P3', 'lsst-it')"))
        c.execute(text("INSERT INTO project_sources (id, project_key, source, settings) VALUES "
                       "('ps1', 'p1', 'github', '{}')"))
        c.execute(text("INSERT INTO event_read (user_id) VALUES ('u1')"))
        c.execute(text("INSERT INTO audit_log (id, action) VALUES ('a1', 'login')"))
        c.execute(text("INSERT INTO ai_settings (id) VALUES (1)"))
        c.execute(text("INSERT INTO system_config (key, value) VALUES ('k', '{}')"))


def test_seed_backfill_and_org_reconcile(mig):
    cfg, sync = mig
    command.upgrade(cfg, BEFORE)
    _seed_legacy(sync)
    command.upgrade(cfg, "head")
    with sync.connect() as c:
        t = c.execute(text("SELECT id, slug, name, edition, seat_limit, status FROM tenants")).all()
        assert len(t) == 1
        assert t[0].id == DEFAULT_TENANT_ID and t[0].slug == "default"
        assert t[0].status == "active" and t[0].seat_limit is None
        assert t[0].edition == get_settings().edition  # edición = flag actual (conftest: enterprise)
        for tbl in ("users", "projects", "project_sources", "source_config", "ai_settings",
                    "audit_log", "event_read"):
            n = c.execute(text(f"SELECT count(*) FROM {tbl} WHERE tenant_id != :t"),
                          {"t": DEFAULT_TENANT_ID}).scalar()
            assert n == 0, tbl
            assert c.execute(text(f"SELECT count(*) FROM {tbl}")).scalar() >= 1, tbl
        # system_config: nullable, las claves previas quedan como "de instancia" (NULL).
        assert c.execute(text("SELECT tenant_id FROM system_config")).scalar() is None
        # source_config: PK surrogate, datos preservados.
        sc = c.execute(text("SELECT id, name, url FROM source_config")).one()
        assert sc.name == "github" and len(sc.id) == 36
        # org (string) -> organizations, enlazado; projects.org intacto.
        orgs = {r.name: r.id for r in c.execute(text(
            "SELECT id, name FROM organizations WHERE tenant_id = :t"), {"t": DEFAULT_TENANT_ID})}
        assert set(orgs) == {"Wolf", "lsst-it"}
        rows = c.execute(text("SELECT key, org, organization_id FROM projects")).all()
        assert {r.key: (r.org, r.organization_id) for r in rows} == {
            "p1": ("Wolf", orgs["Wolf"]), "p2": ("Wolf", orgs["Wolf"]),
            "p3": ("lsst-it", orgs["lsst-it"])}


def test_reversible_roundtrip_keeps_data(mig):
    cfg, sync = mig
    command.upgrade(cfg, BEFORE)
    _seed_legacy(sync)
    command.upgrade(cfg, "0011_multitenancy_f0")  # explícito: "-1" desde head ya sería 0012
    command.downgrade(cfg, "-1")
    with sync.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "tenants" not in tables and "organizations" not in tables
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(users)"))}
        assert "tenant_id" not in cols
        assert c.execute(text("SELECT name FROM source_config")).scalar() == "github"
        assert c.execute(text("SELECT user_id FROM event_read")).scalar() == "u1"
        assert c.execute(text("SELECT username FROM users")).scalar() == "alice"
        assert c.execute(text("SELECT count(*) FROM projects")).scalar() == 3
    command.upgrade(cfg, "head")
    with sync.connect() as c:
        assert c.execute(text("SELECT count(*) FROM tenants")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM organizations")).scalar() == 2


def test_org_slug_collision_and_empty_db(mig):
    cfg, sync = mig
    command.upgrade(cfg, BEFORE)
    with sync.begin() as c:
        c.execute(text("INSERT INTO projects (key, name, org) VALUES "
                       "('a', 'A', 'Wolf'), ('b', 'B', 'wolf')"))
    command.upgrade(cfg, "head")
    with sync.connect() as c:
        slugs = sorted(r[0] for r in c.execute(text("SELECT slug FROM organizations")))
        assert slugs == ["wolf", "wolf-2"]  # mismo slug normalizado -> sufijo, sin romper UNIQUE


async def test_new_inserts_via_services_land_in_default_tenant(mig):
    cfg, _ = mig
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with db_engine.get_sessionmaker()() as s:
        u = await user_service.create_user(s, username="bob", password="bob-password-12",
                                           role=Role.viewer, email="b@x.io")
        await audit_service.record(s, action="t.test", actor_user_id=u.id)
        await config_service.upsert_source(s, "github", "https://api.github.com", "GH_TOKEN")
        await event_service.mark_read(s, u.id)
        await event_service.mark_read(s, u.id)  # segunda vez: update, no duplica
        s.add(ProjectRow(key="px", name="PX"))
        await s.commit()
        # lookup por nombre sobre PK surrogate (ex session.get(name))
        assert (await config_service.set_source_token(s, "github", "tok-123")).token_cipher
    async with db_engine.get_sessionmaker()() as s:
        for model in (User, AuditLog, SourceConfigRow, EventRead, ProjectRow):
            rows = (await s.execute(model.__table__.select())).all()
            assert rows, model
            assert {r.tenant_id for r in rows} == {DEFAULT_TENANT_ID}, model
        assert (await s.get(Tenant, DEFAULT_TENANT_ID)).slug == "default"
        assert (await s.execute(Organization.__table__.select())).all() == []
        assert (await s.execute(EventRead.__table__.select())).all().__len__() == 1


async def test_unique_per_tenant_not_global(mig):
    """users UNIQUE(tenant_id, username): mismo username en OTRO tenant es válido; en el
    mismo tenant, no (equivalente al unique global de hoy con un solo tenant)."""
    from sqlalchemy.exc import IntegrityError

    cfg, _ = mig
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with db_engine.get_sessionmaker()() as s:
        s.add(Tenant(id="t2", slug="acme", name="Acme"))
        await s.commit()
        s.add(User(username="dup", hashed_password="x"))
        s.add(User(username="dup", hashed_password="x", tenant_id="t2"))
        await s.commit()
        s.add(User(username="dup", hashed_password="x"))
        with pytest.raises(IntegrityError):
            await s.commit()
