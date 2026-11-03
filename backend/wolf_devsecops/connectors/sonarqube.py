"""SonarQubeConnector — calidad y cobertura (ARCHITECTURE.md §2, F4).

Lee el quality gate y las métricas de un proyecto en SonarQube y las normaliza a
QualitySummary. Transporte inyectable para los tests de contrato.
"""

from __future__ import annotations

import asyncio
import html
import re

import httpx

from ..core.config import ProjectConfig
from ..models import ProbeResult, QualitySummary, SourceDetail
from .base import Connector, ConnectorUnavailable

_GATE_MAP = {"OK": "OK", "WARN": "WARN", "ERROR": "ERROR", "NONE": "NONE"}

# Severidad de Sonar → nuestra escala (para el pill del reporte). El reporte de
# Sonar es independiente del conteo de postura, así que no hay doble conteo.
_SEV_MAP = {
    "BLOCKER": "CRITICAL", "CRITICAL": "CRITICAL",
    "MAJOR": "HIGH", "MINOR": "MEDIUM", "INFO": "LOW",
    # Modo MQR (Sonar 10.x): impacts con severidad HIGH/MEDIUM/LOW.
    "HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW",
}
_TYPE_LABEL = {"BUG": "Bug", "VULNERABILITY": "Vulnerability", "CODE_SMELL": "Code smell"}

_MAX_ISSUES = 50      # tope de issues listados (crítico primero)
_FIX_MAXLEN = 400     # las descripciones de regla de Sonar son largas: acotamos
_EVIDENCE_LIMIT = 30  # tope de issues para los que traemos líneas de código
_EVIDENCE_CONTEXT = 2  # líneas arriba/abajo de la línea señalada
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str | None) -> str:
    """HTML de Sonar (htmlDesc / code de /sources/lines) → texto plano."""
    if not raw:
        return ""
    text = _TAG_RE.sub("", raw)
    return html.unescape(text).strip()


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

    @staticmethod
    def _rel_path(component: str, project_key: str) -> str:
        """`<projectKey>:ruta/al/archivo` → `ruta/al/archivo` (la key puede tener ':')."""
        prefix = f"{project_key}:"
        return component[len(prefix):] if component.startswith(prefix) else component

    @staticmethod
    def _issue_severity(issue: dict) -> str:
        sev = issue.get("severity")
        if not sev:
            impacts = issue.get("impacts") or []
            ranks = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
            sev = max((i.get("severity", "") for i in impacts), key=lambda s: ranks.get(s, 0), default="")
        return _SEV_MAP.get(sev, "UNKNOWN")

    async def _rule_howto(self, client: httpx.AsyncClient, rule_key: str) -> str:
        """Texto 'cómo arreglarlo' de una regla (sección how_to_fix, si existe)."""
        try:
            data = await self._get(client, "/api/rules/show", {"key": rule_key})
        except (httpx.HTTPError, ConnectorUnavailable):
            return ""
        rule = data.get("rule", {})
        text = ""
        for sec in rule.get("descriptionSections", []) or []:
            if sec.get("key") == "how_to_fix":
                text = _strip_html(sec.get("content"))
                break
        if not text:  # Sonar antiguo: solo htmlDesc (toda la descripción).
            text = _strip_html(rule.get("htmlDesc")) or (rule.get("name") or "")
        if len(text) > _FIX_MAXLEN:
            text = text[:_FIX_MAXLEN].rstrip() + "…"
        return text

    async def _evidence(self, client: httpx.AsyncClient, component: str, line: int) -> list[dict]:
        """Líneas de código ±N alrededor de la señalada (API /sources/lines de Sonar)."""
        frm = max(1, line - _EVIDENCE_CONTEXT)
        to = line + _EVIDENCE_CONTEXT
        try:
            data = await self._get(
                client, "/api/sources/lines", {"key": component, "from": frm, "to": to}
            )
        except (httpx.HTTPError, ConnectorUnavailable):
            return []  # p. ej. el token no tiene permiso "Browse": degradar sin romper
        return [
            {"line": s.get("line"), "code": _strip_html(s.get("code"))}
            for s in data.get("sources", [])
        ]

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        summary = await self.fetch_summary(project)
        sq = project.sonarqube
        project_key = sq.project_key if sq else ""
        findings: list[dict] = []
        try:
            async with self._client() as client:
                search = await self._get(
                    client,
                    "/api/issues/search",
                    {
                        "components": project_key,
                        "resolved": "false",
                        "types": "BUG,VULNERABILITY,CODE_SMELL",
                        "s": "SEVERITY",
                        "asc": "false",
                        "ps": _MAX_ISSUES,
                    },
                )
                issues = search.get("issues", [])[:_MAX_ISSUES]

                # Remediación por regla (única) y evidencia (acotada), en paralelo.
                rule_keys = {i.get("rule") for i in issues if i.get("rule")}
                howto_list = await asyncio.gather(
                    *(self._rule_howto(client, rk) for rk in rule_keys)
                )
                howto = dict(zip(rule_keys, howto_list, strict=True))

                # Evidencia: NUNCA para reglas de secretos (la "evidencia" sería el
                # secreto mismo; el archivo:línea + "revócalo" ya es accionable).
                ev_issues = [
                    i for i in issues
                    if i.get("line") and not str(i.get("rule", "")).startswith("secrets:")
                ][:_EVIDENCE_LIMIT]
                ev_list = await asyncio.gather(
                    *(self._evidence(client, i["component"], int(i["line"])) for i in ev_issues)
                )
                evidence = {id(i): ev for i, ev in zip(ev_issues, ev_list, strict=True)}

                for i in issues:
                    comp = i.get("component", "")
                    line = i.get("line")
                    path = self._rel_path(comp, project_key)
                    target = f"{path}:{line}" if line else path
                    findings.append({
                        "kind": "sonar",
                        "id": i.get("rule"),
                        "pkg": None,
                        "installed": None,
                        "fixed": None,
                        "fix": howto.get(i.get("rule")) or None,
                        "severity": self._issue_severity(i),
                        "title": i.get("message") or _TYPE_LABEL.get(i.get("type", ""), "—"),
                        "target": target or None,
                        "evidence": evidence.get(id(i), []),
                    })
        except (httpx.HTTPError, ConnectorUnavailable):
            findings = []  # el resumen (gate/bugs/vulns) ya viajó; los issues degradan

        payload = summary.model_dump()
        payload["findings"] = findings
        payload["total"] = len(findings)
        payload["shown"] = len(findings)
        return SourceDetail(
            project_key=project.key,
            source="sonarqube",
            status="ok",
            payload=payload,
        )
