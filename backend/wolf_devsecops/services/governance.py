"""GovernanceProvider — overlay de gobierno para el score NETO (ADR-023).

Provee, por proyecto, el conjunto de CVE (MAYÚSCULAS) con **aceptación de riesgo
aprobada y vigente**, para que el aggregator reste esos CVE del pilar de Contenedores
en el neto gobernado. Es SOLO LECTURA sobre `source_exceptions`; no escribe a ninguna
fuente (ADR-001/006 intactos). El conteo crudo nunca se toca (wolf-genjutsu).

Fail-closed: solo entran excepciones `status=applied` + `kind=risk_accepted` con
`cve_id` y sin vencer. Una `proposed`/`write_failed`/`expired`/`expire_failed` NO entra
⇒ su CVE sigue contando y sigue capando la banda.

La postura se mantiene pura (ADR-012): este overlay se inyecta en el `Aggregator` como
abstracción; el default `NullGovernance` deja neto == crudo (tests/CLI/sin DB).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import or_, select

from ..core.cache import TTLCache
from ..db.engine import get_sessionmaker
from ..models_db.exceptions import SourceException


@runtime_checkable
class GovernanceProvider(Protocol):
    async def accepted_cves(self, project_key: str) -> set[str]:
        """CVE (MAYÚSCULAS) con riesgo aceptado aprobado y vigente del proyecto."""
        ...

    async def invalidate(self, project_key: str | None = None) -> None:
        """Descarta el overlay cacheado de un proyecto (o todos si None)."""
        ...


class NullGovernance:
    """Sin gobierno: neto == crudo. Default en tests, CLI y cuando no hay DB."""

    async def accepted_cves(self, project_key: str) -> set[str]:
        return set()

    async def invalidate(self, project_key: str | None = None) -> None:
        return None


class DbGovernance:
    """Lee `source_exceptions` con sesión propia (fuera de request), cacheado con TTL
    corto e invalidable por proyecto. Sin ventana stale: el gobierno es fail-closed."""

    def __init__(self, ttl_seconds: int = 30) -> None:
        # stale_ttl=0: nunca sirve un conjunto vencido (no sub-restar de más).
        self._cache = TTLCache(ttl_seconds, 0)

    async def accepted_cves(self, project_key: str) -> set[str]:
        key = f"gov:{project_key}"
        cached = await self._cache.get(key)
        if cached is not None:
            return cached
        now = datetime.now(UTC)
        q = (
            select(SourceException.cve_id)
            .where(
                SourceException.project_key == project_key,
                SourceException.status == "applied",
                SourceException.kind == "risk_accepted",
                SourceException.cve_id.is_not(None),
                or_(
                    SourceException.expires_at.is_(None),
                    SourceException.expires_at > now,
                ),
            )
        )
        async with get_sessionmaker()() as session:
            rows = (await session.execute(q)).scalars().all()
        result = {str(c).upper() for c in rows if c}
        await self._cache.set(key, result)
        return result

    async def invalidate(self, project_key: str | None = None) -> None:
        prefix = f"gov:{project_key}" if project_key else "gov:"
        await self._cache.invalidate(prefix)
