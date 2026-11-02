"""Rutas de configuración de instancia (/api/v1/settings/*) — SOLO admin (ADR-014/015).

Todo el router va bajo `require_role(*roles_for("settings.manage"))` (401 sin token,
403 si no es admin, fail-closed). Los handlers solo traducen HTTP<->`config_service`;
cada escritura invalida la caché de AppConfig y reinicia el Aggregator (hot-reload),
así el dashboard refleja el cambio sin reiniciar el proceso.

Separación config/secreto (patrón WIF settings.py, ADR-002): la API acepta y devuelve
el NOMBRE de la variable de entorno (`token_env`) y un booleano `token_present`
resuelto en el servidor; el VALOR del token jamás entra ni sale por aquí.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import require_admin_action, require_role
from ..connectors.registry import CONNECTOR_REGISTRY
from ..core.config import SourceConfig
from ..core.credentials import resolve_token
from ..db.session import get_db
from ..models import ProbeResult
from ..models_db.config import AISettingsRow, SourceConfigRow
from ..models_db.user import User
from ..rbac.roles import roles_for
from ..schemas_admin import (
    AISettingsIn,
    AISettingsOut,
    ProjectIn,
    ProjectOut,
    SourceIn,
    SourceOut,
    SourceTokenIn,
    SourceTokenStatusOut,
)
from ..services import audit_service, config_service

# Umbral del aviso "vence pronto" (item 6). El envío de la notificación es del scheduler.
_EXPIRING_SOON = timedelta(days=7)


def _is_expiring_soon(expires_at: datetime | None) -> bool:
    """True si `expires_at` cae dentro de los próximos 7 días. Normaliza fechas naive
    (SQLite las devuelve sin zona) asumiendo UTC."""
    if expires_at is None:
        return False
    exp = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
    return exp - datetime.now(UTC) <= _EXPIRING_SOON


def _token_status(row: SourceConfigRow) -> tuple[bool, str]:
    """(token_present, token_source) de una fuente. `token_source`: 'db' si hay token
    cifrado válido en BD (tiene precedencia), 'env' si solo está la variable de entorno,
    'none' si no hay token por ninguna vía. Nunca expone el valor."""
    db_present = bool(row.token_cipher) and resolve_token(None, row.token_cipher) is not None
    if db_present:
        return True, "db"
    if resolve_token(row.token_env) is not None:
        return True, "env"
    return False, "none"

router = APIRouter(
    prefix="/api/v1/settings",
    tags=["settings"],
    # require_admin_action = admin (settings.manage) + MFA obligatorio (T-AUTH-04).
    dependencies=[Depends(require_admin_action(*roles_for("settings.manage")))],
)
_admin = Depends(require_role(*roles_for("settings.manage")))


def _source_out(row: SourceConfigRow) -> SourceOut:
    present, source = _token_status(row)
    return SourceOut(
        name=row.name,
        url=row.url,
        token_env=row.token_env,
        enabled=row.enabled,
        token_present=present,
        token_source=source,
        token_expires_at=row.token_expires_at,
        token_expiring_soon=_is_expiring_soon(row.token_expires_at),
        updated_at=row.updated_at,
    )


def _token_status_out(row: SourceConfigRow) -> SourceTokenStatusOut:
    present, source = _token_status(row)
    return SourceTokenStatusOut(
        name=row.name,
        token_present=present,
        token_source=source,
        token_expires_at=row.token_expires_at,
        token_expiring_soon=_is_expiring_soon(row.token_expires_at),
    )


def _ai_out(row: AISettingsRow | None) -> AISettingsOut:
    from ..services.config_service import ai_effectively_enabled, env_allows_ai

    if row is None:
        return AISettingsOut(
            provider="local", model="", enabled=False, env_allows_ai=env_allows_ai(),
            effective_enabled=False,
        )
    return AISettingsOut(
        provider=row.provider,
        model=row.model,
        base_url=row.base_url,
        token_env=row.token_env,
        token_present=resolve_token(row.token_env) is not None,
        enabled=row.enabled,
        env_allows_ai=env_allows_ai(),
        effective_enabled=ai_effectively_enabled(),
    )


async def _audit(db: AsyncSession, admin: User, action: str, target_type: str,
                 target_id: str, detail: dict | None = None, result: str = "success") -> None:
    await audit_service.record(
        db, action=action, result=result, actor_user_id=admin.id, actor_username=admin.username,
        target_type=target_type, target_id=target_id, detail=detail,
    )


# ─── Fuentes ───────────────────────────────────────────────────────────────────
@router.get("/sources", response_model=list[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)) -> list[SourceOut]:
    return [_source_out(r) for r in await config_service.list_source_rows(db)]


@router.put("/sources/{name}", response_model=SourceOut)
async def put_source(
    name: str,
    payload: SourceIn,
    admin: User = _admin,
    db: AsyncSession = Depends(get_db),
) -> SourceOut:
    try:
        row = await config_service.upsert_source(
            db, name, payload.url, payload.token_env, payload.enabled,
            updated_by=admin.username,
        )
    except config_service.ConfigWriteError as exc:  # incluye LiteralSecretError
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # El detalle lleva el NOMBRE de la variable, nunca un valor.
    await _audit(db, admin, "config.source_updated", "source", name,
                 {"enabled": row.enabled, "token_env": row.token_env})
    return _source_out(row)


@router.put("/sources/{name}/token", response_model=SourceTokenStatusOut)
async def set_source_token(
    name: str,
    payload: SourceTokenIn,
    admin: User = _admin,
    db: AsyncSession = Depends(get_db),
) -> SourceTokenStatusOut:
    """Guarda el VALOR del token de una fuente CIFRADO en reposo (Fernet), con
    precedencia sobre la env var. Admin + MFA (router). COMPLEMENTA ADR-002: no
    reemplaza el modelo env-only; lo añade como opción opt-in para conectar sin editar
    .env. El texto plano no se persiste, no se devuelve ni se audita: la respuesta solo
    confirma `token_present`, y la auditoría registra la acción SIN el valor."""
    try:
        row = await config_service.set_source_token(
            db, name, payload.token, updated_by=admin.username
        )
    except config_service.ConfigWriteError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # Auditoría SIN el token: solo que se fijó uno cifrado para esta fuente.
    await _audit(db, admin, "config.source_token_set", "source", name,
                 {"token_source": "db"})
    return _token_status_out(row)


@router.delete("/sources/{name}/token", response_model=SourceTokenStatusOut)
async def clear_source_token(
    name: str, admin: User = _admin, db: AsyncSession = Depends(get_db)
) -> SourceTokenStatusOut:
    """Borra el token cifrado de una fuente: vuelve a resolver desde la variable de
    entorno (modelo original). Admin + MFA. Nunca expone el valor."""
    row = await config_service.clear_source_token(db, name, updated_by=admin.username)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Fuente no configurada: {name}")
    await _audit(db, admin, "config.source_token_cleared", "source", name,
                 {"token_source": _token_status(row)[1]})
    return _token_status_out(row)


@router.get("/available-sources")
async def available_sources(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Catálogo de integraciones: toda fuente que el producto sabe conectar
    (CONNECTOR_REGISTRY) + si ya está configurada y si su token está presente.
    La página de 'Conectar' lo usa para mostrar todas las opciones de un vistazo."""
    rows = {r.name: r for r in await config_service.list_source_rows(db)}
    out = []
    for name in CONNECTOR_REGISTRY:
        row = rows.get(name)
        present, source = _token_status(row) if row else (False, "none")
        out.append({
            "name": name,
            "configured": row is not None,
            "enabled": bool(row.enabled) if row else False,
            "token_present": present,
            "token_source": source,  # db | env | none
            "token_expires_at": row.token_expires_at if row else None,
            "token_expiring_soon": _is_expiring_soon(row.token_expires_at) if row else False,
            "supported_versions": CONNECTOR_REGISTRY[name].supported_versions,
        })
    return out


