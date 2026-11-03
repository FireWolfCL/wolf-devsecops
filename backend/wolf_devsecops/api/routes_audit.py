"""Export del audit trail — GET /api/v1/audit/export (admin · feature Pro).

Devuelve el registro de auditoría en JSON (o CSV con ?format=csv) para evidencia de
cumplimiento. `detail` ya viene saneado por audit_service (sin tokens ni secretos).
El export y la retención son features Pro (edición).
"""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import require_role
from ..db.session import get_db
from ..edition import FEATURE_AUDIT_EXPORT, require_feature
from ..rbac.roles import roles_for
from ..services import audit_service

router = APIRouter(prefix="/api/v1")

_COLS = ["created_at", "actor_username", "action", "target_type", "target_id", "result", "ip", "detail"]


def _row(r) -> dict:
    return {
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "actor_username": r.actor_username,
        "action": r.action,
        "target_type": r.target_type,
        "target_id": r.target_id,
        "result": r.result,
        "ip": r.ip,
        "detail": r.detail,
    }


@router.get(
    "/audit/export",
    dependencies=[
        Depends(require_role(*roles_for("settings.manage"))),  # admin (401/403)
        Depends(require_feature(FEATURE_AUDIT_EXPORT)),         # feature Pro (402)
    ],
)
async def export_audit(
    fmt: str = Query("json", alias="format", pattern="^(json|csv)$"),
    limit: int = Query(5000, ge=1, le=50000),
    since_days: int | None = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    rows = await audit_service.list_audit(db, limit=limit, since_days=since_days)
    data = [_row(r) for r in rows]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_COLS)
        w.writeheader()
        for d in data:
            d = {**d, "detail": json.dumps(d["detail"], ensure_ascii=False) if d["detail"] else ""}
            w.writerow(d)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit.csv"},
        )
    return Response(
        content=json.dumps({"count": len(data), "rows": data}, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit.json"},
    )
