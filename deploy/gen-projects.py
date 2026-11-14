#!/usr/bin/env python3
"""Genera config/projects.yaml desde deploy/projects.list.

Cada entrada (repo local o URL git) se convierte en un proyecto con su bloque
`trivy` (report_path = <slug>.json, el reporte que dejó connect-projects.sh).
Si el repo tiene remoto GitHub, se añade el bloque `github`. El proyecto
`wolf-cloud` además lleva defectdojo/sonarqube/harbor (las fuentes generadas).

Sin secretos en el YAML (ADR-002): solo `token_env` por nombre.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import yaml

HOME = os.path.expanduser("~")

SOURCES = {
    "github": {"api_url": "https://api.github.com", "token_env": "GITHUB_TOKEN"},
    "defectdojo": {"base_url": "http://defectdojo-nginx:8080", "token_env": "DEFECTDOJO_TOKEN"},
    "sonarqube": {"base_url": "http://sonarqube:9000", "token_env": "SONAR_TOKEN"},
    "harbor": {"base_url": "http://host.docker.internal:8085", "token_env": "HARBOR_TOKEN"},
    "trivy": {"base_url": "http://trivy:80", "token_env": "TRIVY_TOKEN"},
}

# Reviewer IA (F6, ADR-007): provider `local` = endpoint OpenAI-compatible (ollama
# en el host de IA). Se importa DESHABILITADO; se activa con WOLF_AI_ENABLED=true + toggle
# del panel, y requiere que el backend ALCANCE ese endpoint. Override por entorno.
AI = {
    "provider": os.environ.get("WOLF_AI_PROVIDER", "local"),
    "model": os.environ.get("WOLF_AI_MODEL", "qwen2.5:14b"),
    "base_url": os.environ.get("WOLF_AI_BASE_URL", "http://localhost:11434/v1"),
}


def slug(entry: str) -> str:
    name = entry.rsplit("/", 1)[-1]
    name = re.sub(r"\.git$", "", name)
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def gh_from_url(url: str) -> str | None:
    m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def local_gh(name: str) -> str | None:
    try:
        url = subprocess.check_output(
            ["git", "-C", os.path.join(HOME, name), "remote", "get-url", "origin"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return gh_from_url(url)
    except Exception:
        return None


def main() -> None:
    list_path, out_path = sys.argv[1], sys.argv[2]
    projects: list[dict] = []
    seen: set[str] = set()
    for raw in open(list_path, encoding="utf-8"):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        remote = line.startswith(("http", "git@"))
        name = line.rsplit("/", 1)[-1].replace(".git", "") if remote else line
        key = slug(line)
        if key in seen:
            continue
        seen.add(key)
        gh = gh_from_url(line) if remote else local_gh(name)
        # Organización dueña: los repos locales son de Wolf; los remotos, de su owner
        # (p. ej. lsst-it) para que su postura no afecte el % global de Wolf.
        org = (gh.split("/", 1)[0] if (remote and gh) else "externo") if remote else "Wolf"
        proj: dict = {"key": key, "name": name, "org": org, "trivy": {"report_path": f"{key}.json"}}
        if gh:
            proj["github"] = {"repo": gh, "ci_workflow": "ci.yml"}
        if key == "wolf-cloud":
            proj["defectdojo"] = {"product": "Wolf-Cloud"}
            proj["sonarqube"] = {"project_key": "wolf-cloud"}
            proj["harbor"] = {"project": "wolf-cloud"}
        projects.append(proj)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Generado por deploy/connect-projects.sh — se regenera; no editar a mano.\n")
        yaml.safe_dump(
            {"sources": SOURCES, "projects": projects, "ai": AI},
            f, sort_keys=False, allow_unicode=True, default_flow_style=False,
        )
    print(f"{len(projects)} proyectos escritos en {out_path}")


if __name__ == "__main__":
    main()
