# Rendimiento y carga: agregador + superficie auth/admin

Fecha: 2026-10-31 · Alcance: `core/aggregator.py`, `core/cache.py`, `auth/dependencies.py`, `db/engine.py`, `services/config_service.py`, `api/deps.py`.
Harness: `backend/tests/load/` (ver su README). Suite: **126 passed** (124 previos + 2 nuevos en `tests/test_perf_aggregator.py`).

## Veredicto

**Antes: BLOQUEADO para escalar.** Con 12 proyectos, `GET /api/v1/projects` rendía ~6 req/s y *no* escalaba con la concurrencia. **Después: LISTO hasta la capacidad medida abajo** (SQLite, 1 worker). La carga corrió de verdad (uvicorn + SQLite + upstream falso de 200 ms), generador y servidor en la misma máquina, así que los absolutos son conservadores; lo válido es el delta.

## Hallazgos (por impacto) y medición antes/después

Entorno: macOS, py3.11, 1 worker uvicorn, SQLite, 12 proyectos × (github×2 [CI + release], defectdojo, sonar) = 60 llamadas upstream por listado, upstream con 200 ms.

| # | Hallazgo | Evidencia (antes) | Fix | Después |
|---|---|---|---|---|
| 1 | **La caché TTL nunca se leía en el camino feliz.** `_fetch_block` llamaba siempre a la fuente y solo usaba la caché como respaldo `stale`. Cada request de dashboard = 60 llamadas externas. | `/projects`: c=1 p50 580 ms; c=20 p95 4.3 s; c=50 p95 9.6 s; techo **6.4 req/s** (plano). Upstream recibió **15 180 llamadas** en la corrida. | Cache-first + single-flight por clave (una carga compartida; los demás esperan). | c=1 p50 **1.4 ms**; c=20 p95 58 ms; c=50 p95 538 ms; pico **1 230 req/s**. Upstream: **60 llamadas** en toda la corrida (stampede = 0). |
| 2 | **Conexión de BD retenida durante todo el request** (`get_current_user` hace `db.get` y la transacción quedaba abierta hasta cerrar la sesión: idle-in-transaction durante el fan-out). Pool por defecto = 15; con 40 dashboards lentos, los demás esperan pool. | Con 40 req/s lentos de fondo, `login` c=1: **p50 5 502 ms** (idle: 26 ms). | `await db.commit()` justo tras cargar el usuario (libera la conexión; `expire_on_commit=False` mantiene el objeto). | Misma prueba: login p50 **25 ms**, p95 70 ms. |
| 3 | **argon2 (~25 ms CPU) ejecutado sobre el event loop** en `authenticate`: cada login congela a todos. | `/api/health` p95 0.8 ms en reposo → **25.6 ms** durante logins; login tope 40 req/s. | `asyncio.to_thread` para verify/hash (argon2 libera el GIL). | health p95 19 ms bajo login c=20 (ya no se serializa); login **133 req/s** (3.3×). |
| 4 | **SQLite `database is locked`** con logins concurrentes (2 commits + auditoría por login; busy-timeout corto). | 1 error 500 + 1 excepción en login c=20 (10 s). | `timeout=30`, `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=30000` en `db/engine.py`. | 0 errores / 0 traces en todas las corridas posteriores. |
| 5 | **Sin timeout total por fuente ni tope de salida.** El timeout httpx (10 s) es por llamada; Sonar encadena 2; `list_projects` hace `gather` sin límite sobre N proyectos × 4 fuentes (ráfaga de N×4 sockets). | Estático. | `source_timeout_seconds=15` (`asyncio.wait_for`) + `Semaphore(max_upstream_concurrency=32)`. | Test `test_timeout_degrada...`. |
| 6 | **Fuente caída reintentada en cada request** (el fallo no se cachea). Con una fuente "blackholed" cada dashboard paga 10–15 s. | Estático. | Caché negativa de 10 s + single-flight (los demás comparten el fallo, no lo serializan). | Test unitario. |
| 7 | Estado `stale` duraba solo el TTL (300 s): pasado ese tiempo una caída = `unavailable`. | Estático. | `TTLCache` con ventana fresca (TTL) + ventana dura (12×TTL, `get_stale`). | Test unitario. |

