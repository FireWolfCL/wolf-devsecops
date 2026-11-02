"""Dependencias de autenticación de FastAPI.

`get_current_user` es el ÚNICO punto que lee y valida el access token (A2);
`require_role(*roles)` (A3) es el ÚNICO punto de decisión de capacidad (fail-closed).

Porta `WIF/wif-server/app/core/dependencies.py::get_current_user`, podado a
single-tenant (sin org/membership/RLS): valida firma+expiry+`type==access` (A1),
carga el User por `sub`, y exige `is_active` y `tv == users.token_version`. Esto
último es lo que hace que subir `token_version` (logout-everywhere / reset /
cambio de rol) invalide todos los tokens previos SIN tabla de sesiones (ADR-013).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..models_db.user import User
from ..rbac.roles import Role
from .security import decode_access_token

# tokenUrl: la ruta real de login (para el "Authorize" de /docs). No fuerza nada
# en runtime: solo extrae el header `Authorization: Bearer <token>`.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resuelve el Bearer JWT a su fila `users`. 401 (genérico) ante cualquier
    fallo: firma/expiry inválidos, token que no es `access`, `sub` ilegible,
    usuario inexistente o inactivo, o `tv` desalineado con `token_version`.

    El mensaje y la forma del 401 son uniformes para no filtrar POR QUÉ falló
    (anti-enumeración): "could not validate credentials" cubre todos los casos.
    """
    credentials_exception = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)  # valida firma, exp y type==access
        subject = payload["sub"]
    except (JWTError, KeyError):
        # `from None`: se colapsa el motivo a propósito (anti-enumeración); no debe
        # filtrarse la causa concreta ni siquiera por el traceback encadenado.
        raise credentials_exception from None

    user = await db.get(User, subject)
    # Libera la conexión del pool YA: sin esto la transacción abierta por el SELECT
    # queda "idle in transaction" durante TODO el request (en /projects son varios
    # segundos de fan-out) y el pool se agota con ~pool_size requests lentos.
    # `expire_on_commit=False` → `user` sigue utilizable tras el commit.
    await db.commit()
    if user is None or not user.is_active:
        raise credentials_exception

    # Revocación sin tabla de sesiones: un `tv` ausente se lee como 0 (transición
    # para tokens previos al claim); un `tv` que NO coincide es rechazo duro.
    token_version_claim = payload.get("tv", 0)
    if token_version_claim != (user.token_version or 0):
        raise credentials_exception

    return user


def require_role(*roles: Role):
    """Fábrica de dependencia RBAC (ADR-014), fail-closed.

    Depende de `get_current_user` (sin token válido -> 401 antes de evaluar rol) y
    lanza 403 si `user.role` no está EXPLÍCITAMENTE en `roles`. Sin roles, o con un
    rol desconocido/corrupto, nadie pasa. Devuelve el User si pasa.
    El 403 es genérico: no revela qué rol se requería.
    """
    allowed = frozenset(r.value if isinstance(r, Role) else str(r) for r in roles)

    async def _guard(user: User = Depends(get_current_user)) -> User:
        role_value = user.role.value if isinstance(user.role, Role) else user.role
        if role_value not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return _guard


def require_admin_action(*roles: Role):
    """Como `require_role`, pero además EXIGE MFA cuando el rol es admin (T-AUTH-04,
    ADR-013 #2): un admin sin MFA activo recibe 403 en acciones privilegiadas
    (settings, users, updates) hasta enrolar MFA. Puede seguir usando `/users/me/mfa/*`
    y `/auth/me` (que solo piden `get_current_user`), así el admin de bootstrap enrola
    su MFA antes de administrar. `must_enroll_mfa` del login deja de ser solo hint de UI.
    """
    allowed = frozenset(r.value if isinstance(r, Role) else str(r) for r in roles)

    async def _guard(user: User = Depends(get_current_user)) -> User:
        role_value = user.role.value if isinstance(user.role, Role) else user.role
        if role_value not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        if role_value == Role.admin.value and not user.mfa_enabled:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "MFA obligatorio para administrar: enrola MFA en tu perfil para continuar.",
            )
        return user

    return _guard
