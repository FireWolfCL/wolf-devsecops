"""Tabla `audit_log` (append-only). Taxonomía inicial; se coordinará con
wolf-observability-audit-agent y retención con wolf-privacy-kit.

Invariante: `detail` va SANEADO — nunca token, hash, secreto ni stacktrace (ADR-012 §9).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ._shared import new_uuid, tenant_id_column


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = tenant_id_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    # null = acción de sistema (bootstrap, arranque).
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # snapshot denormalizado: sobrevive al borrado del usuario.
    actor_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)  # event_type
    target_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result: Mapped[str] = mapped_column(String(10), nullable=False, default="success")
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # saneado
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
