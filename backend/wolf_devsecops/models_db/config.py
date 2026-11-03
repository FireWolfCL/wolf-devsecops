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
    Text,
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
    # Almacén cifrado OPT-IN del token (0006, COMPLEMENTA ADR-002). Fernet ciphertext
    # (misma llave/patrón que el secreto MFA; separada de la firma JWT). El texto plano
    # NUNCA se persiste; `resolve_token` le da PRECEDENCIA sobre `token_env`. NULL = la
    # fuente resuelve su token desde el entorno (comportamiento original).
    token_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Expiración conocida del token (cabecera del proveedor, p. ej. GitHub). NULL =
    # desconocida/sin expiración. No es sensible: se expone al panel para el recordatorio.
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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


class LDAPConfigRow(Base):
    """Config de directorio (SSO/LDAP/AD) — fila única `id=1`, espeja AISettingsRow.
    Feature Pro: los endpoints que la escriben van gated por `require_feature(FEATURE_SSO)`.

    Secreto (bind password): sigue el patrón de `source_config` — `bind_password_env`
    guarda el NOMBRE de la env var y `bind_password_cipher` el VALOR cifrado con Fernet
    (misma llave de reposo que el MFA, nunca la de firma JWT). La API expone solo
    `bind_password_present: bool`, jamás el valor. `resolve_token` da precedencia a la BD.
    """

    __tablename__ = "ldap_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)  # fila única
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Réplicas del directorio, ej. ["ldaps://dc1.corp:636", "ldaps://dc2.corp:636"].
    # Se prueban TODAS antes de rechazar (replication lag da fallos intermitentes).
    hosts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "required" = valida el certificado del servidor (recomendado) | "relaxed" = no valida.
    tls_verify: Mapped[str] = mapped_column(String(10), nullable=False, default="required")
    # CA corporativa (PEM) para validar el cert del DC cuando no está en el trust store del
    # SO. NO es secreto (es un cert público): permite sostener tls_verify="required".
    tls_ca_pem: Mapped[str | None] = mapped_column(Text(), nullable=True)
    base_dn: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    # Si está presente → bind DIRECTO con esta plantilla (ej. "uid={username},ou=people,...").
    # Si es NULL → search+bind: se bindea con la cuenta de servicio y se busca por user_filter.
    user_dn_template: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # Filtro de búsqueda del usuario (search+bind). AD: "(sAMAccountName={username})";
    # FreeIPA: "(uid={username})".
    user_filter: Mapped[str] = mapped_column(String(400), nullable=False,
                                             default="(uid={username})")
    bind_dn: Mapped[str | None] = mapped_column(String(400), nullable=True)  # cuenta de servicio
    bind_password_env: Mapped[str | None] = mapped_column(String(120), nullable=True)  # NOMBRE
    bind_password_cipher: Mapped[str | None] = mapped_column(Text(), nullable=True)  # Fernet
    # ldap3 2.9.1 exige int en los timeouts (float revienta con struct.error sobre TLS).
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=8)


class SystemConfigRow(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now(), server_default=func.now()
    )
