"""`wolf-devsecops sync-config` — re-sincroniza config/projects.yaml → BD.

El bootstrap YAML→BD solo corre con la BD vacía (idempotencia de arranque). Este
comando fuerza el re-sync: limpia las tablas de config y re-importa el YAML. Lo
usa `deploy/connect-projects.sh` tras regenerar el YAML con los proyectos nuevos.

ADVERTENCIA: sobrescribe la config de fuentes/proyectos con la del YAML (pisa
cambios hechos a mano en el panel). El YAML es la fuente de verdad en este flujo.
EXCEPCIÓN: el token que el admin cargó por el panel (token_cipher + expiración) se
PRESERVA a través del re-sync — es propiedad del admin, igual que la config de IA;
el YAML nunca trae el valor del token (ADR-002), así que re-importarlo lo dejaría en
null. Tras correrlo, reinicia el backend para que recargue desde BD.
"""

from __future__ import annotations

import asyncio

import typer
from sqlalchemy import delete, select

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
        # El token que el admin cargó por el panel (token_cipher + su expiración) es
        # PROPIEDAD DEL ADMIN, igual que la config de IA (ver config_service.
        # import_app_config): un re-sync del YAML NO debe borrarlo. El YAML (ADR-002)
        # jamás trae el valor del token, así que al re-importar quedaría NULL y la
        # fuente caería a anónima. Lo preservamos por (tenant_id, name) y lo
        # restauramos sobre la fila recién importada. token_env viene del YAML.
        preserved = {
            (r.tenant_id, r.name): (r.token_cipher, r.token_expires_at)
            for r in (await session.execute(select(SourceConfigRow))).scalars()
            if r.token_cipher is not None
        }
        await session.execute(delete(ProjectSourceRow))
        await session.execute(delete(ProjectRow))
        await session.execute(delete(SourceConfigRow))
        await session.commit()
        await config_service.import_app_config(session, cfg)
        if preserved:
            for r in (await session.execute(select(SourceConfigRow))).scalars():
                keep = preserved.get((r.tenant_id, r.name))
                if keep is not None:
                    r.token_cipher, r.token_expires_at = keep
            await session.commit()
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
