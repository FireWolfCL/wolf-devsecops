"""Despacho de eventos a canales externos: Telegram y email (item M).

Lee los eventos pendientes (dispatched_at IS NULL) por encima del umbral de
severidad, arma UN mensaje y lo envía a los canales configurados (si el token/host
está vacío, ese canal se omite). Marca los eventos como enviados para no repetir.

Sin secretos en logs. Los tokens viven en el entorno (WOLF_TELEGRAM_*, WOLF_SMTP_*).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..edition import FEATURE_NOTIFICATIONS, is_enabled
from ..models_db.config import SourceConfigRow, SystemConfigRow
from ..models_db.events import ProjectEvent
from ..settings import Settings
from . import notification_channels as channels

log = logging.getLogger(__name__)

_SEV_RANK = {"info": 0, "good": 1, "warn": 2, "danger": 3}
_TOKEN_REMINDER_DAYS = 7
_REMINDER_MARKER = "token_reminder_last"  # SystemConfigRow: {source: iso del último aviso}


def _has_channel(settings: Settings) -> bool:
    # El envío EXTERNO de alertas es feature Pro. En Community la campana in-app +
    # Novedades siguen funcionando (son base); solo no sale nada por canales externos.
    # Las novedades quedan pendientes y se despachan si sube a Pro. El SEAM de canales
    # (notification_channels) decide "configurado"; aquí solo se suma el gate de edición.
    if not is_enabled(FEATURE_NOTIFICATIONS):
        return False
    return bool(channels.configured_channels(settings))


# Back-compat: envío directo por un canal concreto (lo usa algún call-site histórico
# y los tests). Delegan en el seam para no duplicar la lógica de transporte.
async def send_telegram(settings: Settings, text: str) -> bool:
    return await channels.TelegramChannel().send(settings, "Wolf DevSecOps", text)


async def send_email(settings: Settings, subject: str, body_html: str) -> bool:
    return await channels.EmailChannel().send(settings, subject, body_html)


_ICON = {
    "new_critical": "🔴", "resolved_critical": "✅", "new_high": "🟠",
    "resolved_high": "✅", "improved": "📈", "worsened": "📉",
    "ci_failed": "🚧", "gate_error": "🛑", "exception_expired": "⏰", "exception_expire_failed": "⛔",
}


def _line(ev: ProjectEvent, lang: str) -> str:
    es = lang != "en"
    c, h = abs(ev.crit_delta), abs(ev.high_delta)
    icon = _ICON.get(ev.kind, "•")
    match ev.kind:
        case "new_critical":
            body = f"{c} crítico(s) nuevo(s)" if es else f"{c} new critical(s)"
        case "resolved_critical":
            body = f"{c} crítico(s) resuelto(s)" if es else f"{c} critical(s) resolved"
        case "new_high":
            body = f"{h} alto(s) nuevo(s)" if es else f"{h} new high(s)"
        case "resolved_high":
            body = f"{h} alto(s) resuelto(s)" if es else f"{h} high(s) resolved"
        case "improved":
            body = (f"mejoró: {ev.band_from} → {ev.band_to}" if es
                    else f"improved: {ev.band_from} → {ev.band_to}")
        case "worsened":
            body = (f"empeoró: {ev.band_from} → {ev.band_to}" if es
                    else f"worsened: {ev.band_from} → {ev.band_to}")
        case "ci_failed":
            body = "CI en rojo" if es else "CI is failing"
        case "gate_error":
            body = "quality gate en ERROR" if es else "quality gate is ERROR"
        case "exception_expired":
            body = ("venció una aceptación de riesgo: vuelve a contar" if es
                    else "a risk acceptance expired: it counts again")
        case "exception_expire_failed":
            body = ("venció una aceptación de riesgo pero NO se pudo revertir en la fuente: sigue oculta"
                    if es else "a risk acceptance expired but could NOT be reverted at the source: still hidden")
        case _:
            body = ev.kind
    return f"{icon} <b>{ev.project_key}</b> — {body}"


def build_message(events: list[ProjectEvent], lang: str) -> str:
    es = lang != "en"
    head = ("🐺 <b>Wolf DevSecOps — novedades</b>" if es
            else "🐺 <b>Wolf DevSecOps — what's new</b>")
    lines = [_line(e, lang) for e in events]
    return head + "\n" + "\n".join(lines)


async def dispatch_pending(
    session: AsyncSession, settings: Settings, *, limit: int = 100
) -> dict:
    """Envía los eventos pendientes a los canales configurados y los marca.
    Devuelve conteos. Si no hay canales configurados, igual marca como enviados
    para no acumular indefinidamente (evita avalancha al configurar un canal)."""
    # Sin ningún canal configurado no marcamos nada: así, al conectar un canal más
    # tarde, las novedades recientes (hasta `limit`) sí se envían.
    if not _has_channel(settings):
        return {"pending": 0, "sent": 0, "channels": [], "skipped": "sin canales"}

    threshold = _SEV_RANK.get(settings.notify_min_severity, 1)
    q = (
        select(ProjectEvent)
        .where(ProjectEvent.dispatched_at.is_(None))
        .order_by(ProjectEvent.created_at.asc())
        .limit(limit)
    )
    pending = list((await session.execute(q)).scalars())
    to_send = [e for e in pending if _SEV_RANK.get(e.severity, 0) >= threshold]

    channels_ok: list[str] = []
    if to_send:
        text = build_message(to_send, settings.notify_lang)
        subject = "Wolf DevSecOps — novedades" if settings.notify_lang != "en" else "Wolf DevSecOps — what's new"
        channels_ok = await channels.broadcast(settings, subject, text)

    # Marca TODO lo pendiente (enviado o filtrado) para no re-evaluarlo.
    now = datetime.now(UTC)
    for e in pending:
        e.dispatched_at = now
    await session.commit()
    return {"pending": len(pending), "sent": len(to_send), "channels": channels_ok}


async def check_token_expiry(session: AsyncSession, settings: Settings) -> dict:
    """Recordatorio de renovación: si el token de alguna fuente vence en <= 7 días
    (o ya venció), avisa por los canales configurados. La fecha es fija (capturada al
    probar la conexión), así que no hay refresco. Dedup: a lo sumo un aviso por fuente
    cada 24 h (marcador en system_config)."""
    if not _has_channel(settings):
        return {"reminded": 0, "skipped": "sin canales"}
    now = datetime.now(UTC)
    cutoff = now + timedelta(days=_TOKEN_REMINDER_DAYS)
    rows = list((await session.execute(select(SourceConfigRow))).scalars())
    expiring = [r for r in rows if r.token_expires_at is not None and r.token_expires_at <= cutoff]
    if not expiring:
        return {"reminded": 0}

    marker = (
        await session.execute(select(SystemConfigRow).where(SystemConfigRow.key == _REMINDER_MARKER))
    ).scalar_one_or_none()
    sent: dict = dict((marker.value or {}).get("v", {})) if marker else {}

    to_send = []
    for r in expiring:
        last = sent.get(r.name)
        if last:
            try:
                if now - datetime.fromisoformat(last) < timedelta(hours=24):
                    continue
            except ValueError:
                pass
        to_send.append(r)
    if not to_send:
        return {"reminded": 0}

    es = settings.notify_lang != "en"
    lines = ["🔑 <b>" + ("Tokens por vencer" if es else "Tokens expiring") + "</b>"]
    for r in to_send:
        when = r.token_expires_at.strftime("%Y-%m-%d")
        days = (r.token_expires_at - now).days
        if r.token_expires_at <= now:
            lines.append(f"• <b>{r.name}</b>: " + (f"VENCIDO ({when})" if es else f"EXPIRED ({when})"))
        else:
            lines.append(f"• <b>{r.name}</b>: " + (f"vence en {days}d ({when})" if es else f"expires in {days}d ({when})"))
    lines.append("Renuévalo en Integraciones." if es else "Renew it in Integrations.")
    text = "\n".join(lines)
    subject = "Wolf DevSecOps — token por vencer" if es else "Wolf DevSecOps — token expiring"

    ch_ok = await channels.broadcast(settings, subject, text)

    for r in to_send:
        sent[r.name] = now.isoformat()
    if marker is None:
        session.add(SystemConfigRow(key=_REMINDER_MARKER, value={"v": sent}))
    else:
        marker.value = {"v": sent}
    await session.commit()
    return {"reminded": len(to_send), "channels": ch_ok}


async def send_test(settings: Settings) -> dict:
    """Mensaje de prueba a los canales configurados (para el botón/compando de test)."""
    es = settings.notify_lang != "en"
    text = ("🐺 <b>Wolf DevSecOps</b> — prueba de notificaciones. Si ves esto, el canal funciona."
            if es else "🐺 <b>Wolf DevSecOps</b> — notifications test. If you see this, the channel works.")
    return {"channels": await channels.broadcast(settings, "Wolf DevSecOps — test", text)}
