# Arquitectura — Admin, Auth, RBAC y Centro de Update (Wolf DevSecOps)

> Documento de arquitectura. Autoridad: wolf-chief-architect. Fecha: 2026-10-31.
> Complementa `docs/ARCHITECTURE.md` (agregador) y las ADR-001..011 (no se re-litigan).
> Formaliza las ADR-012..016 (ver `.claude/agent-memory/_shared/decisions.md`).
> Idioma: español. Código e identificadores en inglés.

## Resumen ejecutivo

Wolf DevSecOps deja de ser un agregador sin estado y gana una **capa de administración con
identidad**: usuarios locales (argon2id + TOTP), RBAC de tres roles globales, un panel de
administración donde el admin edita lo que hoy vive en `config/projects.yaml`, y un Centro de
Update que reporta versión/changelog/migraciones y aplica migraciones con salvaguardas. Esto
exige **introducir persistencia** (SQLAlchemy async + Alembic) por primera vez — pero
**acotada a identidad/config/auditoría**: los resultados de escaneo **nunca** se persisten
(preserva ADR-001; las fuentes siguen siendo la verdad y la caché sigue en memoria). Los
tokens de las fuentes **siguen solo en el entorno** (preserva ADR-002): la BD guarda el
*nombre* de la variable (`token_env`), jamás su valor. El grueso del código se **porta de WIF**
(mismo stack FastAPI async + React, en producción) y el endurecimiento de migraciones de
**Wolf-Cloud**; no se inventa criptografía ni flujos de auth nuevos.

El diseño **resuelve la decisión abierta #1** (auth de usuario): deja de asumir "red interna
basta" y hace la autenticación **obligatoria y fail-closed**. `connectors/`, `ai/`,
`core/aggregator.py`, `core/cache.py` y `core/credentials.py` quedan **intactos**: la nueva
capa se monta alrededor del contrato `AppConfig` existente, no lo atraviesa.

**Lo que NO cambia:** el seam `Connector`/`AIProvider` (ADR-006/007), el contrato normalizado
(`models.py`), la degradación por fuente, el versionado `/api/v1`. **Lo que NO se hace
(scope del dueño):** multi-org / RLS, SSO/OIDC (solo seam), self-update firmado, ejecución
del redeploy desde la consola.

---

## 1. Decisiones de identidad y tenencia (restricciones del dueño — no se cuestionan)

- **Single-tenant.** Una instancia que un equipo autohospeda. Roles **globales**; no hay
  organización, ni fila-por-tenant, ni RLS. De WIF se porta `UserRole`, `require_role`,
  `security.py`, el flujo de login/MFA y el Centro de Update; se **descartan**
  deliberadamente `UserType`, `PlatformRole`, `OrgRole`, `OrgMembership`, los GUC de RLS
  (`app.current_org`/`app.platform`) y `switch-org`. Esa poda ES la simplificación a
  single-tenant.
- **Usuarios locales + MFA.** Password **argon2id**, TOTP obligatorio-opcional por usuario,
  recovery codes de un solo uso. (WIF hoy usa bcrypt; se **sube a argon2id** con
  `CryptContext(schemes=["argon2","bcrypt"], deprecated="auto")`, patrón de
  `Check4Work/server/services/identity_service.py`, que re-hashea en el próximo login los
  hashes bcrypt heredados. En Wolf DevSecOps no hay hashes heredados, pero el esquema dual
  deja la puerta abierta a importar usuarios de otra instancia).
- **Seam SSO/OIDC + multi-org a futuro, sin rediseño** (coherente con ADR-006): ver §9.

---

## 2. Persistencia: qué entra a la BD y qué NUNCA (ADR-012)

**Invariante de primer orden (preserva ADR-001):** la BD es sistema-de-registro de
**identidad, configuración y auditoría**. **Jamás** almacena resultados de escaneo (findings,
runs de CI, quality gates, releases): eso se sigue leyendo en vivo de las fuentes y viviendo
en la caché TTL en memoria (`core/cache.py`). Un reviewer de arquitectura debe poder abrir el
esquema y no encontrar ni una tabla de findings.

| Clase de dato | ¿Persiste? | Dónde |
|---|---|---|
| Usuarios, password hash, MFA, roles | Sí | BD (`users`) |
| Sesión / revocación | Sí (como entero) | BD (`users.token_version`) — **no hay tabla de sesiones** |
| Auditoría de acciones admin/auth | Sí | BD (`audit_log`, append-only) |
| Config de fuentes y proyectos | Sí | BD (`source_config`, `projects`, `project_sources`) |
| Config de IA y flags de instancia | Sí | BD (`ai_settings`, `system_config`) |
| **Tokens de fuentes / IA** | **NO** | **Entorno** (`os.environ`), por `token_env` (ADR-002) |
| **Findings, CI runs, quality, releases** | **NO** | Caché en memoria (ADR-001) |
| Resultado de un review de IA | **NO** | Se devuelve y se descarta (ADR-007) |

**Motor:** SQLAlchemy 2.x **async** (coherente con el backend async actual y con WIF
`app/database.py`). Driver por defecto **SQLite + aiosqlite** (un fichero, cero ops para el
equipo que autohospeda); **PostgreSQL + asyncpg** opcional por `WOLF_DATABASE_URL`. El motor
vive tras `db/engine.py`: cambiar de SQLite a Postgres es configuración, no rediseño (mismo
principio que la caché, ADR-005). **Migraciones: Alembic**, env.py async (patrón de
`WIF/wif-server/alembic/env.py`). Cada migración **debe** declarar un `downgrade()` real
(se verifica por AST, §6).

---

## 3. Inventario de componentes (backend)

Paquetes **nuevos** bajo `backend/wolf_devsecops/`, y cómo conviven con lo existente.

