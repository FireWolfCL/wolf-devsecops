"""Tabla `users` (porta WIF app/models/user.py, podada a single-tenant).

Decisión de forma (fiel a WIF): MFA y recovery codes son columnas de `users`, no
tablas aparte — patrón probado en producción, evita joins en el hot-path de login.
NO hay tabla `sessions`: la sesión es stateless (JWT) y la revocación es
`token_version` (el claim `tv`).

Invariante de seguridad: `mfa_secret` se guarda CIFRADO (Fernet) y `mfa_recovery_codes`
como HASHES SHA-256 — nunca en claro. Eso lo garantiza la capa de servicio (A1/A2);
este modelo solo define el almacenamiento.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ..rbac.roles import Role
from ._shared import new_uuid


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # nullable: deja la puerta a cuentas federadas (SSO) sin password local (ADR-013 seam).
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, native_enum=False, length=20, validate_strings=True),
        nullable=False,
        default=Role.viewer,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Lockout de login (gate → HTTP 423).
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # MFA/TOTP.
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)  # cifrado Fernet
    mfa_recovery_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)  # hashes SHA-256

    # Revocación de sesiones sin tabla de sesiones: el access token lleva claim `tv`,
    # que debe igualar este valor; subirlo invalida todos los tokens previos.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Fuerza cambio de password tras bootstrap/reset (y enrolar MFA el admin).
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
