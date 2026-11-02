"""App factory de FastAPI (ARCHITECTURE.md §2).

Construye la app, configura CORS y monta los routers. Sin lógica de negocio.
No carga la config al arrancar: el servidor vive aunque falte projects.yaml, y
/api/health responde igual.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import (
    routes_ai,
    routes_auth,
    routes_codemap,
    routes_dependencies,
    routes_health,
    routes_history,
    routes_projects,
    routes_remediation,
    routes_report,
    routes_settings,
    routes_sources,
    routes_updates,
    routes_users,
)
from .db.engine import dispose_engine, get_sessionmaker
from .services import config_service
from .settings import get_settings

logger = logging.getLogger("wolf_devsecops.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranque: bootstrap YAML→BD (una vez) + carga de config desde BD.

    NO auto-migra (ADR-012): el schema lo gobierna Alembic y las migraciones las
    aplica el admin desde el Centro de Update. Si la BD no está migrada, el servidor
    igual vive (/api/health responde); la config queda sin cargar y el panel/wizard
    guían al admin. Fail-open para salud, fail-closed para datos (get_config lanza).
    """
    settings = get_settings()
    try:
        async with get_sessionmaker()() as session:
            await config_service.bootstrap_config_if_empty(session, settings.config_path)
        await config_service.refresh_config()
    except Exception as exc:  # noqa: BLE001 — arranque tolerante a BD sin migrar
        logger.warning(
            "Config no cargada al arranque: %s. ¿Corriste 'alembic upgrade head'? "
            "El dashboard pedirá configurar desde el panel admin.",
            exc,
        )
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Wolf DevSecOps",
        version=__version__,
        description="Consola agregadora de seguridad de código · Community · Apache-2.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # A2 solo usa GET/POST, pero se incluyen PUT/PATCH/DELETE desde ya para que
        # el CRUD admin de A4 (settings/users) no quede bloqueado por CORS al añadirse.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    app.include_router(routes_health.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_users.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_projects.router)
    app.include_router(routes_history.router)
    app.include_router(routes_report.router)
    app.include_router(routes_dependencies.router)
    app.include_router(routes_codemap.router)
    app.include_router(routes_sources.router)
    app.include_router(routes_ai.router)
    app.include_router(routes_remediation.router)
    app.include_router(routes_updates.version_router)
    app.include_router(routes_updates.router)
    return app


app = create_app()
