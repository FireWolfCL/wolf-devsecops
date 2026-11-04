"""0010 — tabla `source_exceptions` (ADR-021): gobierno de excepciones de hallazgos.

Guarda motivo, autor, aprobador, estado y vencimiento de cada falso positivo / riesgo
aceptado / fuera de alcance / mitigado. El estado que excluye del score vive en la
herramienta; esto es el gobierno + evidencia. `downgrade()` es REAL (ADR-016).

Revision ID: 0010_source_exceptions
Revises: 0009_posture_ci_gate
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_source_exceptions"
down_revision: str | None = "0009_posture_ci_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_exceptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("project_key", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("finding_ref", sa.String(length=200), nullable=False),
        sa.Column("finding_title", sa.String(length=300), nullable=True),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="proposed"),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_source_exceptions_project_key", "source_exceptions", ["project_key"])
    op.create_index("ix_source_exceptions_status", "source_exceptions", ["status"])
    op.create_index("ix_source_exceptions_expires_at", "source_exceptions", ["expires_at"])
    op.create_index("ix_source_exceptions_created_at", "source_exceptions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_source_exceptions_created_at", table_name="source_exceptions")
    op.drop_index("ix_source_exceptions_expires_at", table_name="source_exceptions")
    op.drop_index("ix_source_exceptions_status", table_name="source_exceptions")
    op.drop_index("ix_source_exceptions_project_key", table_name="source_exceptions")
    op.drop_table("source_exceptions")
