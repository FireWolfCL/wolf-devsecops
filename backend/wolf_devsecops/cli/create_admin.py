"""`wolf-devsecops create-admin` — bootstrap del primer admin (ADR §13, ADR-010).

Sin credencial hardcodeada: username y password se toman por argumento o por
stdin (prompt oculto). Crea un User role=admin con `must_change_password=True`
para forzar el cambio en el primer login (y, por ser admin, el front lo empuja a
enrolar MFA vía `must_enroll_mfa`). Headless: no requiere navegador ni frontend.

Regla de bootstrap (fail-closed): por defecto solo crea el admin si NO existe
ningún usuario aún. `--allow-existing` lo permite igualmente (p. ej. un segundo
admin desde consola), decisión consciente del operador.
"""

from __future__ import annotations

import asyncio

import typer

from ..db.engine import dispose_engine, get_sessionmaker
from ..models_db.user import User  # noqa: F401 — asegura el registro del modelo en Base
from ..rbac.roles import Role
from ..services import audit_service, user_service


async def bootstrap_admin(
    session,
    *,
    username: str,
    password: str,
    email: str | None = None,
    full_name: str | None = None,
    allow_existing: bool = False,
) -> User:
    """Crea el primer admin. Lanza RuntimeError si ya hay usuarios y no se permite
    explícitamente. Audita `user.created` como acción de SISTEMA (actor null:
    el bootstrap no lo ejecuta ningún usuario autenticado)."""
    existing = await user_service.count_users(session)
    if existing > 0 and not allow_existing:
        raise RuntimeError(
            f"Ya existen {existing} usuario(s). El bootstrap solo crea el primer admin. "
            "Usa --allow-existing para crear uno más desde consola."
        )

    user = await user_service.create_user(
        session,
        username=username,
        password=password,
        role=Role.admin,
        email=email,
        full_name=full_name,
        must_change_password=True,
    )
    await audit_service.record(
        session,
        action="user.created",
        actor_user_id=None,  # acción de sistema (bootstrap), no de un usuario
        actor_username="system:bootstrap",
        target_type="user",
        target_id=user.id,
        detail={"username": user.username, "role": user.role.value, "bootstrap": True},
    )
    return user


async def _run(username: str, password: str, email: str | None, full_name: str | None, allow_existing: bool) -> str:
    try:
        async with get_sessionmaker()() as session:
            user = await bootstrap_admin(
                session,
                username=username,
                password=password,
                email=email,
                full_name=full_name,
                allow_existing=allow_existing,
            )
            return user.username
    finally:
        await dispose_engine()


def create_admin(
    username: str = typer.Argument(..., help="Nombre de usuario del admin (login)."),
    password: str = typer.Option(
        None,
        "--password",
        help="Contraseña. Si se omite, se pide por stdin de forma oculta.",
        prompt="Contraseña del admin",
        hide_input=True,
        confirmation_prompt=True,
    ),
    email: str = typer.Option(None, "--email", help="Email opcional (solo para el otpauth:// URI de MFA)."),
    full_name: str = typer.Option(None, "--full-name", help="Nombre completo (opcional)."),
    allow_existing: bool = typer.Option(
        False, "--allow-existing", help="Permite crear aunque ya existan usuarios."
    ),
) -> None:
    """Crea el primer administrador (bootstrap headless, sin credencial hardcodeada)."""
    try:
        created = asyncio.run(_run(username, password, email, full_name, allow_existing))
    except RuntimeError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001 — BD no migrada u otro fallo de arranque
        typer.secho(
            f"No se pudo crear el admin: {exc}. ¿Corriste 'alembic upgrade head'?",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    # Nunca se imprime la password (ni siquiera enmascarada).
    typer.secho(
        f"Admin '{created}' creado. Debe cambiar su contraseña en el primer login "
        "y enrolar MFA.",
        fg=typer.colors.GREEN,
    )
