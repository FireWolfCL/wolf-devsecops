"""TTLCache.invalidate: por prefijo y global (motor del 'actualizar ahora' del panel)."""
from __future__ import annotations

from wolf_devsecops.core.cache import TTLCache


async def test_invalidate_por_prefijo():
    c = TTLCache(ttl_seconds=300)
    await c.set("p1:github:summary", 1)
    await c.set("p1:sonarqube:summary", 2)
    await c.set("p2:github:summary", 3)

    n = await c.invalidate("p1:")

    assert n == 2
    assert await c.get("p1:github:summary") is None
    assert await c.get("p2:github:summary") == 3  # otro proyecto, intacto


async def test_invalidate_global():
    c = TTLCache(ttl_seconds=300)
    await c.set("a", 1)
    await c.set("b", 2)

    n = await c.invalidate()

    assert n == 2
    assert await c.get("a") is None
    assert await c.get("b") is None
