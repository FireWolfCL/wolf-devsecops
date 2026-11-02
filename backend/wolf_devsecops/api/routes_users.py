"""Rutas de usuarios (/api/v1/users/*).

Self-service (/users/me/*, A2): `Depends(get_current_user)`, cualquier usuario
autenticado gestiona su propia password y MFA.

CRUD admin (A4): list/create/update/reset-password/unlock, cada ruta con
`require_role(*roles_for("users.manage"))` (admin, fail-closed). Orden de evaluación:
401 (sin token) -> 403 (no admin) -> validación -> lógica. Los handlers solo
traducen HTTP<->`user_service`; las reglas (anti-autobloqueo, último-admin, bump de
token_version) viven en el servicio.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user, require_admin_action
from ..db.session import get_db
from ..models_db.user import User
from ..rbac.roles import roles_for
from ..schemas_admin import (
    MFADisableRequest,
    MFAEnableRequest,
    MFAEnableResponse,
    MFASetupResponse,
    PasswordChange,
    ResetPasswordResponse,
    UserCreate,
    UserListResponse,
    UserOut,
    UserUpdate,
)
from ..services import audit_service, auth_service, user_service

# Todo el router exige sesión válida; A4 añadirá rutas admin con require_role(admin).
router = APIRouter(
    prefix="/api/v1/users",
    tags=["users"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/me/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(
    payload: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await user_service.change_password(
        db, current_user, payload.current_password, payload.new_password
    )
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La contraseña actual es incorrecta")


@router.post("/me/mfa/setup", response_model=MFASetupResponse)
async def setup_mfa(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MFASetupResponse:
    if current_user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA ya está activo")
    secret, otpauth_uri, qr_code_svg = await user_service.start_mfa_setup(db, current_user)
    return MFASetupResponse(secret=secret, otpauth_uri=otpauth_uri, qr_code_svg=qr_code_svg)


@router.post("/me/mfa/enable", response_model=MFAEnableResponse)
async def enable_mfa(
    payload: MFAEnableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MFAEnableResponse:
    if current_user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA ya está activo")
    recovery_codes = await user_service.enable_mfa(db, current_user, payload.code)
    if recovery_codes is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Código de autenticación inválido")
    await audit_service.record(
        db,
        action="user.mfa_enabled",
        actor_user_id=current_user.id,
        actor_username=current_user.username,
        target_type="user",
        target_id=current_user.id,
    )
    return MFAEnableResponse(recovery_codes=recovery_codes)


@router.post("/me/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_mfa(
    payload: MFADisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if not current_user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA no está activo")
    if not await user_service.disable_mfa(db, current_user, payload.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Contraseña incorrecta")
    await audit_service.record(
        db,
        action="user.mfa_disabled",
        actor_user_id=current_user.id,
        actor_username=current_user.username,
        target_type="user",
        target_id=current_user.id,
    )


# ─── CRUD admin (A4) ───────────────────────────────────────────────────────────
# require_admin_action = admin (users.manage) + MFA obligatorio (T-AUTH-04).
_admin_only = Depends(require_admin_action(*roles_for("users.manage")))


@router.get("", response_model=UserListResponse)
async def list_users(
    _admin: User = _admin_only, db: AsyncSession = Depends(get_db)
) -> UserListResponse:
    users, total = await user_service.list_users(db)
    return UserListResponse(items=[UserOut.model_validate(u) for u in users], total=total)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    admin: User = _admin_only,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    try:
        user = await user_service.create_user(
            db,
            username=payload.username,
            password=payload.password,
            role=payload.role,
            email=payload.email,
            full_name=payload.full_name,
            must_change_password=payload.must_change_password,
        )
    except user_service.DuplicateUserError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "username o email ya en uso") from exc
    await audit_service.record(
        db,
        action="user.created",
        actor_user_id=admin.id,
        actor_username=admin.username,
        target_type="user",
        target_id=user.id,
        detail={"role": user.role.value},
    )
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    admin: User = _admin_only,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    try:
        user = await user_service.update_user(
            db,
            user_id,
            acting_user_id=admin.id,
            full_name=payload.full_name,
            role=payload.role,
            is_active=payload.is_active,
        )
    except user_service.UserServiceError as exc:  # SelfLockout / LastAdmin -> 400
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    await audit_service.record(
        db,
        action="user.updated",
        actor_user_id=admin.id,
        actor_username=admin.username,
        target_type="user",
        target_id=user.id,
        # Solo QUÉ campos se tocaron y valores no sensibles (rol/estado).
        detail={
            "fields": sorted(payload.model_dump(exclude_unset=True)),
            "role": user.role.value,
            "is_active": user.is_active,
        },
    )
    return UserOut.model_validate(user)


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_user_password(
    user_id: str,
    admin: User = _admin_only,
    db: AsyncSession = Depends(get_db),
) -> ResetPasswordResponse:
    result = await user_service.reset_password(db, user_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    user, temporary_password = result
    # La password anterior pudo estar comprometida: invalida las sesiones vigentes.
    await auth_service.bump_token_version(db, user)
    await audit_service.record(
        db,
        action="user.password_reset",
        actor_user_id=admin.id,
        actor_username=admin.username,
        target_type="user",
        target_id=user.id,
    )
    return ResetPasswordResponse(temporary_password=temporary_password)


@router.post("/{user_id}/unlock", status_code=status.HTTP_204_NO_CONTENT)
async def unlock_user(
    user_id: str,
    admin: User = _admin_only,
    db: AsyncSession = Depends(get_db),
) -> None:
    user = await user_service.unlock(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    await audit_service.record(
        db,
        action="user.unlocked",
        actor_user_id=admin.id,
        actor_username=admin.username,
        target_type="user",
        target_id=user.id,
    )
