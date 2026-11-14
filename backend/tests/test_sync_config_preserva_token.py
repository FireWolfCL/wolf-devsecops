"""`sync-config` preserva el token que el admin cargó por el panel.

Regresión del bug operacional: cada scan/import corre `wolf-devsecops sync-config`
(connect-projects/scan-target/analyze-sonar/import-trivy), que limpia source_config y
re-importa del YAML. El YAML (ADR-002) nunca trae el valor del token, así que el
re-import dejaba `token_cipher` en NULL y GitHub caía a anónimo (60 req/h) una y otra
vez. El fix: el token del panel es propiedad del admin (como la config de IA) y se
preserva a través del re-sync, casando por (tenant_id, name).
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import create_engine, select

from wolf_devsecops.cli import sync_config as sync_cli
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.models_db import SourceConfigRow
from wolf_devsecops.services import config_service
from wolf_devsecops.services import update_service as us
from wolf_devsecops.settings import get_settings

_YAML = """\
sources:
  github:
    api_url: https://api.github.com
    token_env: GITHUB_TOKEN
  defectdojo:
    base_url: http://defectdojo-nginx:8080
    token_env: DEFECTDOJO_TOKEN
projects:
- key: wolf-tools-kit
  name: Wolf-Tools-Kit
  org: Wolf
  github:
    repo: FireWolfCL/APO-Tools-Kit
    ci_workflow: ci.yml
  defectdojo:
    product: Wolf-Tools-Kit
"""


@pytest.fixture
def db(tmp_path):
    """BD SQLite temporal migrada a head + un YAML de config; restaura al terminar."""
    s = get_settings()
    old = (s.database_url, s.environment, s.config_path)
    path = tmp_path / "sync.db"
    s.database_url = f"sqlite+aiosqlite:///{path}"
    s.environment = "development"
    yaml_path = tmp_path / "projects.yaml"
    yaml_path.write_text(_YAML)
    s.config_path = str(yaml_path)
    asyncio.run(db_engine.dispose_engine())
    cfg = us.build_alembic_config()
    command.upgrade(cfg, "head")
    yield str(yaml_path)
    asyncio.run(db_engine.dispose_engine())
    s.database_url, s.environment, s.config_path = old


def test_sync_config_preserva_el_token_del_panel(db):
    async def scenario() -> None:
        # Arranque: importa el YAML a la BD (bootstrap) y el admin carga el token de
        # GitHub por el panel → queda token_cipher. defectdojo NO recibe token.
        from wolf_devsecops.core.config import load_config

        async with db_engine.get_sessionmaker()() as session:
            await config_service.import_app_config(session, load_config(db))
        async with db_engine.get_sessionmaker()() as session:
            row = await config_service.set_source_token(session, "github", "ghp-secreto-123")
            assert row.token_cipher is not None
            cipher_antes = row.token_cipher

        # Un scan corre sync-config (limpia + re-importa del YAML, que no trae token).
        await sync_cli._run(db)

        # El token de GitHub DEBE sobrevivir; defectdojo sigue sin token.
        async with db_engine.get_sessionmaker()() as session:
            rows = {
                r.name: r
                for r in (await session.execute(select(SourceConfigRow))).scalars()
            }
            assert rows["github"].token_cipher == cipher_antes, "sync-config borró el token"
            assert rows["defectdojo"].token_cipher is None
            # Y sigue resolviendo al valor original (no un cipher basura).
            from wolf_devsecops.auth.security import decrypt_source_token

            assert decrypt_source_token(rows["github"].token_cipher) == "ghp-secreto-123"

    asyncio.run(scenario())
