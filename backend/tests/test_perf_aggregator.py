"""Rendimiento del agregador: cache-first, single-flight, timeout y degradación."""

from __future__ import annotations

import asyncio

import pytest

from wolf_devsecops.connectors.base import ConnectorUnavailable
from wolf_devsecops.core.aggregator import Aggregator
from wolf_devsecops.core.cache import TTLCache
from wolf_devsecops.core.config import AppConfig
from wolf_devsecops.models import SourceStatus


def _agg(timeout: float = 5.0) -> Aggregator:
    a = Aggregator(AppConfig(), TTLCache(300))
    a._timeout = timeout
    return a


@pytest.mark.asyncio
async def test_cache_first_y_single_flight() -> None:
    calls = 0

    async def slow():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "data"

    a = _agg()
    rs = await asyncio.gather(*(a._cached_fetch("k", slow) for _ in range(20)))
    assert calls == 1 and all(r.status == SourceStatus.ok for r in rs)
    await a._cached_fetch("k", slow)  # fresco en caché: no llama a la fuente
    assert calls == 1


@pytest.mark.asyncio
async def test_timeout_degrada_a_unavailable_y_stale() -> None:
    a = _agg(timeout=0.05)

    async def hang():
        await asyncio.sleep(1)

    r = await a._cached_fetch("k", hang)
    assert r.status == SourceStatus.unavailable

    # con un dato previo vencido → stale
    a2 = _agg()
    await a2._cache.set("k", (await a2._cached_fetch("k", _ok)))
    a2._cache._store["k"] = (0.0, a2._cache._store["k"][1], a2._cache._store["k"][2])  # vencido
    a2._neg._store.clear()

    async def boom():
        raise ConnectorUnavailable("down")

    r2 = await a2._cached_fetch("k", boom)
    assert r2.status == SourceStatus.stale and r2.error == "down"


async def _ok():
    return "x"
