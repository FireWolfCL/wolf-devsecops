"""`wolf-devsecops scheduler` — bucle periódico: captura postura + despacha novedades.

Cada WOLF_SNAPSHOT_INTERVAL_SECONDS: snapshot de postura (que detecta los cambios y
emite eventos) y luego dispatch de las novedades pendientes a Telegram/email.

Pensado para correr como servicio (contenedor `scheduler` del compose). `--once`
corre un solo ciclo (útil para un cron externo o para pruebas). Un fallo de un ciclo
se loguea y NO mata el bucle.
"""

from __future__ import annotations

import asyncio
import logging

import typer

from .. import edition
from ..core.aggregator import Aggregator
from ..core.cache import TTLCache
from ..db.engine import dispose_engine, get_sessionmaker
from ..services import (
    audit_service,
    config_service,
    notification_service,
    posture_history_service,
)
from ..settings import get_settings

log = logging.getLogger(__name__)


async def _cycle() -> tuple[int, dict]:
    settings = get_settings()
    sm = get_sessionmaker()
    async with sm() as session:
        config = await config_service.assemble_app_config(session)
        aggregator = Aggregator(config, TTLCache(settings.cache_ttl_seconds))
        written, _skipped = await posture_history_service.capture_snapshots(
            session, aggregator, config
        )
    async with sm() as session:
        disp = await notification_service.dispatch_pending(session, settings)
    async with sm() as session:
        rem = await notification_service.check_token_expiry(session, settings)
    disp["token_reminders"] = rem.get("reminded", 0)
    # Retención de auditoría (feature Pro): purga lo más viejo si la edición lo permite.
    if edition.is_enabled(edition.FEATURE_AUDIT_EXPORT) and settings.audit_retention_days > 0:
        async with sm() as session:
            disp["audit_purged"] = await audit_service.purge_old(
                session, settings.audit_retention_days
            )
    return written, disp


def scheduler(
    once: bool = typer.Option(False, "--once", help="Un solo ciclo y termina."),
) -> None:
    """Bucle snapshot-postura + notificación. Intervalo: WOLF_SNAPSHOT_INTERVAL_SECONDS."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(message)s")
    interval = get_settings().snapshot_interval_seconds

    async def _loop() -> None:
        try:
            while True:
                try:
                    written, disp = await _cycle()
                    log.info(
                        "ciclo OK — snapshots=%s, novedades enviadas=%s (%s)",
                        written, disp.get("sent", 0), ",".join(disp.get("channels", [])) or "sin canales",
                    )
                except Exception as exc:  # noqa: BLE001 — un ciclo roto no mata el bucle
                    log.warning("ciclo con error: %s", exc)
                if once:
                    break
                await asyncio.sleep(interval)
        finally:
            await dispose_engine()

    if not once:
        typer.secho(f"Scheduler iniciado (cada {interval}s). Ctrl+C para parar.", fg=typer.colors.GREEN)
    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        typer.secho("Scheduler detenido.", fg=typer.colors.YELLOW)
