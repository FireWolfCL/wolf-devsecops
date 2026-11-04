"""TrivyConnector — lee un reporte Trivy JSON publicado como artefacto (ADR-006).

Trivy es un CLI, no un servicio: este conector NO ejecuta Trivy (ADR-001), lee un
reporte `trivy ... --format json` (SchemaVersion 2) desde una URL/artefacto y
normaliza sus vulnerabilidades y misconfiguraciones a `FindingsSummary`.

Útil para proyectos que corren Trivy en su CI pero no tienen DefectDojo ni Harbor.
`report_path` es relativo a la URL base de la fuente `trivy`.
"""

from __future__ import annotations

import httpx

from ..core.config import ProjectConfig
from ..models import FindingsSummary, ProbeResult, SeverityCount, SourceDetail
from .base import Connector, ConnectorUnavailable

# Trivy usa severidades en MAYÚSCULAS; UNKNOWN lo tratamos como info.
_TRIVY_SEV = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "UNKNOWN": "info",
}


class TrivyConnector(Connector):
    source_name = "trivy"
    supported_versions = ">=2"  # Trivy JSON SchemaVersion 2

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
        # Trivy no tiene endpoint de salud: comprobamos que la base de reportes
        # responda. La validación real es por proyecto (al leer el reporte).
        try:
            async with self._client() as client:
                resp = await client.get(self._base, headers=self._headers())
            return ProbeResult(
                source=self.source_name,
                reachable=resp.status_code < 500,
                authenticated=bool(self.token),
                version=None,
                compatible=True,
                detail="Trivy = lector de reportes JSON; la validez se verifica por proyecto.",
            )
        except httpx.HTTPError:
            return ProbeResult(
                source=self.source_name,
                reachable=False,
                authenticated=False,
                compatible=False,
                detail="No se pudo contactar el almacén de reportes Trivy.",
            )

    async def _report(self, project: ProjectConfig) -> dict:
        tp = project.trivy
        if tp is None:
            raise ConnectorUnavailable("El proyecto no define bloque 'trivy'.")
        url = f"{self._base}/{tp.report_path.lstrip('/')}"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo descargar el reporte Trivy.") from exc
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"El reporte Trivy respondió HTTP {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise ConnectorUnavailable("El reporte Trivy no es JSON válido.") from exc

    @staticmethod
    def _id_of(item: dict) -> str:
        """Id normalizado (MAYÚSCULAS) de una vuln (VulnerabilityID) o misconfig (ID/AVDID)."""
        return str(item.get("VulnerabilityID") or item.get("ID") or item.get("AVDID") or "").upper()

    @classmethod
    def _counts(cls, report: dict, ignored: set[str] | None = None) -> dict[str, int]:
        ignored = ignored or set()
        counts = {s: 0 for s in ("critical", "high", "medium", "low", "info")}
        for result in report.get("Results") or []:
            if not isinstance(result, dict):
                continue
            # Vulnerabilidades de paquetes + misconfiguraciones (ambas con Severity).
            for key in ("Vulnerabilities", "Misconfigurations"):
                for item in result.get(key) or []:
                    if not isinstance(item, dict):
                        continue
                    if cls._id_of(item) in ignored:  # 6.3.c: excepción vigente → no cuenta
                        continue
                    sev = str(item.get("Severity", "")).upper()
                    norm = _TRIVY_SEV.get(sev, "info")
                    counts[norm] += 1
        return counts

    def _ignored(self, project: ProjectConfig) -> set[str]:
        """Ids suprimidos vigentes para este proyecto (config/wolf-ignore.yaml, 6.3.c)."""
        from ..core import ignore

        return ignore.active_ids_for(project.key)

    async def fetch_summary(self, project: ProjectConfig) -> FindingsSummary:
        report = await self._report(project)
        counts = self._counts(report, self._ignored(project))
        return FindingsSummary(counts=SeverityCount(**counts), total_active=sum(counts.values()))

    # Orden de severidad para priorizar la lista (crítico primero).
    _SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}

    @classmethod
    def _findings_list(cls, report: dict, cap: int = 150, ignored: set[str] | None = None) -> list[dict]:
        """Lista accionable: QUÉ está afectado y CÓMO arreglarlo (versión fijada).

        Normaliza vulnerabilidades (CVE + paquete + versión instalada → versión que
        lo corrige) y misconfiguraciones (ID + resolución). Ordena crítico primero
        y acota a `cap`. Sin rutas absolutas ni código: solo el target que Trivy ya
        publica (p. ej. el nombre del lockfile o de la imagen). Los ids con excepción
        vigente (6.3.c) se omiten para que la lista cuadre con el score.
        """
        ignored = ignored or set()
        out: list[dict] = []
        for r in report.get("Results") or []:
            if not isinstance(r, dict):
                continue
            target = r.get("Target")
            for v in r.get("Vulnerabilities") or []:
                if not isinstance(v, dict):
                    continue
                if cls._id_of(v) in ignored:
                    continue
                out.append({
                    "kind": "vuln",
                    "id": v.get("VulnerabilityID"),
                    "pkg": v.get("PkgName"),
                    "installed": v.get("InstalledVersion"),
                    "fixed": v.get("FixedVersion") or None,  # None = aún sin parche
                    "severity": str(v.get("Severity", "")).upper(),
                    "title": (v.get("Title") or v.get("Description") or "")[:180],
                    "target": target,
                })
            for m in r.get("Misconfigurations") or []:
                if not isinstance(m, dict):
                    continue
                if cls._id_of(m) in ignored:
                    continue
                out.append({
                    "kind": "misconfig",
                    "id": m.get("ID") or m.get("AVDID"),
                    "pkg": None,
                    "installed": None,
                    "fixed": (m.get("Resolution") or "")[:180] or None,
                    "severity": str(m.get("Severity", "")).upper(),
                    "title": (m.get("Title") or "")[:180],
                    "target": target,
                })
        out.sort(key=lambda x: cls._SEV_RANK.get(x["severity"], 5))
        return out[:cap]

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        report = await self._report(project)
        ignored = self._ignored(project)
        counts = self._counts(report, ignored)
        findings = self._findings_list(report, ignored=ignored)
        total = sum(counts.values())
        return SourceDetail(
            project_key=project.key,
            source="trivy",
            status="ok",
            payload={
                "counts": counts,
                "total": total,
                "shown": len(findings),  # cuántas trae la lista (acotada)
                "schema_version": report.get("SchemaVersion"),
                "artifact": report.get("ArtifactName"),
                "findings": findings,  # QUÉ está afectado + versión que lo corrige
                "targets": [
                    r.get("Target")
                    for r in (report.get("Results") or [])
                    if isinstance(r, dict)
                ],
            },
        )

    # ─── Inventario de dependencias (Feature A) ───────────────────────────────
    _MAX_PACKAGES = 5000  # acota la respuesta en imágenes enormes

    @classmethod
    def _inventory_from_report(cls, report: dict) -> tuple[list[dict], bool]:
        """(paquetes, degraded). `Results[].Packages` SOLO existe si el reporte se
        generó con `--list-all-pkgs`. Si un Result trae Vulnerabilities pero no
        Packages, ese Result se degrada a "solo paquetes con CVE" y degraded=True.
        """
        degraded = False
        merged: dict[tuple, dict] = {}

        def entry(target, eco, name, version) -> dict:
            k = (target, name, version)
            if k not in merged:
                merged[k] = {
                    "name": name, "version": version, "ecosystem": eco, "target": target,
                    "vulnerable": False, "cve_ids": [], "fixed_version": None,
                }
            return merged[k]

        for r in report.get("Results") or []:
            if not isinstance(r, dict):
                continue
            target = r.get("Target")
            eco = r.get("Type") or r.get("Class")
            pkgs = r.get("Packages")
            vulns = [v for v in (r.get("Vulnerabilities") or []) if isinstance(v, dict)]
            if pkgs is None and vulns:
                degraded = True
            for pkg in pkgs or []:
                if isinstance(pkg, dict) and pkg.get("Name"):
                    entry(target, eco, pkg["Name"], pkg.get("Version"))
            for v in vulns:
                name = v.get("PkgName")
                if not name:
                    continue
                e = entry(target, eco, name, v.get("InstalledVersion"))
                e["vulnerable"] = True
                cve = v.get("VulnerabilityID")
                if cve and cve not in e["cve_ids"]:
                    e["cve_ids"].append(cve)
                # Varios CVE: nos quedamos con el primer FixedVersion publicado.
                if e["fixed_version"] is None and v.get("FixedVersion"):
                    e["fixed_version"] = v["FixedVersion"]
        # Vulnerables primero, luego orden estable por nombre.
        out = sorted(merged.values(), key=lambda x: (not x["vulnerable"], x["name"], x["version"] or ""))
        return out[: cls._MAX_PACKAGES], degraded

    async def inventory(self, project: ProjectConfig) -> tuple[list[dict], bool]:
        return self._inventory_from_report(await self._report(project))

    async def packages(self, project: ProjectConfig) -> list[dict]:
        """Paquetes del reporte cruzados con sus CVE (solo lectura, ADR-001)."""
        return (await self.inventory(project))[0]
