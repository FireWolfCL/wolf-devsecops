"""Centro de Update v1 (A5, ADR-016): reportar + aplicar migraciones con salvaguardas.

Base: WIF `update_service.py`. Endurecimiento: Wolf-Cloud (reversibilidad por AST,
auto-rollback). FUERA de alcance v1 (ADR-016): self-update, artefactos firmados,
Update Agent y ejecución del redeploy. `update_command` es DISPLAY-ONLY.

Convención de tags: las releases se publican como `vX.Y.Z` (prefijo `v`). Solo
cuentan releases publicadas (no draft, no prerelease): un tag/commit empujado sin
`gh release create` es invisible aquí.

`_fetch_releases` NUNCA lanza: cualquier fallo es un estado degradado, jamás
"estás al día". La respuesta de la fuente es input no confiable: se valida la forma,
el tag contra una regex estricta y nada se interpola en shell/SQL/rutas.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..db import engine as db_engine
from ..settings import get_settings
from . import audit_service
from .migration_reversibility import (
    IrreversibleMigrationError,
    RevisionCheck,
    assert_reversible,
    has_real_downgrade,
)

logger = logging.getLogger("wolf_devsecops.update_service")

TAG_PREFIX = "v"
_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")  # estricto: sin sufijos
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.\-]+)?$")
_REPO_RE = re.compile(r"^(?!\.+/)[A-Za-z0-9_.\-]+/(?!\.+$)[A-Za-z0-9_.\-]+$")
_NOTES_MAX = 4000
_BACKEND_DIR = Path(__file__).resolve().parents[2]


class BackupRequiredError(Exception):
    """En production, aplicar migraciones exige `confirm_backup=true`."""


class MigrationFailedError(Exception):
    """`upgrade head` falló (con o sin auto-rollback exitoso)."""


# ─── Versión ─────────────────────────────────────────────────────────────────
def current_version() -> str:
    return __version__


def _parse(version: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(version.strip()) if isinstance(version, str) else None
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _is_newer(candidate: str, current: str) -> bool | None:
    """True/False si ambas parsean; None si alguna no (nunca asume "newer").

    Compara numéricamente por componente (1.10.0 > 1.9.0); ignora sufijos
    pre-release/build y el prefijo `v`."""
    a, b = _parse(candidate), _parse(current)
    if a is None or b is None:
        return None
    return a > b


# ─── Fuente de releases ──────────────────────────────────────────────────────
async def _fetch_releases() -> tuple[list[Any] | None, str | None]:
    """(releases, None) o (None, reason). NUNCA lanza."""
    s = get_settings()
    repo = s.update_release_repo.strip()
    if not repo:
        return None, "no_repo"
    if not _REPO_RE.match(repo):
        return None, "invalid_repo"
    headers = {"Accept": "application/vnd.github+json"}
    if s.update_release_token:
        headers["Authorization"] = f"Bearer {s.update_release_token}"  # nunca se loguea
    try:
        async with httpx.AsyncClient(timeout=s.update_release_timeout_seconds) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/releases",
                headers=headers,
                params={"per_page": 50},
            )
    except Exception as exc:  # noqa: BLE001 — jamás propagar
        logger.warning("update_service: release check failed: %s", type(exc).__name__)
        return None, "network_error"
    if resp.status_code != 200:
        return None, f"http_{resp.status_code}"
    try:
        payload = resp.json()
    except ValueError:
        return None, "invalid_response"
    if not isinstance(payload, list):
        return None, "invalid_response"
    return payload, None


def _str_or_none(v: Any, limit: int | None = None) -> str | None:
    if not isinstance(v, str):
        return None
    return v[:limit] if limit else v


def _latest_release(releases: list[Any]) -> dict[str, Any]:
    best: tuple[int, int, int] | None = None
    out: dict[str, Any] | None = None
    for entry in releases:
        if not isinstance(entry, dict) or entry.get("draft") or entry.get("prerelease"):
            continue
        tag = entry.get("tag_name")
        m = _TAG_RE.match(tag) if isinstance(tag, str) else None
        if not m:
            continue
        ver = (int(m[1]), int(m[2]), int(m[3]))
        if best is None or ver > best:
            best = ver
            out = {
                "available": True,
                "reason": None,
                "tag": tag,
                "version": ".".join(map(str, ver)),
                "name": _str_or_none(entry.get("name"), 200),
                "notes": _str_or_none(entry.get("body"), _NOTES_MAX) or "",
                "url": _str_or_none(entry.get("html_url"), 500),
                "published_at": _str_or_none(entry.get("published_at"), 40),
            }
    return out or {"available": False, "reason": "no_matching_release"}


async def check_latest() -> dict[str, Any]:
    releases, error = await _fetch_releases()
    if error is not None:
        return {"available": False, "reason": error}
    return _latest_release(releases or [])


# ─── Migraciones ─────────────────────────────────────────────────────────────
def build_alembic_config() -> Config:
    """Config en memoria (patrón WIF). env.py toma la URL de settings; se fija
    también aquí por coherencia."""
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return cfg


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(build_alembic_config())


def _head_revision() -> str | None:
    return _script().get_current_head()


async def _current_db_revision() -> str | None:
    """Lee `alembic_version`. Tabla ausente (BD sin migrar) => None."""
    try:
        async with db_engine.get_engine().connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.first()
    except Exception:  # noqa: BLE001 — BD sin migrar / ilegible: "sin revisión"
        return None
    return row[0] if row else None


def _count_pending(current: str | None, head: str | None) -> int:
    """-1 = pendiente pero no contable (nunca 0, que se leería como al día)."""
    if current == head:
        return 0
    try:
        return len(list(_script().iterate_revisions(head, current or "base")))
    except Exception:  # noqa: BLE001
        return -1


async def migration_status() -> dict[str, Any]:
    head = _head_revision()
    current = await _current_db_revision()
    return {
        "current": current,
        "head": head,
        "pending": _count_pending(current, head),
        "up_to_date": current == head,
    }


def pending_reversibility(current: str | None) -> list[RevisionCheck]:
    """Chequeo AST de cada migración entre `current` y head (no toca la BD)."""
    script = _script()
    head = script.get_current_head()
    if head is None or current == head:
        return []
    return [
        RevisionCheck(rev.revision, has_real_downgrade(rev.path))
        for rev in reversed(list(script.iterate_revisions(head, current or "base")))
    ]


# Serializa aplicaciones concurrentes (T-UPD-01): un segundo /migrate mientras otro
# corre recibe 409 en vez de dos `alembic upgrade` pisándose. Seguro: entre
# `.locked()` y el `async with` no hay await, así que el event loop no intercala.
_apply_lock = asyncio.Lock()
_log = logging.getLogger("wolf_devsecops.update")


class MigrationInProgressError(Exception):
    """Ya hay una migración aplicándose (concurrencia). El router lo mapea a 409."""


async def apply_migrations(*, environment: str, confirm_backup: bool) -> dict[str, Any]:
    """Pipeline de orden fijo (ADR-016 §6). La auditoría la hace el router.

    0. Lock: una sola migración a la vez (T-UPD-01) -> MigrationInProgressError.
    1. Reversibilidad (AST) -> IrreversibleMigrationError, sin tocar la BD.
    2. Gate de backup en production -> BackupRequiredError.
    3. upgrade head en thread (env.py usa asyncio.run); si falla, auto-rollback a
       `antes.current or "base"`; si el rollback falla, error nombrando ambos.
    4. Devuelve el status fresco.

    Los mensajes al cliente NO incluyen el texto crudo de la excepción subyacente
    (T-SEC-01: evita filtrar p.ej. la URL de BD en un repr); el detalle va al log.
    """
    if _apply_lock.locked():
        raise MigrationInProgressError(
            "Ya hay una migración en curso. Reintenta cuando termine."
        )
    async with _apply_lock:
        before = await migration_status()

        assert_reversible(pending_reversibility(before["current"]))  # (1)

        if environment == "production" and not confirm_backup:  # (2)
            raise BackupRequiredError(
                "En production se requiere confirmar un respaldo de la BD antes de migrar "
                "(confirm_backup=true)."
            )

        cfg = build_alembic_config()
        try:  # (3)
            await asyncio.to_thread(command.upgrade, cfg, "head")
        except Exception as exc:  # noqa: BLE001
            target = before["current"] or "base"
            _log.error("upgrade head falló", exc_info=exc)
            try:
                await asyncio.to_thread(command.downgrade, cfg, target)
            except Exception as rb_exc:  # noqa: BLE001
                _log.error("rollback a %s también falló", target, exc_info=rb_exc)
                raise MigrationFailedError(
                    f"La migración falló y el rollback automático a '{target}' también. "
                    "Requiere intervención manual con el respaldo (ver logs del servidor)."
                ) from exc
            raise MigrationFailedError(
                f"La migración falló; se revirtió automáticamente a '{target}' "
                "(ver logs del servidor)."
            ) from exc

        await db_engine.dispose_engine()  # descarta conexiones con esquema viejo cacheado
        return await migration_status()  # (4)


# ─── Composición ─────────────────────────────────────────────────────────────
async def status() -> dict[str, Any]:
    """Todo lo que el panel necesita en una llamada (solo lectura; el único I/O de
    red degrada solo)."""
    s = get_settings()
    latest = await check_latest()
    update_available = None
    if latest.get("available"):
        update_available = _is_newer(latest["version"], current_version())
    return {
        "server": {
            "current_version": current_version(),
            "latest": latest,
            "update_available": update_available,
        },
        "release_source": {
            "repo": s.update_release_repo,
            "configured": bool(s.update_release_repo),
        },
        "migrations": await migration_status(),
        "update_command": s.update_command,  # DISPLAY-ONLY
        "environment": s.environment,
    }


async def audit_migrations_applied(
    db: AsyncSession, *, actor_id: str, actor_username: str, migrations: dict[str, Any],
    result: str = "success", error: str | None = None,
) -> None:
    detail: dict[str, Any] = {"current": migrations.get("current"), "head": migrations.get("head")}
    if error:
        detail["error"] = error[:300]
    await audit_service.record(
        db, action="update.migrations_applied", result=result, actor_user_id=actor_id,
        actor_username=actor_username, target_type="update_center", detail=detail,
    )


__all__ = [
    "BackupRequiredError", "IrreversibleMigrationError", "MigrationFailedError",
    "apply_migrations", "check_latest", "current_version", "migration_status", "status",
]
