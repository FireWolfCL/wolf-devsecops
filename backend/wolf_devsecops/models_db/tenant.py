"""Tablas `tenants` y `organizations` (ADR-022, F0: fontanería de multi-tenancy).

- `tenants` es el PLANO DE CONTROL: NO lleva `tenant_id`. En self-hosted existe una sola
  fila, el tenant default (`DEFAULT_TENANT_ID`, slug `default`), sembrada por la migración
  0011. `edition` y `seat_limit` son una caché del derecho vigente (la fuente de verdad
  sigue siendo la licencia WLA firmada; la sincronización por-tenant llega en F4).
- `organizations` está scoped al tenant (`UNIQUE(tenant_id, slug)`). Reemplaza al string
  `projects.org`, que se conserva denormalizado una release.

F0 NO añade lógica de scoping ni RLS: solo el esquema.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ._shared import new_uuid, tenant_id_column


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Caché de la edición del tenant (community | pro | enterprise).
    edition: Mapped[str] = mapped_column(
        String(20), nullable=False, default="community", server_default="community"
    )
    # Tope de asientos (usuarios). NULL = ilimitado.
    seat_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_organizations_tenant_slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = tenant_id_column()
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["Organization", "Tenant"]
