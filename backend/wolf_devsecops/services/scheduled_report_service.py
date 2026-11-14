"""Reporte de avance PROGRAMADO (feature Pro FEATURE_REPORTS_SCHEDULED).

Cada `every_days`, arma el reporte "qué resolvimos/mejoramos" (reusa event_service)
y lo envía por los canales configurados (email/Telegram). Es un DIGEST: no adjunta el
PDF (eso es on-demand en el panel); entrega el resumen agregado. Dedup por `last_sent_at`
guardado en system_config. Sin secretos.

Config en system_config key `scheduled_report`: {enabled, every_days, last_sent_at}.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..edition import FEATURE_REPORTS_SCHEDULED, is_enabled
from ..models_db.config import SystemConfigRow
from ..settings import Settings
from . import event_service
from . import notification_channels as channels

log = logging.getLogger(__name__)

_KEY = "scheduled_report"
_DEFAULT_EVERY_DAYS = 7
_MIN_DAYS, _MAX_DAYS = 1, 90


def _clamp_days(v: object) -> int:
    try:
        return max(_MIN_DAYS, min(_MAX_DAYS, int(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_EVERY_DAYS


async def _row(session: AsyncSession) -> SystemConfigRow | None:
    return (
        await session.execute(select(SystemConfigRow).where(SystemConfigRow.key == _KEY))
    ).scalar_one_or_none()


def _config(row: SystemConfigRow | None) -> dict:
    v = dict(row.value or {}) if row else {}
    return {
        "enabled": bool(v.get("enabled", False)),
        "every_days": _clamp_days(v.get("every_days", _DEFAULT_EVERY_DAYS)),
        "last_sent_at": v.get("last_sent_at"),
    }


async def get_config(session: AsyncSession) -> dict:
    cfg = _config(await _row(session))
    cfg["available"] = is_enabled(FEATURE_REPORTS_SCHEDULED)
    return cfg


async def set_config(session: AsyncSession, *, enabled: bool, every_days: int) -> dict:
    row = await _row(session)
    prev = _config(row)
    value = {
        "enabled": bool(enabled),
        "every_days": _clamp_days(every_days),
        "last_sent_at": prev.get("last_sent_at"),  # preserva la cadencia
    }
    if row is None:
        session.add(SystemConfigRow(key=_KEY, value=value))
    else:
        row.value = value
    await session.commit()
    return await get_config(session)


def _digest_html(rep: dict, lang: str) -> str:
    es = lang != "en"
    totals = rep.get("totals", {})
    resolved = (totals.get("resolved_critical", 0) or 0) + (totals.get("resolved_high", 0) or 0)
    introduced = (totals.get("new_critical", 0) or 0) + (totals.get("new_high", 0) or 0)
    improved = totals.get("improved", 0) or 0
    days = rep.get("days")
    head = (f"🐺 <b>Wolf DevSecOps — reporte de avance ({days} días)</b>" if es
            else f"🐺 <b>Wolf DevSecOps — progress report ({days} days)</b>")
    lines = [
        head,
        (f"✅ {resolved} hallazgo(s) resuelto(s)/parchado(s)" if es
         else f"✅ {resolved} finding(s) resolved/patched"),
        (f"🔴 {introduced} hallazgo(s) nuevo(s)" if es else f"🔴 {introduced} new finding(s)"),
        (f"📈 {improved} proyecto(s) mejoraron de banda" if es
         else f"📈 {improved} project(s) improved band"),
    ]
    by_project = [p for p in rep.get("by_project", []) if (p.get("resolved_critical") or p.get("new_critical"))]
    if by_project:
        lines.append("" )
        lines.append("<b>" + ("Por proyecto" if es else "By project") + "</b>")
        for p in by_project[:10]:
            lines.append(
                f"• {p['project_key']}: "
                + (f"{p.get('resolved_critical', 0)} resueltos, {p.get('new_critical', 0)} nuevos crít."
                   if es else f"{p.get('resolved_critical', 0)} resolved, {p.get('new_critical', 0)} new crit.")
            )
    return "\n".join(lines)


async def maybe_send(session: AsyncSession, settings: Settings, *, now: datetime | None = None) -> dict:
    """Envía el digest si la feature está activa, está habilitado y toca (por cadencia).
    Devuelve un dict de estado; nunca lanza por un fallo de envío."""
    if not is_enabled(FEATURE_REPORTS_SCHEDULED):
        return {"sent": False, "skipped": "feature"}
    now = now or datetime.now(UTC)
    row = await _row(session)
    cfg = _config(row)
    if not cfg["enabled"]:
        return {"sent": False, "skipped": "disabled"}

    last = cfg.get("last_sent_at")
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < cfg["every_days"] * 86400:
                return {"sent": False, "skipped": "not_due"}
        except ValueError:
            pass

    # Sin ningún canal configurado no avanzamos la cadencia: así, al conectar un canal
    # más tarde, el primer digest sí sale (no se "consume" en silencio).
    if not channels.configured_channels(settings):
        return {"sent": False, "skipped": "no_channels"}

    rep = await event_service.report(session, cfg["every_days"])
    html = _digest_html(rep, settings.notify_lang)
    subject = ("Wolf DevSecOps — reporte de avance" if settings.notify_lang != "en"
               else "Wolf DevSecOps — progress report")
    ch_ok = await channels.broadcast(settings, subject, html)

    value = {"enabled": True, "every_days": cfg["every_days"], "last_sent_at": now.isoformat()}
    if row is None:
        session.add(SystemConfigRow(key=_KEY, value=value))
    else:
        row.value = value
    await session.commit()
    return {"sent": True, "channels": ch_ok, "days": cfg["every_days"]}
