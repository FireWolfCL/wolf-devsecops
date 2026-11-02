"""A0 — persistencia (ADR-012/015): round-trip config BD↔AppConfig + invariantes.

No usa Alembic (eso se prueba en CI con upgrade/downgrade); crea las tablas con
`Base.metadata.create_all` para ejercitar la LÓGICA de config_service.
"""

import pytest

from wolf_devsecops.core.config import (
    AIConfig,
    AppConfig,
    GitHubProjectConfig,
    ProjectConfig,
    SourceConfig,
)
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.services import config_service
from wolf_devsecops.settings import get_settings


@pytest.fixture
async def session(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/a0.db"
    await db_engine.dispose_engine()  # descarta engine previo con otra URL
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        yield s
    await db_engine.dispose_engine()
    config_service.invalidate_config()


def _sample() -> AppConfig:
    return AppConfig(
        sources={"github": SourceConfig(url="https://api.github.com", token_env="GITHUB_TOKEN")},
        projects=[ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))],
        ai=AIConfig(provider="local", model="llama3", base_url="http://x/v1"),
    )


async def test_import_assemble_roundtrip(session):
    assert await config_service.is_config_empty(session)
    await config_service.import_app_config(session, _sample())
    assert not await config_service.is_config_empty(session)

    out = await config_service.assemble_app_config(session)
    assert set(out.sources) == {"github"}
    assert out.sources["github"].token_env == "GITHUB_TOKEN"  # ADR-002: solo el NOMBRE
    assert out.project("p").github.repo == "o/r"
    # ADR-007: la IA se importa DESHABILITADA (no se auto-activa).
    assert out.ai is None


async def test_ai_sale_cuando_se_habilita_en_bd(session):
    """Si el admin habilita la fila ai_settings, assemble la expone."""
    from sqlalchemy import select

    from wolf_devsecops.models_db.config import AISettingsRow

    await config_service.import_app_config(session, _sample())
    row = (await session.execute(select(AISettingsRow))).scalar_one()
    row.enabled = True
    await session.commit()

    out = await config_service.assemble_app_config(session)
    assert out.ai is not None and out.ai.model == "llama3"
