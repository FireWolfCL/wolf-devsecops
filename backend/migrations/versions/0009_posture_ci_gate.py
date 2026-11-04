"""0009 — columnas `ci_status` y `quality_gate` en `posture_snapshots` (triggers 2.5).

Guardan el estado de CI y del quality gate al capturar cada snapshot, para que el
detector de eventos pueda disparar una notificación cuando CI pasa a rojo o el gate
pasa a ERROR entre dos snapshots. Nullable (fuente no configurada / filas anteriores).
`downgrade()` es REAL (dropea las columnas), exigido por ADR-016. El arranque NO
auto-migra: el admin aplica.

Revision ID: 0009_posture_ci_gate
Revises: 0008_posture_rubric_version
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_posture_ci_gate"
down_revision: str | None = "0008_posture_rubric_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("posture_snapshots", sa.Column("ci_status", sa.String(length=20), nullable=True))
    op.add_column("posture_snapshots", sa.Column("quality_gate", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("posture_snapshots", "quality_gate")
    op.drop_column("posture_snapshots", "ci_status")
