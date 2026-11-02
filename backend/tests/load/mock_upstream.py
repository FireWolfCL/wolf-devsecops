"""Upstream falso para pruebas de carga: imita GitHub/DefectDojo/Sonar con latencia fija.

    python tests/load/mock_upstream.py --port 9101 --delay-ms 200

Cuenta las peticiones recibidas en GET /__count (para medir cuánto fan-out real
genera el agregador y cuánto absorbe la caché).
"""
from __future__ import annotations

import argparse
import asyncio

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()
DELAY = 0.2
COUNT = {"total": 0}


@app.get("/__count")
async def count() -> dict:
    return COUNT


@app.get("/{path:path}")
async def any_get(path: str, request: Request) -> dict:
    COUNT["total"] += 1
    await asyncio.sleep(DELAY)
    if "actions/workflows" in path:
        return {"workflow_runs": [{"conclusion": "success", "status": "completed",
                                   "head_branch": "main", "run_number": 42,
                                   "html_url": "http://x", "updated_at": "2026-10-01T00:00:00Z"}]}
    if path.endswith("releases/latest"):
        return {"tag_name": "v1.0.0", "name": "v1", "published_at": "2026-10-01T00:00:00Z",
                "html_url": "http://x", "assets": []}
    if "findings" in path:
        return {"count": 3, "results": [{"severity": "High"}, {"severity": "Low"}, {"severity": "Medium"}]}
    if "qualitygates" in path:
        return {"projectStatus": {"status": "OK"}}
    if "measures" in path:
        return {"component": {"measures": [{"metric": "bugs", "value": "1"},
                                           {"metric": "coverage", "value": "81.5"}]}}
    return {}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9101)
    p.add_argument("--delay-ms", type=int, default=200)
    a = p.parse_args()
    DELAY = a.delay_ms / 1000
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
