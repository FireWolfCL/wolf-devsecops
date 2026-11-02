#!/usr/bin/env python3
"""Helper de analyze-sonar.sh — lista/cablea los proyectos Wolf para SonarQube.

  list  → imprime, por proyecto con org=Wolf, una línea TSV: <key>\t<name>.
          (el .sh resuelve el repo local como ~/<name>).
  wire  → asegura `sonarqube: { project_key: <key> }` en cada proyecto Wolf que
          aún no lo tenga (sin pisar el existente). Imprime cuántos agregó.

Solo toca proyectos Wolf: nunca proyectos externos (lsst-it) ni material ALMA.
config/projects.yaml está gitignored (se regenera), así que editarlo es seguro.
"""
from __future__ import annotations

import sys

import yaml


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


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("uso: sonar_projects.py {list|wire} <projects.yaml>", file=sys.stderr)
        return 2
    return cmd_list(argv[2]) if argv[1] == "list" else cmd_wire(argv[2]) if argv[1] == "wire" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
