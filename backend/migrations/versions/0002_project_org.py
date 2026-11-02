"""0002 — columna `org` en projects (organización dueña del proyecto).

Permite agrupar proyectos por organización (Wolf vs externas como lsst-it) y que
el % de postura de Wolf no se mezcle con el de terceros. `downgrade()` es REAL
(dropea la columna): el chequeo de reversibilidad del Centro de Update lo exige.

Revision ID: 0002_project_org
Revises: 0001_initial
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_project_org"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("org", sa.String(length=100), nullable=False, server_default="Wolf")
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("org")
