"""`wolf-devsecops import-org <org>` — alta masiva de los repos de una organización GitHub.

Lista los repos del org, filtra por ACTIVIDAD (push reciente) y crea un proyecto por
cada uno (bloque `github`, agrupados bajo ese org). No pisa proyectos existentes.

Usa GITHUB_TOKEN si está en el entorno (sube el rate limit a 5000/h; necesario para
que el PANEL luego lea los datos). Sin token igual puede listar repos públicos (60/h).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx
import typer

from ..db.engine import dispose_engine, get_sessionmaker
from ..services import config_service


def _slug(name: str) -> str:
    # Los nombres de repo ya cumplen el patrón de key ([A-Za-z0-9._-]); normaliza a minúscula.
    return name.lower()


async def _list_repos(org: str, cutoff: datetime, token: str) -> list[dict]:
    """Repos del org con push >= cutoff (ordenados por pushed desc: corta temprano)."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    repos: list[dict] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        page = 1
        while True:
            r = await client.get(
                f"https://api.github.com/orgs/{org}/repos",
                headers=headers,
                params={"per_page": 100, "sort": "pushed", "direction": "desc", "page": page},
            )
            if r.status_code != 200:
                raise RuntimeError(f"GitHub HTTP {r.status_code}: {r.text[:120]}")
            batch = r.json()
            if not batch:
                break
            stop = False
            for repo in batch:
                if repo.get("archived"):
                    continue
                pushed = repo.get("pushed_at")
                if pushed:
                    dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                    if dt < cutoff:
                        stop = True  # orden desc: a partir de aquí todos son más viejos
                        break
                repos.append(repo)
            if stop or len(batch) < 100:
                break
            page += 1
    return repos


def import_org(
    org: str = typer.Argument(..., help="Organización GitHub (p. ej. lsst-it)."),
    active_days: int = typer.Option(365, help="Solo repos con push en los últimos N días."),
    limit: int | None = typer.Option(None, help="Tope de repos a agregar (los más recientes)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo cuenta; no escribe."),
) -> None:
    """Agrega como proyectos los repos ACTIVOS de una organización GitHub."""
    token = os.environ.get("GITHUB_TOKEN", "")
    cutoff = datetime.now(UTC) - timedelta(days=active_days)

    async def _run() -> tuple[int, int, int]:
        repos = await _list_repos(org, cutoff, token)
        if limit:
            repos = repos[:limit]
        added = skipped = 0
        try:
            async with get_sessionmaker()() as session:
                for repo in repos:
                    key = _slug(repo["name"])
                    if await config_service.project_exists(session, key):
                        skipped += 1
                        continue
                    if dry_run:
                        added += 1
                        continue
                    await config_service.upsert_project(
                        session,
                        key=key,
                        name=repo["name"],
                        sources={"github": {"repo": repo["full_name"], "ci_workflow": "ci.yml"}},
                        org=org,
                    )
                    added += 1
        finally:
            await dispose_engine()
        return len(repos), added, skipped

    try:
        total, added, skipped = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    verb = "se agregarían" if dry_run else "agregados"
    auth = "autenticado" if token else "SIN token (rate limit 60/h; pon GITHUB_TOKEN)"
    typer.secho(
        f"{org}: {total} repos activos (<{active_days}d) · {verb}: {added} · ya existían: {skipped}. [{auth}]",
        fg=typer.colors.GREEN,
    )
