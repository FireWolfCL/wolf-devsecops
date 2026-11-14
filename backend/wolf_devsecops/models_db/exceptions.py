"""Tabla `source_exceptions` (ADR-021): gobierno de las excepciones de hallazgos.

Cada fila es una decisión de gestión sobre un hallazgo: falso positivo, riesgo
aceptado, fuera de alcance o mitigado. El ESTADO que excluye del score vive en la
herramienta de origen (DefectDojo/Sonar); esta tabla guarda el GOBIERNO que la
herramienta no expone de forma uniforme: motivo, autor, aprobador y vencimiento.

NO es un segundo sistema de exclusión (6.3): la exclusión la hace el estado nativo
(filtrado por `active`, 6.3.a). Append + updates de estado. Sin secretos.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ._shared import new_uuid, tenant_id_column

# kind: false_positive | risk_accepted | out_of_scope | mitigated
# status: proposed | applied | revoked | expired


class SourceException(Base):
    __tablename__ = "source_exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = tenant_id_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    project_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # defectdojo | sonarqube
    finding_ref: Mapped[str] = mapped_column(String(200), nullable=False)  # id/issue key en la fuente
    finding_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)  # secrets|sca|... (6.3.d)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed", index=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Seguimiento de parche (0012): si el hallazgo es un CVE, se rastrea hasta que Trivy
    # publique `FixedVersion`. cve_id NULL = no rastreable (no-op honesto). fixed_version
    # NULL = aceptado sin parche; al aparecer se fija junto a patchable_since (y se avisa 1 vez).
    cve_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fixed_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    patchable_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
