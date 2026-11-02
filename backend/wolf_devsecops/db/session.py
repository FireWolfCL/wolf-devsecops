"""`get_db()` — dependencia FastAPI que abre/cierra una AsyncSession por request.

A diferencia de WIF, NO fija GUC de RLS (`app.current_org`): este producto es
single-tenant (ADR-013), los roles son globales.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from .engine import get_sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_sessionmaker()() as session:
        yield session
