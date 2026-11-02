"""Rate-limit in-process para endpoints de auth (T-AUTH-01 del modelo de amenazas).

El lockout por-cuenta no frena password-spraying (muchos usuarios, pocos intentos
c/u) ni fuerza bruta distribuida. Esto añade un límite por ventana deslizante sobre
(IP, username) para /auth/login y por IP para /auth/mfa/verify → 429 uniforme.

Limitación conocida: es POR PROCESO (sin Redis). Con varios workers el límite se
multiplica por worker; para despliegues multi-worker, complementar con rate-limit en
el reverse proxy. El producto por defecto corre 1 worker (ver PERFORMANCE-ADMIN.md).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_hits: int, window_seconds: float) -> None:
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_prune = 0.0

    def allow(self, key: str) -> bool:
        """Registra un intento para `key`. Devuelve False si excede el límite."""
        now = time.monotonic()
        dq = self._hits[key]
        cutoff = now - self.window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        self._maybe_prune(now)
        if len(dq) >= self.max_hits:
            return False
        dq.append(now)
        return True

    def _maybe_prune(self, now: float) -> None:
        # Evita crecimiento ilimitado del dict: cada 5 min borra claves sin hits vivos.
        if now - self._last_prune < 300:
            return
        self._last_prune = now
        cutoff = now - self.window
        for key in [k for k, dq in self._hits.items() if not dq or dq[-1] <= cutoff]:
            del self._hits[key]


# Límites por defecto (conservadores; un humano legítimo no los alcanza).
login_limiter = SlidingWindowLimiter(max_hits=10, window_seconds=60.0)
mfa_limiter = SlidingWindowLimiter(max_hits=10, window_seconds=60.0)
