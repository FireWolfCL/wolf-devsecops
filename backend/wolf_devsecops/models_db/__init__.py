"""Modelos ORM SQLAlchemy (NO confundir con models.py, que es el contrato Pydantic).

Importa todos los modelos para que queden registrados en `Base.metadata` (lo usa
Alembic como `target_metadata`).
"""

from __future__ import annotations

from .audit import AuditLog
from .config import (
    AISettingsRow,
    ProjectRow,
    ProjectSourceRow,
    SourceConfigRow,
    SystemConfigRow,
)
from .posture import PostureSnapshot
from .user import User

__all__ = [
    "AuditLog",
    "AISettingsRow",
    "PostureSnapshot",
    "ProjectRow",
    "ProjectSourceRow",
    "SourceConfigRow",
    "SystemConfigRow",
    "User",
]
