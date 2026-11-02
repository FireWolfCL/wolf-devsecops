"""0005 — columna `dispatched_at` en `project_events` (canales de notificación).

Marca qué eventos ya se enviaron por Telegram/email para no re-notificar.
`downgrade()` es REAL (ADR-016). El arranque aplica (entrypoint).

Revision ID: 0005_event_dispatched
Revises: 0004_project_events
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_event_dispatched"
down_revision: str | None = "0004_project_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_events",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_events", "dispatched_at")
