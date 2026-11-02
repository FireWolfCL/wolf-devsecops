"""Resolución del token de una fuente, con PRECEDENCIA del almacén cifrado en BD.

Orden de resolución (0006, COMPLEMENTA ADR-002):
  1. Si la fuente tiene un token cifrado en BD (`token_cipher`), se descifra y se usa.
  2. Si no, se cae a la variable de entorno cuyo NOMBRE es `token_env` (modelo original).

Así los tokens que ya viven en el entorno (.env) siguen funcionando sin cambios, y los
que el admin pega desde la UI de Integraciones tienen prioridad y permiten conectar sin
editar deploy/.env.

Nunca imprime ni registra el valor. Si no hay token por ninguna vía, devuelve None y el
conector trata la fuente como no autenticada (degrada, no revienta).
"""

from __future__ import annotations

import os


def resolve_token(token_env: str | None, token_cipher: str | None = None) -> str | None:
    """Token efectivo de una fuente: BD cifrada > variable de entorno.

    `token_cipher` es el ciphertext Fernet almacenado para la fuente (o None). Si viene,
    se descifra con la llave de reposo de la instancia y, si da un valor no vacío, GANA.
    Si el ciphertext está corrupto o la llave no corresponde, NO revienta ni devuelve un
    token basura: degrada a la env var (fail-safe para la carga de config). El valor del
    token jamás se loguea.
    """
    if token_cipher:
        # Import perezoso: evita el ciclo credentials ↔ auth.security (settings, rbac).
        from ..auth.security import decrypt_source_token

        try:
            value = decrypt_source_token(token_cipher)
        except ValueError:
            value = None  # ciphertext inválido/clave equivocada → se cae a la env var
        if value:
            return value

    if not token_env:
        return None
    return os.environ.get(token_env) or None
