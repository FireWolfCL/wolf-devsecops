"""`wolf-devsecops bot` — bot de Telegram para CONSULTAR el estatus desde el chat.

Long-polling (getUpdates). Comandos: /status, /criticos, /proyecto <key>, /help.
Solo responde en el chat configurado (WOLF_TELEGRAM_CHAT_ID) para no filtrar la
postura a terceros. Lee los datos con el mismo Aggregator que el panel.

Pensado para correr como servicio (contenedor `bot` del compose). Si no hay token,
sale con aviso (fail-safe: el canal simplemente no se levanta).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import typer

from ..core.aggregator import Aggregator
from ..core.cache import TTLCache
from ..db.engine import dispose_engine, get_sessionmaker
from ..services import config_service
from ..settings import get_settings

log = logging.getLogger(__name__)


async def _aggregator() -> Aggregator:
    settings = get_settings()
    async with get_sessionmaker()() as session:
        config = await config_service.assemble_app_config(session)
    return Aggregator(config, TTLCache(settings.cache_ttl_seconds))


async def _status_text(es: bool) -> str:
    agg = await _aggregator()
    ov = await agg.global_posture()
    out = ["🐺 <b>Wolf DevSecOps</b>"]
    for g in ov.orgs:
        org = g.org or ("Todas" if es else "All")
        score = f"{g.global_score}%" if g.global_score is not None else "—"
        crit = len(g.critical)
        risk = len(g.at_risk)
        if es:
            out.append(f"• <b>{org}</b>: {score} ({g.band}) · {g.project_count} proyectos · {crit} críticos · {risk} en riesgo")
        else:
            out.append(f"• <b>{org}</b>: {score} ({g.band}) · {g.project_count} projects · {crit} critical · {risk} at risk")
    return "\n".join(out)


async def _criticos_text(es: bool) -> str:
    agg = await _aggregator()
    items = await agg.list_projects()
    bad = [p for p in items if p.posture_band in ("critico", "en_riesgo")]
    bad.sort(key=lambda p: (p.posture_band != "critico", p.posture_score or 999))
    if not bad:
        return "✅ " + ("Sin proyectos en riesgo." if es else "No projects at risk.")
    head = "🔴 " + ("Proyectos que necesitan atención:" if es else "Projects needing attention:")
    lines = [head]
    for p in bad[:20]:
        sc = f"{p.posture_score}%" if p.posture_score is not None else "—"
        lines.append(f"• <b>{p.name}</b> — {sc} ({p.posture_band})")
    return "\n".join(lines)


async def _project_text(key: str, es: bool) -> str:
    agg = await _aggregator()
    items = await agg.list_projects()
    p = next((x for x in items if x.key == key or x.name.lower() == key.lower()), None)
    if p is None:
        return ("No encontré ese proyecto." if es else "Project not found.")
    sc = f"{p.posture_score}%" if p.posture_score is not None else "—"
    f = p.findings
    cf = p.container_findings
    def tot(x):  # noqa: ANN001
        return 0 if x is None else x.critical + x.high + x.medium + x.low
    return (
        f"🐺 <b>{p.name}</b> — {sc} ({p.posture_band})\n"
        + (f"Hallazgos: {tot(f)} · Contenedores: {tot(cf)}" if es
           else f"Findings: {tot(f)} · Containers: {tot(cf)}")
    )


def _help_text(es: bool) -> str:
    if es:
        return ("Comandos:\n/status — postura global\n/criticos — proyectos en riesgo\n"
                "/proyecto &lt;key&gt; — un proyecto\n/help — esta ayuda")
    return ("Commands:\n/status — global posture\n/criticos — projects at risk\n"
            "/proyecto &lt;key&gt; — one project\n/help — this help")


async def _handle(text: str, es: bool) -> str:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/").split("@")[0]  # soporta /status@MiBot
    arg = parts[1] if len(parts) > 1 else ""
    if cmd in ("status", "estado"):
        return await _status_text(es)
    if cmd in ("criticos", "crit", "criticals"):
        return await _criticos_text(es)
    if cmd in ("proyecto", "project", "p"):
        return await _project_text(arg.strip(), es) if arg else ("Uso: /proyecto <key>" if es else "Usage: /proyecto <key>")
    return _help_text(es)


async def _send(client: httpx.AsyncClient, token: str, chat_id: str | int, text: str) -> None:
    try:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        )
    except httpx.HTTPError as exc:
        log.warning("send error: %s", exc)


def bot() -> None:
    """Bot de Telegram de estatus (long-polling). Requiere WOLF_TELEGRAM_BOT_TOKEN."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [bot] %(message)s")
    settings = get_settings()
    token = settings.telegram_bot_token
    allowed = str(settings.telegram_chat_id) if settings.telegram_chat_id else ""
    es = settings.notify_lang != "en"
    if not token:
        typer.secho("Falta WOLF_TELEGRAM_BOT_TOKEN; el bot no se levanta.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    async def _loop() -> None:
        offset: int | None = None
        typer.secho("Bot de estatus iniciado (long-polling).", fg=typer.colors.GREEN)
        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                while True:
                    try:
                        r = await client.get(
                            f"https://api.telegram.org/bot{token}/getUpdates",
                            params={"timeout": 30, "offset": offset},
                        )
                        updates = r.json().get("result", [])
                    except (httpx.HTTPError, ValueError) as exc:
                        log.warning("getUpdates error: %s", exc)
                        await asyncio.sleep(3)
                        continue
                    for upd in updates:
                        offset = upd["update_id"] + 1
                        msg = upd.get("message") or upd.get("channel_post") or {}
                        chat = msg.get("chat", {}).get("id")
                        text = msg.get("text", "")
                        if not text or chat is None:
                            continue
                        if allowed and str(chat) != allowed:
                            continue  # ignora chats no autorizados
                        try:
                            reply = await _handle(text, es)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("handle error: %s", exc)
                            reply = "⚠️ Error al consultar el estatus." if es else "⚠️ Error fetching status."
                        await _send(client, token, chat, reply)
        finally:
            await dispose_engine()

    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        typer.secho("Bot detenido.", fg=typer.colors.YELLOW)
