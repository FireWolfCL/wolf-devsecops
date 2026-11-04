"""JenkinsConnector — estado del CI desde la API JSON de Jenkins (MEJORAS 1.4).

Mismo patrón que GitHub/GitLab pero Jenkins es CI-only (no tiene releases nativas).
Lee `/job/<job>/lastBuild/api/json` y traduce al contrato normalizado (CISummary).
Habla SOLO con Jenkins; no agrega ni cachea (eso es del aggregator). Tolerante a
campos desconocidos (ADR-008).

Auth: Jenkins usa Basic con `usuario:api_token`. El token (resuelto server-side,
ADR-002) se espera en ese formato; se envía como cabecera Authorization: Basic.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from ..core.config import ProjectConfig
from ..models import CIStatus, CISummary, ProbeResult, SourceDetail
from .base import Connector, ConnectorUnavailable

# result de un build Jenkins → CIStatus. result=null + building=true → corriendo.
_FAILURE = {"FAILURE", "UNSTABLE", "ABORTED"}


def _map_ci_status(result: str | None, building: bool) -> CIStatus:
    if building or result is None:
        return CIStatus.running if building else CIStatus.unknown
    r = result.upper()
    if r == "SUCCESS":
        return CIStatus.success
    if r in _FAILURE:
        return CIStatus.failure
    return CIStatus.unknown  # NOT_BUILT u otros


class JenkinsConnector(Connector):
    source_name = "jenkins"
    supported_versions = ">=2"  # Jenkins 2.x (cabecera X-Jenkins)

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
            # `usuario:api_token` → Basic. Si no trae ':', se envía igual (algunos
            # setups aceptan solo el token como usuario).
            headers["Authorization"] = "Basic " + base64.b64encode(self.token.encode()).decode()
        return headers

    async def probe(self) -> ProbeResult:
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base}/api/json", headers=self._headers())
            version = resp.headers.get("X-Jenkins")
            authenticated = resp.status_code == 200 and bool(self.token)
            return ProbeResult(
                source=self.source_name,
                reachable=resp.status_code < 500,
                authenticated=authenticated,
                version=version,
                compatible=True,
                detail=None if authenticated else "Sin credenciales válidas (usuario:api_token).",
            )
        except httpx.HTTPError:
            return ProbeResult(
                source=self.source_name, reachable=False, authenticated=False,
                compatible=False, detail="No se pudo contactar Jenkins.",
            )

    async def _last_build(self, project: ProjectConfig) -> dict | None:
        jk = project.jenkins
        if jk is None:
            return None
        # `job` puede ser anidado ("carpeta/job/hijo"); se permite '/' en el path.
        url = f"{self._base}/job/{quote(jk.job, safe='/')}/lastBuild/api/json"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar Jenkins.") from exc
        if resp.status_code == 404:
            return None  # el job aún no tiene builds
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"Jenkins respondió HTTP {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise ConnectorUnavailable("Respuesta de Jenkins no es JSON válido.") from exc

    async def fetch_summary(self, project: ProjectConfig) -> CISummary:
        jk = project.jenkins
        workflow = jk.job if jk else "job"
        build = await self._last_build(project)
        if build is None:
            return CISummary(status=CIStatus.unknown, workflow=workflow)
        ts = build.get("timestamp")
        finished = (
            datetime.fromtimestamp(ts / 1000, tz=UTC)
            if isinstance(ts, (int, float)) and not build.get("building")
            else None
        )
        return CISummary(
            status=_map_ci_status(build.get("result"), bool(build.get("building"))),
            workflow=workflow,
            branch=None,  # Jenkins no expone la rama de forma uniforme
            run_number=build.get("number"),
            html_url=build.get("url"),
            finished_at=finished,
        )

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        build = await self._last_build(project)
        return SourceDetail(
            project_key=project.key,
            source="jenkins",
            status="ok",
            payload={"last_build": build or {}},
        )
