"""0003 — tabla `posture_snapshots` (ADR-019, tendencia histórica de postura).

Solo métricas AGREGADAS por proyecto (nunca findings). `downgrade()` es REAL
(dropea índices y tabla), exigido por ADR-016. El arranque NO auto-migra: el admin aplica.

Revision ID: 0003_posture_snapshots
Revises: 0002_project_org
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_posture_snapshots"
down_revision: str | None = "0002_project_org"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "posture_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_key", sa.String(length=100), nullable=False),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("compliance_pct", sa.Float(), nullable=False),
        sa.Column("band", sa.String(length=40), nullable=False),
        sa.Column("crit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("high", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("med", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("low", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_pct", sa.Float(), nullable=True),
    )
    op.create_index("ix_posture_snapshots_project_key", "posture_snapshots", ["project_key"])
    op.create_index("ix_posture_snapshots_captured_at", "posture_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_posture_snapshots_captured_at", table_name="posture_snapshots")
    op.drop_index("ix_posture_snapshots_project_key", table_name="posture_snapshots")
    op.drop_table("posture_snapshots")
