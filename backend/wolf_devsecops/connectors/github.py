"""GitHubConnector — estado del CI desde GitHub Actions (ARCHITECTURE.md §2).

Habla SOLO con la API de GitHub y traduce al contrato normalizado. No cachea, no
agrega (eso es del aggregator). Tolerante a campos desconocidos: usa solo lo que
necesita del JSON, así un cambio aditivo de la API no lo rompe (ADR-008).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import httpx

from ..core.config import ProjectConfig
from ..models import (
    CIStatus,
    CISummary,
    ModuleNode,
    ModuleTree,
    ProbeResult,
    ReleaseSummary,
    SourceDetail,
)
from .base import Connector, ConnectorUnavailable

_API_VERSION = "2022-11-28"

_FAILURE = {"failure", "timed_out", "startup_failure", "cancelled", "action_required"}
_RUNNING = {"in_progress", "queued", "requested", "waiting", "pending"}


def _map_ci_status(conclusion: str | None, status: str | None) -> CIStatus:
    if conclusion == "success":
        return CIStatus.success
    if conclusion in _FAILURE:
        return CIStatus.failure
    if conclusion is None and status in _RUNNING:
        return CIStatus.running
    return CIStatus.unknown


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GitHubConnector(Connector):
    source_name = "github"
    supported_versions = ">=2022-11-28"  # X-GitHub-Api-Version

    def __init__(self, source_config, token, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(source_config, token)
        # transport inyectable: los tests de contrato le pasan un MockTransport
        # con respuestas-ejemplo grabadas; en producción es None (red real).
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10.0, transport=self._transport)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @property
    def _base(self) -> str:
        return self.source_config.url.rstrip("/")

    async def probe(self) -> ProbeResult:
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base}/rate_limit", headers=self._headers())
            reachable = True
            authenticated = resp.status_code == 200 and bool(self.token)
            version = resp.headers.get("X-GitHub-Api-Version", _API_VERSION)
            return ProbeResult(
                source=self.source_name,
                reachable=reachable,
                authenticated=authenticated,
                version=version,
                compatible=True,
                detail=None if authenticated else "Sin token válido (acceso limitado/anónimo).",
            )
        except httpx.HTTPError:
            return ProbeResult(
                source=self.source_name,
                reachable=False,
                authenticated=False,
                compatible=False,
                detail="No se pudo contactar la API de GitHub.",
            )

    async def _latest_run(self, project: ProjectConfig) -> dict | None:
        gh = project.github
        if gh is None:
            return None
        url = f"{self._base}/repos/{gh.repo}/actions/workflows/{gh.ci_workflow}/runs"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers(), params={"per_page": 1})
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar la API de GitHub.") from exc
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"GitHub respondió HTTP {resp.status_code}.")
        runs = resp.json().get("workflow_runs", [])
        return runs[0] if runs else None

    async def fetch_summary(self, project: ProjectConfig) -> CISummary:
        gh = project.github
        run = await self._latest_run(project)
        workflow = gh.ci_workflow if gh else "ci.yml"
        if run is None:
            return CISummary(status=CIStatus.unknown, workflow=workflow)
        return CISummary(
            status=_map_ci_status(run.get("conclusion"), run.get("status")),
            workflow=workflow,
            branch=run.get("head_branch"),
            run_number=run.get("run_number"),
            html_url=run.get("html_url"),
            finished_at=_parse_dt(run.get("updated_at")),
        )

    async def fetch_detail(self, project: ProjectConfig) -> SourceDetail:
        run = await self._latest_run(project)
        return SourceDetail(
            project_key=project.key,
            source="github",
            status="ok",  # el aggregator ajusta el estado final
            payload={"latest_run": run or {}},
        )

    async def fetch_release(self, project: ProjectConfig) -> ReleaseSummary:
        """Último release del repo y, si existe, el asset SBOM (F5)."""
        gh = project.github
        if gh is None:
            return ReleaseSummary()
        url = f"{self._base}/repos/{gh.repo}/releases/latest"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar la API de GitHub.") from exc
        if resp.status_code == 404:
            return ReleaseSummary()  # el repo aún no tiene releases
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"GitHub respondió HTTP {resp.status_code}.")
        data = resp.json()
        sbom_url = None
        for asset in data.get("assets", []):
            name = str(asset.get("name", "")).lower()
            if "sbom" in name or name.endswith(".cyclonedx.json"):
                sbom_url = asset.get("browser_download_url")
                break
        return ReleaseSummary(
            tag=data.get("tag_name"),
            name=data.get("name"),
            published_at=_parse_dt(data.get("published_at")),
            html_url=data.get("html_url"),
            sbom_url=sbom_url,
        )

    async def fetch_tree(
        self, project: ProjectConfig, max_nodes: int = 2000, max_depth: int = 6
    ) -> ModuleTree:
        """Árbol de archivos/dirs del repo vía Git Trees API (solo lectura, ADR-001:
        no clona nada). Acotado: `max_depth` y `max_nodes`; si recorta,
        `truncated=True` (también si GitHub mismo truncó el árbol)."""
        gh = project.github
        if gh is None:
            raise ConnectorUnavailable("El proyecto no define bloque 'github'.")
        try:
            async with self._client() as client:
                repo_resp = await client.get(f"{self._base}/repos/{gh.repo}", headers=self._headers())
                if repo_resp.status_code != 200:
                    raise ConnectorUnavailable(f"GitHub respondió HTTP {repo_resp.status_code}.")
                branch = repo_resp.json().get("default_branch") or "HEAD"
                resp = await client.get(
                    f"{self._base}/repos/{gh.repo}/git/trees/{branch}",
                    headers=self._headers(),
                    params={"recursive": "1"},
                )
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable("No se pudo contactar la API de GitHub.") from exc
        except ValueError as exc:
            raise ConnectorUnavailable("Respuesta de GitHub no es JSON válido.") from exc
        if resp.status_code != 200:
            raise ConnectorUnavailable(f"GitHub respondió HTTP {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ConnectorUnavailable("Respuesta de GitHub no es JSON válido.") from exc

        truncated = bool(data.get("truncated"))
        raw = [t for t in data.get("tree", []) if isinstance(t, dict) and t.get("path")]
        nodes: list[ModuleNode] = []
        for t in raw:
            kind = {"blob": "file", "tree": "dir"}.get(t.get("type"))
            if kind is None:  # submódulos (commit) y otros: fuera
                continue
            path = str(t["path"])
            if path.count("/") + 1 > max_depth:
                truncated = True
                continue
            if len(nodes) >= max_nodes:
                truncated = True
                break
            nodes.append(ModuleNode(path=path, type=kind, size=t.get("size") if kind == "file" else None))
        nodes.sort(key=lambda n: n.path)
        return ModuleTree(
            project_key=project.key, generated_at=datetime.now(UTC), nodes=nodes, truncated=truncated
        )


# ─── Capacidad de ESCRITURA (Feature D, ADR-017) ─────────────────────────────
# SEPARADA del conector de lectura: usa su PROPIO token (PAT de escritura con scope
# mínimo contents:write + pull_requests:write), resuelto server-side por un token_env
# dedicado (ADR-002). NUNCA mergea, nunca toca main/default/otros branches, nunca
# force-push. Solo: crear un branch con prefijo reservado wolf-sec/, commitear la
# propuesta y abrir un PR en estado DRAFT. Idempotente: si el branch/PR ya existe,
# no duplica. El token jamás se devuelve, loguea ni audita.


class GitHubWriteError(Exception):
    """Error de una operación de escritura (mensaje saneado: nunca incluye el token)."""


class GitHubWriteClient:
    _API_VERSION = "2022-11-28"

    def __init__(
        self, api_base: str, token: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._base = api_base.rstrip("/")
        self._token = token
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15.0, transport=self._transport)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._API_VERSION,
            "Authorization": f"Bearer {self._token}",
        }

    async def default_branch_and_sha(self, repo: str) -> tuple[str, str]:
        """(default_branch, head_sha) del repo. El branch nuevo se crea DESDE este sha."""
        try:
            async with self._client() as client:
                repo_resp = await client.get(f"{self._base}/repos/{repo}", headers=self._headers())
                if repo_resp.status_code != 200:
                    raise GitHubWriteError(f"GitHub respondió HTTP {repo_resp.status_code} al leer el repo.")
                branch = repo_resp.json().get("default_branch")
                if not branch:
                    raise GitHubWriteError("El repo no declara un branch por defecto.")
                ref_resp = await client.get(
                    f"{self._base}/repos/{repo}/git/ref/heads/{branch}", headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise GitHubWriteError("No se pudo contactar la API de GitHub.") from exc
        if ref_resp.status_code != 200:
            raise GitHubWriteError(f"GitHub respondió HTTP {ref_resp.status_code} al leer el ref.")
        sha = (ref_resp.json().get("object") or {}).get("sha")
        if not sha:
            raise GitHubWriteError("No se pudo resolver el sha del branch por defecto.")
        return branch, sha

    async def create_branch(self, repo: str, new_branch: str, from_sha: str) -> bool:
        """Crea refs/heads/<new_branch>. Devuelve True si lo creó, False si YA existía
        (idempotente: 422 'Reference already exists' no es un error)."""
        if not new_branch.startswith("wolf-sec/fix-"):
            # Cinturón: este cliente SOLO crea branches del prefijo reservado.
            raise GitHubWriteError("El branch debe usar el prefijo reservado 'wolf-sec/fix-'.")
        payload = {"ref": f"refs/heads/{new_branch}", "sha": from_sha}
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self._base}/repos/{repo}/git/refs", headers=self._headers(), json=payload
                )
        except httpx.HTTPError as exc:
            raise GitHubWriteError("No se pudo contactar la API de GitHub.") from exc
        if resp.status_code == 201:
            return True
        if resp.status_code == 422:
            return False  # ya existe: idempotente
        raise GitHubWriteError(f"GitHub respondió HTTP {resp.status_code} al crear el branch.")

    async def get_file_sha(self, repo: str, path: str, branch: str) -> str | None:
        """sha del blob si el archivo existe en ese branch, o None (404)."""
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self._base}/repos/{repo}/contents/{path}",
                    headers=self._headers(),
                    params={"ref": branch},
                )
        except httpx.HTTPError as exc:
            raise GitHubWriteError("No se pudo contactar la API de GitHub.") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitHubWriteError(f"GitHub respondió HTTP {resp.status_code} al leer el archivo.")
        data = resp.json()
        if isinstance(data, list):
            raise GitHubWriteError("La ruta apunta a un directorio, no a un archivo.")
        return data.get("sha")

    async def put_file(
        self, repo: str, path: str, content: str, message: str, branch: str, sha: str | None = None
    ) -> None:
        """Crea o actualiza UN archivo en `branch`. Si `sha` viene, actualiza; si no,
        crea. GitHub no genera un commit nuevo si el contenido es idéntico (idempotente)."""
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        try:
            async with self._client() as client:
                resp = await client.put(
                    f"{self._base}/repos/{repo}/contents/{path}",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise GitHubWriteError("No se pudo contactar la API de GitHub.") from exc
        if resp.status_code not in (200, 201):
            raise GitHubWriteError(f"GitHub respondió HTTP {resp.status_code} al escribir el archivo.")

    async def find_open_pull(self, repo: str, head_branch: str, base: str) -> dict | None:
        """PR abierto para head_branch → base, o None. Clave de idempotencia del PR."""
        owner = repo.split("/")[0]
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self._base}/repos/{repo}/pulls",
                    headers=self._headers(),
                    params={"head": f"{owner}:{head_branch}", "base": base, "state": "open"},
                )
        except httpx.HTTPError as exc:
            raise GitHubWriteError("No se pudo contactar la API de GitHub.") from exc
        if resp.status_code != 200:
            raise GitHubWriteError(f"GitHub respondió HTTP {resp.status_code} al listar PRs.")
        items = resp.json()
        return items[0] if isinstance(items, list) and items else None

    async def open_pull(
        self, repo: str, head_branch: str, base: str, title: str, body: str, draft: bool = True
    ) -> dict:
        """Abre un PR en estado DRAFT (draft=True SIEMPRE por defecto). NUNCA mergea."""
        payload = {
            "title": title,
            "head": head_branch,
            "base": base,
            "body": body,
            "draft": draft,
        }
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self._base}/repos/{repo}/pulls", headers=self._headers(), json=payload
                )
        except httpx.HTTPError as exc:
            raise GitHubWriteError("No se pudo contactar la API de GitHub.") from exc
        if resp.status_code == 201:
            return resp.json()
        if resp.status_code == 422:
            # Muy probablemente el PR ya existe: el servicio lo resuelve con find_open_pull.
            raise GitHubWriteError("already_exists")
        raise GitHubWriteError(f"GitHub respondió HTTP {resp.status_code} al abrir el PR.")
