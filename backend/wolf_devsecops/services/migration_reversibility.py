"""Chequeo de reversibilidad de migraciones (ADR-016 §6, paso 1 del pipeline).

Portado de Wolf-Cloud `updates/reversibility.py`, sin dependencias nuevas. Se
analiza el AST del archivo de la migración (nunca se importa ni se ejecuta):
ejecutar `downgrade()` mutaría el esquema, que es lo que este chequeo previene.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class IrreversibleMigrationError(Exception):
    """Al menos una migración pendiente no declara un `downgrade()` real."""


@dataclass(frozen=True, slots=True)
class RevisionCheck:
    revision: str
    reversible: bool


def has_real_downgrade(path: str | Path) -> bool:
    """True si el `downgrade()` del archivo hace algo más que `pass`/docstring.

    Fail-closed: archivo ilegible, sintaxis inválida o sin `downgrade` => False.
    """
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "downgrade":
            relevant = [
                n
                for n in node.body
                if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
                and not isinstance(n, ast.Pass)
            ]
            return len(relevant) > 0
    return False


def assert_reversible(checks: Sequence[RevisionCheck]) -> None:
    """Levanta si alguna revisión pendiente es irreversible (las agrupa en un mensaje)."""
    bad = [c.revision for c in checks if not c.reversible]
    if bad:
        raise IrreversibleMigrationError(
            "Estas migraciones pendientes no declaran un downgrade real: "
            f"{', '.join(bad)}. No se aplica ninguna hasta que lo tengan (ADR-016)."
        )
