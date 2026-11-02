"""Engine async + sessionmaker, perezosos (patrón WIF app/database.py).

Cambiar de SQLite a Postgres es solo `WOLF_DATABASE_URL`: el resto del código
depende de `AsyncSession`, no del driver (mismo principio que la caché, ADR-005).
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..settings import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        if url.startswith("sqlite"):
            # SQLite serializa escrituras: busy-timeout largo en vez de "database is
            # locked" inmediato, y WAL para que lecturas y 1 escritor convivan.
            _engine = create_async_engine(url, future=True, connect_args={"timeout": 30})

            @event.listens_for(_engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        else:
            # Postgres/MySQL: el pool se dimensiona por config (ver PERFORMANCE-ADMIN.md).
            _engine = create_async_engine(
                url,
                future=True,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def dispose_engine() -> None:
    """Cierra el engine (shutdown del lifespan) y resetea los singletons."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