@router.post("/sources/{name}/test", response_model=ProbeResult)
async def test_source(
    name: str, admin: User = _admin, db: AsyncSession = Depends(get_db)
) -> ProbeResult:
    """Prueba la conexión a una fuente YA configurada: corre `Connector.probe()`
    (salud + autenticación + versión soportada). No lanza por fallo de la fuente —
    devuelve un ProbeResult con reachable/authenticated/compatible, que la UI pinta
    como semáforo. El token se resuelve server-side; nunca viaja al navegador.

    Nota de seguridad: el server hace una petición saliente a la URL configurada por
    el admin (superficie SSRF acotada a admin). Revisión: wolf-threat-hardening.
    """
    if name not in CONNECTOR_REGISTRY:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Fuente desconocida: {name}")
    row = (
        await db.execute(select(SourceConfigRow).where(SourceConfigRow.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Fuente no configurada: {name}")

    # Defensa SSRF (T-SSRF-01): aunque el write-path ya valida, una fila sembrada por
    # bootstrap/YAML podría apuntar a IMDS. Se revalida antes de hacer la petición
    # saliente para que el botón "probar" no sea un canal a la metadata de la nube.
    try:
        config_service.assert_url_egress_allowed(row.url)
    except config_service.ConfigWriteError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    connector = CONNECTOR_REGISTRY[name](
        SourceConfig(url=row.url, token_env=row.token_env, token_cipher=row.token_cipher),
        resolve_token(row.token_env, row.token_cipher),  # BD cifrada > env
    )
    result = await connector.probe()
    # Si el conector capturó la expiración del token (p. ej. GitHub la anuncia en la
    # cabecera), se persiste para que el panel y el recordatorio sepan cuándo vence.
    expiry = getattr(connector, "last_token_expiry", None)
    if expiry is not None:
        await config_service.record_source_token_expiry(db, name, expiry)
    await _audit(db, admin, "config.source_tested", "source", name,
                 {"reachable": result.reachable, "authenticated": result.authenticated,
                  "compatible": result.compatible})
    return result


# ─── Proyectos ─────────────────────────────────────────────────────────────────
@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(db: AsyncSession = Depends(get_db)) -> list[ProjectOut]:
    return [ProjectOut(**v) for v in await config_service.list_project_views(db)]


@router.get("/projects/{key}", response_model=ProjectOut)
async def get_project(key: str, db: AsyncSession = Depends(get_db)) -> ProjectOut:
    view = await config_service.get_project_view(db, key)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proyecto no encontrado")
    return ProjectOut(**view)


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectIn, admin: User = _admin, db: AsyncSession = Depends(get_db)
) -> ProjectOut:
    if not payload.key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "key es obligatorio al crear")
    if await config_service.project_exists(db, payload.key):
        raise HTTPException(status.HTTP_409_CONFLICT, "El proyecto ya existe")
    try:
        view = await config_service.upsert_project(
            db, payload.key, payload.name, payload.sources, payload.enabled
        )
    except config_service.ConfigWriteError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _audit(db, admin, "config.project_updated", "project", payload.key,
                 {"op": "create", "sources": sorted(view["sources"])})
    return ProjectOut(**view)


@router.put("/projects/{key}", response_model=ProjectOut)
async def update_project(
    key: str, payload: ProjectIn, admin: User = _admin, db: AsyncSession = Depends(get_db)
) -> ProjectOut:
    if payload.key is not None and payload.key != key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "key del cuerpo no coincide con la ruta")
    if not await config_service.project_exists(db, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proyecto no encontrado")
    try:
        view = await config_service.upsert_project(
            db, key, payload.name, payload.sources, payload.enabled
        )
    except config_service.ConfigWriteError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _audit(db, admin, "config.project_updated", "project", key,
                 {"op": "update", "sources": sorted(view["sources"])})
    return ProjectOut(**view)


@router.delete("/projects/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    key: str, admin: User = _admin, db: AsyncSession = Depends(get_db)
) -> None:
    if not await config_service.delete_project(db, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proyecto no encontrado")
    await _audit(db, admin, "config.project_updated", "project", key, {"op": "delete"})


# ─── IA (bajo el techo del entorno, ADR-007/015) ───────────────────────────────
@router.get("/ai", response_model=AISettingsOut)
async def get_ai(db: AsyncSession = Depends(get_db)) -> AISettingsOut:
    return _ai_out(await config_service.get_ai_row(db))


@router.put("/ai", response_model=AISettingsOut)
async def put_ai(
    payload: AISettingsIn, admin: User = _admin, db: AsyncSession = Depends(get_db)
) -> AISettingsOut:
    try:
        row, toggled = await config_service.set_ai_settings(
            db, payload.provider, payload.model, payload.base_url, payload.token_env,
            payload.enabled,
        )
    except config_service.EnvForbidsAIError as exc:
        # Techo fail-closed: el entorno prohíbe la IA; el admin no puede activarla.
        await _audit(db, admin, "config.ai_toggled", "ai", "ai",
                     {"enabled": True, "blocked_by": "env"}, result="denied")
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except config_service.ConfigWriteError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _audit(
        db, admin, "config.ai_toggled" if toggled else "config.ai_updated", "ai", "ai",
        {"enabled": row.enabled, "provider": row.provider, "model": row.model},
    )
    return _ai_out(row)
