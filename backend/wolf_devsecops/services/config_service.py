"""Ensambla el `AppConfig` (contrato de siempre) desde la BD, con bootstrap YAML→BD
y caché invalidable (ADR-015).

Clave del diseño: `get_config()` cambia de FUENTE (BD en vez de YAML) pero no de TIPO
(sigue devolviendo el mismo `AppConfig` Pydantic). Por eso `connectors/`,
`core/aggregator.py` y `ai/` quedan intactos: reciben el contrato de siempre.

ADR-002: al escribir config se rechaza cualquier valor con pinta de secreto; a la BD
solo entra `token_env` (el NOMBRE de la variable), nunca el valor.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import (
    AIConfig,
    AppConfig,
    ConfigError,
    DefectDojoProjectConfig,
    GitHubProjectConfig,
    HarborProjectConfig,
    ProjectConfig,
    SonarProjectConfig,
    SourceConfig,
    TrivyProjectConfig,
    WSSProjectConfig,
    load_config,
)
from ..db.engine import get_sessionmaker
from ..models_db.config import (
    AISettingsRow,
    ProjectRow,
    ProjectSourceRow,
    SourceConfigRow,
    SystemConfigRow,
)
from ..settings import get_settings

# Mapea un `source` a su sub-config tipada del AppConfig (ADR-006: añadir una
# fuente nueva = añadir una entrada aquí + su Connector, sin tocar el esquema).
_SOURCE_SUBCONFIG = {
    "github": GitHubProjectConfig,
    "defectdojo": DefectDojoProjectConfig,
    "sonarqube": SonarProjectConfig,
    "wss": WSSProjectConfig,
    "harbor": HarborProjectConfig,  # imágenes de contenedor (ADR-006)
    "trivy": TrivyProjectConfig,  # reportes Trivy JSON
}

# ─── Caché de AppConfig (reemplaza el @lru_cache de get_config) ─────────────────
_cached: AppConfig | None = None


def get_cached_config() -> AppConfig:
    """Config actual (sync, hot-path). Lanza si aún no se cargó (BD no migrada)."""
    if _cached is None:
        raise ConfigError(
            "La configuración no está cargada. ¿Corriste 'alembic upgrade head' y "
            "arrancó el bootstrap? Configúrala desde el panel admin o el wizard CLI."
        )
    return _cached


def invalidate_config() -> None:
    """Marca la caché como sucia; la próxima refresh_config() la reconstruye."""
    global _cached
    _cached = None


async def refresh_config() -> AppConfig:
    """Reconstruye el AppConfig desde la BD y lo cachea."""
    global _cached
    async with get_sessionmaker()() as session:
        _cached = await assemble_app_config(session)
    return _cached


# ─── Ensamblado BD → AppConfig ──────────────────────────────────────────────────
async def assemble_app_config(session: AsyncSession) -> AppConfig:
    source_rows = (
        await session.execute(select(SourceConfigRow).where(SourceConfigRow.enabled.is_(True)))
    ).scalars().all()
    sources = {
        r.name: SourceConfig(
            url=r.url,
            token_env=r.token_env,
            # Se PORTA el ciphertext (no el texto plano) a la config en memoria: el
            # conector lo descifra al vuelo vía resolve_token, con precedencia sobre env.
            token_cipher=r.token_cipher,
            token_expires_at=r.token_expires_at,
        )
        for r in source_rows
    }

    project_rows = (
        await session.execute(select(ProjectRow).where(ProjectRow.enabled.is_(True)))
    ).scalars().all()
    projects: list[ProjectConfig] = []
    for pr in project_rows:
        ps_rows = (
            await session.execute(
                select(ProjectSourceRow).where(ProjectSourceRow.project_key == pr.key)
            )
        ).scalars().all()
        kwargs: dict = {"key": pr.key, "name": pr.name, "org": pr.org}
        for ps in ps_rows:
            model = _SOURCE_SUBCONFIG.get(ps.source)
            if model is not None:
                kwargs[ps.source] = model.model_validate(ps.settings or {})
        projects.append(ProjectConfig(**kwargs))

    ai_row = (
        await session.execute(select(AISettingsRow).where(AISettingsRow.id == 1))
    ).scalar_one_or_none()
    ai = None
    if ai_row is not None and ai_row.enabled and ai_row.model:
        ai = AIConfig(
            provider=ai_row.provider,
            model=ai_row.model,
            base_url=ai_row.base_url,
            token_env=ai_row.token_env,
        )
    return AppConfig(sources=sources, projects=projects, ai=ai)


# ─── Bootstrap YAML → BD (una vez, ADR-015) ──────────────────────────────────────
async def is_config_empty(session: AsyncSession) -> bool:
    n_sources = (await session.execute(select(func.count()).select_from(SourceConfigRow))).scalar()
    n_projects = (await session.execute(select(func.count()).select_from(ProjectRow))).scalar()
    return (n_sources or 0) == 0 and (n_projects or 0) == 0


async def import_app_config(session: AsyncSession, cfg: AppConfig) -> None:
    """Escribe un AppConfig (p. ej. el leído del YAML) a las tablas de config."""
    for name, s in cfg.sources.items():
        session.add(
            SourceConfigRow(name=name, url=s.url, token_env=s.token_env, enabled=True,
                            updated_by="system")
        )
    for p in cfg.projects:
        session.add(ProjectRow(key=p.key, name=p.name, org=p.org, enabled=True))
        for source in _SOURCE_SUBCONFIG:
            sub = getattr(p, source, None)
            if sub is not None:
                session.add(
                    ProjectSourceRow(project_key=p.key, source=source, settings=sub.model_dump())
                )
    if cfg.ai is not None:
        # La config de IA, una vez creada, es PROPIEDAD DEL ADMIN: un re-sync del
        # YAML NO la pisa (ni provider/model/base_url ni enabled). El bloque `ai`
        # del YAML es solo la SEMILLA del primer arranque. Así, si el admin apuntó
        # el proveedor a otro endpoint/modelo desde el panel, un `sync-config`
        # (p. ej. al reconectar proyectos) no se lo revierte. Se crea DESHABILITADA
        # solo si aún no existe; activar IA es decisión consciente (ADR-007/015).
        existing = await session.get(AISettingsRow, 1)
        if existing is None:
            session.add(
                AISettingsRow(id=1, provider=cfg.ai.provider, model=cfg.ai.model,
                              base_url=cfg.ai.base_url, token_env=cfg.ai.token_env, enabled=False)
            )
    await session.commit()


async def bootstrap_config_if_empty(session: AsyncSession, yaml_path: str) -> bool:
    """Si las tablas de config están vacías y existe el YAML semilla, lo importa.

    Devuelve True si importó algo. No lanza si no hay YAML: el dashboard arranca
    vacío y el admin configura desde el panel (o el wizard CLI).
    """
    if not await is_config_empty(session):
        return False
    p = Path(yaml_path)
    if not p.exists():
        return False
    cfg = load_config(p)  # loader YAML existente (valida y rechaza secretos literales)
    await import_app_config(session, cfg)
    await _set_system_flag(session, "config_bootstrapped", True)
    return True


async def _set_system_flag(session: AsyncSession, key: str, value: object) -> None:
    row = (
        await session.execute(select(SystemConfigRow).where(SystemConfigRow.key == key))
    ).scalar_one_or_none()
    if row is None:
        session.add(SystemConfigRow(key=key, value={"v": value}))
    else:
        row.value = {"v": value}
    await session.commit()


# ═══ ESCRITURA (A4, ADR-015) ═══════════════════════════════════════════════════
# Cada write: valida → persiste → commit → invalida caché → reensambla → reset del
# agregador. Así el dashboard refleja el cambio SIN reiniciar el proceso.

# Nombre de variable de entorno aceptable: MAYÚSCULAS, dígitos y '_' (convención).
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,119}$")
# Prefijos de tokens conocidos que, aun siendo "MAYÚSCULAS+dígitos", delatan un valor.
_TOKEN_PREFIXES = ("AKIA", "ASIA", "GHP_", "GHO_", "GHS_", "GITHUB_PAT_", "SK_", "XOX")
_SOURCE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class ConfigWriteError(ValueError):
    """Entrada de escritura inválida (→ 400)."""


class LiteralSecretError(ConfigWriteError):
    """`token_env` trae algo con pinta de token y no un nombre de variable (→ 400)."""


class EnvForbidsAIError(Exception):
    """El entorno prohíbe la IA (WOLF_AI_ENABLED=false): no se puede activar (→ 409)."""


def validate_token_env(token_env: str | None) -> str | None:
    """Acepta solo un NOMBRE de variable de entorno (ADR-002). Rechaza valores con
    pinta de token: minúsculas/guiones/puntos, prefijos conocidos o cadenas largas
    hex/alfanuméricas sin separador. El mensaje NUNCA repite el valor recibido."""
    if token_env is None or token_env == "":
        return None
    v = token_env.strip()
    looks_literal = (
        not _ENV_NAME_RE.match(v)
        or v.startswith(_TOKEN_PREFIXES)
        or (len(v) >= 32 and "_" not in v)
        or (len(v) >= 24 and any(c.isdigit() for c in v) and v.count("_") <= 1)
    )
    if looks_literal:
        raise LiteralSecretError(
            "token_env debe ser el NOMBRE de una variable de entorno (p. ej. GITHUB_TOKEN), "
            "no el valor del token. Los secretos nunca se guardan en la BD."
        )
    return v


# Hosts de metadata de nube (IMDS). Jamás son una fuente DevSecOps legítima y son
# el blanco SSRF de mayor valor: leer el rol de la instancia o, peor, exfiltrar un
# token de fuente adjunto en el header hacia 169.254.169.254 (rompería ADR-002). Se
# bloquean AUNQUE el actor sea admin. Deliberadamente NO se bloquean los demás rangos
# privados: el producto alcanza, por diseño, servicios internos (Harbor/Sonar/
# DefectDojo en la red del equipo). Ver docs/THREAT-MODEL-ADMIN.md (T-SSRF-01).
_BLOCKED_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog"})
_IMDS_IPV6_ALIAS = "fd00:ec2::254"


def _is_blocked_egress_host(host: str) -> bool:
    """True si `host` es un endpoint de metadata de nube (IMDS v4/v6)."""
    h = (host or "").strip().lower()
    if not h:
        return False
    if h in _BLOCKED_METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    # Link-local cubre el IMDS IPv4 169.254.169.254 (y fe80::/10); se añade el alias
    # IMDSv6 fd00:ec2::254 (unique-local, fuera de link-local).
    return ip.is_link_local or h == _IMDS_IPV6_ALIAS


def assert_url_egress_allowed(url: str) -> None:
    """Rechaza una URL cuyo host sea metadata de nube (SSRF→IMDS). Proporcional:
    NO bloquea rangos privados (el producto alcanza la red interna a propósito)."""
    if _is_blocked_egress_host(urlsplit(url).hostname or ""):
        raise ConfigWriteError(
            "La URL apunta a un endpoint de metadata de la nube (IMDS); no se permite "
            "como fuente ni como base_url."
        )


def _validate_url(url: str) -> str:
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")) or len(u) > 400:
        raise ConfigWriteError("url debe ser http(s):// y de hasta 400 caracteres")
    assert_url_egress_allowed(u)
    return u


async def _after_write(session: AsyncSession) -> None:
    """Invalida y reconstruye la caché de AppConfig desde la MISMA sesión y fuerza
    la reconstrucción del Aggregator (hot-reload). Import perezoso: evita el ciclo
    api.deps ↔ config_service."""
    global _cached
    invalidate_config()
    _cached = await assemble_app_config(session)
    from ..api.deps import reset_aggregator

    reset_aggregator()


async def upsert_source(
    session: AsyncSession,
    name: str,
    url: str,
    token_env: str,
    enabled: bool = True,
    *,
    updated_by: str | None = None,
) -> SourceConfigRow:
    """Crea o actualiza una fuente. Solo entra el NOMBRE de la variable (ADR-002)."""
    if name not in _SOURCE_SUBCONFIG:
        raise ConfigWriteError(f"Fuente desconocida: {name}")
    env_name = validate_token_env(token_env)
    if env_name is None:
        raise ConfigWriteError("token_env es obligatorio (nombre de variable de entorno)")
    url = _validate_url(url)
    row = await session.get(SourceConfigRow, name)
    if row is None:
        row = SourceConfigRow(name=name, url=url, token_env=env_name, enabled=enabled,
                              updated_by=updated_by)
        session.add(row)
    else:
        row.url, row.token_env, row.enabled, row.updated_by = url, env_name, enabled, updated_by
    await session.commit()
    await _after_write(session)
    await session.refresh(row)  # updated_at lo fija el servidor/onupdate
    return row


async def list_source_rows(session: AsyncSession) -> list[SourceConfigRow]:
    return list(
        (await session.execute(select(SourceConfigRow).order_by(SourceConfigRow.name)))
        .scalars()
        .all()
    )


# ─── Almacén cifrado del token de una fuente (0006, COMPLEMENTA ADR-002) ─────────
# El VALOR del token se guarda SOLO cifrado (Fernet, llave de reposo de la instancia).
# El texto plano nunca se persiste, nunca se devuelve y nunca se audita. Da PRECEDENCIA
# sobre `token_env` (ver core/credentials.resolve_token). Opt-in por fuente: permite
# conectar desde la UI sin editar deploy/.env, sin romper el modelo env-only existente.


async def set_source_token(
    session: AsyncSession, name: str, token_plain: str, *, updated_by: str | None = None
) -> SourceConfigRow:
    """Cifra y guarda el token de una fuente YA configurada. La fuente debe existir
    (se crea antes con `upsert_source`/Conectar). Lanza ConfigWriteError si no existe o
    si el token viene vacío. Tras escribir, reensambla la config (hot-reload): el nuevo
    token toma efecto en el próximo request sin reiniciar el proceso."""
    from ..auth.security import encrypt_source_token

    row = await session.get(SourceConfigRow, name)
    if row is None:
        raise ConfigWriteError(f"Fuente no configurada: {name}")
    token = (token_plain or "").strip()
    if not token:
        raise ConfigWriteError("El token no puede estar vacío.")
    row.token_cipher = encrypt_source_token(token)  # texto plano se descarta aquí
    row.updated_by = updated_by
    await session.commit()
    await _after_write(session)
    await session.refresh(row)
    return row


async def clear_source_token(
    session: AsyncSession, name: str, *, updated_by: str | None = None
) -> SourceConfigRow | None:
    """Borra el token cifrado de una fuente: vuelve a resolver desde la env var.
    También limpia la expiración (era del token recién borrado). None si no existe."""
    row = await session.get(SourceConfigRow, name)
    if row is None:
        return None
    row.token_cipher = None
    row.token_expires_at = None
    row.updated_by = updated_by
    await session.commit()
    await _after_write(session)
    await session.refresh(row)
    return row


async def record_source_token_expiry(
    session: AsyncSession, name: str, expires_at: datetime | None
) -> None:
    """Persiste la expiración del token de una fuente (capturada del proveedor, p. ej.
    la cabecera `github-authentication-token-expiration`). No-op si la fuente no existe o
    si la fecha no cambió. Reensambla la config para que el panel vea la fecha al vuelo."""
    row = await session.get(SourceConfigRow, name)
    if row is None or row.token_expires_at == expires_at:
        return
    row.token_expires_at = expires_at
    await session.commit()
    await _after_write(session)


async def project_exists(session: AsyncSession, key: str) -> bool:
    return (await session.get(ProjectRow, key)) is not None


async def get_project_view(session: AsyncSession, key: str) -> dict | None:
    """Proyecto + su sub-config por fuente (incluye deshabilitados). None si no existe."""
    pr = await session.get(ProjectRow, key)
    if pr is None:
        return None
    ps = (
        await session.execute(select(ProjectSourceRow).where(ProjectSourceRow.project_key == key))
    ).scalars().all()
    return {
        "key": pr.key,
        "name": pr.name,
        "enabled": pr.enabled,
        "created_at": pr.created_at,
        "sources": {r.source: dict(r.settings or {}) for r in ps},
    }


async def list_project_views(session: AsyncSession) -> list[dict]:
    keys = (await session.execute(select(ProjectRow.key).order_by(ProjectRow.key))).scalars().all()
    return [v for k in keys if (v := await get_project_view(session, k)) is not None]


async def upsert_project(
    session: AsyncSession,
    key: str,
    name: str,
    sources: dict[str, dict],
    enabled: bool = True,
    org: str | None = None,
) -> dict:
    """Crea o reemplaza un proyecto y su sub-config por fuente (reemplazo total de
    `project_sources`). Cada sub-config se valida contra su modelo tipado; los campos
    desconocidos se descartan (nunca se persiste algo fuera del contrato)."""
    clean: dict[str, dict] = {}
    for source, raw in (sources or {}).items():
        model = _SOURCE_SUBCONFIG.get(source)
        if model is None:
            raise ConfigWriteError(f"Fuente desconocida en el proyecto: {source}")
        try:
            clean[source] = model.model_validate(raw or {}).model_dump()
        except ValidationError as exc:
            raise ConfigWriteError(f"Config inválida para '{source}'") from exc

    row = await session.get(ProjectRow, key)
    if row is None:
        session.add(ProjectRow(key=key, name=name, enabled=enabled))
    else:
        row.name, row.enabled = name, enabled
        await session.execute(delete(ProjectSourceRow).where(ProjectSourceRow.project_key == key))
    await session.flush()
    for source, settings in clean.items():
        session.add(ProjectSourceRow(project_key=key, source=source, settings=settings))
    await session.commit()
    await _after_write(session)
    view = await get_project_view(session, key)
    assert view is not None
    return view


async def delete_project(session: AsyncSession, key: str) -> bool:
    """Borra un proyecto y su sub-config. False si no existía."""
    row = await session.get(ProjectRow, key)
    if row is None:
        return False
    await session.execute(delete(ProjectSourceRow).where(ProjectSourceRow.project_key == key))
    await session.delete(row)
    await session.commit()
    await _after_write(session)
    return True


# ─── IA bajo techo del entorno (ADR-007/015) ────────────────────────────────────
def env_allows_ai() -> bool:
    """Techo fail-closed: `WOLF_AI_ENABLED` del entorno."""
    return bool(get_settings().ai_enabled)


def ai_effectively_enabled(config: AppConfig | None = None) -> bool:
    """IA activa ⟺ el entorno NO la prohíbe Y ai_settings.enabled Y bloque `ai` válido.

    `assemble_app_config` solo produce `AppConfig.ai` cuando la fila está enabled y
    tiene modelo, así que `config.ai is not None` equivale a (db enabled AND bloque
    válido). Sin config cargada → False (fail-closed)."""
    if not env_allows_ai():
        return False
    if config is None:
        try:
            config = get_cached_config()
        except ConfigError:
            return False
    return config.ai is not None


async def get_ai_row(session: AsyncSession) -> AISettingsRow | None:
    return (
        await session.execute(select(AISettingsRow).where(AISettingsRow.id == 1))
    ).scalar_one_or_none()


async def set_ai_settings(
    session: AsyncSession,
    provider: str,
    model: str,
    base_url: str | None,
    token_env: str | None,
    enabled: bool,
) -> tuple[AISettingsRow, bool]:
    """Guarda la config de IA. Devuelve (fila, toggled) donde `toggled` indica si
    `enabled` cambió. Activar con el entorno prohibiéndolo → EnvForbidsAIError (409);
    desactivar y editar campos siempre se permite."""
    from ..ai.registry import AI_PROVIDER_REGISTRY

    if enabled and not env_allows_ai():
        raise EnvForbidsAIError("El entorno prohíbe la IA (WOLF_AI_ENABLED=false)")
    if provider not in AI_PROVIDER_REGISTRY:
        raise ConfigWriteError(f"Proveedor de IA desconocido: {provider}")
    env_name = validate_token_env(token_env)
    if base_url:
        base_url = _validate_url(base_url)
    if enabled and not model.strip():
        raise ConfigWriteError("Para activar la IA se requiere un modelo")

    row = await get_ai_row(session)
    previous = bool(row.enabled) if row is not None else False
    if row is None:
        row = AISettingsRow(id=1)
        session.add(row)
    row.provider, row.model = provider, model.strip()
    row.base_url, row.token_env, row.enabled = base_url or None, env_name, enabled
    await session.commit()
    await _after_write(session)
    await session.refresh(row)
    return row, previous != enabled
