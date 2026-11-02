# Harness de carga (Wolf DevSecOps)

Sin dependencias nuevas (httpx + uvicorn, ya en el venv). Ver `docs/PERFORMANCE-ADMIN.md`.

```bash
export WOLF_DATABASE_URL=sqlite+aiosqlite:////tmp/load.db   # o postgresql+asyncpg://...
export WOLF_JWT_SECRET=<>=32 chars>  WOLF_MFA_FERNET_KEY=<Fernet key>  LOAD_TOKEN=x
cd backend && export PYTHONPATH=.
python tests/load/seed.py --projects 12 --upstream http://127.0.0.1:9101   # imprime TOKEN=...
python tests/load/mock_upstream.py --port 9101 --delay-ms 200 &           # upstream falso (cuenta llamadas en /__count)
uvicorn wolf_devsecops.main:create_app --factory --port 8000 &
python tests/load/run_load.py --token $TOKEN --scenario projects --ramp 1,5,20,50 --duration 10 --slo-p95-ms 500
python tests/load/run_load.py --scenario login --ramp 1,5,20
python tests/load/run_load.py --token $TOKEN --scenario mixed --concurrency 30 --duration 1800   # soak
```
`--upstream http://127.0.0.1:9` prueba degradación rápida (conexión rechazada). Sale con código 1 si se rompe el SLO (usable como gate de CI).
Ejecutar generador y servidor en la misma máquina sesga a la baja los números absolutos; compara antes/después, no valores absolutos.
