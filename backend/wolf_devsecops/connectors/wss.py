"""WSSConnector — Wolf Security Suite (ADR-011, F9) — OPCIONAL.

Aporta vulnerabilidades de infraestructura/activos desde WSS y las normaliza a
FindingsSummary, en el MISMO seam y contrato que las demás fuentes. Sin
dependencia dura: si no está configurada, el aggregator la marca not_configured.

⚠️ El shape REST de abajo es un SUPUESTO razonable; hay que CONFIRMARLO con el
equipo de Wolf Security Suite (decisión abierta #7). Cuando se confirme, se ajusta
solo este archivo y su fixture; el resto no cambia.
"""

from __future__ import annotations

import httpx

from ..core.config import ProjectConfig
from ..models import FindingsSummary, ProbeResult, SeverityCount, SourceDetail
from .base import Connector, ConnectorUnavailable

_SEV = ("critical", "high", "medium", "low", "info")


class WSSConnector(Connector):
    source_name = "wss"
    supported_versions = ">=1.1"  # Wolf Security Suite 1.1 en producción

    def __init__(self, source_config, token, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(source_config, token)
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10.0, transport=self._transport)

    @property
    def _base(self) -> str:
        return self.source_config.url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def probe(self) -> ProbeResult:
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base}/api/v1/health", headers=self._headers())
            body = resp.json() if resp.status_code == 200 else {}
            return ProbeResult(
                source=self.source_name,
                reachable=True,
                authenticated=bool(self.token) and resp.status_code == 200,
                version=body.get("version"),
                compatible=True,
                detail=None,
            )
        except httpx.HTTPError:
            return ProbeResult(
                source=self.source_name,
                reachable=False,
                authenticated=False,
                compatible=False,
                detail="No se pudo contactar Wolf Security Suite.",
            )

    async def _vulns(self, product: str) -> dict:
        # SUPUESTO: /api/v1/vulnerabilities?product=X → {total, by_severity:{...}}
        url = f"{self._base}/api/v1/vulnerabilities"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers(), params={"product": product})
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar Wolf Security Suite.") from exc
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"WSS respondió HTTP {resp.status_code}.")
        return resp.json()

    async def fetch_summary(self, project: ProjectConfig) -> FindingsSummary:
        wss = project.wss
        payload = await self._vulns(wss.product if wss else "")
        by_sev = payload.get("by_severity", {})
        counts = SeverityCount(**{s: int(by_sev.get(s, 0)) for s in _SEV})
        total = payload.get("total", counts.total)
        return FindingsSummary(counts=counts, total_active=total)

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        wss = project.wss
        payload = await self._vulns(wss.product if wss else "")
        return SourceDetail(
            project_key=project.key,
            source="wss",
            status="ok",
            payload=payload,
        )
