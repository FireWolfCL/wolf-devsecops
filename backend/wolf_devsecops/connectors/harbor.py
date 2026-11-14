"""HarborConnector — postura de seguridad de imágenes de contenedor (ADR-006).

Harbor es un registry con API REST que escanea imágenes con Trivy por dentro. Este
conector LEE esa API (no re-escanea, ADR-001) y normaliza las vulnerabilidades por
imagen a `FindingsSummary`, en el MISMO seam que las demás fuentes. Si no está
configurada, el aggregator la marca not_configured (sin dependencia dura).

Cubre el hueco de "contenedor ya construido": CVEs por imagen, estado de escaneo y
(en el detalle) firma Cosign cuando Harbor la expone como accessory.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime

import httpx

from ..core.config import ProjectConfig
from ..models import FindingsSummary, ProbeResult, RegistryImage, SeverityCount, SourceDetail
from .base import Connector, ConnectorUnavailable

# Harbor reporta severidades capitalizadas; Unknown lo tratamos como info.
_HARBOR_SEV = {
    "Critical": "critical",
    "High": "high",
    "Medium": "medium",
    "Low": "low",
    "Unknown": "info",
    "None": "info",
}


class HarborConnector(Connector):
    source_name = "harbor"
    supported_versions = ">=2.0"  # API /api/v2.0

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
                url = f"{self._base}/api/v2.0/systeminfo"
                resp = await client.get(url, headers=self._headers())
            body = resp.json() if resp.status_code == 200 else {}
            version = body.get("harbor_version")
            compatible = bool(version) and (version.lstrip("v").startswith("2"))
            return ProbeResult(
                source=self.source_name,
                reachable=True,
                authenticated=bool(self.token) and resp.status_code == 200,
                version=version,
                compatible=compatible if version else True,
                detail=None if compatible or not version else "Harbor <2.0 no soportado.",
            )
        except httpx.HTTPError:
            return ProbeResult(
                source=self.source_name,
                reachable=False,
                authenticated=False,
                compatible=False,
                detail="No se pudo contactar Harbor.",
            )

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> object:
        try:
            resp = await client.get(f"{self._base}{path}", headers=self._headers(), params=params)
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar Harbor.") from exc
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"Harbor respondió HTTP {resp.status_code}.")
        return resp.json()

    async def _list_repositories(self, client: httpx.AsyncClient, project: str) -> list[str]:
        data = await self._get(client, f"/api/v2.0/projects/{project}/repositories", page_size=100)
        repos: list[str] = []
        for r in data or []:
            name = r.get("name", "")
            # Harbor devuelve "proyecto/repo"; la API de artifacts usa solo "repo".
            repos.append(name.split("/", 1)[1] if "/" in name else name)
        return repos

    async def _artifacts(self, client: httpx.AsyncClient, project: str, repo: str) -> list[dict]:
        enc = urllib.parse.quote(repo, safe="")
        data = await self._get(
            client,
            f"/api/v2.0/projects/{project}/repositories/{enc}/artifacts",
            with_scan_overview="true",
            page_size=50,
        )
        return data or []

    @staticmethod
    def _accumulate(artifact: dict, counts: dict[str, int]) -> int:
        """Suma las severidades del scan_overview de un artefacto. Devuelve cuántos
        reportes de escaneo contó (tolerante a shape desconocido, ADR-008)."""
        scanned = 0
        overview = artifact.get("scan_overview") or {}
        for report in overview.values():
            if not isinstance(report, dict):
                continue
            summary = ((report.get("summary") or {}).get("summary")) or {}
            for harbor_sev, norm in _HARBOR_SEV.items():
                counts[norm] += int(summary.get(harbor_sev, 0) or 0)
            scanned += 1
        return scanned

    async def _scan_data(self, project: ProjectConfig) -> dict:
        hp = project.harbor
        if hp is None:
            raise ConnectorUnavailable("El proyecto no define bloque 'harbor'.")
        counts = {s: 0 for s in ("critical", "high", "medium", "low", "info")}
        scanned = 0
        async with self._client() as client:
            repos = (
                [hp.repository] if hp.repository
                else await self._list_repositories(client, hp.project)
            )
            for repo in repos:
                for artifact in await self._artifacts(client, hp.project, repo):
                    scanned += self._accumulate(artifact, counts)
        total = sum(counts.values())
        return {
            "counts": counts,
            "total": total,
            "repositories": len(repos),
            "artifacts_scanned": scanned,
            "harbor_project": hp.project,
        }

    async def image_presence(self, project: ProjectConfig) -> RegistryImage:
        """¿Hay alguna imagen publicada? (solo lectura). Cuenta artefactos, estén o no
        escaneados; devuelve el tag/fecha del push más reciente."""
        hp = project.harbor
        if hp is None:
            raise ConnectorUnavailable("El proyecto no define bloque 'harbor'.")
        count = 0
        best: tuple[datetime, str | None] | None = None
        async with self._client() as client:
            repos = (
                [hp.repository] if hp.repository
                else await self._list_repositories(client, hp.project)
            )
            for repo in repos:
                for artifact in await self._artifacts(client, hp.project, repo):
                    count += 1
                    raw = artifact.get("push_time")
                    try:
                        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if best is None or when > best[0]:
                        tags = artifact.get("tags") or []
                        tag = tags[0].get("name") if tags and isinstance(tags[0], dict) else None
                        best = (when, tag)
        return RegistryImage(
            present=count > 0,
            tag=best[1] if best else None,
            pushed_at=best[0] if best else None,
            artifacts=count,
        )

    async def fetch_summary(self, project: ProjectConfig) -> FindingsSummary:
        data = await self._scan_data(project)
        counts = SeverityCount(**data["counts"])
        return FindingsSummary(counts=counts, total_active=data["total"])

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        data = await self._scan_data(project)
        return SourceDetail(
            project_key=project.key,
            source="harbor",
            status="ok",
            payload=data,
        )
