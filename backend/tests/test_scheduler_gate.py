"""Gate Pro del scheduler: en Community el ciclo automático se omite (no captura);
en Pro+ pasa el gate y sigue. No toca la BD: mockea la sesión y los servicios."""
from __future__ import annotations

import pytest

from wolf_devsecops.cli import scheduler as sched
from wolf_devsecops.settings import get_settings


class _DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def _no_db(monkeypatch):
    # get_sessionmaker() -> sm; sm() -> async context manager
    monkeypatch.setattr(sched, "get_sessionmaker", lambda: (lambda: _DummySession()))

    async def _noop(session):
        return "env"

    monkeypatch.setattr(sched.config_service, "resolve_license_into_settings", _noop)


async def test_community_omite_el_ciclo(monkeypatch, _no_db):
    s = get_settings()
    s.edition, s.license, s.license_required = "community", "", False

    called = {"capture": False}

    async def _spy(*a, **k):
        called["capture"] = True
        return (0, [])

    monkeypatch.setattr(sched.posture_history_service, "capture_snapshots", _spy)

    written, disp = await sched._cycle()

    assert written == 0
    assert disp.get("skipped") == "scheduler_pro"
    assert disp.get("edition") == "community"
    assert called["capture"] is False  # no capturó nada en Community


async def test_pro_pasa_el_gate(monkeypatch, _no_db):
    s = get_settings()
    s.edition, s.license, s.license_required = "pro", "", False

    class _Sentinel(Exception):
        pass

    async def _boom(session):
        raise _Sentinel()

    # Si pasa el gate, lo siguiente que hace es armar el config → prueba que llegó ahí.
    monkeypatch.setattr(sched.config_service, "assemble_app_config", _boom)

    with pytest.raises(_Sentinel):
        await sched._cycle()
