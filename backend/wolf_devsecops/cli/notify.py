"""`wolf-devsecops notify` — despacha los eventos pendientes a Telegram/email.

`--test` envía un mensaje de prueba a los canales configurados (no toca la BD).
"""

from __future__ import annotations

import asyncio

import typer

from ..db.engine import dispose_engine, get_sessionmaker
from ..services import notification_service
from ..settings import get_settings


def notify(
    test: bool = typer.Option(False, "--test", help="Envía un mensaje de prueba a los canales."),
) -> None:
    """Envía las novedades pendientes (o un test) por Telegram/email."""

    async def _run() -> dict:
        settings = get_settings()
        if test:
            return await notification_service.send_test(settings)
        try:
            async with get_sessionmaker()() as session:
                return await notification_service.dispatch_pending(session, settings)
        finally:
            await dispose_engine()

    try:
        res = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    channels = ", ".join(res.get("channels", [])) or "ninguno (sin canales configurados)"
    if test:
        typer.secho(f"Prueba enviada a: {channels}.", fg=typer.colors.GREEN)
    else:
        typer.secho(
            f"Pendientes: {res['pending']} · enviados: {res['sent']} · canales: {channels}.",
            fg=typer.colors.GREEN,
        )
