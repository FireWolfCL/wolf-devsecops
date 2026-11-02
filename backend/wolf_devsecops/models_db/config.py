"""Tablas de configuración editable por el admin (ADR-015). Reemplazan
`config/projects.yaml` como fuente de verdad en caliente.

ADR-002 intacto: `token_env` guarda el NOMBRE de la variable de entorno, jamás el
valor del token. `config_service` rechaza en escritura cualquier valor con pinta de
secreto.

ADR-006 (extensibilidad): añadir una fuente nueva (GitLab, Jenkins…) es un `source`
nuevo con su JSON en `project_sources.settings` — NO toca el esquema.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ._shared import new_uuid


class SourceConfigRow(Base):
    __tablename__ = "source_config"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)  # github/defectdojo/...
    url: Mapped[str] = mapped_column(String(400), nullable=False)
    token_env: Mapped[str] = mapped_column(String(120), nullable=False)  # NOMBRE, no valor
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now(), server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ProjectRow(Base):
    __tablename__ = "projects"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    org: Mapped[str] = mapped_column(String(100), nullable=False, server_default="Wolf")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectSourceRow(Base):
    __tablename__ = "project_sources"
    __table_args__ = (UniqueConstraint("project_key", "source", name="uq_project_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_key: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.key", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)  # github/defectdojo/...
    # Lo específico por fuente: {repo, ci_workflow} github; {product} defectdojo/wss;
    # {project_key} sonarqube. Un source nuevo = un JSON nuevo, sin migración.
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class AISettingsRow(Base):
    __tablename__ = "ai_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)  # fila única
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    base_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    token_env: Mapped[str | None] = mapped_column(String(120), nullable=True)  # NOMBRE, no valor
    # Toggle de runtime (ADR-015). Opera BAJO el techo WOLF_AI_ENABLED del entorno.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SystemConfigRow(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now(), server_default=func.now()
    )
