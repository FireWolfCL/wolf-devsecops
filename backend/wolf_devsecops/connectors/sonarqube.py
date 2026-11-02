"""SonarQubeConnector — calidad y cobertura (ARCHITECTURE.md §2, F4).

Lee el quality gate y las métricas de un proyecto en SonarQube y las normaliza a
QualitySummary. Transporte inyectable para los tests de contrato.
"""

from __future__ import annotations

import httpx

from ..core.config import ProjectConfig
from ..models import ProbeResult, QualitySummary, SourceDetail
from .base import Connector, ConnectorUnavailable

_GATE_MAP = {"OK": "OK", "WARN": "WARN", "ERROR": "ERROR", "NONE": "NONE"}


class SonarQubeConnector(Connector):
    source_name = "sonarqube"
    supported_versions = ">=9.9"

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
                resp = await client.get(f"{self._base}/api/system/status", headers=self._headers())
            body = resp.json() if resp.status_code == 200 else {}
            return ProbeResult(
                source=self.source_name,
                reachable=True,
                authenticated=bool(self.token),
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
                detail="No se pudo contactar SonarQube.",
            )

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict) -> dict:
        resp = await client.get(f"{self._base}{path}", headers=self._headers(), params=params)
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"SonarQube respondió HTTP {resp.status_code}.")
        return resp.json()

    async def fetch_summary(self, project: ProjectConfig) -> QualitySummary:
        sq = project.sonarqube
        project_key = sq.project_key if sq else ""
        try:
            async with self._client() as client:
                gate = await self._get(
                    client, "/api/qualitygates/project_status", {"projectKey": project_key}
                )
                measures = await self._get(
                    client,
                    "/api/measures/component",
                    {
                        "component": project_key,
                        "metricKeys": "bugs,vulnerabilities,code_smells,coverage",
                    },
                )
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar SonarQube.") from exc

        gate_status = gate.get("projectStatus", {}).get("status", "NONE")
        values: dict[str, str] = {
            m.get("metric"): m.get("value")
            for m in measures.get("component", {}).get("measures", [])
        }

        def _int(metric: str) -> int:
            try:
                return int(values.get(metric, 0))
            except (TypeError, ValueError):
                return 0

        coverage = None
        if values.get("coverage") is not None:
            try:
                coverage = float(values["coverage"])
            except (TypeError, ValueError):
                coverage = None

        return QualitySummary(
            quality_gate=_GATE_MAP.get(gate_status, "NONE"),
            bugs=_int("bugs"),
            vulnerabilities=_int("vulnerabilities"),
            code_smells=_int("code_smells"),
            coverage=coverage,
        )

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        summary = await self.fetch_summary(project)
        return SourceDetail(
            project_key=project.key,
            source="sonarqube",
            status="ok",
            payload=summary.model_dump(),
        )
