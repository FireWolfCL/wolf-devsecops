"""DeclarativeBase compartido por todos los modelos ORM (models_db/)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base de SQLAlchemy 2.x. `Base.metadata` es el target de Alembic."""
