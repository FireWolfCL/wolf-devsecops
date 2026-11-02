"""`wolf-devsecops sync-config` — re-sincroniza config/projects.yaml → BD.

El bootstrap YAML→BD solo corre con la BD vacía (idempotencia de arranque). Este
comando fuerza el re-sync: limpia las tablas de config y re-importa el YAML. Lo
usa `deploy/connect-projects.sh` tras regenerar el YAML con los proyectos nuevos.

ADVERTENCIA: sobrescribe la config de fuentes/proyectos con la del YAML (pisa
cambios hechos a mano en el panel). El YAML es la fuente de verdad en este flujo.
Tras correrlo, reinicia el backend para que recargue desde BD.
"""

from __future__ import annotations

import asyncio

import typer
from sqlalchemy import delete

from ..core.config import load_config
from ..db.engine import dispose_engine, get_sessionmaker
from ..models_db.config import ProjectRow, ProjectSourceRow, SourceConfigRow
from ..services import config_service
from ..settings import get_settings


async def _run(config_path: str | None) -> tuple[int, int]:
    s = get_settings()
    path = config_path or s.config_path
    cfg = load_config(path)  # valida y rechaza secretos literales (ADR-002)
    async with get_sessionmaker()() as session:
        await session.execute(delete(ProjectSourceRow))
        await session.execute(delete(ProjectRow))
        await session.execute(delete(SourceConfigRow))
        await session.commit()
        await config_service.import_app_config(session, cfg)
    await dispose_engine()
    return len(cfg.projects), len(cfg.sources)


def sync_config(
    config_path: str = typer.Option(
        None, "--config", help="Ruta al projects.yaml (default: WOLF_CONFIG_PATH)."
    ),
) -> None:
    """Re-sincroniza projects.yaml a la BD (limpia + re-importa)."""
    try:
        projects, sources = asyncio.run(_run(config_path))
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(
        f"Config re-sincronizada: {projects} proyectos, {sources} fuentes. "
        "Reinicia el backend para que recargue.",
        fg=typer.colors.GREEN,
    )
