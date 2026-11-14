"""DefectDojoConnector — hallazgos unificados (ARCHITECTURE.md §2, F3).

Lee los findings activos de un producto en DefectDojo y los normaliza a
FindingsSummary. Tolerante a campos desconocidos. Transporte inyectable para los
tests de contrato.
"""

from __future__ import annotations

import httpx

from ..core.config import ProjectConfig
from ..core.cve import extract_cve
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

# Categorías de hallazgo por tipo de escaneo (6.2). Wolf no corre estos escáneres:
# corren en el CI del proyecto y reportan a DefectDojo; aquí solo LEEMOS y agrupamos.
# El nombre del test_type (p.ej. "Gitleaks Scan", "Semgrep JSON Report") se mapea a una
# categoría estable. Orden de evaluación importa: lo específico antes que lo genérico.
FINDING_CATEGORIES = ("secrets", "sast", "sca", "container", "iac", "dast", "other")
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("secrets", ("gitleaks", "trufflehog", "detect-secrets", "secret")),
    ("dast", ("zap", "burp", "nikto", "nuclei", "dast", "wfuzz", "arachni")),
    ("iac", ("checkov", "kics", "terrascan", "tfsec", "trivy config", "trivy operator",
             "cloudformation", "terraform", "kubescape", "iac")),
    ("sca", ("grype", "snyk", "dependency", "npm audit", "yarn audit", "safety", "retire",
             "osv", "composer", "bundler", "sca", "trivy")),  # trivy fs ≈ deps → sca
    ("container", ("anchore", "clair", "container", "docker", "image scan")),
    ("sast", ("semgrep", "bandit", "sarif", "spotbugs", "checkmarx", "eslint", "gosec",
              "brakeman", "sonar", "codeql", "sast")),
)


def _scan_type_name(finding: dict) -> str:
    """Nombre del test_type de un finding (DefectDojo `related_fields=true`), tolerante
    a que falte: related_fields.test.test_type.name → tags → ''."""
    rel = finding.get("related_fields")
    if isinstance(rel, dict):
        test = rel.get("test")
        if isinstance(test, dict):
            tt = test.get("test_type")
            if isinstance(tt, dict) and tt.get("name"):
                return str(tt["name"])
            if isinstance(tt, str) and tt:
                return tt
    tags = finding.get("tags")
    if isinstance(tags, list) and tags:
        return " ".join(str(t) for t in tags)
    return ""


def _category(finding: dict) -> str:
    """Categoría estable del finding a partir del nombre del escáner. 'other' si no calza."""
    name = _scan_type_name(finding).lower()
    if name:
        for cat, needles in _CATEGORY_RULES:
            if any(n in name for n in needles):
                return cat
    return "other"


def _is_managed_out(finding: dict) -> bool:
    """¿El finding está gestionado como NO-abierto y por tanto NO debe pesar en el
    score? (6.3.a) — falso positivo, riesgo aceptado, fuera de alcance o mitigado.

    DefectDojo ya los marca `active=false` y la query pide `active=true`, así que en
    producción no llegan; esto es defensa en profundidad: si el servidor (o un cambio
    futuro del filtro) los devuelve igual, no se cuentan. Falso positivo ≠ riesgo
    aceptado, pero ambos salen del conteo del puntaje.
    """
    return bool(
        finding.get("false_p")
        or finding.get("risk_accepted")
        or finding.get("out_of_scope")
        or finding.get("is_mitigated")
        or finding.get("mitigated")
    )


def _finding_status(finding: dict) -> str:
    """Estado de gestión del finding en DefectDojo (2.3: el solape Trivy↔DefectDojo).

    Un CVE que aparece también en Contenedores se marca 'en triage' leyendo este
    estado: el equipo ya lo está tratando. Orden de prioridad del rótulo:
    riesgo aceptado > en revisión > mitigado > verificado > activo.
    """
    if finding.get("risk_accepted"):
        return "risk_accepted"
    if finding.get("under_review"):
        return "under_review"
    if finding.get("is_mitigated") or finding.get("mitigated"):
        return "mitigated"
    if finding.get("verified"):
        return "verified"
    return "active"


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
        # related_fields=true trae test.test_type.name para categorizar por tipo de
        # escaneo (6.2) sin una segunda llamada (ADR-012: una lectura por fuente).
        params = {
            "product_name": product, "active": "true", "limit": 100,
            "related_fields": "true",
        }
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
        categories: dict[str, int] = {}
        excluded = 0  # findings gestionados-fuera devueltos pese al filtro active=true
        for finding in payload.get("results", []):
            if _is_managed_out(finding):  # 6.3.a: no pesa en el score
                excluded += 1
                continue
            key = _SEV_MAP.get(str(finding.get("severity", "")).lower())
            if key:
                setattr(counts, key, getattr(counts, key) + 1)
            cat = _category(finding)
            categories[cat] = categories.get(cat, 0) + 1
        # `count` es el total del API (con paginación); restamos los que el guardia
        # atrapó en esta página para no inflar total_active con gestionados-fuera.
        total = payload.get("count", counts.total) - excluded
        return FindingsSummary(counts=counts, total_active=total, categories=categories)

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
            if _is_managed_out(f):  # 6.3.a: fuera del score → fuera del drill-down activo
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
                "category": _category(f),  # secrets|sast|sca|container|iac|dast|other (6.2)
                "status": _finding_status(f),  # gestión en DefectDojo → 'en triage' (2.3)
                "ref": str(f["id"]) if f.get("id") is not None else None,  # id DB para escritura (ADR-021)
                "id": f.get("cve") or f.get("vuln_id_from_tool") or None,
                # CVE canónico para el seguimiento de parche de riesgos aceptados (ADR-021).
                # Orden: campo `cve` → `vulnerability_ids` → id de la herramienta → título.
                "cve": extract_cve(f.get("cve"), f.get("vulnerability_ids"),
                                   f.get("vuln_id_from_tool"), f.get("title")),
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

    # ─── ESCRITURA acotada (ADR-021): fija el estado nativo de un finding ───────
    # Única escritura de este conector. Usa el token de lectura (ADR-002/020); si no
    # tiene permiso, 403 saneado. Para suprimir: active=False + el flag; revocar:
    # active=True + flags en False.
    async def set_finding_state(
        self,
        finding_id: str,
        *,
        active: bool,
        false_p: bool = False,
        risk_accepted: bool = False,
        out_of_scope: bool = False,
        is_mitigated: bool = False,
    ) -> None:
        # finding_id es dato del usuario y va en el PATH: exigir id numérico de
        # DefectDojo evita cualquier traversal/inyección en la URL.
        if not str(finding_id).isdigit():
            raise ConnectorUnavailable("Id de finding inválido para DefectDojo (debe ser numérico).")
        url = f"{self._base}/api/v2/findings/{finding_id}/"
        payload = {
            "active": active,
            "false_p": false_p,
            "risk_accepted": risk_accepted,
            "out_of_scope": out_of_scope,
            "is_mitigated": is_mitigated,
        }
        try:
            async with self._client() as client:
                resp = await client.patch(url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar DefectDojo.") from exc
        if resp.status_code == 403:
            raise ConnectorUnavailable("El token de DefectDojo no tiene permiso de escritura.")
        if resp.status_code == 404:
            raise ConnectorUnavailable("El finding no existe en DefectDojo.")
        if resp.status_code not in (200, 202):
            raise ConnectorUnavailable(f"DefectDojo respondió HTTP {resp.status_code} al escribir el estado.")
