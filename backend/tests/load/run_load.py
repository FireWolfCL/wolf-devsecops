"""Harness de carga asyncio+httpx (sin dependencias nuevas).

    python tests/load/run_load.py --base http://127.0.0.1:8000 --token $TOKEN \
        --scenario projects --concurrency 50 --duration 20

Escenarios: projects (GET /api/v1/projects), project (GET /api/v1/projects/p0),
login (POST /auth/login rotando viewers), mixed (90% projects + 10% login),
health (GET /api/health como línea base). `--ramp 1,5,10,25,50` barre concurrencias.
Reporta p50/p95/p99, req/s y errores por escenario. Sale con 1 si se incumple
--slo-p95-ms o --max-error-rate (úsalo como gate).
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import statistics
import sys
import time

import httpx


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


async def worker(client, a, stop_at, lat, errs, codes, idx, counter):
    while time.monotonic() < stop_at:
        sc = a.scenario
        if sc == "mixed":
            sc = "login" if next(counter) % 10 == 0 else "projects"
        t0 = time.perf_counter()
        try:
            if sc == "login":
                u = f"viewer{(idx + next(counter)) % a.viewers}"
                r = await client.post("/api/v1/auth/login", data={"username": u, "password": a.password})
            elif sc == "project":
                r = await client.get("/api/v1/projects/p0", headers={"Authorization": f"Bearer {a.token}"})
            elif sc == "health":
                r = await client.get("/api/health")
            else:
                r = await client.get("/api/v1/projects", headers={"Authorization": f"Bearer {a.token}"})
            dt = (time.perf_counter() - t0) * 1000
            codes[r.status_code] = codes.get(r.status_code, 0) + 1
            (lat if r.status_code == 200 else errs).append(dt)
        except httpx.HTTPError:
            errs.append((time.perf_counter() - t0) * 1000)
            codes["exc"] = codes.get("exc", 0) + 1


async def run_level(a, conc: int) -> dict:
    lat, errs, codes = [], [], {}
    counter = itertools.count()
    limits = httpx.Limits(max_connections=conc, max_keepalive_connections=conc)
    async with httpx.AsyncClient(base_url=a.base, timeout=a.timeout, limits=limits) as c:
        stop_at = time.monotonic() + a.duration
        t0 = time.monotonic()
        await asyncio.gather(*(worker(c, a, stop_at, lat, errs, codes, i, counter) for i in range(conc)))
        el = time.monotonic() - t0
    n = len(lat) + len(errs)
    return dict(conc=conc, n=n, rps=n / el, ok=len(lat), err=len(errs), codes=codes,
                p50=pct(lat, .5), p95=pct(lat, .95), p99=pct(lat, .99),
                mean=statistics.fmean(lat) if lat else 0.0)


async def main(a) -> int:
    levels = [int(x) for x in a.ramp.split(",")] if a.ramp else [a.concurrency]
    print(f"scenario={a.scenario} duration={a.duration}s/level base={a.base}")
    print(f"{'conc':>5} {'req':>7} {'rps':>8} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8} {'err%':>6}  codes")
    bad = False
    for lv in levels:
        r = await run_level(a, lv)
        er = r["err"] / r["n"] if r["n"] else 1.0
        print(f"{r['conc']:>5} {r['n']:>7} {r['rps']:>8.1f} {r['p50']:>8.1f} {r['p95']:>8.1f} "
              f"{r['p99']:>8.1f} {er*100:>6.2f}  {r['codes']}")
        if (a.slo_p95_ms and r["p95"] > a.slo_p95_ms) or er > a.max_error_rate:
            bad = True
    print("SLO:", "FAIL" if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--token", default="")
    p.add_argument("--scenario", choices=["projects", "project", "login", "mixed", "health"], default="projects")
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--ramp", default="")
    p.add_argument("--duration", type=float, default=15)
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--viewers", type=int, default=20)
    p.add_argument("--password", default="LoadTest#12345")
    p.add_argument("--slo-p95-ms", type=float, default=0)
    p.add_argument("--max-error-rate", type=float, default=0.01)
    sys.exit(asyncio.run(main(p.parse_args())))
