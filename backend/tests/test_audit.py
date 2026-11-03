"""Audit export/retención: list_audit (ventana/orden) y purge_old."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.models_db.audit import AuditLog
from wolf_devsecops.services import audit_service
from wolf_devsecops.settings import get_settings


@pytest.fixture
async def session(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/audit.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        yield s
    await db_engine.dispose_engine()


async def _seed(session):
    now = datetime.now(UTC)
    for days, action in [(100, "old"), (10, "mid"), (0, "new")]:
        session.add(AuditLog(action=action, result="success", created_at=now - timedelta(days=days)))
    await session.commit()


async def test_list_orden_y_ventana(session):
    await _seed(session)
    rows = await audit_service.list_audit(session)
    assert [r.action for r in rows] == ["new", "mid", "old"]  # más recientes primero
    recent = await audit_service.list_audit(session, since_days=30)
    assert {r.action for r in recent} == {"new", "mid"}  # la de 100 días queda fuera


async def test_purge_old(session):
    await _seed(session)
    deleted = await audit_service.purge_old(session, retention_days=30)
    assert deleted == 1  # solo la de 100 días
    rest = await audit_service.list_audit(session)
    assert {r.action for r in rest} == {"new", "mid"}
    assert await audit_service.purge_old(session, retention_days=0) == 0  # 0 = no purga
