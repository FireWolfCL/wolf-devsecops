"""0004 — tablas `project_events` y `event_read` (campana de notificaciones).

`project_events` (append-only): cambios detectados entre snapshots de postura
(mejora/empeora/vuln nueva/resuelta). `event_read`: marcador por usuario para los
no-leídos. `downgrade()` es REAL (ADR-016). El arranque aplica (entrypoint).

Revision ID: 0004_project_events
Revises: 0003_posture_snapshots
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_project_events"
down_revision: str | None = "0003_posture_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("project_key", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False, server_default="info"),
        sa.Column("band_from", sa.String(length=40), nullable=True),
        sa.Column("band_to", sa.String(length=40), nullable=True),
        sa.Column("compliance_from", sa.Float(), nullable=True),
        sa.Column("compliance_to", sa.Float(), nullable=True),
        sa.Column("crit_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("high_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", sa.JSON(), nullable=True),
    )
    op.create_index("ix_project_events_created_at", "project_events", ["created_at"])
    op.create_index("ix_project_events_project_key", "project_events", ["project_key"])

    op.create_table(
        "event_read",
        sa.Column("user_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "last_read_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("event_read")
    op.drop_index("ix_project_events_project_key", table_name="project_events")
    op.drop_index("ix_project_events_created_at", table_name="project_events")
    op.drop_table("project_events")
