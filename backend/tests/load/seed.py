"""Siembra una BD SQLite (o la URL dada) para la carga: schema, usuarios, config.

    WOLF_DATABASE_URL=sqlite+aiosqlite:///./load.db WOLF_JWT_SECRET=... \
    WOLF_MFA_FERNET_KEY=... python tests/load/seed.py --projects 12 \
        --upstream http://127.0.0.1:9 --password 'LoadTest#12345'

Crea 1 admin 'loadadmin' y N viewers 'viewerN' (misma password). Imprime el
Bearer del admin en la última línea (`TOKEN=...`).
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from wolf_devsecops import models_db  # noqa: E402,F401
from wolf_devsecops.auth.security import create_access_token  # noqa: E402
from wolf_devsecops.core.config import (  # noqa: E402
    AppConfig,
    DefectDojoProjectConfig,
    GitHubProjectConfig,
    ProjectConfig,
    SonarProjectConfig,
    SourceConfig,
)
from wolf_devsecops.db.base import Base  # noqa: E402
from wolf_devsecops.db.engine import dispose_engine, get_engine, get_sessionmaker  # noqa: E402
from wolf_devsecops.rbac.roles import Role  # noqa: E402
from wolf_devsecops.services import config_service, user_service  # noqa: E402


async def main(a: argparse.Namespace) -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with get_sessionmaker()() as s:
        admin = await user_service.create_user(s, username="loadadmin", password=a.password, role=Role.admin)
        for i in range(a.viewers):
            await user_service.create_user(s, username=f"viewer{i}", password=a.password, role=Role.viewer)
        cfg = AppConfig(
            sources={n: SourceConfig(url=a.upstream, token_env="LOAD_TOKEN") for n in ("github", "defectdojo", "sonarqube")},
            projects=[ProjectConfig(key=f"p{i}", name=f"Proyecto {i}",
                                    github=GitHubProjectConfig(repo=f"org/r{i}"),
                                    defectdojo=DefectDojoProjectConfig(product=f"prod{i}"),
                                    sonarqube=SonarProjectConfig(project_key=f"k{i}"))
                      for i in range(a.projects)],
        )
        await config_service.import_app_config(s, cfg)
        print("TOKEN=" + create_access_token(admin.id, admin.role, admin.token_version or 0))
    await dispose_engine()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--projects", type=int, default=12)
    p.add_argument("--viewers", type=int, default=20)
    p.add_argument("--upstream", default="http://127.0.0.1:9")
    p.add_argument("--password", default="LoadTest#12345")
    asyncio.run(main(p.parse_args()))
