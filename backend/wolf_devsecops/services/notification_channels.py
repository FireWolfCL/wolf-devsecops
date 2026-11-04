"""Seam `NotificationChannel` + registry (MEJORAS 2.5, ARCHITECTURE §8).

Un canal de salida de alertas es una clase con `is_configured(settings)` y
`send(settings, subject, text_html)`. El despacho (notification_service) itera el
registry de canales CONFIGURADOS, sin saber de Telegram ni SMTP. Añadir Slack,
Teams o un webhook es registrar una clase más, sin tocar el despacho.

Invariantes: los tokens viven en el entorno (settings), nunca en logs; un canal
que falla devuelve False y no aborta a los demás. El envío externo es feature Pro
(lo gatea notification_service con FEATURE_NOTIFICATIONS); los canales en sí no
conocen la edición.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

import httpx

from ..settings import Settings

log = logging.getLogger(__name__)


class NotificationChannel(ABC):
    """Un destino de alertas (Telegram, email, …). Stateless: recibe `settings`."""

    name: str = "channel"

    @abstractmethod
    def is_configured(self, settings: Settings) -> bool:
        """¿Tiene lo mínimo (token/host/destinatario) para enviar?"""

    @abstractmethod
    async def send(self, settings: Settings, subject: str, text_html: str) -> bool:
        """Envía. Devuelve True si salió; nunca lanza (captura y loguea sin secretos)."""


class TelegramChannel(NotificationChannel):
    name = "telegram"

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.telegram_bot_token and settings.telegram_chat_id)

    async def send(self, settings: Settings, subject: str, text_html: str) -> bool:
        if not self.is_configured(settings):
            return False
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(url, json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text_html,
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


class EmailChannel(NotificationChannel):
    name = "email"

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.smtp_host and settings.notify_email_to)

    def _send_sync(self, settings: Settings, subject: str, body_html: str) -> bool:
        recipients = [a.strip() for a in settings.notify_email_to.split(",") if a.strip()]
        if not settings.smtp_host or not recipients:
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from or settings.smtp_user or "wolf-devsecops@localhost"
        msg["To"] = ", ".join(recipients)
        plain = body_html.replace("<b>", "").replace("</b>", "")
        msg.set_content(plain)
        msg.add_alternative(
            f"<html><body>{body_html.replace(chr(10), '<br>')}</body></html>", subtype="html"
        )
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

    async def send(self, settings: Settings, subject: str, text_html: str) -> bool:
        if not self.is_configured(settings):
            return False
        return await asyncio.to_thread(self._send_sync, settings, subject, text_html)


# Registry. Añadir un canal = una clase más aquí (o via register()).
_REGISTRY: list[NotificationChannel] = [TelegramChannel(), EmailChannel()]


def all_channels() -> list[NotificationChannel]:
    return list(_REGISTRY)


def configured_channels(settings: Settings) -> list[NotificationChannel]:
    return [c for c in _REGISTRY if c.is_configured(settings)]


def register(channel: NotificationChannel) -> None:
    """Registra un canal nuevo (p. ej. Slack/webhook) sin tocar el despacho."""
    _REGISTRY.append(channel)


async def broadcast(settings: Settings, subject: str, text_html: str) -> list[str]:
    """Envía a todos los canales configurados; devuelve los nombres que salieron OK."""
    ok: list[str] = []
    for ch in configured_channels(settings):
        if await ch.send(settings, subject, text_html):
            ok.append(ch.name)
    return ok
