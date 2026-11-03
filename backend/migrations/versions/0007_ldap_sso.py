"""0007 — SSO/LDAP (feature Pro): marca de fuente de auth + config del directorio.

Añade:
- `users.auth_source` ("local" | "ldap"): cómo se bindea el usuario. Default "local",
  server_default "local" para que las filas existentes queden como auth local sin tocar
  el login actual. Los usuarios "ldap" no tienen `hashed_password` (seam ADR-013).
- tabla `ldap_config` (fila única id=1): host(s) réplica, base_dn, bind directo por
  plantilla o search+bind con cuenta de servicio, TLS, timeouts. El bind password se
  guarda cifrado (Fernet, misma llave de reposo que el MFA) en `bind_password_cipher`,
  o por NOMBRE de env var en `bind_password_env`; el valor jamás se persiste en claro.

`downgrade()` es REAL (ADR-016): descarta la tabla y la columna. batch_alter_table
para que el drop de columna funcione también en SQLite (autohospedaje, ADR-012).

Revision ID: 0007_ldap_sso
Revises: 0006_source_token_cipher
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_ldap_sso"
down_revision: str | None = "0006_source_token_cipher"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "auth_source",
                sa.String(length=10),
                nullable=False,
                server_default="local",
            )
        )

    op.create_table(
        "ldap_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hosts", sa.JSON(), nullable=False),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("start_tls", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tls_verify", sa.String(length=10), nullable=False, server_default="required"),
        sa.Column("tls_ca_pem", sa.Text(), nullable=True),
        sa.Column("base_dn", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("user_dn_template", sa.String(length=400), nullable=True),
        sa.Column(
            "user_filter", sa.String(length=400), nullable=False, server_default="(uid={username})"
        ),
        sa.Column("bind_dn", sa.String(length=400), nullable=True),
        sa.Column("bind_password_env", sa.String(length=120), nullable=True),
        sa.Column("bind_password_cipher", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="8"),
    )


def downgrade() -> None:
    op.drop_table("ldap_config")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("auth_source")
