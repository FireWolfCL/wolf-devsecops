"""Helpers compartidos por los modelos ORM."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

# Tenant por defecto (ADR-022, F0). UUID FIJO y bien conocido: es el tenant del
# despliegue self-hosted single-tenant y el destino del `server_default` de
# `tenant_id`, de modo que todo INSERT existente cae en él sin tocar servicios.
# La migración 0011 siembra la fila con este mismo id; NO cambiarlo jamás.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_TENANT_SLUG = "default"


def new_uuid() -> str:
    """PK portable: UUID4 como string (SQLite no tiene gen_random_uuid)."""
    return str(uuid.uuid4())


def tenant_id_column(*, primary_key: bool = False) -> Mapped[str]:
    """Columna `tenant_id` estándar (ADR-022): FK→tenants.id ON DELETE CASCADE, NOT NULL,
    con `server_default` = tenant default (F0: los INSERT actuales no cambian)."""
    return mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
        server_default=DEFAULT_TENANT_ID,
    )
