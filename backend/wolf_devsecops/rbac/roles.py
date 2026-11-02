"""Roles globales (ADR-014). Es el `UserRole` que WIF ya define (admin/operator/viewer).

El enforcement vive en `auth/dependencies.py::require_role` (fail-closed). La matriz
rol→capacidad está en `docs/ARCHITECTURE-ADMIN.md` §5; aquí solo el enum, que es el
contrato que la columna `users.role` y los guards comparten.

Seam de crecimiento (ADR-006): si algún día hacen falta permisos finos, se añade una
tabla `role_permissions` y `require_role` pasa a `require_permission` — aditivo, sin
tocar los routers. No se construye hasta que haya demanda (no sobre-diseñar).
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    admin = "admin"  # configura todo: fuentes, proyectos, usuarios, IA, update
    operator = "operator"  # ve todo + dispara análisis IA; no configura
    viewer = "viewer"  # solo lectura

    @classmethod
    def order(cls) -> list[Role]:
        """De menor a mayor privilegio (para UI/validaciones de jerarquía)."""
        return [cls.viewer, cls.operator, cls.admin]


# --- Matriz rol -> capacidad (ADR-014, docs/ARCHITECTURE-ADMIN.md §5) -------------
# Fuente ÚNICA de la política ejecutable: los routers aplican `require_role(...)` con
# estos mismos conjuntos y los tests verifican ambos contra esta tabla. Default-deny:
# una capacidad o rol ausente aquí NO se concede.

ALL_ROLES: tuple[Role, ...] = (Role.viewer, Role.operator, Role.admin)

CAPABILITY_ROLES: dict[str, frozenset[Role]] = {
    "projects.read": frozenset(ALL_ROLES),  # GET /projects*, /sources/*
    "system.version.read": frozenset(ALL_ROLES),  # GET /updates/version (A5)
    "profile.self": frozenset(ALL_ROLES),  # /users/me/*
    "ai.review": frozenset({Role.operator, Role.admin}),  # POST /projects/{k}/ai/review
    "ai.code_fix": frozenset({Role.operator, Role.admin}),  # POST /projects/{k}/ai/code-fix (B, ADR-018)
    "remediation.write": frozenset({Role.operator, Role.admin}),  # POST /projects/{k}/remediation/branch (D, ADR-017)
    "settings.manage": frozenset({Role.admin}),  # /settings/* (A4)
    "users.manage": frozenset({Role.admin}),  # /users CRUD, reset, unlock (A4)
    "updates.manage": frozenset({Role.admin}),  # /updates/status, /migrate (A5)
}


def roles_for(capability: str) -> tuple[Role, ...]:
    """Roles autorizados para una capacidad, en orden de privilegio. Capacidad
    desconocida -> () (nadie): fail-closed."""
    allowed = CAPABILITY_ROLES.get(capability, frozenset())
    return tuple(r for r in Role.order() if r in allowed)


def role_can(role: object, capability: str) -> bool:
    """True solo si `role` está explícitamente concedido para `capability`."""
    try:
        return Role(role) in CAPABILITY_ROLES.get(capability, frozenset())
    except ValueError:
        return False
