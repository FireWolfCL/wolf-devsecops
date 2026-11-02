"""Rutas de autenticación (/api/v1/auth/*) — ADR-013, §8/§10.

Traducen HTTP ⇄ `auth_service`. NO deciden auth: delegan. Códigos:
- 200 login/verify OK · 401 credencial inválida (uniforme) · 423 cuenta bloqueada.

Login por `username` (OAuth2PasswordRequestForm.username lleva el username, no un
email). `/login` y `/mfa/verify` son públicos; `/me` exige sesión válida.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..auth.ratelimit import login_limiter, mfa_limiter
from ..auth.security import decode_mfa_token
from ..db.session import get_db
from ..models_db.user import User
from ..schemas_admin import LoginResponse, MeOut, MFAVerifyRequest, Token
from ..services import auth_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_LOCKED_MESSAGE = (
    "Cuenta temporalmente bloqueada por demasiados intentos fallidos. Reintenta más tarde."
)
_INVALID_CREDENTIALS = "Usuario o contraseña incorrectos"
_RATE_LIMITED = "Demasiados intentos. Espera un momento y reintenta."


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Paso 1 del login. Password OK + MFA activo ⇒ `mfa_required` + `mfa_token`
    (sin access token). Password OK sin MFA ⇒ access token directo. En ambos casos
    se exponen `must_change_password` y `must_enroll_mfa` para el front."""
    ip = _client_ip(request)

    # Rate-limit app-level (T-AUTH-01): frena spraying/fuerza bruta distribuida que el
    # lockout por-cuenta no cubre. Clave (IP, username); 429 uniforme.
    if not login_limiter.allow(f"{ip or '-'}:{form_data.username.lower()}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, _RATE_LIMITED)

    user = await auth_service.get_user_by_username(db, form_data.username)

    # Lockout primero: una cuenta bloqueada ni siquiera verifica la password (423).
    if user is not None and auth_service.is_locked(user):
        raise HTTPException(status.HTTP_423_LOCKED, _LOCKED_MESSAGE)

    if not await auth_service.authenticate(db, user, form_data.password):
        # Solo se contabiliza el fallo contra una cuenta existente y activa (no se
        # bloquea a un inactivo ni se crea señal para un username inexistente).
        if user is not None and user.is_active:
            await auth_service.record_failed_login(db, user, ip)
        await auth_service.audit_login_failed(db, form_data.username, ip)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            _INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    assert user is not None  # authenticate True ⇒ user existe y está activo

    if user.mfa_enabled:
        return LoginResponse(
            mfa_required=True,
            mfa_token=auth_service.issue_mfa_token(user),
            must_change_password=user.must_change_password,
            must_enroll_mfa=auth_service.must_enroll_mfa(user),
        )

    access_token = await auth_service.complete_login(db, user, ip)
    return LoginResponse(
        access_token=access_token,
        must_change_password=user.must_change_password,
        must_enroll_mfa=auth_service.must_enroll_mfa(user),
    )


@router.post("/mfa/verify", response_model=Token)
async def verify_mfa(
    request: Request,
    payload: MFAVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Paso 2 del login: canjea `mfa_token` + código (TOTP o recovery) por la sesión."""
    ip = _client_ip(request)
    if not mfa_limiter.allow(ip or "-"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, _RATE_LIMITED)
    invalid_challenge = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Desafío MFA inválido o expirado"
    )

    try:
        decoded = decode_mfa_token(payload.mfa_token)
        user_id = decoded["sub"]
    except (JWTError, KeyError):
        # `from None`: motivo colapsado a propósito (anti-enumeración); no se filtra
        # la causa concreta por el traceback encadenado.
        raise invalid_challenge from None

    user = await db.get(User, user_id)
    if user is None or not user.is_active or not user.mfa_enabled:
        raise invalid_challenge

    if auth_service.is_locked(user):
        raise HTTPException(status.HTTP_423_LOCKED, _LOCKED_MESSAGE)

    if not await auth_service.verify_mfa(db, user, payload.code):
        await auth_service.record_failed_login(db, user, ip)
        await auth_service.audit_login_failed(db, user.username, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Código de autenticación inválido")

    access_token = await auth_service.complete_login(db, user, ip)
    return Token(
        access_token=access_token,
        must_change_password=user.must_change_password,
        must_enroll_mfa=auth_service.must_enroll_mfa(user),
    )


@router.get("/me", response_model=MeOut)
async def me(current_user: User = Depends(get_current_user)) -> MeOut:
    """Identidad del portador del token. `must_enroll_mfa` se calcula en vivo."""
    return MeOut(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
        mfa_enabled=current_user.mfa_enabled,
        must_change_password=current_user.must_change_password,
        must_enroll_mfa=auth_service.must_enroll_mfa(current_user),
        last_login_at=current_user.last_login_at,
    )
