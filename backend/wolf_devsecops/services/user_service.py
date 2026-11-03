"""CRUD de usuarios + ciclo de vida de MFA + reset/unlock + self-service.

Porta la lógica de `WIF/wif-server/app/api/v1/users.py` +
`app/services/user_service.py`, podada a single-tenant y login por `username`.

A2 construye TODA la lógica de dominio aquí para que sea usable por A4 (CRUD admin
de /users) sin reescribirla; A2 solo MONTA las rutas self-service (/users/me/*).
Las rutas admin (list/create/update/reset/unlock) las cablea A4 con
`require_role(admin)` (A3). Por eso estas funciones NO deciden autorización: eso es
del guard del router. La ÚNICA regla de política que vive aquí es el guard
anti-autobloqueo de `update_user` (depende del actor, no del rol).
"""

from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.security import (
    decrypt_mfa_secret,
    encrypt_mfa_secret,
    generate_qr_code_svg,
    generate_recovery_codes,
    generate_totp_secret,
    get_totp_uri,
    hash_password,
    hash_recovery_code,
    verify_password,
    verify_totp_code,
)
from ..models_db.user import User
from ..rbac.roles import Role
from ..settings import get_settings


class UserServiceError(ValueError):
    """Error de dominio traducible a 4xx por el router (p. ej. 400/409)."""


class DuplicateUserError(UserServiceError):
    """username/email ya en uso (→ 409)."""


class SelfLockoutError(UserServiceError):
    """Un admin intenta quitarse su propio rol o desactivarse (→ 400)."""


class LastAdminError(UserServiceError):
    """La operación dejaría la instancia sin ningún admin activo (→ 400)."""


def _generate_temporary_password() -> str:
    """Password temporal de un solo uso para reset admin. Alta entropía; se
    devuelve en claro UNA vez y se guarda solo su hash argon2id. No es una
    primitiva cripto (es un random url-safe): por eso vive aquí, no en A1."""
    return secrets.token_urlsafe(12)


# ─── CRUD (usable por A4) ──────────────────────────────────────────────────────
async def list_users(session: AsyncSession) -> tuple[list[User], int]:
    total = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    rows = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    return list(rows), total


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def count_users(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one()


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str | None,
    role: Role = Role.viewer,
    email: str | None = None,
    full_name: str | None = None,
    must_change_password: bool = False,
    auth_source: str = "local",
) -> User:
    """Crea un usuario. `auth_source="local"` → password hasheada (argon2id) obligatoria.
    `auth_source="ldap"` → cáscara federada sin password local (el bind va contra el
    directorio); `password` se ignora y `must_change_password` no aplica. Lanza
    DuplicateUserError si el username/email ya existe (el router lo mapea a 409)."""
    is_ldap = auth_source == "ldap"
    if not is_ldap and not password:
        raise ValueError("Una cuenta local requiere password.")
    user = User(
        username=username,
        email=email,
        hashed_password=None if is_ldap else hash_password(password),
        full_name=full_name,
        role=role,
        auth_source=auth_source,
        must_change_password=False if is_ldap else must_change_password,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateUserError("username o email ya en uso") from exc
    await session.commit()
    return user


async def update_user(
    session: AsyncSession,
    user_id: str,
    *,
    acting_user_id: str,
    full_name: str | None = None,
    role: Role | None = None,
    is_active: bool | None = None,
) -> User | None:
    """Actualiza campos administrables de un usuario. Devuelve None si no existe.

    Guard anti-autobloqueo (ADR §13): el actor NO puede quitarse su propio rol ni
    desactivarse a sí mismo — así un admin no puede dejar la instancia sin ningún
    admin por error. Lanza SelfLockoutError (→ 400)."""
    if user_id == acting_user_id and (
        (role is not None and role != Role.admin) or is_active is False
    ):
        raise SelfLockoutError("no puedes cambiar tu propio rol ni desactivarte")

    user = await session.get(User, user_id)
    if user is None:
        return None

    # Guard ÚLTIMO-ADMIN (defensa en profundidad, independiente del actor): no se puede
    # degradar ni desactivar al último admin activo, aunque quien lo pida no sea el
    # propio afectado (p. ej. un actor ya desactivado en paralelo).
    removes_admin = (role is not None and role != Role.admin) or is_active is False
    if user.role == Role.admin and user.is_active and removes_admin:
        other_admins = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role == Role.admin, User.is_active.is_(True), User.id != user.id)
            )
        ).scalar_one()
        if other_admins == 0:
            raise LastAdminError("no se puede degradar ni desactivar al último admin activo")

    if full_name is not None:
        user.full_name = full_name
    # Cambio efectivo de rol o de is_active => sube token_version EN LA MISMA transacción
    # (ADR-013): el cambio surte efecto y todas las sesiones previas del usuario caen.
    # (Equivale a auth_service.bump_token_version, pero atómico con el update; no se
    # importa auth_service aquí porque él ya depende de user_service.)
    session_invalidating = False
    if role is not None and role != user.role:
        user.role = role
        session_invalidating = True
    if is_active is not None and is_active != user.is_active:
        user.is_active = is_active
        session_invalidating = True
    if session_invalidating:
        user.token_version = (user.token_version or 0) + 1
    await session.commit()
    return user


