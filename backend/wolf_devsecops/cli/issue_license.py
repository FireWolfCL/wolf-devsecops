"""`wolf-devsecops issue-license` — emite una licencia firmada (LADO WLA).

Firma con la llave PRIVADA (ES256) que vive en tu Wolf License Authority, pasada
por --key (ruta) o la variable WOLF_LICENSE_SIGNING_KEY. La privada NUNCA está en
este repo. Imprime el token para entregar al cliente (WOLF_LICENSE del despliegue).
"""

from __future__ import annotations

import os
import pathlib

import typer

from .. import licensing


def issue_license(
    customer: str = typer.Argument(..., help="Nombre del cliente/organización."),
    edition: str = typer.Option("pro", help="community | pro | enterprise."),
    days: int = typer.Option(365, help="Vigencia en días."),
    key: str = typer.Option(
        "", help="Ruta a la llave PRIVADA PEM (o WOLF_LICENSE_SIGNING_KEY)."
    ),
) -> None:
    """Emite una licencia firmada para un cliente (requiere la llave privada WLA)."""
    key_path = key or os.environ.get("WOLF_LICENSE_SIGNING_KEY", "")
    if not key_path:
        typer.secho(
            "Falta la llave privada: --key <ruta> o WOLF_LICENSE_SIGNING_KEY.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)
    try:
        pem = pathlib.Path(key_path).read_text(encoding="utf-8")
    except OSError as exc:
        typer.secho(f"No se pudo leer la llave: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if edition not in ("community", "pro", "enterprise"):
        typer.secho("edición inválida (community|pro|enterprise).", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        token = licensing.issue_license(
            edition=edition, customer=customer, days=days, private_key_pem=pem
        )
    except Exception as exc:  # noqa: BLE001 — error de firma saneado
        typer.secho(f"Error al firmar: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(
        f"Licencia {edition} para «{customer}» ({days}d):", fg=typer.colors.GREEN, err=True
    )
    typer.echo(token)  # a stdout, para redirigir a un archivo si se quiere
