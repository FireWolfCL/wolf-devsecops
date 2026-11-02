"""Caché TTL en memoria, async-safe (ARCHITECTURE.md §2).

Aislada tras esta interfaz a propósito: migrar a Redis (roadmap v0.5) es un cambio
local, no un rediseño. Guarda clave → (expiración fresca, expiración dura, valor).

Dos ventanas (PERFORMANCE-ADMIN.md):
- `get()` devuelve solo datos FRESCOS (dentro del TTL): evita llamar a la fuente.
- `get_stale()` devuelve el dato aunque haya vencido, hasta `stale_ttl_seconds`:
  es el respaldo "stale" cuando la fuente cae (degradación, no pantalla vacía).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int = 300, stale_ttl_seconds: int | None = None) -> None:
        self._ttl = ttl_seconds
        # Por defecto el dato "stale" sobrevive 12x el TTL fresco (1 h con TTL 300).
        self._stale_ttl = stale_ttl_seconds if stale_ttl_seconds is not None else ttl_seconds * 12
        self._store: dict[str, tuple[float, float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Valor fresco o None."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            fresh_until, hard_until, value = entry
            now = time.monotonic()
            if now >= hard_until:
                self._store.pop(key, None)
                return None
            return value if now < fresh_until else None

    async def get_stale(self, key: str) -> Any | None:
        """Último valor conocido, fresco o vencido, mientras no pase la ventana dura."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            _, hard_until, value = entry
            if time.monotonic() >= hard_until:
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            now = time.monotonic()
            self._store[key] = (now + self._ttl, now + self._ttl + self._stale_ttl, value)
            if len(self._store) > 4096:  # purga oportunista: acota memoria
                for k in [k for k, (_, hard, _v) in self._store.items() if now >= hard]:
                    self._store.pop(k, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