async def reset_password(
    session: AsyncSession, user_id: str, new_password: str | None = None
) -> tuple[User, str] | None:
    """Reset admin de la password de OTRO usuario. Si no se da `new_password`,
    genera una temporal. Fuerza `must_change_password=True`, limpia el lockout y
    devuelve (user, password_en_claro) para entregarla UNA vez. None si no existe.
    """
    user = await session.get(User, user_id)
    if user is None:
        return None
    plaintext = new_password or _generate_temporary_password()
    user.hashed_password = hash_password(plaintext)
    user.must_change_password = True
    user.failed_login_attempts = 0
    user.locked_until = None
    await session.commit()
    return user, plaintext


async def unlock(session: AsyncSession, user_id: str) -> User | None:
    """Limpia el lockout y el contador de fallos de un usuario. None si no existe."""
    user = await session.get(User, user_id)
    if user is None:
        return None
    user.failed_login_attempts = 0
    user.locked_until = None
    await session.commit()
    return user


# ─── Self-service (montado por A2 en /users/me/*) ──────────────────────────────
async def change_password(
    session: AsyncSession, user: User, current_password: str, new_password: str
) -> bool:
    """Cambia la password propia. Devuelve False si `current_password` no coincide.
    Al cambiarla, baja `must_change_password` (el usuario ya eligió una suya)."""
    if not user.hashed_password or not verify_password(current_password, user.hashed_password):
        return False
    user.hashed_password = hash_password(new_password)
    user.must_change_password = False
    await session.commit()
    return True


async def start_mfa_setup(session: AsyncSession, user: User) -> tuple[str, str, str]:
    """Genera un secreto TOTP nuevo (aún NO activo), lo guarda CIFRADO (Fernet)
    como tentativo y devuelve (secret, otpauth_uri, qr_code_svg). MFA sigue
    deshabilitado hasta que `enable_mfa` confirme un código válido."""
    secret = generate_totp_secret()
    user.mfa_secret = encrypt_mfa_secret(secret)
    await session.commit()
    account = user.email or user.username
    uri = get_totp_uri(secret, account, issuer=get_settings().mfa_issuer)
    return secret, uri, generate_qr_code_svg(uri)


async def enable_mfa(session: AsyncSession, user: User, code: str) -> list[str] | None:
    """Verifica `code` contra el secreto tentativo y activa MFA. Devuelve los
    recovery codes en claro (UNA vez; en BD solo sus hashes), o None si no hay
    secreto pendiente o el código no valida."""
    if not user.mfa_secret:
        return None
    try:
        secret = decrypt_mfa_secret(user.mfa_secret)
    except ValueError:
        return None
    if not verify_totp_code(secret, code):
        return None

    recovery_codes = generate_recovery_codes()
    user.mfa_enabled = True
    user.mfa_recovery_codes = [hash_recovery_code(c) for c in recovery_codes]
    await session.commit()
    return recovery_codes


async def disable_mfa(session: AsyncSession, user: User, password: str) -> bool:
    """Desactiva MFA exigiendo la password del usuario (re-autenticación de una
    acción sensible). Devuelve False si la password no coincide; limpia secreto y
    recovery codes al desactivar."""
    if not user.hashed_password or not verify_password(password, user.hashed_password):
        return False
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_recovery_codes = None
    await session.commit()
    return True
