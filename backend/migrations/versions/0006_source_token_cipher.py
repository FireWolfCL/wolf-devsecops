"""0006 — almacén cifrado opt-in del token de una fuente + su expiración.

COMPLEMENTA (no reemplaza) ADR-002: `source_config.token_env` sigue guardando el
NOMBRE de la variable de entorno y su valor puede seguir viviendo SOLO en el entorno.
Esta migración AÑADE dos columnas en `source_config`:

- `token_cipher`     — el VALOR del token cifrado con Fernet (AES-128-CBC + HMAC,
                       la MISMA llave/patrón que el secreto MFA: `WOLF_MFA_FERNET_KEY`,
                       separada de la llave de firma JWT). El texto plano NUNCA se
                       persiste. `resolve_token` le da PRECEDENCIA sobre la env var.
- `token_expires_at` — fecha de expiración del token, capturada de la cabecera
                       `github-authentication-token-expiration` cuando GitHub la
                       devuelve (item 5/6). NULL = sin expiración conocida.

`downgrade()` es REAL (ADR-016): descarta ambas columnas (y con ellas los tokens
cifrados — el admin vuelve al modelo env-only). Se usa batch_alter_table para que
el drop funcione también en SQLite (default de autohospedaje, ADR-012).

Revision ID: 0006_source_token_cipher
Revises: 0005_event_dispatched
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_source_token_cipher"
down_revision: str | None = "0005_event_dispatched"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_config") as batch:
        # Ciphertext Fernet del token (opt-in). Nullable: la mayoría de las fuentes
        # sigue resolviendo el token desde el entorno (token_cipher = NULL).
        batch.add_column(sa.Column("token_cipher", sa.Text(), nullable=True))
        # Expiración conocida del token (capturada del proveedor). Nullable.
        batch.add_column(
            sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("source_config") as batch:
        batch.drop_column("token_expires_at")
        batch.drop_column("token_cipher")
