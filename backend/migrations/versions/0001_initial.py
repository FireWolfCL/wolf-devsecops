"""0001 initial — identidad, config y auditoría (ADR-012).

Crea las 7 tablas de la capa admin. NINGUNA tabla de findings/CI/quality/releases
(preserva ADR-001). `token_env` guarda el NOMBRE de la variable, no el valor (ADR-002).

`downgrade()` es REAL (dropea todo): el chequeo de reversibilidad por AST del Centro
de Update (ADR-016) rechaza aplicar migraciones con downgrade vacío.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=100), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mfa_secret", sa.String(length=255), nullable=True),
        sa.Column("mfa_recovery_codes", sa.JSON(), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("actor_username", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=60), nullable=True),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("result", sa.String(length=10), nullable=False, server_default="success"),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    op.create_table(
        "source_config",
        sa.Column("name", sa.String(length=40), primary_key=True),
        sa.Column("url", sa.String(length=400), nullable=False),
        sa.Column("token_env", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
    )

    op.create_table(
        "projects",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "project_sources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_key", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["project_key"], ["projects.key"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_key", "source", name="uq_project_source"),
    )
    op.create_index("ix_project_sources_project_key", "project_sources", ["project_key"])

    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="local"),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("base_url", sa.String(length=400), nullable=True),
        sa.Column("token_env", sa.String(length=120), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "system_config",
        sa.Column("key", sa.String(length=80), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("system_config")
    op.drop_table("ai_settings")
    op.drop_index("ix_project_sources_project_key", table_name="project_sources")
    op.drop_table("project_sources")
    op.drop_table("projects")
    op.drop_table("source_config")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
