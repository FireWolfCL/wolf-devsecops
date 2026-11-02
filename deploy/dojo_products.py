#!/usr/bin/env python3
"""Helper de import-trivy-to-dojo.sh — lee/edita config/projects.yaml.

Dos modos:
  list   → imprime, por proyecto con bloque `trivy`, una línea TSV:
             <key>\t<name>\t<report_path>\t<product>\t<wired>
           donde `product` = defectdojo.product (si existe) o el nombre, y
           `wired` = 1 si el proyecto YA tiene bloque defectdojo, 0 si no.
  wire   → asegura que cada proyecto con `trivy` tenga un bloque
             defectdojo: { product: <name> }  (sin pisar el que ya exista),
           y reescribe el YAML preservando el resto. Imprime cuántos agregó.

No toca credenciales ni fuentes; solo el mapeo proyecto→producto. El archivo
config/projects.yaml está gitignored (se regenera), así que editarlo es seguro.
"""
from __future__ import annotations

import sys

import yaml


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _product(proj: dict) -> str:
    dd = proj.get("defectdojo") or {}
    return dd.get("product") or proj.get("name") or proj.get("key") or ""


def cmd_list(path: str) -> int:
    data = _load(path)
    for proj in data.get("projects") or []:
        if "trivy" not in proj:
            continue
        rp = (proj.get("trivy") or {}).get("report_path") or ""
        wired = "1" if proj.get("defectdojo") else "0"
        print("\t".join([proj.get("key", ""), proj.get("name", ""), rp, _product(proj), wired]))
    return 0


def cmd_wire(path: str) -> int:
    data = _load(path)
    added = 0
    for proj in data.get("projects") or []:
        if "trivy" not in proj or proj.get("defectdojo"):
            continue
        proj["defectdojo"] = {"product": _product(proj)}
        added += 1
    if added:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# Generado por deploy/connect-projects.sh — se regenera; no editar a mano.\n")
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, default_flow_style=False)
    print(added)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("uso: dojo_products.py {list|wire} <projects.yaml>", file=sys.stderr)
        return 2
    cmd, path = argv[1], argv[2]
    if cmd == "list":
        return cmd_list(path)
    if cmd == "wire":
        return cmd_wire(path)
    print(f"comando desconocido: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