```
backend/
  alembic.ini                 # NUEVO — script_location=migrations, url vacía (runtime)
  migrations/                 # NUEVO — Alembic async env.py + versions/
    env.py
    versions/0001_initial.py  # NUEVO — todas las tablas de §4 (con downgrade real)
  wolf_devsecops/
    main.py                   # CAMBIA — lifespan: init DB, bootstrap, monta routers nuevos
    settings.py               # CAMBIA — + DATABASE_URL, JWT_SECRET, MFA_FERNET_KEY, etc.
    db/                        # NUEVO — infraestructura de persistencia
      __init__.py
      base.py                 # DeclarativeBase (Base) compartido
      engine.py               # create_async_engine + async_sessionmaker (perezoso)
      session.py              # get_db(): dependencia async generadora (patrón WIF app/database.py)
    models_db/                # NUEVO — modelos ORM SQLAlchemy (NO confundir con models.py Pydantic)
      __init__.py
      user.py                 # User (porta WIF app/models/user.py, podado a single-tenant)
      audit.py                # AuditLog (append-only)
      config.py               # SourceConfigRow, ProjectRow, ProjectSourceRow, AISettingsRow, SystemConfigRow
    auth/                     # NUEVO — identidad (seam de §9)
      __init__.py
      security.py             # argon2id, JWT(tv), TOTP, Fernet, recovery codes, QR SVG (porta WIF core/security.py)
      dependencies.py         # get_current_user (valida tv), require_role(*roles) fail-closed (porta WIF core/dependencies.py)
    rbac/                     # NUEVO — modelo de roles
      __init__.py
      roles.py                # Role(Enum)=admin|operator|viewer + matriz rol→capacidades (doc ejecutable)
    services/                 # NUEVO — lógica de dominio (nunca en routers)
      __init__.py
      auth_service.py         # login, lockout(423), verify_mfa, bump token_version (porta WIF auth flow)
      user_service.py         # CRUD usuarios, MFA setup/enable/disable, reset-password, guard anti-autobloqueo
      config_service.py       # ensambla AppConfig desde BD + import YAML (bootstrap) + invalidación de caché
      audit_service.py        # writer append-only del audit_log (saneado, sin secretos)
      update_service.py       # versión vs releases, migration_status, apply_migrations (porta WIF + Wolf-Cloud)
    api/
      deps.py                 # CAMBIA — get_config() lee de BD (vía config_service), + get_db, get_current_user
      routes_auth.py          # NUEVO — /api/v1/auth/*  (login, mfa/verify, me)
      routes_users.py         # NUEVO — /api/v1/users/* (CRUD admin + me/mfa/*, reset-password)
      routes_settings.py      # NUEVO — /api/v1/settings/* (fuentes, proyectos, IA) — admin only
      routes_updates.py       # NUEVO — version_router (auth) + router (admin) (porta WIF updates.py)
      routes_projects.py      # CAMBIA — + Depends(require viewer); lógica intacta
      routes_sources.py       # CAMBIA — + Depends(require viewer); lógica intacta
      routes_ai.py            # CAMBIA — + Depends(require operator); + gate por system_config.ai_enabled
      routes_health.py        # INTACTO — /api/health, /api/meta sin auth (ops)
    schemas_admin.py          # NUEVO — DTOs Pydantic de auth/user/settings/update (espejo de WIF schemas)
    core/
      config.py               # CAMBIA LIGERO — AppConfig se mantiene; load_config() pasa a ser el importador YAML→BD
      aggregator.py           # INTACTO
      cache.py                # INTACTO
      credentials.py          # INTACTO (sigue resolviendo token_env desde el entorno)
    connectors/               # INTACTO (todo el paquete)
    ai/                       # INTACTO (todo el paquete)
    cli/                      # CAMBIA — + comando `create-admin` (bootstrap del primer admin, §Riesgos)
```

### Responsabilidad y contrato de cada módulo nuevo

