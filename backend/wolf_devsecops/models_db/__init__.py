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
from .events import EventRead, ProjectEvent
from .exceptions import SourceException
from .posture import PostureSnapshot
from .user import User

__all__ = [
    "AuditLog",
    "AISettingsRow",
    "EventRead",
    "PostureSnapshot",
    "SourceException",
    "ProjectEvent",
    "ProjectRow",
    "ProjectSourceRow",
    "SourceConfigRow",
    "SystemConfigRow",
    "User",
]
