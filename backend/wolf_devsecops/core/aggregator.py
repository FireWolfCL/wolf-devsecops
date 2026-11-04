"""Agregador: orquesta los conectores de un proyecto → ProjectSummary.

Responsabilidades (ARCHITECTURE.md §2):
- construir cada conector a partir de la config y su token,
- ejecutarlos en paralelo,
- aplicar caché (TTL) y degradación por fuente,
- ensamblar el contrato normalizado.

Degradación (fail-open en disponibilidad): si una fuente falla, su bloque se marca
`unavailable` y el resto del proyecto se muestra igual. Una fuente no definida para
el proyecto es `not_configured`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import quote

from ..connectors.base import Connector, ConnectorUnavailable
from ..connectors.registry import CONNECTOR_REGISTRY
from ..models import (
    CISummary,
    DependencyInventory,
    DependencyItem,
    FindingsSummary,
    GlobalPosture,
    PostureOverview,
    ProjectListItem,
    ProjectSummary,
    QualitySummary,
    SeverityCount,
    SourceDetail,
    SourceResult,
    SourceStatus,
    ToolDashboard,
    ToolRow,
)
from ..settings import get_settings
from .cache import TTLCache
from .config import AppConfig, ProjectConfig
from .credentials import resolve_token
from .posture import score_global, score_project

_NEGATIVE_TTL_SECONDS = 10  # cuánto se recuerda un fallo de fuente

# Para el semáforo del listado: cuán "malo" es cada estado.
_STATUS_RANK = {
    SourceStatus.unavailable: 3,
    SourceStatus.stale: 2,
    SourceStatus.ok: 1,
    SourceStatus.not_configured: 0,
}


class Aggregator:
    def __init__(self, config: AppConfig, cache: TTLCache) -> None:
        self._config = config
        self._cache = cache
        settings = get_settings()
        self._settings = settings
        self._timeout = settings.source_timeout_seconds
        self._sem = asyncio.Semaphore(settings.max_upstream_concurrency)
        self._neg = TTLCache(_NEGATIVE_TTL_SECONDS, 0)
        self._inflight: dict[str, asyncio.Task] = {}

    # ─── construcción de conectores ──────────────────────────────────────────

    def _build_connector(self, source_name: str) -> Connector | None:
        source = self._config.sources.get(source_name)
        cls = CONNECTOR_REGISTRY.get(source_name)
        if source is None or cls is None:
            return None
        token = resolve_token(source.token_env, source.token_cipher)  # BD cifrada > env
        return cls(source, token)

    def connector_for(self, source_name: str) -> Connector | None:
        """Conector con token resuelto para una fuente (lo usa la escritura acotada
        ADR-021). None si la fuente no está configurada."""
        return self._build_connector(source_name)

    def _project_uses(self, project: ProjectConfig, source_name: str) -> bool:
        return getattr(project, source_name, None) is not None

    # ─── obtención de un bloque, con caché y degradación ──────────────────────

    async def _cached_fetch(self, cache_key: str, call) -> SourceResult:
        """Cache-first + single-flight + timeout por fuente + degradación.

        1. Dato fresco en caché → se sirve sin tocar la fuente.
        2. Si no, UNA sola llamada por clave (las demás peticiones concurrentes
           esperan su resultado: sin stampede), acotada por `source_timeout_seconds`
           y por el semáforo global de salida.
        3. Si la fuente falla → último dato conocido como `stale`, o `unavailable`.
        """
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached
        # Caché negativa corta: una fuente caída no se reintenta en cada request.
        failed = await self._neg.get(cache_key)
        if failed is not None:
            return failed

        # Single-flight: la primera petición lanza la carga como Task propia (un
        # cliente que se desconecta no la cancela); las demás esperan ESE resultado,
        # también si es un fallo (sin reintentos serializados contra una fuente caída).
        task = self._inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(self._load(cache_key, call))
            self._inflight[cache_key] = task
            task.add_done_callback(lambda _t, k=cache_key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _load(self, cache_key: str, call) -> SourceResult:
        try:
            async with self._sem:
                data = await asyncio.wait_for(call(), timeout=self._timeout)
            result = SourceResult(status=SourceStatus.ok, data=data, fetched_at=datetime.now(UTC))
            await self._cache.set(cache_key, result)
            return result
        except (ConnectorUnavailable, TimeoutError) as exc:
            stale = await self._cache.get_stale(cache_key)
            msg = (
                str(exc)
                if isinstance(exc, ConnectorUnavailable)
                else "La fuente excedió el tiempo máximo de respuesta."
            )
            if stale is not None:
                failure = SourceResult(
                    status=SourceStatus.stale,
                    data=stale.data,
                    error=msg,
                    fetched_at=stale.fetched_at,
                )
            else:
                failure = SourceResult(status=SourceStatus.unavailable, error=msg)
            await self._neg.set(cache_key, failure)
            return failure

    async def _fetch_block(self, project: ProjectConfig, source_name: str) -> SourceResult:
        # ¿la fuente está definida tanto en la config global como en el proyecto?
        if source_name not in self._config.sources or not self._project_uses(project, source_name):
            return SourceResult(status=SourceStatus.not_configured)

        connector = self._build_connector(source_name)
        if connector is None:
            return SourceResult(status=SourceStatus.not_configured)

        return await self._cached_fetch(
            f"{project.key}:{source_name}:summary", lambda: connector.fetch_summary(project)
        )

    async def _containers_block(self, project: ProjectConfig) -> SourceResult:
        """Postura de contenedores: Harbor + Trivy fusionados en un FindingsSummary.

        Fail-open: suma los counts de las sub-fuentes con dato. `not_configured`
        solo si el proyecto no usa ninguna; `unavailable` si usa alguna y todas
        están caídas; `stale`/`ok` según lo mejor disponible.
        """
        subs = [
            await self._fetch_block(project, name)
            for name in ("harbor", "trivy")
            if name in self._config.sources and self._project_uses(project, name)
        ]
        if not subs:
            return SourceResult(status=SourceStatus.not_configured)

        merged = SeverityCount()
        total = 0
        fetched = None
        for s in subs:
            if s.data is not None:
                c = s.data.counts
                merged.critical += c.critical
                merged.high += c.high
                merged.medium += c.medium
                merged.low += c.low
                merged.info += c.info
                total += s.data.total_active
                if s.fetched_at and (fetched is None or s.fetched_at > fetched):
                    fetched = s.fetched_at

        if any(s.status == SourceStatus.ok for s in subs):
            status = SourceStatus.ok
        elif any(s.status == SourceStatus.stale for s in subs):
            status = SourceStatus.stale
        else:
            return SourceResult(
                status=SourceStatus.unavailable,
                error="Las fuentes de contenedor (Harbor/Trivy) no están disponibles.",
            )
        return SourceResult(
            status=status,
            data=FindingsSummary(counts=merged, total_active=total),
            fetched_at=fetched,
        )

    # ─── API pública ──────────────────────────────────────────────────────────

    def _web_links(self, project: ProjectConfig) -> dict[str, str]:
        """Deep-links de salida hacia cada herramienta, con su URL PÚBLICA
        (la que navega el operador; distinta de la URL interna de la API).

        DATO de confianza: se construyen SOLO desde la config del proyecto y los
        settings, nunca desde la respuesta de una fuente. Los componentes van
        URL-encoded para que ningún valor rompa el href.
        """
        s = self._settings
        links: dict[str, str] = {}
        sonar = s.tool_web_url("sonarqube")
        if project.sonarqube and sonar:
            links["sonarqube"] = f"{sonar}/dashboard?id={quote(project.sonarqube.project_key, safe='')}"
        dd = s.tool_web_url("defectdojo")
        if project.defectdojo and dd:
            links["defectdojo"] = f"{dd}/product?name={quote(project.defectdojo.product, safe='')}"
        harbor = s.tool_web_url("harbor")
        if project.harbor and harbor:
            links["harbor"] = f"{harbor}/harbor/projects"
        if project.github and project.github.repo:
            links["github"] = f"https://github.com/{quote(project.github.repo, safe='/')}"
        if project.gitlab and "gitlab" in self._config.sources:
            gl_base = self._config.sources["gitlab"].url.rstrip("/")
            if gl_base.endswith("/api/v4"):
                gl_base = gl_base[: -len("/api/v4")].rstrip("/")
            links["gitlab"] = f"{gl_base}/{quote(project.gitlab.project, safe='/')}"
        if project.jenkins and "jenkins" in self._config.sources:
            jk_base = self._config.sources["jenkins"].url.rstrip("/")
            links["jenkins"] = f"{jk_base}/job/{quote(project.jenkins.job, safe='/')}"
        return links

    # Qué enlace de proyecto corresponde a cada pestaña del dashboard de herramientas.
    _TOOL_LINK_SOURCE = {
        "sonarqube": "sonarqube",
        "defectdojo": "defectdojo",
        "containers": "harbor",  # "Contenedores" = Harbor + Trivy; el UI es Harbor
        "github": "github",
    }

    def _forge_source(self, project: ProjectConfig) -> str | None:
        """Qué fuente provee CI/release: GitHub, GitLab (1.3) o Jenkins (1.4, CI-only).
        Prioridad github > gitlab > jenkins si hay varios. None = sin forge."""
        for name in ("github", "gitlab", "jenkins"):
            if name in self._config.sources and self._project_uses(project, name):
                return name
        return None

    async def get_summary(self, project: ProjectConfig) -> ProjectSummary:
        forge = self._forge_source(project) or "github"
        ci, findings, quality, release, containers = await asyncio.gather(
            self._fetch_block(project, forge),
            self._fetch_block(project, "defectdojo"),
            self._fetch_block(project, "sonarqube"),
            self._release_block(project),
            self._containers_block(project),
        )
        summary = ProjectSummary(
            key=project.key,
            name=project.name,
            org=project.org,
            ci=ci,
            findings=findings,
            quality=quality,
            release=release,
            containers=containers,
            links=self._web_links(project),
            generated_at=datetime.now(UTC),
        )
        summary.posture = score_project(summary)  # al vuelo (ADR-012: no se persiste)
        return summary

    async def global_posture(self) -> PostureOverview:
        """% de cumplimiento POR organización (el de Wolf no se mezcla con externas)."""
        summaries = await asyncio.gather(
            *(self.get_summary(p) for p in self._config.projects)
        )
        by_org: dict[str, list] = {}
        for s in summaries:
            by_org.setdefault(s.org or "Wolf", []).append(s)
        orgs: list[GlobalPosture] = []
        for org, subs in by_org.items():
            gp = score_global(subs)
            gp.org = org
            orgs.append(gp)
        # Titular: Wolf si existe; si no, la organización con más proyectos.
        orgs.sort(key=lambda g: (g.org != "Wolf", -g.project_count, g.org))
        primary = orgs[0].org if orgs else "Wolf"
        return PostureOverview(orgs=orgs, primary=primary)

    # ─── Dashboard por herramienta (vista transpuesta) ────────────────────────
    @staticmethod
    def _dump(result: SourceResult) -> dict | None:
        return result.data.model_dump(mode="json") if result.data is not None else None

    def _tool_block(self, s: ProjectSummary, tool: str) -> tuple[SourceStatus, dict | None, str | None]:
        """(status, summary_dump, error) de una herramienta para un proyecto."""
        if tool == "github":
            ci, rel = s.ci, s.release
            if ci.status == SourceStatus.not_configured and rel.status == SourceStatus.not_configured:
                return SourceStatus.not_configured, None, None
            status = max((ci.status, rel.status), key=lambda st: _STATUS_RANK[st])
            return status, {"ci": self._dump(ci), "release": self._dump(rel)}, ci.error or rel.error
        block = {"sonarqube": s.quality, "defectdojo": s.findings, "containers": s.containers}[tool]
        return block.status, self._dump(block), block.error

    @staticmethod
    def _tool_totals(tool: str, rows: list[ToolRow]) -> dict:
        data = [r.summary for r in rows if r.summary]
        if tool in ("defectdojo", "containers"):
            agg = {k: 0 for k in ("critical", "high", "medium", "low", "info")}
            total = 0
            for d in data:
                for k in agg:
                    agg[k] += (d.get("counts") or {}).get(k, 0)
                total += d.get("total_active", 0) or 0
            return {"counts": agg, "total_active": total}
        if tool == "sonarqube":
            gates = {g: 0 for g in ("OK", "WARN", "ERROR", "NONE")}
            bugs = vulns = 0
            for d in data:
                gates[d.get("quality_gate", "NONE")] = gates.get(d.get("quality_gate", "NONE"), 0) + 1
                bugs += d.get("bugs", 0) or 0
                vulns += d.get("vulnerabilities", 0) or 0
            return {"gates": gates, "bugs": bugs, "vulnerabilities": vulns}
        if tool == "github":
            ci_counts = {c: 0 for c in ("success", "failure", "running", "unknown")}
            releases = sbom = 0
            for d in data:
                ci = d.get("ci") or {}
                st = ci.get("status")
                if st in ci_counts:
                    ci_counts[st] += 1
                rel = d.get("release") or {}
                if rel.get("tag"):
                    releases += 1
                if rel.get("sbom_url"):
                    sbom += 1
            return {"ci": ci_counts, "releases": releases, "sbom": sbom}
        return {}

    async def get_tool_dashboard(self, tool: str) -> ToolDashboard:
        """Una herramienta, TODOS los proyectos que la usan (single pane of glass)."""
        summaries = await asyncio.gather(
            *(self.get_summary(p) for p in self._config.projects)
        )
        link_source = self._TOOL_LINK_SOURCE.get(tool)
        rows: list[ToolRow] = []
        for s in summaries:
            status, summary, error = self._tool_block(s, tool)
            if status == SourceStatus.not_configured:
                continue  # el proyecto no usa esta herramienta
            link = s.links.get(link_source) if link_source else None
            rows.append(
                ToolRow(key=s.key, name=s.name, status=status, summary=summary, error=error, link=link)
            )
        return ToolDashboard(
            tool=tool,
            rows=rows,
            totals=self._tool_totals(tool, rows),
            configured_count=len(rows),
            tool_url=self._tool_home(tool),
            generated_at=datetime.now(UTC),
        )

    def _tool_home(self, tool: str) -> str | None:
        """Home pública de una herramienta para el botón "Abrir en…" de la pestaña."""
        s = self._settings
        if tool == "sonarqube":
            base = s.tool_web_url("sonarqube")
            return f"{base}/projects" if base else None
        if tool == "defectdojo":
            base = s.tool_web_url("defectdojo")
            return f"{base}/product" if base else None
        if tool == "containers":
            base = s.tool_web_url("harbor")
            return f"{base}/harbor/projects" if base else None
        return None  # github: no hay "home" único; el enlace es por repo (en cada fila)

    async def _release_block(self, project: ProjectConfig) -> SourceResult:
        # Release/SBOM se derivan del forge del proyecto: GitHub o GitLab (F5, 1.3).
        forge = self._forge_source(project)
        if forge is None:
            return SourceResult(status=SourceStatus.not_configured)
        connector = self._build_connector(forge)
        if connector is None or not hasattr(connector, "fetch_release"):
            return SourceResult(status=SourceStatus.not_configured)

        return await self._cached_fetch(
            f"{project.key}:{forge}:release", lambda: connector.fetch_release(project)
        )

    async def get_dependencies(self, project: ProjectConfig) -> SourceResult:
        """Inventario de dependencias (Trivy). Misma caché TTL + degradación."""
        connector = (
            self._build_connector("trivy")
            if "trivy" in self._config.sources and self._project_uses(project, "trivy")
            else None
        )
        if connector is None:
            return SourceResult(status=SourceStatus.not_configured)

        async def call() -> DependencyInventory:
            pkgs, degraded = await connector.inventory(project)
            return DependencyInventory(
                project_key=project.key,
                generated_at=datetime.now(UTC),
                degraded=degraded,
                packages=[DependencyItem(**p) for p in pkgs],
            )

        return await self._cached_fetch(f"{project.key}:trivy:dependencies", call)

    async def get_codemap(self, project: ProjectConfig) -> SourceResult:
        """Árbol de módulos (GitHub Trees API, read-only). Caché TTL + degradación."""
        connector = (
            self._build_connector("github")
            if "github" in self._config.sources and self._project_uses(project, "github")
            else None
        )
        if connector is None:
            return SourceResult(status=SourceStatus.not_configured)
        return await self._cached_fetch(
            f"{project.key}:github:codemap",
            lambda: connector.fetch_tree(
                project,
                max_nodes=self._settings.codemap_max_nodes,
                max_depth=self._settings.codemap_max_depth,
            ),
        )

    async def get_source_detail(self, project: ProjectConfig, source: str) -> SourceDetail:
        """Detalle crudo-normalizado de UNA fuente para un proyecto (routes_sources)."""
        if source not in self._config.sources or not self._project_uses(project, source):
            return SourceDetail(
                project_key=project.key, source=source, status=SourceStatus.not_configured
            )
        connector = self._build_connector(source)
        if connector is None:
            return SourceDetail(
                project_key=project.key, source=source, status=SourceStatus.not_configured
            )
        try:
            detail = await connector.fetch_detail(project)
            detail.status = SourceStatus.ok
            return detail
        except ConnectorUnavailable as exc:
            return SourceDetail(
                project_key=project.key,
                source=source,
                status=SourceStatus.unavailable,
                error=str(exc),
            )

    async def list_projects(self) -> list[ProjectListItem]:
        summaries = await asyncio.gather(
            *(self.get_summary(p) for p in self._config.projects)
        )
        return [self._to_list_item(s) for s in summaries]

    def _to_list_item(self, s: ProjectSummary) -> ProjectListItem:
        ci_data: CISummary | None = s.ci.data
        findings_data: FindingsSummary | None = s.findings.data
        quality_data: QualitySummary | None = s.quality.data
        containers_data: FindingsSummary | None = s.containers.data
        statuses = [
            s.ci.status, s.findings.status, s.quality.status,
            s.release.status, s.containers.status,
        ]
        worst = max(statuses, key=lambda st: _STATUS_RANK[st])
        posture = s.posture
        return ProjectListItem(
            key=s.key,
            name=s.name,
            org=s.org,
            ci_status=ci_data.status if ci_data else None,
            findings=findings_data.counts if findings_data else None,
            container_findings=containers_data.counts if containers_data else None,
            quality_gate=quality_data.quality_gate if quality_data else None,
            worst_source_status=worst,
            posture_score=posture.score if posture else None,
            posture_band=posture.band if posture else "sin_datos",
            posture_coverage=posture.coverage if posture else 0,
        )
