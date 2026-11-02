"""Rutas del Centro de Update v1 (/api/v1/updates/*, ADR-016).

- `version_router`: GET /version — cualquier usuario autenticado.
- `router`: GET /status, POST /migrate — SOLO admin (`updates.manage`, fail-closed).

El redeploy NO se ejecuta: `update_command` se devuelve para copiar y correr en el host.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user, require_admin_action, require_role
from ..db.session import get_db
from ..models_db.user import User
from ..rbac.roles import roles_for
from ..schemas_admin import (
    ApplyMigrationsIn,
    ApplyMigrationsOut,
    CurrentVersionOut,
    UpdateStatusOut,
)
from ..services import update_service

version_router = APIRouter(prefix="/api/v1/updates", tags=["updates"])
router = APIRouter(
    prefix="/api/v1/updates",
    tags=["updates"],
    dependencies=[Depends(require_admin_action(*roles_for("updates.manage")))],
)


@version_router.get("/version", response_model=CurrentVersionOut)
async def get_version(_user: User = Depends(get_current_user)) -> CurrentVersionOut:
    return CurrentVersionOut(current_version=update_service.current_version())


@router.get("/status", response_model=UpdateStatusOut)
async def get_status() -> UpdateStatusOut:
    return UpdateStatusOut.model_validate(await update_service.status())


@router.post("/migrate", response_model=ApplyMigrationsOut)
async def migrate(
    payload: ApplyMigrationsIn,
    admin: User = Depends(require_role(*roles_for("updates.manage"))),
    db: AsyncSession = Depends(get_db),
) -> ApplyMigrationsOut:
    from ..settings import get_settings

    try:
        migrations = await update_service.apply_migrations(
            environment=get_settings().environment, confirm_backup=payload.confirm_backup
        )
    except (update_service.IrreversibleMigrationError, update_service.BackupRequiredError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except update_service.MigrationInProgressError as exc:
        # T-UPD-01: ya hay una migración aplicándose.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except update_service.MigrationFailedError as exc:
        # Mensaje controlado (sin texto crudo de la causa, T-SEC-01); el detalle va al log.
        await update_service.audit_migrations_applied(
            db, actor_id=admin.id, actor_username=admin.username, migrations={},
            result="failure", error=str(exc),
        )
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — fallo inesperado: 500 GENÉRICO (T-SEC-01)
        await update_service.audit_migrations_applied(
            db, actor_id=admin.id, actor_username=admin.username, migrations={},
            result="failure", error=type(exc).__name__,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "La migración falló por un error inesperado. Revisa los logs del servidor.",
        ) from exc

    await update_service.audit_migrations_applied(
        db, actor_id=admin.id, actor_username=admin.username, migrations=migrations
    )
    return ApplyMigrationsOut(migrations=migrations)
