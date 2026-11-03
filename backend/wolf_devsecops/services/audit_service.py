"""Writer append-only del `audit_log` (ADR-012 §9).

Regla dura: `detail` va SANEADO. Nunca se persiste token, hash, secreto ni
stacktrace. El llamador pasa solo metadatos no sensibles.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models_db.audit import AuditLog

# Claves que jamás deben entrar a audit_log.detail (defensa en profundidad).
_SENSITIVE_KEYS = {"token", "password", "secret", "hashed_password", "mfa_secret", "code"}


def _sanitize(detail: dict | None) -> dict | None:
    if not detail:
        return detail
    return {k: v for k, v in detail.items() if k.lower() not in _SENSITIVE_KEYS}


async def record(
    session: AsyncSession,
    *,
    action: str,
    result: str = "success",
    actor_user_id: str | None = None,
    actor_username: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
    commit: bool = True,
) -> None:
    """Escribe una fila de auditoría. `commit=False` para unirla a otra transacción."""
    session.add(
        AuditLog(
            action=action,
            result=result,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            target_type=target_type,
            target_id=target_id,
            detail=_sanitize(detail),
            ip=ip,
        )
    )
    if commit:
        await session.commit()


async def list_audit(
    session: AsyncSession, *, limit: int = 5000, since_days: int | None = None
) -> list[AuditLog]:
    """Filas de auditoría, más recientes primero. `since_days` acota la ventana."""
    q = select(AuditLog)
    if since_days is not None:
        q = q.where(AuditLog.created_at >= datetime.now(UTC) - timedelta(days=since_days))
    q = q.order_by(AuditLog.created_at.desc()).limit(limit)
    return list((await session.execute(q)).scalars())


async def purge_old(session: AsyncSession, retention_days: int) -> int:
    """Borra filas más viejas que `retention_days` (retención, feature Pro). 0 = no purga.
    Devuelve cuántas filas se borraron."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    res = await session.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    await session.commit()
    return res.rowcount or 0