| Módulo | Responsabilidad única | Depende de | Porta de |
|---|---|---|---|
| `db/engine.py` | Crear engine async + sessionmaker, perezoso. Sin lógica. | `settings` | WIF `app/database.py` (engine) |
| `db/session.py` | `get_db()` — abre/cierra `AsyncSession` por request. **No fija GUC de RLS** (podado). | `engine` | WIF `app/database.py::get_db` |
| `models_db/user.py` | Tabla `users`: identidad, hash, MFA, rol, token_version, lockout. Sin I/O. | `db.base` | WIF `app/models/user.py` |
| `models_db/audit.py` | Tabla `audit_log` append-only. Sin I/O. | `db.base` | — (nuevo; taxonomía ↔ wolf-observability-audit-agent) |
| `models_db/config.py` | Tablas de config editables por admin. Sin secretos (solo `token_env`). | `db.base` | — (nuevo; espeja `AppConfig`) |
| `auth/security.py` | Primitivas: `hash_password`/`verify_password` (argon2id), `create_access_token`(claim `tv`)/`decode_access_token`, `create_mfa_token`, TOTP, `encrypt_mfa_secret`/`decrypt_mfa_secret` (Fernet), recovery codes, `generate_qr_code_svg`. **No toca BD.** | `settings`, `passlib`, `jose`, `pyotp`, `cryptography`, `qrcode` | WIF `app/core/security.py` |
| `auth/dependencies.py` | `get_current_user` (decodifica, valida `type==access`, `is_active`, y `tv == users.token_version` → 401); `require_role(*roles)` (403 si `user.role not in roles`, **fail-closed**). | `security`, `db.session`, `models_db.user`, `rbac.roles` | WIF `app/core/dependencies.py` |
| `rbac/roles.py` | `Role(str,Enum)=admin\|operator\|viewer` + matriz rol→capacidades (§5). | — | WIF `UserRole` (ya es admin/operator/viewer) |
| `services/auth_service.py` | Login (verifica password), **lockout HTTP 423** (`locked_until`/`failed_login_attempts`), `verify_mfa_code`, emisión de token, bump de `token_version` en logout-everywhere/reset. **Única pieza que decide auth.** | `security`, `user_service`, `audit_service` | WIF `auth.py` + `user_service.record_failed_login` |
| `services/user_service.py` | CRUD de usuarios, MFA setup/enable/disable, reset-password (temporary password), **guard "no te quites tu propio rol / no te desactives"**. | `security`, `models_db.user`, `audit_service` | WIF `app/api/v1/users.py` lógica |
| `services/config_service.py` | Ensambla `AppConfig` desde BD; **bootstrap**: si tablas vacías y existe YAML, lo importa; **rechaza secretos literales** en escritura (igual que `core/config.py`); mantiene caché de `AppConfig` con **invalidación explícita** en cada write. | `models_db.config`, `core.config` (AppConfig), `audit_service` | — (nuevo) |
| `services/audit_service.py` | Escribir una fila `audit_log` por acción sensible. Mensajes **saneados** (nunca token, hash ni stacktrace). | `models_db.audit` | WIF patrón de audit en routers |
| `services/update_service.py` | Versión actual vs releases (tag-prefix), `migration_status` (current/head/pending), `apply_migrations` (worker thread + gate de backup + reversibilidad + auto-rollback). **Nunca despliega.** | `settings`, `httpx`, `alembic`, `reversibility`(Wolf-Cloud), `audit_service` | WIF `update_service.py` + Wolf-Cloud `updates/{reversibility,migrations,backup}.py` |
| `api/routes_auth.py` | Traducir HTTP⇄`auth_service`. **Delegan.** | `auth_service`, `schemas_admin` | WIF `app/api/v1/auth.py` |
| `api/routes_users.py` | HTTP⇄`user_service`. Router con `Depends(get_current_user)`; rutas admin con `Depends(require_role(admin))`. | `user_service` | WIF `app/api/v1/users.py` |
| `api/routes_settings.py` | HTTP⇄`config_service`. Todo `Depends(require_role(admin))`. | `config_service` | WIF `app/api/v1/settings.py` (patrón de separación config/secreto) |
| `api/routes_updates.py` | `version_router` (cualquier user autenticado) + `router` (admin). Audita en migrate. | `update_service` | WIF `app/api/v1/updates.py` |

### Interfaz `require_role` (`auth/dependencies.py`) — punto de enforcement

```python
def require_role(*roles: Role):
    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:          # fail-closed: solo pertenencia explícita pasa
            raise HTTPException(403, "Insufficient role")
        return user
    return _check
```

`get_current_user` es el **único** punto que lee y valida el token; `require_role` es el
**único** punto que decide capacidad. Ningún router implementa su propia comprobación. La
validación `tv == users.token_version` (claim `tv`, por defecto `0` si falta) es lo que hace
que un cambio de password/rol o un "cerrar sesión en todo" invaliden todos los tokens previos
sin tabla de sesiones (patrón WIF).

---

## 4. Esquema de base de datos (ADR-012) + migración Alembic inicial

Columnas clave. Tipos en notación SQLAlchemy/portable (SQLite y Postgres).

### `users` (porta WIF `app/models/user.py`, podado a single-tenant)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | UUID / String PK | `uuid4` en app (SQLite no tiene `gen_random_uuid`) |
| `username` | String(100) unique not null | login local (WIF usa `email`; aquí `username` + `email` opcional) |
| `email` | String(255) unique nullable | para `provisioning_uri` del TOTP |
| `hashed_password` | String(255) not null | **argon2id** (`CryptContext(["argon2","bcrypt"])`) |
| `full_name` | String(100) nullable | |
| `role` | Enum(`admin`/`operator`/`viewer`) not null | default `viewer` |
| `is_active` | Boolean not null | default `true` (fail-closed: inactivo → 401) |
| `failed_login_attempts` | Integer not null | default `0` |
| `locked_until` | DateTime nullable | gate de lockout → **HTTP 423** |
| `mfa_enabled` | Boolean not null | default `false` |
| `mfa_secret` | String(255) nullable | **secreto TOTP cifrado con Fernet** (nunca en claro) |
| `mfa_recovery_codes` | JSON nullable | **lista de hashes SHA-256** de un solo uso (no los códigos) |
| `token_version` | Integer not null | default `0` — fuente del claim `tv` (revocación) |
| `must_change_password` | Boolean not null | default `false` — fuerza cambio tras bootstrap/reset |
| `created_at` / `updated_at` / `last_login_at` | DateTime | |

> **Decisión de forma (fiel a WIF):** MFA y recovery codes son **columnas de `users`**, no
> tablas aparte. Es el patrón probado en producción y evita joins en el hot-path de login.
> **No hay tabla `sessions`:** la sesión es stateless (JWT) y la revocación es `token_version`.
> Si a futuro se quiere refresh-tokens/sesiones server-side, es una tabla aditiva + un seam
> en `auth_service`, no un rediseño.

### `audit_log` (append-only; taxonomía con wolf-observability-audit-agent)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | UUID/String PK | |
| `created_at` | DateTime not null | índice |
| `actor_user_id` | UUID/String nullable | null = acción de sistema (bootstrap, arranque) |
| `actor_username` | String(100) nullable | snapshot denormalizado (sobrevive al borrado del user) |
| `action` | String(80) not null | `event_type`: `auth.login`, `auth.login_failed`, `user.locked`, `user.created`, `user.updated`, `user.password_reset`, `user.mfa_enabled`, `user.mfa_disabled`, `config.source_updated`, `config.project_updated`, `config.ai_toggled`, `update.migrations_applied` |
| `target_type` | String(60) nullable | `user` / `source` / `project` / `ai` / `migration` |
| `target_id` | String(120) nullable | |
| `result` | Enum(`success`/`failure`) not null | |
| `detail` | JSON nullable | **saneado**: nunca token, hash, secreto ni stacktrace |
| `ip` | String(64) nullable | |

