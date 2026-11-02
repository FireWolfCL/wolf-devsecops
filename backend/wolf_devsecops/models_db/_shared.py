"""Helpers compartidos por los modelos ORM."""

from __future__ import annotations

import uuid


def new_uuid() -> str:
    """PK portable: UUID4 como string (SQLite no tiene gen_random_uuid)."""
    return str(uuid.uuid4())
