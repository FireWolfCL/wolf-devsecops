"""Resolución de credenciales: token_env → valor desde el entorno.

Nunca imprime ni registra el valor. Si la variable no existe, devuelve None y el
conector tratará la fuente como no autenticada (degrada, no revienta).
"""

from __future__ import annotations

import os


def resolve_token(token_env: str | None) -> str | None:
    if not token_env:
        return None
    value = os.environ.get(token_env)
    return value or None
