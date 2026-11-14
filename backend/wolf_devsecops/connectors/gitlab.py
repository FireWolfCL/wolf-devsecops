"""GitLabConnector — CI (pipelines) y releases desde la API v4 de GitLab (1.3).

Hermano de GitHubConnector: es una fuente de "forge" (CI + release). Habla SOLO con
la API de GitLab y traduce al contrato normalizado (CISummary / ReleaseSummary). No
cachea ni agrega (eso es del aggregator). Tolerante a campos desconocidos (ADR-008).

Autenticación con PAT vía cabecera PRIVATE-TOKEN (ADR-002: el token se resuelve
server-side; aquí llega ya resuelto). `project` se URL-encodea para la API.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

import httpx

from ..core.config import ProjectConfig
from ..models import CIStatus, CISummary, ProbeResult, ProvenanceInfo, ReleaseSummary, SourceDetail
from .base import Connector, ConnectorUnavailable

# status de un pipeline GitLab → nuestro CIStatus normalizado.
_FAILURE = {"failed"}
_RUNNING = {"running", "pending", "created", "waiting_for_resource", "preparing", "scheduled"}
_SUCCESS = {"success"}


def _map_ci_status(status: str | None) -> CIStatus:
    s = (status or "").lower()
    if s in _SUCCESS:
        return CIStatus.success
    if s in _FAILURE:
        return CIStatus.failure
    if s in _RUNNING:
        return CIStatus.running
    return CIStatus.unknown  # canceled, skipped, manual, None → desconocido


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GitLabConnector(Connector):
    source_name = "gitlab"
    supported_versions = ">=15"  # API v4 estable

    def __init__(self, source_config, token, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(source_config, token)
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10.0, transport=self._transport)

    @property
    def _api(self) -> str:
        base = self.source_config.url.rstrip("/")
        return base if base.endswith("/api/v4") else f"{base}/api/v4"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
        return headers

    @staticmethod
    def _pid(project: ProjectConfig) -> str:
        return quote(project.gitlab.project, safe="")  # "grupo/repo" → "grupo%2Frepo"

    async def probe(self) -> ProbeResult:
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._api}/version", headers=self._headers())
            authenticated = resp.status_code == 200 and bool(self.token)
            version = None
            if resp.status_code == 200:
                try:
                    version = resp.json().get("version")
                except ValueError:
                    version = None
            return ProbeResult(
                source=self.source_name,
                reachable=True,
                authenticated=authenticated,
                version=version,
                compatible=True,
                detail=None if authenticated else "Sin token válido (/version requiere autenticación).",
            )
        except httpx.HTTPError:
            return ProbeResult(
                source=self.source_name, reachable=False, authenticated=False,
                compatible=False, detail="No se pudo contactar la API de GitLab.",
            )

    async def _latest_pipeline(self, project: ProjectConfig) -> dict | None:
        gl = project.gitlab
        if gl is None:
            return None
        params: dict[str, str] = {"per_page": "1", "order_by": "updated_at", "sort": "desc"}
        if gl.ref:
            params["ref"] = gl.ref
        url = f"{self._api}/projects/{self._pid(project)}/pipelines"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers(), params=params)
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar la API de GitLab.") from exc
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"GitLab respondió HTTP {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ConnectorUnavailable("Respuesta de GitLab no es JSON válido.") from exc
        return data[0] if isinstance(data, list) and data else None

    async def fetch_provenance(self, project: ProjectConfig) -> ProvenanceInfo:
        """Procedencia read-only (ADR-001): último commit del branch por defecto +
        autor, SIN depender de pipelines. 2 GET: proyecto (default_branch) y el último
        commit de ese branch. La API de commits de GitLab no expone username, así que
        `author` es el nombre git (`author_is_login=False`); si falta, None → "desconocido"."""
        gl = project.gitlab
        if gl is None:
            raise ConnectorUnavailable("El proyecto no define bloque 'gitlab'.")
        pid = self._pid(project)
        try:
            async with self._client() as client:
                proj_resp = await client.get(
                    f"{self._api}/projects/{pid}", headers=self._headers()
                )
                if proj_resp.status_code != 200:
                    raise ConnectorUnavailable(f"GitLab respondió HTTP {proj_resp.status_code}.")
                branch = gl.ref or proj_resp.json().get("default_branch") or "HEAD"
                commit_resp = await client.get(
                    f"{self._api}/projects/{pid}/repository/commits",
                    headers=self._headers(),
                    params={"per_page": "1", "ref_name": branch},
                )
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar la API de GitLab.") from exc
        except ValueError as exc:
            raise ConnectorUnavailable("Respuesta de GitLab no es JSON válido.") from exc
        if commit_resp.status_code != 200:
            raise ConnectorUnavailable(f"GitLab respondió HTTP {commit_resp.status_code}.")
        try:
            data = commit_resp.json()
        except ValueError as exc:
            raise ConnectorUnavailable("Respuesta de GitLab no es JSON válido.") from exc
        if not isinstance(data, list) or not data:
            return ProvenanceInfo(default_branch=branch, source="gitlab")
        c = data[0]
        return ProvenanceInfo(
            default_branch=branch,
            sha_short=str(c.get("short_id") or c.get("id") or "")[:7] or None,
            message=(c.get("title") or None),
            author=(c.get("author_name") or None),
            author_is_login=False,  # la API de commits de GitLab no da username
            authored_at=_parse_dt(c.get("authored_date") or c.get("created_at")),
            html_url=c.get("web_url"),
            source="gitlab",
        )

    async def fetch_summary(self, project: ProjectConfig) -> CISummary:
        gl = project.gitlab
        workflow = (gl.ref if gl and gl.ref else "pipeline")
        pipe = await self._latest_pipeline(project)
        if pipe is None:
            return CISummary(status=CIStatus.unknown, workflow=workflow)
        return CISummary(
            status=_map_ci_status(pipe.get("status")),
            workflow=workflow,
            branch=pipe.get("ref"),
            run_number=pipe.get("id"),
            html_url=pipe.get("web_url"),
            finished_at=_parse_dt(pipe.get("updated_at")),
        )

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        pipe = await self._latest_pipeline(project)
        return SourceDetail(
            project_key=project.key,
            source="gitlab",
            status="ok",
            payload={"latest_pipeline": pipe or {}},
        )

    async def fetch_release(self, project: ProjectConfig) -> ReleaseSummary:
        """Último release del proyecto y, si existe, el asset SBOM (F5)."""
        gl = project.gitlab
        if gl is None:
            return ReleaseSummary()
        url = f"{self._api}/projects/{self._pid(project)}/releases"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers(), params={"per_page": "1"})
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar la API de GitLab.") from exc
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"GitLab respondió HTTP {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ConnectorUnavailable("Respuesta de GitLab no es JSON válido.") from exc
        if not isinstance(data, list) or not data:
            return ReleaseSummary()  # aún no hay releases
        rel = data[0]
        sbom_url = None
        for link in ((rel.get("assets") or {}).get("links") or []):
            name = str(link.get("name", "")).lower()
            if "sbom" in name or name.endswith(".cyclonedx.json"):
                sbom_url = link.get("url")
                break
        links = rel.get("_links") or {}
        return ReleaseSummary(
            tag=rel.get("tag_name"),
            name=rel.get("name"),
            published_at=_parse_dt(rel.get("released_at") or rel.get("created_at")),
            html_url=links.get("self"),
            sbom_url=sbom_url,
        )
