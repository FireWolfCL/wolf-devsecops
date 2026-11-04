"""0008 — columna `rubric_version` en `posture_snapshots` (tendencia 2.1).

Guarda la versión de la rúbrica vigente al capturar cada snapshot, para poder
marcar en la tendencia los puntos donde cambió la rúbrica (un salto de score por
cambio de rúbrica no es una regresión real). Nullable: las filas anteriores no la
tienen. `downgrade()` es REAL (dropea la columna), exigido por ADR-016. El arranque
NO auto-migra: el admin aplica.

Revision ID: 0008_posture_rubric_version
Revises: 0007_ldap_sso
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_posture_rubric_version"
down_revision: str | None = "0007_ldap_sso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "posture_snapshots",
        sa.Column("rubric_version", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("posture_snapshots", "rubric_version")
