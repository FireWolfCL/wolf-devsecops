"""DefectDojoConnector — hallazgos unificados (ARCHITECTURE.md §2, F3).

Lee los findings activos de un producto en DefectDojo y los normaliza a
FindingsSummary. Tolerante a campos desconocidos. Transporte inyectable para los
tests de contrato.
"""

from __future__ import annotations

import httpx

from ..core.config import ProjectConfig
from ..models import FindingsSummary, ProbeResult, SeverityCount, SourceDetail
from .base import Connector, ConnectorUnavailable

_SEV_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "info",
    "informational": "info",
}


class DefectDojoConnector(Connector):
    source_name = "defectdojo"
    supported_versions = ">=2.30"

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
            headers["Authorization"] = f"Token {self.token}"
        return headers

    async def probe(self) -> ProbeResult:
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self._base}/api/v2/user_profile/", headers=self._headers()
                )
            return ProbeResult(
                source=self.source_name,
                reachable=True,
                authenticated=resp.status_code == 200 and bool(self.token),
                compatible=True,
                detail=None if resp.status_code == 200 else f"HTTP {resp.status_code}",
            )
        except httpx.HTTPError:
            return ProbeResult(
                source=self.source_name,
                reachable=False,
                authenticated=False,
                compatible=False,
                detail="No se pudo contactar DefectDojo.",
            )

    async def _active_findings(self, product: str) -> dict:
        url = f"{self._base}/api/v2/findings/"
        params = {"product_name": product, "active": "true", "limit": 100}
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers(), params=params)
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar DefectDojo.") from exc
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"DefectDojo respondió HTTP {resp.status_code}.")
        return resp.json()

    async def fetch_summary(self, project: ProjectConfig) -> FindingsSummary:
        dd = project.defectdojo
        payload = await self._active_findings(dd.product if dd else "")
        counts = SeverityCount()
        for finding in payload.get("results", []):
            key = _SEV_MAP.get(str(finding.get("severity", "")).lower())
            if key:
                setattr(counts, key, getattr(counts, key) + 1)
        total = payload.get("count", counts.total)
        return FindingsSummary(counts=counts, total_active=total)

    # Orden de severidad para priorizar la lista (crítico primero).
    _SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

    @classmethod
    def _findings_list(cls, results: list[dict], cap: int = 150) -> list[dict]:
        """Lista accionable normalizada (misma forma que Trivy): QUÉ está afectado
        y CÓMO mejorarlo. DefectDojo rara vez trae la versión que corrige, así que
        la remediación sale de `mitigation` (texto saneado, inerte en el front).
        """
        out: list[dict] = []
        for f in results:
            if not isinstance(f, dict):
                continue
            sev = str(f.get("severity", "")).upper()
            pkg = f.get("component_name") or None
            ver = f.get("component_version") or None
            mit = (f.get("mitigation") or "").strip()
            # "Cómo mejorarlo": preferimos la mitigación de DefectDojo; si no hay,
            # el front cae a la heurística de paquete/versión.
            fix = mit[:300] if mit else None
            out.append({
                "kind": "vuln",
                "id": f.get("cve") or f.get("vuln_id_from_tool") or None,
                "pkg": pkg,
                "installed": ver,
                "fixed": None,  # DefectDojo no publica versión fija de forma fiable
                "fix": fix,     # remediación explícita (mitigation) si existe
                "severity": sev,
                "title": (f.get("title") or f.get("description") or "")[:180],
                # description la consume la proyección de IA (projection.py), que la
                # sanea y trunca antes de cualquier egress; el front no la muestra.
                "description": (f.get("description") or "")[:500],
                "target": f.get("file_path") or None,
            })
        out.sort(key=lambda x: cls._SEV_RANK.get(x["severity"], 5))
        return out[:cap]

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        dd = project.defectdojo
        payload = await self._active_findings(dd.product if dd else "")
        results = payload.get("results", [])
        findings = self._findings_list(results)
        return SourceDetail(
            project_key=project.key,
            source="defectdojo",
            status="ok",
            payload={
                "total": payload.get("count", len(results)),
                "shown": len(findings),
                "findings": findings,  # QUÉ está afectado + cómo mejorarlo
            },
        )