Hallazgos **no aplicados** (propuestos, ver Backlog): cliente httpx nuevo por llamada (sin keep-alive/TLS reuse); `reset_aggregator` descarta la caché entera; `/projects` calcula el resumen completo cuando el listado solo necesita 4 campos.

## Respuestas a las preguntas del encargo

**1. Fan-out.** Ya era concurrente (`asyncio.gather` por proyecto y por fuente; `list_projects` también). Faltaba: lectura de caché (#1), timeout total (#5), tope de salida (#5), single-flight (#1), caché negativa (#6). `github` se consulta dos veces por proyecto (CI y release): son endpoints distintos, no es duplicación. Stale-while-revalidate real (servir stale y refrescar en background) **no existe**: lo que había era stale-on-error; ahora dura 12×TTL.

**2. Auth en el hot path.** `get_current_user` = 1 SELECT por PK (`users.id`), sin problema de índice (`username` es unique+index; el login por username usa ese índice). El costo real eran #2 y #3, no el SELECT. Medido con caché caliente: 1 230 req/s de `/projects` autenticado incluyendo ese SELECT. Pool: SQLAlchemy usa por defecto 5+10; para no-SQLite ahora es configurable (`WOLF_DB_POOL_SIZE=10`, `WOLF_DB_MAX_OVERFLOW=10`, `WOLF_DB_POOL_TIMEOUT=10`, `pool_pre_ping`, `pool_recycle=1800`).

**3. Caché de config y `reset_aggregator`.** `get_cached_config()` es una lectura de variable global: costo cero por request. Una edición admin (`_after_write`) hace UN `assemble_app_config` (1+N+1 queries, N = proyectos; a 50 proyectos son ~52 queries en una sola sesión, aceptable para una operación admin poco frecuente) y `reset_aggregator()`, que **descarta la `TTLCache` completa**: el siguiente dashboard paga el fan-out frío (60 llamadas con 12 proyectos, ~600 ms hoy). No hay thundering herd gracias al single-flight (#1), pero cada edición = un dashboard frío para todos. Mejora propuesta: conservar la caché y purgar solo claves del proyecto/fuente editados. Nota: `assemble_app_config` tiene N+1 (un SELECT de `ProjectSourceRow` por proyecto); un único `IN`/join lo reduce a 3 queries.

**4. N+1 / payloads.** N+1 solo en `assemble_app_config` (arriba). DefectDojo pide `limit=100` findings y cuenta severidades en el cliente (el `count` total sí viene del servidor; con >100 hallazgos activos los contadores por severidad se truncan: **bug de exactitud**, no de rendimiento: usar el endpoint agregado/`severity` filtrado). Los payloads `fetch_detail` devuelven crudo; revisar tamaño si Trivy/Harbor crecen.

## SLOs sugeridos (1 worker, SQLite, caché caliente)

| Ruta | p95 | Throughput sostenido | Error |
|---|---|---|---|
| `GET /api/v1/projects`, `/projects/{key}` (caché caliente) | ≤ 100 ms a c≤20 | ≥ 300 req/s | < 0.1 % |
| `GET /api/v1/projects` (frío, N=12, upstream 200 ms) | ≤ 1.5 s (un solo fan-out) | n/a | < 1 % |
| `POST /auth/login` | ≤ 400 ms a c≤20 | ≥ 100 req/s | < 0.1 % (sin 423 espurios) |
| `GET /api/health` | ≤ 50 ms bajo carga mixta | n/a | 0 |

Gate: `run_load.py --slo-p95-ms 500 --max-error-rate 0.01` (exit 1 si se rompe). **Pendiente**: soak 30–60 min (`--scenario mixed --duration 1800`) no se corrió en esta sesión; sin él no se firma estabilidad de memoria/conexiones a largo plazo.

## Capacidad: SQLite vs Postgres

- **SQLite (WAL, 1 worker)**: lecturas escalan bien (el hot path de lectura es 1 SELECT por PK). Lo que serializa son las **escrituras**: login (2 commits + auditoría), cambios admin, auditoría de acciones. Medido: 133 logins/s sin errores con WAL. Es suficiente para una consola interna: **hasta ~50–100 usuarios concurrentes / ~20 req/s sostenidos de dashboards con 1 worker** con holgura amplia.
- **Cuándo recomendar Postgres**: (a) más de 1 worker/réplica uvicorn (SQLite en archivo compartido entre procesos es frágil y no sirve en Kubernetes/multi-nodo); (b) auditoría con volumen alto de escrituras (`audit_log` crece sin límite; ver retención); (c) >~100–200 usuarios concurrentes o logins en ráfaga (inicio de turno); (d) HA/backup en caliente. Cambia solo `WOLF_DATABASE_URL=postgresql+asyncpg://…` (+ driver). Antes de producción multi-worker: caché compartida (Redis), porque la `TTLCache` es **por proceso** (cada worker repite el fan-out; el single-flight tampoco cruza procesos).
- **Pool (Postgres)**: `pool_size + max_overflow >= concurrencia de requests que tocan BD por worker` y `workers × (size+overflow) < max_connections`. Defaults dados: 10+10 por worker; con el fix #2 la conexión se libera tras autenticar, así que el pool solo cubre la ventana real de queries (ms), no el fan-out de segundos.

## Backlog priorizado

1. **Redis (o caché compartida)** antes de >1 worker (ya previsto v0.5 tras la interfaz `TTLCache`). Alto.
2. **Stale-while-revalidate real**: servir dato vencido al instante y refrescar en background (hoy: fresco ≤ TTL, o espera de la fuente). Medio-alto, mejora el p95 del primer request tras expirar.
3. **Reusar `httpx.AsyncClient` por conector/fuente** (keep-alive, pool, TLS). Hoy se crea uno por llamada; con 60 llamadas por fan-out frío son 60 handshakes TLS. Medio.
4. **Purga selectiva de caché en `reset_aggregator`** (solo proyecto/fuente editado) y `assemble_app_config` sin N+1. Medio.
5. **Exactitud DefectDojo >100 findings** (contadores truncados). Medio (corrección).
6. **Retención/purga de `audit_log`** y rate-limit de `/auth/login` en el reverse proxy (el lockout es anti-fuerza-bruta, no anti-DoS: cada intento paga ~25 ms de CPU argon2). Medio.
7. Métricas (wolf-observability): latencia por ruta, hit-rate de caché, profundidad del pool, p95 por fuente. Sin ellas el tuning en producción es a ciegas.

## Cambios aplicados (todos bajo suite verde)

- `core/cache.py`: ventana fresca + dura, `get_stale`, purga oportunista (acota memoria).
- `core/aggregator.py`: `_cached_fetch` (cache-first, single-flight, timeout, semáforo, caché negativa, stale) compartido por summary y release.
- `settings.py`: `source_timeout_seconds`, `max_upstream_concurrency`, `db_pool_size`, `db_max_overflow`, `db_pool_timeout`.
- `db/engine.py`: pragmas/timeout SQLite; pool configurable en otros motores.
- `auth/dependencies.py`: `commit()` tras cargar el usuario (libera conexión).
- `services/auth_service.py`: argon2 en hilo.
- Nuevos: `tests/test_perf_aggregator.py`, `tests/load/{seed,mock_upstream,run_load}.py` + README.

## Seguridad / aislamiento

Producto single-tenant: las claves de caché son por `proyecto:fuente`, sin datos por usuario, así que un acierto de caché no cruza fronteras de autorización (la autorización ocurre antes, en `require_role`). Si algún día hubiera datos filtrados por rol/usuario, la clave debe incluirlo. La caché negativa y stale no filtran detalles internos: el mensaje de error ya pasa saneado por el conector.

## Limitaciones de esta medición

Generador y servidor comparten CPU; un solo worker; upstream sintético con latencia fija; BD pequeña (13 usuarios, 12 proyectos); no se probó Postgres, ni soak, ni fuente "blackholed" (solo rechazada/rápida en tests unitarios).
