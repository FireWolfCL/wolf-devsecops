"""Lógica de autenticación — la ÚNICA pieza que decide auth (ADR-013/014).

Porta el flujo login/MFA de `WIF/wif-server/app/api/v1/auth.py` +
`app/services/user_service.py` (record_failed_login/verify_mfa_code), podado a
single-tenant (sin org/membership). Los routers (`routes_auth`) solo TRADUCEN
estas funciones a HTTP (incl. el mapeo lockout→423); no deciden nada.

Invariantes de seguridad (ver cada función):
- Se **bindea** con la credencial: se verifica la password del usuario; NUNCA se
  compara un hash almacenado a mano (lo hace `verify_password`, A1).
- **Fallo uniforme**: usuario inexistente, inactivo o password mala dan el MISMO
  resultado (None/False) y un verify dummy iguala el tiempo (anti-enumeración; el
  router responde 401 idéntico en los tres casos).
- **Nunca** se registra password, token ni secreto (ni en audit, ni en logs).
- La sesión lleva claim `tv`: subir `token_version` invalida todos los tokens.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.security import (
    create_access_token,
    create_mfa_token,
    decrypt_mfa_secret,
    hash_password,
    needs_rehash,
    verify_and_consume_recovery_code,
    verify_password,
    verify_totp_code,
)
from ..models_db.user import User
from ..rbac.roles import Role
from ..settings import get_settings
from . import audit_service


def _utcnow() -> datetime:
    """UTC timezone-aware. (El modelo usa DateTime(timezone=True); SQLite puede
    devolver naïve al releer — `is_locked` normaliza ese caso.)"""
    return datetime.now(UTC)


# Hash dummy (argon2id) para igualar el tiempo del verify cuando el usuario no
# existe o no tiene password local: sin esto, un atacante distingue "usuario
# inexistente" (rápido) de "password mala" (lento) por timing. Perezoso y cacheado
# para no pagar el coste argon2 al importar el módulo.
_dummy_hash_cache: str | None = None


def _dummy_hash() -> str:
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = hash_password("timing-equalizer-not-a-real-password")
    return _dummy_hash_cache


# ─── Carga / estado ──────────────────────────────────────────────────────────
async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Busca por `username` (login local). Devuelve None si no existe."""
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


def is_locked(user: User) -> bool:
    """True si la cuenta está bloqueada por lockout (gate → 423). Normaliza un
    `locked_until` naïve (SQLite) como UTC para no comparar naïve con aware."""
    if user.locked_until is None:
        return False
    locked_until = user.locked_until
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=UTC)
    return locked_until > _utcnow()


def must_enroll_mfa(user: User) -> bool:
    """ADR-013 (decisión #2): MFA es obligatorio para admin. Un admin sin MFA
    activo PUEDE entrar, pero esta bandera señala al front que debe forzar el
    enrolamiento. La verificación dura (bloquear al admin sin MFA) es A6."""
    return user.role == Role.admin and not user.mfa_enabled


# ─── Password (bind) ───────────────────────────────────────────────────────────
async def authenticate(session: AsyncSession, user: User | None, password: str) -> bool:
    """Verifica la password del usuario (bind). Devuelve True solo si el usuario
    existe, está activo, tiene password local y la password coincide.

    Fail-closed y de tiempo uniforme: ante usuario None/inactivo/sin-password
    ejecuta igualmente un verify contra un hash dummy, de modo que el camino de
    fallo tarde lo mismo que el de password incorrecta (anti-enumeración).

    Si el hash está obsoleto (p. ej. un bcrypt importado), lo RE-HASHEA a argon2id
    con la password en claro recién validada y lo persiste (patrón Check4Work).
    """
    if user is None or not user.is_active or not user.hashed_password:
        await asyncio.to_thread(verify_password, password, _dummy_hash())
        return False

    # argon2 es CPU-bound (~25 ms): fuera del event loop para no congelar al resto.
    if not await asyncio.to_thread(verify_password, password, user.hashed_password):
        return False

    if needs_rehash(user.hashed_password):
        user.hashed_password = await asyncio.to_thread(hash_password, password)
        await session.commit()
    return True


