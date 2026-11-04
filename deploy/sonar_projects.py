#!/usr/bin/env python3
"""Helper de analyze-sonar.sh — lista/cablea los proyectos Wolf para SonarQube.

  list  → imprime, por proyecto con org=Wolf, una línea TSV: <key>\t<name>.
          (el .sh resuelve el repo local como ~/<name>).
  upsert→ alta/actualización de UN proyecto arbitrario (objetivo del operador):
          `upsert <yaml> --key K --name N [--trivy-report K.json] [--sonar] [--org O]`.
          Crea el proyecto si no existe; si existe, solo fija los bloques pedidos
          (no pisa github/defectdojo/etc.). Asegura sources.trivy/sonarqube.
  drop  → `drop <yaml> --key K` quita un proyecto por key (idempotente).
  wire  → asegura `sonarqube: { project_key: <key> }` en cada proyecto Wolf que
          aún no lo tenga (sin pisar el existente). Imprime cuántos agregó.

Solo toca proyectos Wolf: nunca proyectos externos (lsst-it) ni material ALMA.
config/projects.yaml está gitignored (se regenera), así que editarlo es seguro.
"""
from __future__ import annotations

import argparse
import sys

import yaml

_DEFAULT_SOURCES = {
    "sonarqube": {"base_url": "http://sonarqube:9000", "token_env": "SONAR_TOKEN"},
    "trivy": {"base_url": "http://trivy:80", "token_env": "TRIVY_TOKEN"},
}
_HEADER = "# Generado por deploy/ (installer / scan-target) — se regenera; no editar a mano.\n"


def _dump(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_HEADER)
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def cmd_list(path: str) -> int:
    for p in _load(path).get("projects") or []:
        if p.get("org") == "Wolf":
            print(f"{p.get('key','')}\t{p.get('name','')}")
    return 0


def cmd_wire(path: str) -> int:
    data = _load(path)
    added = 0
    for p in data.get("projects") or []:
        if p.get("org") == "Wolf" and not p.get("sonarqube"):
            p["sonarqube"] = {"project_key": p["key"]}
            added += 1
    if added:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# Generado por deploy/connect-projects.sh — se regenera; no editar a mano.\n")
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, default_flow_style=False)
    print(added)
    return 0


def cmd_upsert(path: str, key: str, name: str, trivy_report: str | None, sonar: bool, org: str) -> int:
    try:
        data = _load(path)
    except FileNotFoundError:
        data = {}
    sources = data.setdefault("sources", {})
    projects = data.setdefault("projects", [])
    proj = next((p for p in projects if p.get("key") == key), None)
    if proj is None:
        proj = {"key": key, "name": name or key, "org": org}
        projects.append(proj)
    if trivy_report:
        proj["trivy"] = {"report_path": trivy_report}
        sources.setdefault("trivy", dict(_DEFAULT_SOURCES["trivy"]))
    if sonar:
        proj["sonarqube"] = {"project_key": key}
        sources.setdefault("sonarqube", dict(_DEFAULT_SOURCES["sonarqube"]))
    _dump(path, data)
    print(key)
    return 0


def cmd_drop(path: str, key: str) -> int:
    data = _load(path)
    before = len(data.get("projects") or [])
    data["projects"] = [p for p in (data.get("projects") or []) if p.get("key") != key]
    if len(data["projects"]) != before:
        _dump(path, data)
    print(before - len(data["projects"]))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("uso: sonar_projects.py {list|wire|upsert|drop} <projects.yaml> [opts]", file=sys.stderr)
        return 2
    cmd, path = argv[1], argv[2]
    if cmd == "list":
        return cmd_list(path)
    if cmd == "wire":
        return cmd_wire(path)
    ap = argparse.ArgumentParser(prog=f"sonar_projects.py {cmd}")
    ap.add_argument("--key", required=True)
    if cmd == "upsert":
        ap.add_argument("--name", default="")
        ap.add_argument("--trivy-report", default=None)
        ap.add_argument("--sonar", action="store_true")
        ap.add_argument("--org", default="Local")
        a = ap.parse_args(argv[3:])
        return cmd_upsert(path, a.key, a.name, a.trivy_report, a.sonar, a.org)
    if cmd == "drop":
        a = ap.parse_args(argv[3:])
        return cmd_drop(path, a.key)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
