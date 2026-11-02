"""Entrypoint de la CLI `wolf-devsecops` (ADR-010, F8)."""

from __future__ import annotations

import typer

from .create_admin import create_admin
from .init import run_init
from .snapshot_posture import snapshot_posture
from .sync_config import sync_config

app = typer.Typer(help="Wolf DevSecOps — consola agregadora de seguridad de código.")

# Bootstrap del primer admin (A2, ADR §13): headless, sin credencial hardcodeada.
app.command(name="create-admin")(create_admin)
# Re-sync de config/projects.yaml → BD (lo usa deploy/connect-projects.sh).
app.command(name="sync-config")(sync_config)
# Snapshot agregado de postura por proyecto (ADR-019, tendencia histórica).
app.command(name="snapshot-posture")(snapshot_posture)


@app.command()
def init(
    config_out: str = "config/projects.yaml",
    env_out: str = "deploy/.env.example",
) -> None:
    """Asistente de configuración: detecta modo, prueba fuentes y escribe la config."""
    run_init(config_out, env_out)


if __name__ == "__main__":
    app()
