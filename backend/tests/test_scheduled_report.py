"""Reporte de avance programado: gate Pro, dedup por cadencia, no avanza sin canales."""
from __future__ import annotations

import pytest

from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.services import scheduled_report_service as svc
from wolf_devsecops.settings import get_settings


@pytest.fixture
async def session(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/sr.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        yield s
    await db_engine.dispose_engine()


@pytest.fixture
def _channels(monkeypatch):
    """Simula un canal configurado y captura los envíos."""
    sent: list[tuple[str, str]] = []

    def _configured(settings):
        return ["telegram"]

    async def _broadcast(settings, subject, text):
        sent.append((subject, text))
        return ["telegram"]

    monkeypatch.setattr(svc.channels, "configured_channels", _configured)
    monkeypatch.setattr(svc.channels, "broadcast", _broadcast)
    return sent


async def test_gate_pro(session, _channels):
    get_settings().edition = "community"
    await svc.set_config(session, enabled=True, every_days=7)
    out = await svc.maybe_send(session, get_settings())
    assert out == {"sent": False, "skipped": "feature"}
    assert _channels == []


async def test_deshabilitado_no_envia(session, _channels):
    get_settings().edition = "enterprise"
    await svc.set_config(session, enabled=False, every_days=7)
    out = await svc.maybe_send(session, get_settings())
    assert out["skipped"] == "disabled"
    assert _channels == []


async def test_sin_canales_no_avanza_la_cadencia(session, monkeypatch):
    get_settings().edition = "enterprise"
    monkeypatch.setattr(svc.channels, "configured_channels", lambda s: [])
    await svc.set_config(session, enabled=True, every_days=7)
    out = await svc.maybe_send(session, get_settings())
    assert out["skipped"] == "no_channels"
    # No marcó last_sent: queda listo para enviar cuando haya un canal.
    assert (await svc.get_config(session))["last_sent_at"] is None


async def test_envia_y_luego_no_toca(session, _channels):
    get_settings().edition = "enterprise"
    await svc.set_config(session, enabled=True, every_days=7)

    out = await svc.maybe_send(session, get_settings())
    assert out["sent"] is True
    assert out["channels"] == ["telegram"]
    assert len(_channels) == 1
    assert (await svc.get_config(session))["last_sent_at"] is not None

    # Segundo intento inmediato: aún no toca (dedup por cadencia).
    out2 = await svc.maybe_send(session, get_settings())
    assert out2["skipped"] == "not_due"
    assert len(_channels) == 1  # no reenvió


async def test_config_clamp_y_roundtrip(session):
    get_settings().edition = "enterprise"
    cfg = await svc.set_config(session, enabled=True, every_days=999)  # fuera de rango
    assert cfg["every_days"] == 90  # clamp al máximo
    assert cfg["enabled"] is True
    assert cfg["available"] is True
