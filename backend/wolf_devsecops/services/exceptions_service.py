"""Gestión de excepciones de hallazgos con escritura a la fuente (ADR-021).

Orquesta la ÚNICA escritura de estado hacia DefectDojo/Sonar (junto a la de GitHub,
ADR-017): refleja el estado nativo del hallazgo (falso positivo / riesgo aceptado /
fuera de alcance / mitigado), lo registra en `source_exceptions` para gobierno y lo
audita. RBAC: operator+ aplica FP/out-of-scope/mitigado; riesgo aceptado SIEMPRE pasa
por propuesta→aprobación de un admin (con MFA). La aceptación vence y, al vencer, el
scheduler la revierte y alerta.

Invariantes de integridad (revisión wolf-threat-hardening, ADR-021):
- La fila de gobierno se PERSISTE antes de escribir a la fuente: ningún write externo
  queda sin fila (si el write falla, la fila queda `write_failed`, no se pierde).
- `expire_due` NO marca `expired` si no pudo revertir en la fuente: queda `expire_failed`
  con alerta `danger` distinta, para que el hallazgo oculto no pase por "vencido" en silencio.
- Una sola excepción vigente por (source, finding_ref): no se apilan.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.aggregator import Aggregator
from ..core.cve import extract_cve
from ..models import ExceptionIn
from ..models_db.events import ProjectEvent
from ..models_db.exceptions import SourceException
from . import audit_service

log = logging.getLogger(__name__)

DEFAULT_RISK_DAYS = 90  # vencimiento por defecto de "aceptar riesgo"
_ACTIVE_STATUSES = ("proposed", "applied")

_DD_FLAG = {
    "false_positive": "false_p",
    "risk_accepted": "risk_accepted",
    "out_of_scope": "out_of_scope",
    "mitigated": "is_mitigated",
}
_SONAR_TX = {
    "false_positive": "falsepositive",
    "risk_accepted": "accept",
    "out_of_scope": "wontfix",
    "mitigated": "wontfix",
}


class ExceptionError(Exception):
    """Error de negocio de una excepción (mensaje saneado para el usuario)."""


async def _write_source(aggregator: Aggregator, source: str, finding_ref: str, kind: str, *, revoke: bool) -> None:
    """Refleja el estado en la herramienta de origen. Lanza ExceptionError saneado;
    cualquier error inesperado se loguea server-side y se colapsa a un mensaje genérico (M4)."""
    connector = aggregator.connector_for(source)
    if connector is None:
        raise ExceptionError(f"La fuente '{source}' no está configurada.")
    try:
        if source == "defectdojo":
            if revoke:
                await connector.set_finding_state(finding_ref, active=True)
            else:
                await connector.set_finding_state(finding_ref, active=False, **{_DD_FLAG[kind]: True})
        elif source == "sonarqube":
            await connector.transition_issue(finding_ref, "reopen" if revoke else _SONAR_TX[kind])
        else:
            raise ExceptionError(f"Fuente no soportada para escritura: {source}")
    except ExceptionError:
        raise
    except Exception as exc:  # noqa: BLE001 — no filtrar detalle interno al cliente (M4)
        log.warning("writeback inesperado en %s: %s", source, exc)
        raise ExceptionError("Error inesperado al escribir en la fuente.") from exc


def _expiry_for(kind: str, expires_days: int | None, now: datetime) -> datetime | None:
    if kind != "risk_accepted":
        return None  # solo la aceptación de riesgo vence (MEJORAS 6.3.b)
    return now + timedelta(days=expires_days or DEFAULT_RISK_DAYS)


async def _active_exists(session: AsyncSession, source: str, finding_ref: str) -> bool:
    q = select(SourceException.id).where(
        SourceException.source == source,
        SourceException.finding_ref == finding_ref,
        SourceException.status.in_(_ACTIVE_STATUSES),
    ).limit(1)
    return (await session.execute(q)).first() is not None


async def create(
    session: AsyncSession, aggregator: Aggregator, project_key: str, data: ExceptionIn,
    *, user_id: str | None, username: str | None, ip: str | None = None,
) -> SourceException:
    """Crea una excepción. risk_accepted SIEMPRE queda 'proposed' (aplicar exige admin+MFA
    por /approve, cierra A1). FP/out-of-scope/mitigado se aplican tras persistir la fila."""
    if await _active_exists(session, data.source, data.finding_ref):
        raise ExceptionError("Ya existe una excepción vigente para ese hallazgo (revócala primero).")
    now = datetime.now(UTC)
    row = SourceException(
        project_key=project_key, source=data.source, finding_ref=data.finding_ref,
        finding_title=data.finding_title, category=data.category, kind=data.kind,
        justification=data.justification, created_by=username, created_by_id=user_id,
        # Seguimiento de parche: el CVE explícito gana; si no, se deriva del título con regex
        # estricta. Sin CVE bien formado → NULL (no se rastrea; no-op honesto).
        cve_id=(data.cve_id.upper() if data.cve_id else extract_cve(data.finding_title)),
        fixed_version=(data.fixed_version or None),
    )
    if data.kind == "risk_accepted":
        row.status = "proposed"
        row.expires_at = _expiry_for("risk_accepted", data.expires_days, now)  # intención (se fija al aprobar)
        session.add(row)
        await session.flush()
        await audit_service.record(
            session, action="exception.proposed", actor_user_id=user_id, actor_username=username,
            target_type="finding", target_id=f"{data.source}:{data.finding_ref}",
            detail={"kind": data.kind, "project": project_key}, ip=ip, commit=False,
        )
        await session.commit()
        return row
    # FP/out-of-scope/mitigado: persistir la fila ANTES de escribir (M1), luego escribir.
    row.status = "applied"
    session.add(row)
    await session.flush()
    await audit_service.record(
        session, action="exception.applied", actor_user_id=user_id, actor_username=username,
        target_type="finding", target_id=f"{data.source}:{data.finding_ref}",
        detail={"kind": data.kind, "project": project_key}, ip=ip, commit=False,
    )
    await session.commit()
    try:
        await _write_source(aggregator, data.source, data.finding_ref, data.kind, revoke=False)
    except ExceptionError as exc:
        row.status = "write_failed"
        await audit_service.record(
            session, action="exception.write_failed", result="error", actor_user_id=user_id,
            actor_username=username, target_type="finding",
            target_id=f"{data.source}:{data.finding_ref}", detail={"kind": data.kind}, ip=ip, commit=False,
        )
        await session.commit()
        raise exc
    return row


async def approve(
    session: AsyncSession, aggregator: Aggregator, exc_id: str,
    *, user_id: str | None, username: str | None, ip: str | None = None,
) -> SourceException:
    """Un admin aprueba una excepción 'proposed' (solo risk_accepted): escribe a la fuente."""
    row = (
        await session.execute(select(SourceException).where(SourceException.id == exc_id).with_for_update())
    ).scalars().first()
    if row is None:
        raise ExceptionError("La excepción no existe.")
    if row.status != "proposed":
        raise ExceptionError(f"La excepción no está pendiente de aprobación (estado: {row.status}).")
    if row.kind != "risk_accepted":  # B1: solo se aprueban aceptaciones de riesgo
        raise ExceptionError("Solo se aprueban aceptaciones de riesgo.")
    now = datetime.now(UTC)
    row.status = "applied"
    row.approved_by = username
    row.approved_at = now
    row.expires_at = row.expires_at or _expiry_for("risk_accepted", None, now)
    await audit_service.record(
        session, action="exception.approved", actor_user_id=user_id, actor_username=username,
        target_type="finding", target_id=f"{row.source}:{row.finding_ref}",
        detail={"kind": row.kind, "project": row.project_key,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None}, ip=ip, commit=False,
    )
    await session.commit()
    try:
        await _write_source(aggregator, row.source, row.finding_ref, row.kind, revoke=False)
    except ExceptionError as exc:
        row.status = "write_failed"
        await audit_service.record(
            session, action="exception.write_failed", result="error", actor_user_id=user_id,
            actor_username=username, target_type="finding",
            target_id=f"{row.source}:{row.finding_ref}", detail={"kind": row.kind}, ip=ip, commit=False,
        )
        await session.commit()
        # write_failed NO entra al conjunto aceptado (fail-closed), pero invalidamos
        # por si acaso para que el neto gobernado se recalcule limpio (ADR-023).
        await aggregator.invalidate_governance(row.project_key)
        raise exc
    # Aceptación ya applied: refresca el overlay de gobierno para que el score la reste.
    await aggregator.invalidate_governance(row.project_key)
    return row


async def revoke(
    session: AsyncSession, aggregator: Aggregator, exc_id: str,
    *, user_id: str | None, username: str | None, ip: str | None = None,
) -> SourceException:
    """Revoca una excepción: revierte el estado en la fuente (vuelve a contar)."""
    row = (
        await session.execute(select(SourceException).where(SourceException.id == exc_id).with_for_update())
    ).scalars().first()
    if row is None:
        raise ExceptionError("La excepción no existe.")
    if row.status not in ("applied", "proposed", "write_failed", "expire_failed"):
        raise ExceptionError(f"La excepción ya está {row.status}.")
    if row.status in ("applied", "expire_failed"):
        await _write_source(aggregator, row.source, row.finding_ref, row.kind, revoke=True)
    row.status = "revoked"
    await audit_service.record(
        session, action="exception.revoked", actor_user_id=user_id, actor_username=username,
        target_type="finding", target_id=f"{row.source}:{row.finding_ref}",
        detail={"kind": row.kind, "project": row.project_key}, ip=ip, commit=False,
    )
    await session.commit()
    # El CVE vuelve a contar: refresca el overlay de gobierno (ADR-023).
    await aggregator.invalidate_governance(row.project_key)
    return row


async def list_exceptions(
    session: AsyncSession, *, project: str | None = None, category: str | None = None,
    status: str | None = None, limit: int = 1000,
) -> list[SourceException]:
    q = select(SourceException)
    if project:
        q = q.where(SourceException.project_key == project)
    if category:
        q = q.where(SourceException.category == category)
    if status:
        q = q.where(SourceException.status == status)
    q = q.order_by(SourceException.created_at.desc()).limit(limit)
    return list((await session.execute(q)).scalars())


async def expire_due(session: AsyncSession, aggregator: Aggregator) -> int:
    """Revierte en la fuente las aceptaciones de riesgo vencidas y alerta. Lo corre el
    scheduler. Si NO pudo revertir, deja la fila en `expire_failed` con alerta `danger`
    (A2): nunca marca `expired` un hallazgo que sigue oculto. Devuelve cuántas procesó."""
    now = datetime.now(UTC)
    q = select(SourceException).where(
        SourceException.status == "applied",
        SourceException.kind == "risk_accepted",
        SourceException.expires_at.is_not(None),
        SourceException.expires_at <= now,
    )
    due = list((await session.execute(q)).scalars())
    for row in due:
        try:
            await _write_source(aggregator, row.source, row.finding_ref, row.kind, revoke=True)
        except ExceptionError:
            # No se pudo revertir (p. ej. token sin permiso): el hallazgo SIGUE oculto.
            # No mentir con "expired": estado + alerta DISTINTOS para que un humano actúe.
            row.status = "expire_failed"
            session.add(ProjectEvent(
                project_key=row.project_key, kind="exception_expire_failed", severity="danger"))
            await audit_service.record(
                session, action="exception.expire_failed", result="error", actor_username="scheduler",
                target_type="finding", target_id=f"{row.source}:{row.finding_ref}",
                detail={"kind": row.kind, "project": row.project_key}, commit=False,
            )
            continue
        row.status = "expired"
        session.add(ProjectEvent(
            project_key=row.project_key, kind="exception_expired", severity="warn"))
        await audit_service.record(
            session, action="exception.expired", actor_username="scheduler",
            target_type="finding", target_id=f"{row.source}:{row.finding_ref}",
            detail={"kind": row.kind, "project": row.project_key}, commit=False,
        )
    if due:
        await session.commit()
        # Aceptaciones vencidas vuelven a contar: refresca el overlay de los afectados.
        for pk in {row.project_key for row in due}:
            await aggregator.invalidate_governance(pk)
    return len(due)


async def check_patchable(session: AsyncSession, aggregator: Aggregator) -> int:
    """Re-chequeo periódico (scheduler): ¿un riesgo aceptado "sin parche" ya tiene parche?

    Candidatas: `risk_accepted` en `applied`, con `cve_id` y `fixed_version` NULL. Busca el CVE
    en el último reporte Trivy del proyecto (vía aggregator, solo lectura: ADR-001, sin
    escanear ni escribir a la fuente). Si ahora trae FixedVersion: fija `fixed_version` +
    `patchable_since`, emite UN evento `exception_patchable` y audita. Dedup natural: al
    quedar `fixed_version` fijado la fila deja de ser candidata → una alerta por excepción.
    Si el reporte no está disponible (fuente caída/no configurada) la fila se deja intacta:
    jamás se infiere nada de una lectura fallida. Devuelve cuántas pasaron a parchables.
    """
    q = select(SourceException).where(
        SourceException.status == "applied",
        SourceException.kind == "risk_accepted",
        SourceException.cve_id.is_not(None),
        SourceException.fixed_version.is_(None),
    )
    rows = list((await session.execute(q)).scalars())
    fixes_by_project: dict[str, dict[str, str | None] | None] = {}  # 1 lectura por proyecto
    now = datetime.now(UTC)
    n = 0
    for row in rows:
        if row.project_key not in fixes_by_project:
            fixes_by_project[row.project_key] = await aggregator.cve_fixes(row.project_key)
        fixes = fixes_by_project[row.project_key]
        fixed = fixes.get((row.cve_id or "").upper()) if fixes else None
        if not fixed:
            continue
        row.fixed_version = fixed[:200]
        row.patchable_since = now
        session.add(ProjectEvent(
            project_key=row.project_key, kind="exception_patchable", severity="warn",
            detail={"cve": row.cve_id, "fixed_version": row.fixed_version, "source": row.source}))
        await audit_service.record(
            session, action="exception.patchable", actor_username="scheduler",
            target_type="finding", target_id=f"{row.source}:{row.finding_ref}",
            detail={"project": row.project_key, "cve": row.cve_id, "fixed_version": row.fixed_version},
            commit=False,
        )
        n += 1
    if n:
        await session.commit()
    return n
