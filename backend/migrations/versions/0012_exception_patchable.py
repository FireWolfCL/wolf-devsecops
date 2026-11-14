"""0012 — seguimiento de parche en excepciones (ADR-021): `cve_id`, `fixed_version`, `patchable_since`.

Un riesgo aceptado porque "no había parche" debe avisar cuando el parche aparece. La fila
de gobierno guarda el CVE del hallazgo y la versión fija conocida (NULL = aceptado sin
parche); el scheduler re-chequea contra el último reporte Trivy y, al aparecer
`fixed_version`, marca `patchable_since` y emite el evento `exception_patchable`.

Solo columnas nullable sin default: las filas existentes quedan "no rastreadas" (cve_id NULL),
sin backfill ni cambio de comportamiento. No toca `tenant_id` ni RLS (0011).
SQLite-safe vía `batch_alter_table`. `downgrade()` es REAL (ADR-016).

Revision ID: 0012_exception_patchable
Revises: 0011_multitenancy_f0
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_exception_patchable"
down_revision: str | None = "0011_multitenancy_f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_exceptions") as batch:
        batch.add_column(sa.Column("cve_id", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("fixed_version", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("patchable_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_exceptions") as batch:
        batch.drop_column("patchable_since")
        batch.drop_column("fixed_version")
        batch.drop_column("cve_id")
