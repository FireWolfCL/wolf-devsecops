"""Tabla `posture_snapshots` (ADR-019): tendencia histórica de postura.

Invariante (ADR-012/019): SOLO métricas AGREGADAS por proyecto y momento —
% de cumplimiento, banda, conteos por severidad, % de cobertura, timestamp.
NUNCA findings individuales ni resultados de escaneo. Misma familia que `audit_log`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ._shared import new_uuid


class PostureSnapshot(Base):
    __tablename__ = "posture_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    compliance_pct: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(40), nullable=False)
    crit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    med: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Versión de la rúbrica vigente al capturar (POSTURE-RUBRIC.md). Nullable:
    # las filas anteriores a 0008 no la tienen. Sirve para marcar en la tendencia
    # los puntos donde cambió la rúbrica (un salto de score puede deberse a eso,
    # no a una regresión real).
    rubric_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
