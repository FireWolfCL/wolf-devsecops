"""MEJORAS 2.5 — seam NotificationChannel + triggers CI-rojo / gate-ERROR."""

from __future__ import annotations

from wolf_devsecops.models_db.posture import PostureSnapshot as Row
from wolf_devsecops.services import event_service
from wolf_devsecops.services import notification_channels as ch
from wolf_devsecops.settings import Settings


def _row(ci=None, gate=None, band="aceptable", pct=80.0):
    return Row(project_key="p", compliance_pct=pct, band=band, crit=0, high=0, med=0, low=0,
               ci_status=ci, quality_gate=gate)


def test_registry_trae_telegram_y_email():
    names = {c.name for c in ch.all_channels()}
    assert {"telegram", "email"} <= names


def test_is_configured_gatea_por_credenciales():
    s = Settings()
    tg, em = ch.TelegramChannel(), ch.EmailChannel()
    assert not tg.is_configured(s) and not em.is_configured(s)
    s.telegram_bot_token = "x"
    s.telegram_chat_id = "y"
    assert tg.is_configured(s)
    assert ch.configured_channels(s) == [c for c in ch.all_channels() if c.name == "telegram"]


def test_register_agrega_canal():
    class Dummy(ch.NotificationChannel):
        name = "dummy"
        def is_configured(self, settings):
            return False
        async def send(self, settings, subject, text_html):
            return True

    before = len(ch.all_channels())
    ch.register(Dummy())
    try:
        assert len(ch.all_channels()) == before + 1
        assert any(c.name == "dummy" for c in ch.all_channels())
    finally:
        ch._REGISTRY.pop()  # no contaminar otros tests


def test_detect_ci_rojo_dispara_solo_en_transicion():
    # verde → rojo: evento. rojo → rojo: nada (no repetir).
    evs = event_service.detect(_row(ci="success"), _row(ci="failure"))
    assert any(e.kind == "ci_failed" and e.severity == "danger" for e in evs)
    assert event_service.detect(_row(ci="failure"), _row(ci="failure")) == []


def test_detect_gate_error_dispara_solo_en_transicion():
    evs = event_service.detect(_row(gate="OK"), _row(gate="ERROR"))
    assert any(e.kind == "gate_error" for e in evs)
    assert event_service.detect(_row(gate="ERROR"), _row(gate="ERROR")) == []
