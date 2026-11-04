"""Flujo del wizard `wolf-devsecops init` (ADR-010, F8).

Pasos: elegir modo → pedir fuentes → probar cada una (salud/token/versión) →
dar de alta proyectos → escribir config/projects.yaml y deploy/.env.
Interactivo con Typer; la lógica de escritura/probe vive en writer.py y probe.py.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
from pathlib import Path

import typer

from .probe import probe_source
from .writer import build_config_dict, write_config, write_env_example

# URLs internas del modo all-in-one (ADR-009).
_ALLINONE_URLS = {
    "github": "https://api.github.com",
    "defectdojo": "http://defectdojo-nginx:8080",
    "sonarqube": "http://sonarqube:9000",
}
_DEFAULT_URLS = {
    "github": "https://api.github.com",
    "defectdojo": "http://localhost:8080",
    "sonarqube": "http://localhost:9000",
}
_TOKEN_ENVS = {
    "github": "GITHUB_TOKEN",
    "defectdojo": "DEFECTDOJO_TOKEN",
    "sonarqube": "SONAR_TOKEN",
}


_KEY_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,98}[a-z0-9])?$")


def _scan_cmd(key: str, name: str, repo: str, image: str, config_out: str) -> list[str]:
    """Comando de deploy/scan-target.sh para el objetivo local del proyecto."""
    cmd = ["bash", "deploy/scan-target.sh", "--key", key, "--name", name, "--config", config_out]
    if repo:
        cmd += ["--repo", repo]
    if image:
        cmd += ["--image", image]
    return cmd


def run_init(config_out: str = "config/projects.yaml", env_out: str = "deploy/.env.example") -> None:
    typer.echo("🐺 Wolf DevSecOps — asistente de configuración\n")
    mode = typer.prompt("Modo [all-in-one/connect]", default="connect")
    base = _ALLINONE_URLS if mode == "all-in-one" else _DEFAULT_URLS

    sources: dict[str, dict] = {}
    for name in ("github", "defectdojo", "sonarqube"):
        if not typer.confirm(f"¿Configurar {name}?", default=(name == "github")):
            continue
        url = typer.prompt(f"  URL de {name}", default=base[name])
        token_env = _TOKEN_ENVS[name]
        sources[name] = {"url": url, "token_env": token_env}
        result = asyncio.run(probe_source(name, url, token_env))
        _report_probe(name, result)

    projects: list[dict] = []
    targets: list[tuple[str, str, str, str]] = []
    while typer.confirm("¿Añadir un proyecto?", default=not projects):
        key = typer.prompt("  key (id corto)")
        proj: dict = {"key": key, "name": typer.prompt("  nombre", default=key)}
        if "github" in sources and typer.confirm("  ¿tiene repo en GitHub?", default=True):
            proj["github"] = {
                "repo": typer.prompt("    owner/repo"),
                "ci_workflow": typer.prompt("    workflow", default="ci.yml"),
            }
        if "defectdojo" in sources and typer.confirm("  ¿producto en DefectDojo?", default=False):
            proj["defectdojo"] = {"product": typer.prompt("    producto")}
        if "sonarqube" in sources and typer.confirm("  ¿proyecto en Sonar?", default=False):
            proj["sonarqube"] = {"project_key": typer.prompt("    project key")}
        # Objetivo LOCAL a escanear (ruta de repo y/o imagen): el bundle lo escanea
        # (ADR-001: el panel no escanea) y publica <key>.json; mismo project_key en todo.
        repo = typer.prompt("  ruta local del repo (Enter = omitir)", default="", show_default=False).strip()
        image = typer.prompt("  imagen de contenedor (Enter = omitir)", default="", show_default=False).strip()
        if repo and not Path(repo).expanduser().is_dir():
            typer.echo(f"  ⚠ {repo} no es un directorio; se omite el repo.")
            repo = ""
        if repo or image:
            if not _KEY_RE.match(key):
                typer.echo("  ⚠ la key debe ser minúsculas/dígitos/./_/-; no se podrá escanear con esa key.")
            else:
                repo = str(Path(repo).expanduser().resolve()) if repo else ""
                proj["trivy"] = {"report_path": f"{key}.json"}  # repo o solo-imagen -> <key>.json
                sources.setdefault("trivy", {"url": "http://trivy:80", "token_env": "TRIVY_TOKEN"})
                if repo and "sonarqube" in sources:
                    proj.setdefault("sonarqube", {"project_key": key})
                targets.append((key, str(proj["name"]), repo, image))
        projects.append(proj)

    config = build_config_dict(sources, projects)
    write_config(config_out, config)
    write_env_example(env_out, [s["token_env"] for s in sources.values()])
    typer.echo(f"\n✅ Escrito {config_out} y {env_out}. Rellena los tokens en el .env y arranca.")

    for key, name, repo, image in targets:
        cmd = _scan_cmd(key, name, repo, image, config_out)
        typer.echo(f"\nEscaneo real de '{key}' (con el stack ya levantado y SONAR_TOKEN emitido):")
        typer.echo("  " + shlex.join(cmd))
        if typer.confirm("¿Ejecutarlo ahora?", default=False):
            rc = subprocess.run(cmd, check=False).returncode  # noqa: S603 — args fijos, sin shell
            typer.echo("  ✓ listo" if rc == 0 else f"  ⚠ terminó con avisos (rc={rc}); reintenta el comando de arriba")


def _report_probe(name: str, r) -> None:  # noqa: ANN001
    if not r.reachable:
        typer.echo(f"  ⚠ {name}: no responde ({r.detail})")
    elif not r.compatible:
        typer.echo(f"  ⚠ {name}: versión no soportada (detectada {r.version})")
    elif not r.authenticated:
        typer.echo(f"  ⚠ {name}: alcanzable pero sin token válido ({r.detail or 'revisa el token'})")
    else:
        typer.echo(f"  ✓ {name}: OK (versión {r.version or 'n/d'})")
