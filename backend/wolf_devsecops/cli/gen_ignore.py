"""`wolf-devsecops gen-ignore` — genera `.trivyignore` y `.gitleaksignore` a partir
de `config/wolf-ignore.yaml` (MEJORAS 6.3.c).

Mantiene UNA fuente de verdad versionable (el YAML con motivo/aprobador/vencimiento)
y deriva de ahí los archivos que cada escáner entiende, para que el CI del equipo
ignore exactamente lo mismo que Wolf excluye del score. Las entradas VENCIDAS no se
escriben (vuelven a contar) y se avisan. Es solo-lectura hacia las fuentes (ADR-001):
escribe archivos locales, no toca ninguna herramienta.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from ..core import ignore as ign
from ..settings import get_settings

_HEADER = "# Generado por `wolf-devsecops gen-ignore` desde config/wolf-ignore.yaml — NO editar a mano."


def gen_ignore(
    project: str = typer.Option(..., "--project", "-p", help="project key (scope del YAML)"),
    out: Path = typer.Option(Path("."), "--out", "-o", help="directorio destino"),
    path: str = typer.Option("", "--ignore-file", help="ruta a wolf-ignore.yaml (por defecto settings)"),
) -> None:
    """Escribe .trivyignore y .gitleaksignore del proyecto (excluye las entradas vencidas)."""
    ignore_path = path or get_settings().ignore_path
    data = ign.load_ignore(ignore_path)
    today = date.today()
    entries = [e for e in data.entries_for(project) if not e.is_expired(today)]
    expired = data.expired(project, today)

    def _write(name: str, tools: set[str]) -> int:
        picked = [e for e in entries if (e.tool in tools or e.tool == "")]
        lines = [_HEADER]
        for e in picked:
            why = f"  # {e.reason}".rstrip() if e.reason else ""
            lines.append(f"{e.match}{why}")
        (out / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(picked)

    out.mkdir(parents=True, exist_ok=True)
    n_trivy = _write(".trivyignore", {"trivy"})
    n_leaks = _write(".gitleaksignore", {"gitleaks"})
    typer.secho(
        f"Escrito {out}/.trivyignore ({n_trivy}) y {out}/.gitleaksignore ({n_leaks}).",
        fg=typer.colors.GREEN,
    )
    if expired:
        typer.secho(
            f"⚠ {len(expired)} excepción(es) vencida(s) para {project}: vuelven a contar en el score. "
            f"Renueva el 'vence' o resuelve: {', '.join(e.match for e in expired)}",
            fg=typer.colors.YELLOW,
            err=True,
        )