### Config (editable por admin; reemplaza `projects.yaml` como fuente de verdad)

`source_config` — una fila por fuente:

| Columna | Tipo | Notas |
|---|---|---|
| `name` | String(40) PK | `github`/`defectdojo`/`sonarqube`/`wss` |
| `url` | String(400) not null | `api_url`/`base_url` |
| `token_env` | String(120) not null | **NOMBRE** de la env var; **jamás el valor** (ADR-002) |
| `enabled` | Boolean not null | |
| `updated_at` / `updated_by` | | auditoría ligera |

`projects` — una fila por proyecto:

| `key` String PK | `name` String not null | `enabled` Boolean | `created_at` |

`project_sources` — binding proyecto↔fuente (un row por par; **crecer = un row, no migración**, alinea ADR-006):

| `id` PK | `project_key` FK→projects | `source` String(40) | `settings` JSON | UniqueConstraint(`project_key`,`source`) |

> `settings` JSON lleva lo específico por fuente (`{repo, ci_workflow}` para github,
> `{product}` para defectdojo/wss, `{project_key}` para sonarqube). Así añadir una fuente nueva
> (GitLab, Jenkins…) **no toca el esquema**: es un `source` nuevo con su JSON, idéntico al seam
> `Connector`.

`ai_settings` — fila única (`id=1`):

| `provider` (`local`/`hosted`) | `model` | `base_url` nullable | `token_env` nullable | `enabled` Boolean |

`system_config` — clave/valor tipado de instancia (incluye `config_bootstrapped: bool`, `ai_enabled: bool`):

| `key` String PK | `value` JSON | `updated_at` |

### Migración Alembic inicial `0001_initial`

- Crea las 7 tablas anteriores. **Debe declarar `downgrade()` real** (drop de todas) — lo exige
  el chequeo de reversibilidad por AST (§6); una migración con `downgrade` vacío **no se aplica**.
- `alembic.ini` con `sqlalchemy.url` vacía (se fija en runtime desde `WOLF_DATABASE_URL`, patrón
  WIF: credencial nunca versionada). `migrations/env.py` async, `target_metadata = Base.metadata`.
- **Arranque NO auto-migra.** El Centro de Update reporta migraciones pendientes y el admin las
  aplica conscientemente (§6). El arranque sí verifica que la BD exista/responda (fail-closed).

---

## 5. Modelo RBAC (ADR-014)

Tres roles **globales** (columna `users.role`; es el `UserRole` que WIF ya define). Punto de
enforcement **único**: `require_role(*roles)` en `auth/dependencies.py`, **fail-closed**.

| Capacidad | viewer | operator | admin |
|---|:--:|:--:|:--:|
| Ver dashboards / proyectos / detalles de fuente (`GET /projects*`, `/sources/*`) | ✅ | ✅ | ✅ |
| Ver versión del sistema (`GET /updates/version`) | ✅ | ✅ | ✅ |
| Gestionar su propio perfil / MFA / password (`/users/me/*`) | ✅ | ✅ | ✅ |
| Disparar review de IA (`POST /projects/{k}/ai/review`) | ❌ | ✅ | ✅ |
| Configurar fuentes, proyectos, IA (`/settings/*`) | ❌ | ❌ | ✅ |
| Gestionar usuarios (`/users` CRUD, reset, unlock) | ❌ | ❌ | ✅ |
| Ver estado de update y aplicar migraciones (`/updates/status`, `/updates/migrate`) | ❌ | ❌ | ✅ |

Enforcement por router:

| Router | Guard |
|---|---|
| `routes_health` | ninguno (ops; `/api/health`, `/api/meta`) |
| `routes_auth` | `/login`, `/mfa/verify` públicos; `/me` = `get_current_user` |
| `routes_projects`, `routes_sources` | `Depends(require_role(viewer, operator, admin))` |
| `routes_ai` | `Depends(require_role(operator, admin))` **y** gate `system_config.ai_enabled` |
| `routes_users` | router: `get_current_user`; rutas admin: `require_role(admin)`; `/users/me/*`: cualquiera autenticado |
| `routes_settings` | `Depends(require_role(admin))` |
| `routes_updates` | `version_router`: `get_current_user`; `router`: `require_role(admin)` |

**Por qué solo un enum y no una matriz de permisos en BD:** el dueño fijó roles globales y
single-tenant; una tabla `permissions` sería complejidad sin demanda. El **seam de crecimiento**
es claro: si algún día hacen falta permisos finos, se añade una tabla `role_permissions` y
`require_role` pasa a `require_permission` — aditivo, sin tocar los routers (solo el decorador).
Se documenta como seam, no se construye (regla: no sobre-diseñar).

---

## 6. Centro de Update v1 (ADR-016) — reportar + aplicar migraciones con salvaguardas

Alcance v1 (restricción del dueño): **reportar** (versión actual vs. última release + changelog
+ migraciones pendientes) y **aplicar migraciones** desde la UI con audit + gate de backup en
producción + chequeo de reversibilidad. El **redeploy se MUESTRA como comando** para copiar;
**no se auto-ejecuta**. **Nada de self-update firmado** por ahora.

### Qué se porta y qué NO (divergencia consciente con Wolf-Cloud)

De **WIF `app/services/update_service.py` + `app/api/v1/updates.py`** (base del v1):
- Versión por **prefijo de tag** (`_COMPONENT_TAG_PREFIXES`), no `/releases/latest`; comparación
  **tri-estado** (`_is_newer` devuelve `None`, nunca "newer", ante input no parseable).
- `_fetch_releases()` **nunca lanza**: sin repo → `no_repo`, sin red → `network_error`, etc.
  **Degradación offline fail-closed** (nunca "estás al día" cuando no pudo leer).
- `migration_status()` = `{current, head, pending, up_to_date}` leyendo `alembic/versions` y
  `alembic_version`.
