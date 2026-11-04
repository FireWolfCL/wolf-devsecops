"""Entrypoint de la CLI `wolf-devsecops` (ADR-010, F8)."""

from __future__ import annotations

import typer

from .bot import bot
from .create_admin import create_admin
from .gen_ignore import gen_ignore
from .import_org import import_org
from .init import run_init
from .issue_license import issue_license
from .notify import notify
from .scheduler import scheduler
from .snapshot_posture import snapshot_posture
from .sync_config import sync_config

app = typer.Typer(help="Wolf DevSecOps — consola agregadora de seguridad de código.")

# Bootstrap del primer admin (A2, ADR §13): headless, sin credencial hardcodeada.
app.command(name="create-admin")(create_admin)
# Re-sync de config/projects.yaml → BD (lo usa deploy/connect-projects.sh).
app.command(name="sync-config")(sync_config)
# Snapshot agregado de postura por proyecto (ADR-019, tendencia histórica).
app.command(name="snapshot-posture")(snapshot_posture)
# Notificaciones (item M): dispatch de novedades a Telegram/email (y --test).
app.command(name="notify")(notify)
# Bucle periódico: snapshot + notify cada WOLF_SNAPSHOT_INTERVAL_SECONDS.
app.command(name="scheduler")(scheduler)
# Bot de Telegram para consultar el estatus desde el chat.
app.command(name="bot")(bot)
# Alta masiva de los repos ACTIVOS de una organización GitHub.
app.command(name="import-org")(import_org)
# Emisión de licencias firmadas (lado WLA; requiere la llave privada).
app.command(name="issue-license")(issue_license)
# Genera .trivyignore/.gitleaksignore desde config/wolf-ignore.yaml (6.3.c).
app.command(name="gen-ignore")(gen_ignore)


@app.command()
def init(
    config_out: str = "config/projects.yaml",
    env_out: str = "deploy/.env.example",
) -> None:
    """Asistente de configuración: detecta modo, prueba fuentes y escribe la config."""
    run_init(config_out, env_out)


if __name__ == "__main__":
    app()
