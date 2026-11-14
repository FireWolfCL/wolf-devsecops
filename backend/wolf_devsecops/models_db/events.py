"""Tablas de notificaciones del panel.

`project_events` (append-only): cada fila es un CAMBIO detectado entre dos snapshots
de postura de un proyecto — mejora, empeora, vuln nueva o resuelta. NO guarda texto
humano: guarda datos estructurados (kind + deltas) y el frontend compone el mensaje
en el idioma del usuario. Alimenta la campana y el reporte de avance.

`event_read` (un marcador por usuario): `last_read_at` para contar no-leídos. Append/update.

Solo métricas agregadas (ADR-019): nunca findings crudos, nunca secretos.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ._shared import new_uuid, tenant_id_column


class ProjectEvent(Base):
    __tablename__ = "project_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = tenant_id_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    project_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Taxonomía: improved | worsened | new_critical | resolved_critical | new_high | resolved_high
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # Semáforo de UI: good | warn | danger | info
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="info")
    # Valores para que el frontend arme el texto (banda y % antes/después).
    band_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    band_to: Mapped[str | None] = mapped_column(String(40), nullable=True)
    compliance_from: Mapped[float | None] = mapped_column(Float, nullable=True)
    compliance_to: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Delta de hallazgos por severidad (positivo = aparecieron, negativo = se resolvieron).
    crit_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Marca de envío por los canales externos (Telegram/email). NULL = pendiente.
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EventRead(Base):
    __tablename__ = "event_read"

    # PK compuesta (tenant_id, user_id) (ADR-022 F0).
    tenant_id: Mapped[str] = tenant_id_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