- `apply_migrations(environment, confirm_backup)`: **gate de backup en producción**
  (`if environment=="production" and not confirm_backup: raise`) y `alembic upgrade head` vía
  **`await asyncio.to_thread(command.upgrade, cfg, "head")`** (obligatorio: el env.py async usa
  `asyncio.run`, no puede correr dentro del loop de FastAPI).
- API: **`version_router` separado** (cualquier user autenticado ve la versión) vs **`router`
  gated** (admin ve status y aplica). **Audita `update.migrations_applied`** con `{current, head}`.
- `update_command` **display-only** (se muestra para copiar).

De **Wolf-Cloud `updates/`** (endurecimiento, adaptado a async):
- `reversibility.has_real_downgrade(module)` + `assert_reversible(checks)`: por **AST**, rechaza
  aplicar si alguna migración pendiente tiene `downgrade()` vacío (solo docstring/`pass`).
  `migrations.pending_reversibility()` es el puente. **Se corre ANTES de tocar la BD.**
- `migrations.apply_migrations`: **auto-rollback** — si `upgrade head` falla, hace
  `command.downgrade(cfg, antes.current or "base")`; si el rollback también falla, exige
  intervención manual nombrando ambos errores. (En Wolf-Cloud es sync; aquí se envuelve en
  `asyncio.to_thread`, igual que WIF.)
- `backup.take_snapshot`: el gate de backup del dueño puede ser (v1) el **booleano
  `confirm_backup`** de WIF, y endurecerse (v1.1) con un `pg_dump`/copia real estilo Wolf-Cloud
  cuando el deploy es Postgres. Se documenta el camino; v1 exige al menos el booleano confirmado.

**Lo que NO se porta de Wolf-Cloud ADR-006 (scope del dueño):** artefacto firmado + verificación
de firma, el **Update Agent separado** con su propio usuario de sistema, la ejecución del deploy
y el health-check post-deploy con rollback automático de versión. En Wolf DevSecOps v1 la
consola **reporta y migra**; el redeploy lo hace el operador con el comando mostrado. Esta
divergencia es deliberada y se registra en ADR-016 para no re-litigarla.

### Pipeline de `apply_migrations` (orden fijo, cada paso su excepción)

1. `assert_reversible(pending_reversibility())` → si hay `downgrade` vacío: **400**, no se toca nada.
2. Gate de backup: en `production`, `confirm_backup` debe ser `true` (y, v1.1, snapshot real) → si no: **400**.
3. `await asyncio.to_thread(command.upgrade, cfg, "head")`; si falla → auto-rollback a `antes.current`.
4. Auditar `update.migrations_applied {current, head}` y devolver `migration_status()` fresco.

---

## 7. Migración de la configuración YAML→BD (ADR-015)

Hoy `config/projects.yaml` es la fuente de verdad y `get_config()` (en `api/deps.py`) la lee con
`@lru_cache`. El nuevo diseño hace la **BD** la fuente de verdad, editable por el admin, con
**compatibilidad hacia atrás**:

1. **Bootstrap (una vez):** al arrancar, si las tablas de config están **vacías** y existe
   `config/projects.yaml`, `config_service` **lo importa** a `source_config`/`projects`/
   `project_sources`/`ai_settings`, marca `system_config.config_bootstrapped=true` y lo audita
   (`config.*`). Si no hay YAML y no hay config en BD: el dashboard arranca vacío y el admin
   configura desde el panel (o corre el wizard CLI, que ahora puede escribir a BD).
2. **Tras el bootstrap, la BD manda.** El YAML queda como semilla/export; no se vuelve a leer en
   caliente. (Opcional: un botón "re-importar YAML" explícito en el panel, auditado.)
3. **`get_config()` cambia de fuente, no de tipo:** sigue devolviendo el **mismo `AppConfig`
   Pydantic** (de `core/config.py`), ahora **ensamblado desde BD** por `config_service`. Por eso
   `connectors/`, `core/aggregator.py` y `ai/` **no se tocan**: reciben el contrato de siempre.
4. **Invalidación de caché (riesgo real):** el `@lru_cache` actual serviría config vieja tras una
   edición del admin. Se reemplaza por una **caché de `AppConfig` en `config_service` con
   invalidación explícita** en cada escritura (`invalidate()` al final de cada endpoint de
   `/settings/*`). El agregador comparte config vía `get_config()`; tras invalidar, la próxima
   request reconstruye `AppConfig` y el `Aggregator` toma la config nueva. **Criterio de hecho:**
   editar una fuente en el panel cambia el dashboard sin reiniciar el proceso.
