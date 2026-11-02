"""`wolf-devsecops snapshot-posture` — captura la postura ACTUAL de cada proyecto
y escribe una fila agregada por proyecto en `posture_snapshots` (ADR-019).

Solo métricas agregadas (%, banda, conteos, cobertura). Acción explícita: se
dispara tras import/connect-projects o por cron; NO en cada GET. Requiere que la
migración 0003 esté aplicada (ADR-016: el arranque no auto-migra).
"""

from __future__ import annotations

import asyncio

import typer

from ..core.aggregator import Aggregator
from ..core.cache import TTLCache
from ..db.engine import dispose_engine, get_sessionmaker
from ..services import config_service, posture_history_service
from ..settings import get_settings


async def _run() -> tuple[int, list[str]]:
    try:
        async with get_sessionmaker()() as session:
            config = await config_service.assemble_app_config(session)
            aggregator = Aggregator(config, TTLCache(get_settings().cache_ttl_seconds))
            return await posture_history_service.capture_snapshots(session, aggregator, config)
    finally:
        await dispose_engine()


def snapshot_posture() -> None:
    """Guarda un snapshot agregado de postura por proyecto (tendencia histórica)."""
    try:
        written, skipped = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    msg = f"Snapshots de postura guardados: {written}."
    if skipped:
        msg += f" Omitidos (sin datos/error): {', '.join(skipped)}."
    typer.secho(msg, fg=typer.colors.GREEN)
