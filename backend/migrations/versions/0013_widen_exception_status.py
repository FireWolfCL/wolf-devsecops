"""0013 — ensancha source_exceptions.status a VARCHAR(20).

Los estados `write_failed` (12 chars) y `expire_failed` (13) NO caben en VARCHAR(10):
una escritura/vencimiento fallido intentaba guardar ese estado y reventaba con
`value too long for type character varying(10)` → HTTP 500 en approve/expire.
Reversible (el downgrade vuelve a 10; falla a propósito si hay valores largos vivos).

Revision ID: 0013_widen_exception_status
Revises: 0012_exception_patchable
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013_widen_exception_status"
down_revision: str | None = "0012_exception_patchable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_exceptions") as batch:
        batch.alter_column(
            "status", existing_type=sa.String(10), type_=sa.String(20), existing_nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("source_exceptions") as batch:
        batch.alter_column(
            "status", existing_type=sa.String(20), type_=sa.String(10), existing_nullable=False
        )