# ─── Lockout ────────────────────────────────────────────────────────────────
async def record_failed_login(
    session: AsyncSession, user: User, ip: str | None = None
) -> bool:
    """Incrementa el contador de fallos; al llegar a `login_max_attempts` fija
    `locked_until` y resetea el contador. Devuelve True si ESTE intento bloqueó la
    cuenta (el llamador puede entonces alertar). Audita `user.locked` al bloquear.
    """
    settings = get_settings()
    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    locked = False
    if user.failed_login_attempts >= settings.login_max_attempts:
        user.locked_until = _utcnow() + timedelta(minutes=settings.login_lockout_minutes)
        user.failed_login_attempts = 0
        locked = True
    await session.commit()

    if locked:
        await audit_service.record(
            session,
            action="user.locked",
            result="failure",
            actor_user_id=user.id,
            actor_username=user.username,
            target_type="user",
            target_id=user.id,
            detail={"locked_until": user.locked_until.isoformat()},
            ip=ip,
        )
    return locked


async def reset_failed_login(session: AsyncSession, user: User) -> None:
    """Limpia el contador y cualquier lockout tras un login completamente exitoso."""
    if (user.failed_login_attempts or 0) > 0 or user.locked_until is not None:
        user.failed_login_attempts = 0
        user.locked_until = None
        await session.commit()


# ─── MFA (segundo factor) ──────────────────────────────────────────────────────
async def verify_mfa(session: AsyncSession, user: User, code: str) -> bool:
    """Verifica el segundo factor: acepta un código TOTP O un recovery code de un
    solo uso. Un recovery code acertado se CONSUME (se persiste la lista sin él).
    Devuelve False si no valida como ninguno de los dos."""
    if user.mfa_secret:
        try:
            secret = decrypt_mfa_secret(user.mfa_secret)
        except ValueError:
            secret = None
        if secret is not None and verify_totp_code(secret, code):
            return True

    if user.mfa_recovery_codes:
        remaining = verify_and_consume_recovery_code(code, user.mfa_recovery_codes)
        if remaining is not None:
            user.mfa_recovery_codes = remaining
            await session.commit()
            return True

    return False


# ─── Emisión de sesión ─────────────────────────────────────────────────────────
def issue_access_token(user: User) -> str:
    """Emite el access token de sesión con el `token_version` ACTUAL del usuario
    (claim `tv`), su rol y su id como `sub`."""
    return create_access_token(
        subject=user.id,
        role=user.role,
        token_version=user.token_version or 0,
    )


def issue_mfa_token(user: User) -> str:
    """Emite el token MFA efímero que solo sirve para cruzar /auth/mfa/verify."""
    return create_mfa_token(subject=user.id)


async def complete_login(
    session: AsyncSession, user: User, ip: str | None = None
) -> str:
    """Cierra un login exitoso: resetea el contador de fallos, fija `last_login_at`,
    audita `auth.login` y devuelve un access token fresco."""
    await reset_failed_login(session, user)
    user.last_login_at = _utcnow()
    await session.commit()
    await audit_service.record(
        session,
        action="auth.login",
        actor_user_id=user.id,
        actor_username=user.username,
        target_type="user",
        target_id=user.id,
        detail={"mfa": user.mfa_enabled},
        ip=ip,
    )
    return issue_access_token(user)


async def audit_login_failed(
    session: AsyncSession, username: str, ip: str | None = None
) -> None:
    """Audita `auth.login_failed`. `username` es el intentado (puede no existir);
    nunca se registra la password. `actor_user_id` queda null (no se confirma
    identidad en un fallo)."""
    await audit_service.record(
        session,
        action="auth.login_failed",
        result="failure",
        actor_username=username,
        target_type="user",
        detail={"username": username},
        ip=ip,
    )


# ─── Revocación de sesiones ────────────────────────────────────────────────────
async def bump_token_version(session: AsyncSession, user: User) -> int:
    """Sube `token_version` → invalida TODOS los tokens emitidos antes (logout-
    everywhere, reset de password, cambio de rol). Devuelve el nuevo valor.
    Es deliberadamente grueso: no hay denylist por token, solo un contador
    (ADR-013, sin tabla de sesiones)."""
    user.token_version = (user.token_version or 0) + 1
    await session.commit()
    return user.token_version
