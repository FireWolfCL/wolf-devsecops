"""Gestión de excepciones de hallazgos (ADR-021) — escritura acotada a la fuente.

RBAC (ADR-014): leer el listado = cualquier rol; aplicar FP/out-of-scope/mitigado =
operator+; APROBAR riesgo aceptado = solo admin (con MFA). Las escrituras son Pro
(`exceptions.writeback`). Cada acción se audita. El estado que excluye del score lo
escribe el servicio en la herramienta de origen; aquí solo va la autorización + contrato.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import require_admin_action, require_role
from ..core.aggregator import Aggregator
from ..core.config import AppConfig
from ..db.session import get_db
from ..edition import FEATURE_EXCEPTIONS_WRITEBACK, require_feature
from ..models import ExceptionIn, ExceptionList, ExceptionOut
from ..models_db.exceptions import SourceException
from ..models_db.user import User
from ..rbac.roles import ALL_ROLES, Role
from ..services import exceptions_service as svc
from .deps import get_aggregator, get_config

router = APIRouter(prefix="/api/v1")

_writeback = Depends(require_feature(FEATURE_EXCEPTIONS_WRITEBACK))


def _out(row: SourceException) -> ExceptionOut:
    return ExceptionOut(
        id=row.id, project_key=row.project_key, source=row.source, finding_ref=row.finding_ref,
        finding_title=row.finding_title, category=row.category, kind=row.kind,
        justification=row.justification, status=row.status, created_by=row.created_by,
        created_at=row.created_at, approved_by=row.approved_by, approved_at=row.approved_at,
        expires_at=row.expires_at, cve_id=row.cve_id, fixed_version=row.fixed_version,
        patchable_since=row.patchable_since,
    )


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/projects/{key}/exceptions", response_model=ExceptionOut, dependencies=[_writeback])
async def create_exception(
    key: str,
    body: ExceptionIn,
    request: Request,
    user: User = Depends(require_role(Role.operator, Role.admin)),
    aggregator: Aggregator = Depends(get_aggregator),
    config: AppConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> ExceptionOut:
    if config.project(key) is None:
        raise HTTPException(status_code=404, detail=f"Proyecto desconocido: {key}")
    try:
        row = await svc.create(
            db, aggregator, key, body, user_id=user.id, username=user.username, ip=_ip(request),
        )
    except svc.ExceptionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _out(row)


@router.post("/exceptions/{exc_id}/approve", response_model=ExceptionOut, dependencies=[_writeback])
async def approve_exception(
    exc_id: str,
    request: Request,
    user: User = Depends(require_admin_action(Role.admin)),  # solo admin + MFA
    aggregator: Aggregator = Depends(get_aggregator),
    db: AsyncSession = Depends(get_db),
) -> ExceptionOut:
    try:
        row = await svc.approve(db, aggregator, exc_id, user_id=user.id, username=user.username, ip=_ip(request))
    except svc.ExceptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(row)


@router.post("/exceptions/{exc_id}/revoke", response_model=ExceptionOut, dependencies=[_writeback])
async def revoke_exception(
    exc_id: str,
    request: Request,
    user: User = Depends(require_role(Role.operator, Role.admin)),
    aggregator: Aggregator = Depends(get_aggregator),
    db: AsyncSession = Depends(get_db),
) -> ExceptionOut:
    try:
        row = await svc.revoke(db, aggregator, exc_id, user_id=user.id, username=user.username, ip=_ip(request))
    except svc.ExceptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(row)


@router.get("/exceptions", response_model=ExceptionList)
async def list_exceptions(
    project: str | None = Query(None),
    category: str | None = Query(None),
    status: str | None = Query(None),
    _user: User = Depends(require_role(*ALL_ROLES)),  # lectura: cualquier rol (6.3.d)
    db: AsyncSession = Depends(get_db),
) -> ExceptionList:
    rows = await svc.list_exceptions(db, project=project, category=category, status=status)
    return ExceptionList(items=[_out(r) for r in rows])