5. **Secretos siguen en el entorno (ADR-002 intacto):** el panel edita `token_env` (el **nombre**
   de la variable), nunca el valor. `config_service` **rechaza** en escritura cualquier intento
   de guardar un valor con pinta de token (misma regla que `_reject_literal_secrets` de
   `core/config.py`). El navegador jamás ve un token (el panel muestra "variable `GITHUB_TOKEN`:
   presente/ausente", resuelto server-side por `core/credentials.py`).
6. **El toggle de IA se mueve al panel** (`ai_settings.enabled` / `system_config.ai_enabled`),
   reemplazando `WOLF_AI_ENABLED` como control de runtime — **pero** el env sobrevive como
   **techo de seguridad fail-closed**: si `WOLF_AI_ENABLED=false` en el entorno, el admin **no
   puede** activar IA desde el panel (respeta el gate de wolf-ai-safety-agent, ADR-007). El panel
   solo ofrece el toggle una vez que ai-safety dio el visto bueno. Precedencia:
   `IA activa ⟺ env no la prohíbe Y ai_settings.enabled Y hay bloque ai válido`.

---

## 8. Contratos HTTP nuevos (bajo `/api/v1`, ADR-006)

Espejo de WIF; JSON de `schemas_admin.py`. **authn/authz explícita por endpoint** (regla de
seguridad: "assumed authenticated" se rechaza).

| Método | Ruta | Cuerpo / Respuesta | authz | Códigos |
|---|---|---|---|---|
| POST | `/api/v1/auth/login` | form `username,password` → `LoginResponse{access_token?, mfa_required, mfa_token?}` | público | 200, 401, **423** (locked) |
| POST | `/api/v1/auth/mfa/verify` | `{mfa_token, code}` → `Token{access_token}` | público (mfa_token) | 200, 401, 423 |
| GET | `/api/v1/auth/me` | → `MeOut{id,username,role,mfa_enabled,...}` | autenticado | 200, 401 |
| GET | `/api/v1/users` | → `UserListResponse` | admin | 200, 401, 403 |
| POST | `/api/v1/users` | `UserCreate` → `UserOut` | admin | 201, 401, 403, 409 |
| PATCH | `/api/v1/users/{id}` | `UserUpdate` → `UserOut` | admin | 200, **400** (auto-rol/auto-desactivación), 403 |
| POST | `/api/v1/users/{id}/reset-password` | → `{temporary_password}` (una vez) | admin | 200, 403 |
| POST | `/api/v1/users/{id}/unlock` | → 204 | admin | 204, 403 |
| POST | `/api/v1/users/me/change-password` | `{current,new}` → 204 | autenticado | 204, 400 |
| POST | `/api/v1/users/me/mfa/setup` | → `{secret, otpauth_uri, qr_code_svg}` | autenticado | 200, 400 (ya activo) |
| POST | `/api/v1/users/me/mfa/enable` | `{code}` → `{recovery_codes}` (una vez) | autenticado | 200, 400 |
| POST | `/api/v1/users/me/mfa/disable` | `{password}` → 204 | autenticado | 204, 401 |
| GET | `/api/v1/settings/sources` | → lista de fuentes (sin valores de token) | admin | 200, 403 |
| PUT | `/api/v1/settings/sources/{name}` | `SourceIn{url, token_env, enabled}` → ... | admin | 200, **400** (token literal), 403 |
| GET/POST/PUT/DELETE | `/api/v1/settings/projects[/{key}]` | CRUD de proyectos | admin | 200/201/204, 403, 404 |
| GET/PUT | `/api/v1/settings/ai` | `AISettingsIn{provider,model,base_url,token_env,enabled}` | admin | 200, **409** (env prohíbe IA), 403 |
| GET | `/api/v1/updates/version` | → `CurrentVersionOut` | autenticado | 200 |
| GET | `/api/v1/updates/status` | → `UpdateStatusOut{server,migrations,update_command,environment,...}` | admin | 200, 403 |
| POST | `/api/v1/updates/migrate` | `{confirm_backup}` → `ApplyMigrationsOut` | admin | 200, **400** (irreversible / falta backup), 403, 500 |

**Endpoints existentes que cambian su authz (resuelve decisión abierta #1):**

| `GET /api/v1/projects`, `/projects/{k}`, `/projects/{k}/sources/{s}` | ahora **autenticado (viewer+)** |
| `POST /api/v1/projects/{k}/ai/review` | ahora **operator+** y gated por `ai_enabled` |
| `/api/health`, `/api/meta` | **siguen sin auth** (ops; `/api/meta` no debe revelar config sensible) |

---

## 9. Fronteras de confianza, clasificación de datos y seams

### Fronteras de confianza (actualiza §5 de ARCHITECTURE.md)

| Frontera | Qué cruza | Autenticación |
|---|---|---|
| Navegador ⇄ `/api/v1/*` | Datos agregados + config + identidad | **JWT Bearer obligatorio** (argon2id+TOTP al emitir). Fail-closed. |
| Navegador ⇄ `/api/v1/auth/login` | credenciales (clase **secret** en tránsito) | público; lockout 423; **exige TLS** en el reverse proxy |
| Backend ⇄ BD | identidad/config/audit (sin findings, sin tokens de fuente) | credencial de BD por `WOLF_DATABASE_URL` (secret, solo entorno) |
| Backend ⇄ entorno | JWT secret, Fernet key, tokens de fuente/IA, DB URL | `os.environ`; **fail-closed si faltan en prod** |
| Backend ⇄ fuentes externas | igual que hoy (ADR-002) | token por fuente, solo server-side |

### Clasificación de datos

| Clase | Ejemplos | Regla |
|---|---|---|
| **secret** | password hash, `mfa_secret` (Fernet), JWT secret, Fernet key, tokens de fuente, DB URL | nunca en logs/JSON/`audit_log.detail`/navegador; hash/cifrado en reposo |
| **internal** | findings, CI, quality, config de proyectos, audit_log | tras auth; no público |
| **recovery codes** | códigos MFA | se muestran **una vez** en claro al generarlos; en BD solo hashes SHA-256 |
| PII | — (no hay datos personales de terceros; solo cuentas operativas del equipo) | username/email tratados como **internal** |

### Seam SSO/OIDC + multi-org (futuro, ADR-013/ADR-006)

- **Identidad tras un seam:** `auth_service.authenticate(username, password)` es el único punto
  que valida credenciales locales. Un `IdentityProvider` futuro (OIDC) se añade como alternativa
  que resuelve un `User` y emite el mismo JWT — sin tocar routers ni `require_role`. `User` ya
  tiene el gancho (`hashed_password` nullable para cuentas federadas).
- **Multi-org tras un seam:** hoy `role` es global. Multi-org = añadir `organization` +
  `membership(user, org, role)` y cambiar `require_role` por un `require_org_role` scoped +
  RLS — exactamente lo que WIF ya tiene y que aquí se **podó a propósito**. El contrato HTTP no
  cambia para el cliente single-tenant. Se documenta; no se construye.

---

## 10. Flujos críticos (happy path + error)

### F-Login con MFA
1. `POST /auth/login` (username/password). Si `locked_until>now` → **423** (fail-closed).
2. Password inválida → `record_failed_login` (incrementa; al umbral setea `locked_until`, audita
   `user.locked`) → **401**.
3. Password OK y `mfa_enabled` → `LoginResponse{mfa_required:true, mfa_token}` (corto, `type=mfa`),
   **sin** access token. Front pide el código → `POST /auth/mfa/verify{mfa_token, code}`.
4. TOTP inválido → `record_failed_login` (puede bloquear) → 401. Válido → emite access token
   (claim `tv=users.token_version`), resetea contador, audita `auth.login`.
5. Password OK y sin MFA → emite access token directo.
   **Error de BD caída:** el login **falla cerrado** (500 saneado); no se emite token.

### F-Editar una fuente desde el panel (config YAML→BD en caliente)
1. Admin `PUT /settings/sources/github {url, token_env:"GITHUB_TOKEN", enabled:true}`.
2. `config_service` valida: si `token_env` trae un valor con pinta de token → **400** (ADR-002).
3. Escribe la fila, audita `config.source_updated`, **invalida** la caché de `AppConfig`.
4. Próxima request a `/projects` reconstruye `AppConfig` desde BD; el `Aggregator` usa la URL
   nueva y resuelve el token desde el entorno (`core/credentials.py`). El token **nunca** viajó
   al navegador ni a la BD.
   **Error:** token_env ausente en el entorno → la fuente queda `not_configured` (degradación de
   siempre), no rompe el dashboard.

### F-Aplicar migraciones desde el Centro de Update
1. Admin abre `/updates` → `GET /updates/status` muestra current/head/pending + changelog +
   `update_command` (display-only). Sin red → degrada con motivo, no miente "al día".
2. Admin pulsa "Aplicar". En producción, sin `confirm_backup` marcado → **400** (gate de backup).
3. Backend: `assert_reversible(pending_reversibility())` → si hay `downgrade` vacío → **400**,
   no toca la BD.
4. `asyncio.to_thread(command.upgrade, cfg, "head")`. Si falla → auto-rollback a `antes.current`;
   si el rollback falla → 500 nombrando ambos errores ("intervención manual con el respaldo").
5. Éxito → audita `update.migrations_applied{current,head}`, devuelve status fresco, y la UI
   muestra el `update_command` para que el operador haga el redeploy **a mano** (no se auto-ejecuta).

---

## 11. Orden de implementación (fases) y agente responsable

Principio: **identidad y persistencia primero** (todo lo demás las necesita), luego enforcement,
luego panel y update, frontend al final. Cada fase verificable y con gate de QA/seguridad.

| Fase | Entregable | Agente(s) | "Hecho" verificable |
|---|---|---|---|
| **A0 — Persistencia** | `db/`, `models_db/`, Alembic `0001_initial` (con downgrade real), `config_service` (ensamblar `AppConfig` + import YAML), `get_config()` DB-backed con invalidación | **wolf-server-core-agent** (+ reversibilidad de Wolf-Cloud) | `alembic upgrade head` crea tablas; dashboard sigue funcionando leyendo config desde BD; `alembic downgrade base` revierte |
| **A1 — Crypto primitives** | `auth/security.py` (argon2id, JWT+tv, TOTP, Fernet, recovery, QR); provisión de `JWT_SECRET`/`MFA_FERNET_KEY` fail-closed | **wolf-security-crypto-agent** | tests de hash/verify, encrypt/decrypt MFA, TOTP verify, recovery one-shot; arranque aborta en prod sin secretos |
| **A2 — Identidad + login/MFA** | `models_db/user.py`, `auth_service`, `user_service`, `routes_auth`, bootstrap del primer admin (CLI `create-admin`) | **wolf-identity-directory-agent** (+ A1) | login+MFA end-to-end; lockout 423; `tv` invalida tokens; primer admin creado sin credencial hardcodeada |
| **A3 — RBAC enforcement** | `rbac/roles.py`, `auth/dependencies.py` (`get_current_user`, `require_role`), aplicar guards a TODOS los routers | **wolf-rbac-agent** | viewer no puede `POST /ai/review` (403); no-admin no entra a `/settings` (403); matriz §5 cubierta por tests |
| **A4 — Panel admin (config)** | `routes_settings`, `routes_users`, CRUD de fuentes/proyectos/IA, toggle IA bajo techo env | **wolf-server-core-agent** + **wolf-frontend-agent** | admin edita fuente/proyecto y el dashboard cambia sin reiniciar; token nunca sale al navegador |
| **A5 — Centro de Update** | `update_service` (WIF + Wolf-Cloud), `routes_updates` (version_router + router), audit en migrate | **wolf-update-center-agent** | status muestra versión/changelog/pending; migrate con gate backup + reversibilidad + auto-rollback; redeploy solo mostrado |
| **A6 — Frontend auth** | `AuthContext`, `tokenStorage`, interceptores (401→/login), páginas Login/MFA/Profile/Users/Settings/UpdateCenter, guard de rol en nav | **wolf-frontend-agent** | flujo login→MFA; enrolamiento MFA (QR+recovery); nav oculta lo no permitido; server es autoritativo |
| **Transversal** | QA de cada fase; revisión de amenazas de la superficie nueva; gate de IA | **wolf-qa-validation-agent**, **wolf-threat-hardening**, **wolf-ai-safety-agent** | reversibilidad de migraciones en CI; modelo de amenazas de auth/token; toggle IA solo tras visto bueno ai-safety |

Dependencias: A1→A2→A3 en serie (crypto antes que identidad antes que enforcement). A4/A5 dependen
de A3 (necesitan `require_role`). A6 puede empezar contra A2 y completarse tras A4/A5. A0 habilita
todo. El reviewer IA (F6 del MVP) solo se **expone** tras el visto bueno de wolf-ai-safety-agent.

---

## 12. Mapa de páginas frontend (ADR-013/014) — protección por rol

Stack y patrones portados de `wif-frontend` (axios `baseURL "/api/v1"`, token en `localStorage`,
interceptor 401→`/login`, AuthContext con login de dos pasos). El diseño visual lo afina
wolf-web-designer-agent / wolf-frontend-agent.

| Página / ruta | Qué hace | Visible para |
|---|---|---|
| `Login` `/login` | user/password → si `mfa_required`, paso TOTP con `mfa_token` (`login()` → `completeMfaLogin()`, patrón WIF `AuthContext`) | público |
| `Profile` `/profile` | datos de cuenta, cambiar password, **enrolar MFA** (setup → QR SVG `dangerouslySetInnerHTML` + secreto → enable → recovery codes una vez; disable con password) | autenticado |
| `Dashboard` `/` y `ProjectDetail` `/projects/:key` | lo actual, ahora tras login | viewer+ |
| `Users` `/admin/users` | CRUD de usuarios, reset-password, unlock, cambiar rol (con guard anti-autobloqueo en UI, espejo del server) | admin |
| `Settings` `/admin/settings` | editar fuentes (url + `token_env`, mostrando "presente/ausente", nunca el valor), proyectos, **toggle IA** (deshabilitado si el env lo prohíbe) | admin |
| `UpdateCenter` `/admin/updates` | versión actual vs. release + changelog, migraciones pendientes, botón aplicar (checkbox backup en prod), `update_command` copy-to-clipboard **display-only** | admin |
| `VersionInfo` (o badge) | versión actual del sistema | autenticado |

**Protección de navegación:** `AuthContext` + un `RequireRole` wrapper ligero (mejora sobre WIF,
que chequea inline) que oculta/redirige rutas según `user.role`. **El guard de front es solo
hint**: el servidor (`require_role`) es **la autoridad** — el front nunca decide acceso, solo
ergonomía. Interceptor 401 limpia token y va a `/login`; 403 muestra "sin permiso".

---

## 13. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| **JWT secret / Fernet key débiles** (WIF trae defaults `changeme-*`) | `settings` exige `WOLF_JWT_SECRET` y `WOLF_MFA_FERNET_KEY` desde el entorno; **el arranque aborta en `environment=production` si son el default o faltan** (fail-closed). Doc de generación (`secrets.token_urlsafe`). Dueño del control: **wolf-security-crypto-agent**. |
| **Bootstrap del primer admin** | **Sin credencial hardcodeada.** CLI `wolf-devsecops create-admin` (headless, coherente con ADR-010) o seed por env de un **password temporal de un solo uso** con `must_change_password=true` + enrolar MFA en el primer login. Si no hay ningún usuario, la API no expone datos (fail-closed) y solo permite el bootstrap. |
| **La BD viola ADR-001** (persistir findings) | Invariante §2 explícito + revisión de QA: ninguna tabla de findings/CI. `audit_log.detail` nunca lleva payloads de escaneo. |
| **La BD viola ADR-002** (token en BD) | `source_config.token_env` guarda el **nombre**; `config_service` **rechaza** valores con pinta de token en escritura (misma regla que `core/config.py`). El navegador ve "presente/ausente", nunca el valor. |
| **Config stale tras edición** | `@lru_cache` de `get_config()` se reemplaza por caché invalidable en `config_service`; cada write de `/settings/*` invalida. Test: editar fuente cambia el dashboard sin reiniciar. |
| **Migración irreversible / a medias** | `assert_reversible` (AST, Wolf-Cloud) antes de tocar BD; auto-rollback si `upgrade` falla; gate de backup en prod; CI corre reversibilidad (wolf-qa-validation-agent). |
| **Admin se auto-bloquea** | Guard "no te quites tu propio rol / no te desactives" en `user_service` (WIF `PATCH /users/{id}`), con test. |
| **Auth rompe el frontend actual / deploy expuesto** | Resuelve decisión abierta #1: auth obligatoria; el deploy exige reverse proxy TLS; `/api/meta` no revela config sensible. Migración del front en A6. |
| **Admin salta el gate de IA de ai-safety** | El toggle del panel opera **bajo** el techo `WOLF_AI_ENABLED` del entorno (fail-closed); solo se ofrece tras visto bueno de **wolf-ai-safety-agent** (ADR-007). |
| **alembic.command en el event loop async** | `apply_migrations` corre `command.upgrade` en `asyncio.to_thread` (patrón WIF); nunca directo en el loop. |

---

## 14. Decisiones abiertas (requieren stakeholder)

1. **Login por `username` vs `email`.** WIF usa `email`; aquí propongo `username` (+ email opcional
   para el `otpauth` URI). Confirmar con wolf-product-orchestrator / wolf-identity-directory-agent.
2. **MFA obligatorio para admin.** ¿Se fuerza MFA al rol admin, o queda opcional por usuario?
   Recomiendo obligatorio para admin. Dueños: wolf-security-crypto-agent + product.
3. **Backup real vs. booleano en prod (Update Center).** v1 = `confirm_backup` booleano (WIF);
   ¿se exige `pg_dump`/snapshot real (Wolf-Cloud `backup.take_snapshot`) para el deploy Postgres
   en v1 o v1.1? Dueño: wolf-update-center-agent + product.
4. **SQLite vs Postgres por defecto.** Propongo SQLite para el equipo que autohospeda; Postgres por
   `WOLF_DATABASE_URL`. Confirmar con wolf-performance-scale-agent si se esperan muchas cuentas.
5. **Alcance del audit_log / retención.** La taxonomía §4 es inicial; coordinar el modelo y la
   retención con **wolf-observability-audit-agent** (y Ley 21.719 vía wolf-data-privacy-agent si
   `email`/`full_name` cuentan como datos personales del equipo).
```
