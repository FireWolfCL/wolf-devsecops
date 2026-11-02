"""Despacho de eventos a canales externos: Telegram y email (item M).

Lee los eventos pendientes (dispatched_at IS NULL) por encima del umbral de
severidad, arma UN mensaje y lo envía a los canales configurados (si el token/host
está vacío, ese canal se omite). Marca los eventos como enviados para no repetir.

Sin secretos en logs. Los tokens viven en el entorno (WOLF_TELEGRAM_*, WOLF_SMTP_*).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models_db.events import ProjectEvent
from ..settings import Settings

log = logging.getLogger(__name__)

_SEV_RANK = {"info": 0, "good": 1, "warn": 2, "danger": 3}
_ICON = {
    "new_critical": "🔴", "resolved_critical": "✅", "new_high": "🟠",
    "resolved_high": "✅", "improved": "📈", "worsened": "📉",
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
        case _:
            body = ev.kind
    return f"{icon} <b>{ev.project_key}</b> — {body}"


def build_message(events: list[ProjectEvent], lang: str) -> str:
    es = lang != "en"
    head = ("🐺 <b>Wolf DevSecOps — novedades</b>" if es
            else "🐺 <b>Wolf DevSecOps — what's new</b>")
    lines = [_line(e, lang) for e in events]
    return head + "\n" + "\n".join(lines)


async def send_telegram(settings: Settings, text: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        if r.status_code != 200:
            log.warning("telegram sendMessage HTTP %s", r.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("telegram error: %s", exc)
        return False


def _send_email_sync(settings: Settings, subject: str, body_html: str) -> bool:
    recipients = [a.strip() for a in settings.notify_email_to.split(",") if a.strip()]
    if not settings.smtp_host or not recipients:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user or "wolf-devsecops@localhost"
    msg["To"] = ", ".join(recipients)
    # Texto plano (fallback) + HTML.
    plain = body_html.replace("<b>", "").replace("</b>", "")
    msg.set_content(plain)
    msg.add_alternative(f"<html><body>{body_html.replace(chr(10), '<br>')}</body></html>", subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            if settings.smtp_starttls:
                s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("smtp error: %s", exc)
        return False


async def send_email(settings: Settings, subject: str, body_html: str) -> bool:
    return await asyncio.to_thread(_send_email_sync, settings, subject, body_html)


async def dispatch_pending(
    session: AsyncSession, settings: Settings, *, limit: int = 100
) -> dict:
    """Envía los eventos pendientes a los canales configurados y los marca.
    Devuelve conteos. Si no hay canales configurados, igual marca como enviados
    para no acumular indefinidamente (evita avalancha al configurar un canal)."""
    # Sin ningún canal configurado no marcamos nada: así, al conectar un canal más
    # tarde, las novedades recientes (hasta `limit`) sí se envían.
    has_channel = bool(settings.telegram_bot_token and settings.telegram_chat_id) or bool(
        settings.smtp_host and settings.notify_email_to
    )
    if not has_channel:
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
        if await send_telegram(settings, text):
            channels_ok.append("telegram")
        if await send_email(settings, subject, text):
            channels_ok.append("email")

    # Marca TODO lo pendiente (enviado o filtrado) para no re-evaluarlo.
    now = datetime.now(UTC)
    for e in pending:
        e.dispatched_at = now
    await session.commit()
    return {"pending": len(pending), "sent": len(to_send), "channels": channels_ok}


async def send_test(settings: Settings) -> dict:
    """Mensaje de prueba a los canales configurados (para el botón/compando de test)."""
    es = settings.notify_lang != "en"
    text = ("🐺 <b>Wolf DevSecOps</b> — prueba de notificaciones. Si ves esto, el canal funciona."
            if es else "🐺 <b>Wolf DevSecOps</b> — notifications test. If you see this, the channel works.")
    ok: list[str] = []
    if await send_telegram(settings, text):
        ok.append("telegram")
    if await send_email(settings, "Wolf DevSecOps — test", text):
        ok.append("email")
    return {"channels": ok}
