"""0011 — multi-tenancy F0 (ADR-022): tablas `tenants`/`organizations` + `tenant_id`.

Fontanería de datos SIN cambio de comportamiento visible (un solo tenant):

1. `tenants` (plano de control, sin tenant_id) y SIEMBRA del tenant default con UUID fijo
   `00000000-0000-0000-0000-000000000001`, slug `default`, edición = flag `WOLF_EDITION`.
2. `organizations` (scoped, UNIQUE(tenant_id, slug)).
3. `tenant_id` en users, projects, project_sources, source_config, ai_settings, ldap_config,
   audit_log, project_events, event_read, posture_snapshots, source_exceptions:
   NOT NULL + FK→tenants ON DELETE CASCADE + `server_default` = tenant default. Se añade en
   un solo paso (columna con server_default NOT NULL): el motor rellena las filas existentes
   con el default, equivalente a "add nullable → backfill → NOT NULL" pero atómico.
   `system_config.tenant_id` es NULLABLE y sin default (NULL = clave de instancia).
4. Unicidad/PK: users → UNIQUE(tenant_id, username|email); source_config → PK uuid +
   UNIQUE(tenant_id, name); ai_settings/ldap_config → UNIQUE(tenant_id); event_read → PK
   (tenant_id, user_id).
5. `projects.organization_id` FK→organizations; reconcilia `SELECT DISTINCT projects.org`
   → una organización por valor bajo el tenant default y enlaza los proyectos. `projects.org`
   NO se borra (queda denormalizado una release).

SQLite-safe: `batch_alter_table` / reconstrucción de tabla (los cambios de PK se hacen creando
la tabla nueva, copiando y renombrando — portable a Postgres sin colisión de nombres de PK).

`downgrade()` es REAL (ADR-016): revierte en orden inverso. Con un solo tenant los datos
quedan intactos; con >1 tenant el downgrade de users/source_config puede violar las
unicidades globales antiguas (por diseño: F0 no habilita N>1, eso es F3).

Revision ID: 0011_multitenancy_f0
Revises: 0010_source_exceptions
Create Date: 2026-11-05
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_multitenancy_f0"
down_revision: str | None = "0010_source_exceptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Debe coincidir con models_db._shared.DEFAULT_TENANT_ID (se duplica a propósito: una
# migración es una foto inmutable y no debe depender de código que puede cambiar).
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Tablas que ganan tenant_id NOT NULL con default (source_config y event_read se
# reconstruyen aparte porque cambia su PK).
_SIMPLE_TABLES = (
    "users",
    "projects",
    "project_sources",
    "ai_settings",
    "ldap_config",
    "audit_log",
    "project_events",
    "posture_snapshots",
    "source_exceptions",
)


def _fk_name(table: str) -> str:
    return f"fk_{table}_tenant_id_tenants"


def _tenant_col(table: str, *, fk: bool = True) -> sa.Column:
    """`tenant_id` NOT NULL con server_default. `fk=False` en batch_alter_table (la FK se
    crea aparte con nombre explícito vía create_foreign_key)."""
    args = (
        [sa.ForeignKey("tenants.id", ondelete="CASCADE", name=_fk_name(table))] if fk else []
    )
    return sa.Column(
        "tenant_id",
        sa.String(length=36),
        *args,
        nullable=False,
        server_default=DEFAULT_TENANT_ID,
    )


def _meta_with_tenants() -> sa.MetaData:
    """MetaData con un stub de `tenants` para que las FK de las tablas nuevas resuelvan."""
    meta = sa.MetaData()
    sa.Table("tenants", meta, sa.Column("id", sa.String(length=36), primary_key=True))
    return meta


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return (s or "org")[:100]


def _default_edition() -> str:
    """Edición acorde al flag actual (WOLF_EDITION). Falla a community, como edition.py."""
    try:
        from wolf_devsecops.edition import normalize
        from wolf_devsecops.settings import get_settings

        return normalize(get_settings().edition)
    except Exception:  # pragma: no cover - la migración nunca debe caerse por esto
        return "community"


def _rebuild_source_config(conn) -> None:
    old = sa.Table("source_config", sa.MetaData(), autoload_with=conn)
    new = sa.Table(
        "source_config_new",
        _meta_with_tenants(),
        sa.Column("id", sa.String(length=36)),
        _tenant_col("source_config"),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("url", sa.String(length=400), nullable=False),
        sa.Column("token_env", sa.String(length=120), nullable=False),
        sa.Column("token_cipher", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_source_config"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_source_config_tenant_name"),
    )
    new.create(conn)
    rows = [dict(r._mapping) for r in conn.execute(sa.select(old))]
    for r in rows:
        r["id"] = str(uuid.uuid4())
        r["tenant_id"] = DEFAULT_TENANT_ID
    if rows:
        conn.execute(sa.insert(new), rows)
    op.drop_table("source_config")
    op.rename_table("source_config_new", "source_config")


def _restore_source_config(conn) -> None:
    cur = sa.Table("source_config", sa.MetaData(), autoload_with=conn)
    old = sa.Table(
        "source_config_old",
        sa.MetaData(),
        sa.Column("name", sa.String(length=40), primary_key=True),
        sa.Column("url", sa.String(length=400), nullable=False),
        sa.Column("token_env", sa.String(length=120), nullable=False),
        sa.Column("token_cipher", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
    )
    old.create(conn)
    keep = [c.name for c in old.columns]
    rows = [{k: r._mapping[k] for k in keep} for r in conn.execute(sa.select(cur))]
    if rows:
        conn.execute(sa.insert(old), rows)
    op.drop_table("source_config")
    op.rename_table("source_config_old", "source_config")


def _rebuild_event_read(conn) -> None:
    old = sa.Table("event_read", sa.MetaData(), autoload_with=conn)
    new = sa.Table(
        "event_read_new",
        _meta_with_tenants(),
        _tenant_col("event_read"),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", name="pk_event_read"),
    )
    new.create(conn)
    rows = [dict(r._mapping) for r in conn.execute(sa.select(old))]
    for r in rows:
        r["tenant_id"] = DEFAULT_TENANT_ID
    if rows:
        conn.execute(sa.insert(new), rows)
    op.drop_table("event_read")
    op.rename_table("event_read_new", "event_read")


def _restore_event_read(conn) -> None:
    cur = sa.Table("event_read", sa.MetaData(), autoload_with=conn)
    old = sa.Table(
        "event_read_old",
        sa.MetaData(),
        sa.Column("user_id", sa.String(length=36), primary_key=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    old.create(conn)
    rows = [
        {"user_id": r._mapping["user_id"], "last_read_at": r._mapping["last_read_at"]}
        for r in conn.execute(sa.select(cur))
    ]
    if rows:
        conn.execute(sa.insert(old), rows)
    op.drop_table("event_read")
    op.rename_table("event_read_old", "event_read")


def upgrade() -> None:
    conn = op.get_bind()

    # 1. tenants + seed del default ------------------------------------------------
    tenants = op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("edition", sa.String(length=20), nullable=False, server_default="community"),
        sa.Column("seat_limit", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.bulk_insert(
        tenants,
        [{
            "id": DEFAULT_TENANT_ID,
            "slug": "default",
            "name": "Default",
            "edition": _default_edition(),
            "seat_limit": None,
            "status": "active",
        }],
    )

    # 2. organizations ------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        _tenant_col("organizations"),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_organizations_tenant_slug"),
    )

    # 3. tenant_id (+ cambios de unicidad) en las tablas simples ------------------
    for table in _SIMPLE_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(_tenant_col(table, fk=False))
            batch.create_foreign_key(
                _fk_name(table), "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
            )
            if table == "users":
                batch.drop_constraint("uq_users_username", type_="unique")
                batch.drop_constraint("uq_users_email", type_="unique")
                batch.create_unique_constraint(
                    "uq_users_tenant_username", ["tenant_id", "username"])
                batch.create_unique_constraint("uq_users_tenant_email", ["tenant_id", "email"])
            elif table == "ai_settings":
                batch.create_unique_constraint("uq_ai_settings_tenant", ["tenant_id"])
            elif table == "ldap_config":
                batch.create_unique_constraint("uq_ldap_config_tenant", ["tenant_id"])

    # system_config: NULLABLE, sin default (NULL = clave de instancia / plano de control).
    with op.batch_alter_table("system_config") as batch:
        batch.add_column(sa.Column("tenant_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            _fk_name("system_config"), "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
        )

    # 4. cambios de PK: source_config (PK uuid) y event_read (PK compuesta) --------
    _rebuild_source_config(conn)
    _rebuild_event_read(conn)

    # 5. projects.organization_id + reconciliación desde `org` ---------------------
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("organization_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_projects_organization_id_organizations",
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="SET NULL",
        )

    orgs = sa.table(
        "organizations",
        sa.column("id", sa.String), sa.column("tenant_id", sa.String),
        sa.column("slug", sa.String), sa.column("name", sa.String),
    )
    projects = sa.table(
        "projects", sa.column("org", sa.String), sa.column("organization_id", sa.String)
    )
    distinct_orgs = [
        r[0] for r in conn.execute(sa.select(projects.c.org).distinct().order_by(projects.c.org))
    ]
    used_slugs: set[str] = set()
    for value in distinct_orgs:
        base = _slugify(value)
        slug, n = base, 2
        while slug in used_slugs:  # "Wolf" y "wolf" colisionan en slug
            slug, n = f"{base[:90]}-{n}", n + 1
        used_slugs.add(slug)
        org_id = str(uuid.uuid4())
        conn.execute(sa.insert(orgs).values(
            id=org_id, tenant_id=DEFAULT_TENANT_ID, slug=slug, name=value))
        conn.execute(
            sa.update(projects).where(projects.c.org == value).values(organization_id=org_id))


def downgrade() -> None:
    conn = op.get_bind()

    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("fk_projects_organization_id_organizations", type_="foreignkey")
        batch.drop_column("organization_id")

    _restore_event_read(conn)
    _restore_source_config(conn)

    with op.batch_alter_table("system_config") as batch:
        batch.drop_constraint(_fk_name("system_config"), type_="foreignkey")
        batch.drop_column("tenant_id")

    for table in reversed(_SIMPLE_TABLES):
        with op.batch_alter_table(table) as batch:
            if table == "users":
                batch.drop_constraint("uq_users_tenant_username", type_="unique")
                batch.drop_constraint("uq_users_tenant_email", type_="unique")
                batch.create_unique_constraint("uq_users_username", ["username"])
                batch.create_unique_constraint("uq_users_email", ["email"])
            elif table == "ai_settings":
                batch.drop_constraint("uq_ai_settings_tenant", type_="unique")
            elif table == "ldap_config":
                batch.drop_constraint("uq_ldap_config_tenant", type_="unique")
            batch.drop_constraint(_fk_name(table), type_="foreignkey")
            batch.drop_column("tenant_id")

    op.drop_table("organizations")
    op.drop_table("tenants")
