"""Flujo del wizard `wolf-devsecops init` (ADR-010, F8).

Pasos: elegir modo → pedir fuentes → probar cada una (salud/token/versión) →
dar de alta proyectos → escribir config/projects.yaml y deploy/.env.
Interactivo con Typer; la lógica de escritura/probe vive en writer.py y probe.py.
"""

from __future__ import annotations

import asyncio

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
        projects.append(proj)

    config = build_config_dict(sources, projects)
    write_config(config_out, config)
    write_env_example(env_out, [s["token_env"] for s in sources.values()])
    typer.echo(f"\n✅ Escrito {config_out} y {env_out}. Rellena los tokens en el .env y arranca.")


def _report_probe(name: str, r) -> None:  # noqa: ANN001
    if not r.reachable:
        typer.echo(f"  ⚠ {name}: no responde ({r.detail})")
    elif not r.compatible:
        typer.echo(f"  ⚠ {name}: versión no soportada (detectada {r.version})")
    elif not r.authenticated:
        typer.echo(f"  ⚠ {name}: alcanzable pero sin token válido ({r.detail or 'revisa el token'})")
    else:
        typer.echo(f"  ✓ {name}: OK (versión {r.version or 'n/d'})")
