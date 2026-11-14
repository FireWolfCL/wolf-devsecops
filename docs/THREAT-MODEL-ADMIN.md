# Modelo de amenazas — Capa Admin, Auth, RBAC, Conectores y Centro de Update

> Autoridad: **wolf-threat-hardening**. Fecha: 2026-10-31. Postura: auditoría read-only + fixes
> pequeños y claramente correctos (anotados en §7). Alcance: **superficie NUEVA** de Wolf
> DevSecOps (auth local + MFA, RBAC, panel de config, Centro de Update, conectores Harbor/Trivy).
> Complementa `docs/ARCHITECTURE-ADMIN.md` y ADR-012..016. NO re-litiga decisiones del dueño
> (single-tenant, token en localStorage portado de WIF, redeploy manual): se evalúan como riesgo
> y se proponen controles compensatorios.

## Resultado (TL;DR)

La capa nueva está **bien construida**: alg-allowlist JWT, revocación por `token_version`,
lockout 423, anti-enumeración con hash dummy, separación de claves JWT/Fernet, fail-closed de
secretos en prod, recovery codes con comparación en tiempo constante, RBAC fail-closed en una
única dependencia aplicada a **todos** los routers, reversibilidad AST + auto-rollback + gate de
backup en el Centro de Update, y `token_env` que guarda el **nombre** de la variable, nunca el
valor. No se encontró ningún endpoint sin guard, ni una vía de escalada operator→admin, ni
secretos en el audit log.

Hallazgos que importan antes de producción:

1. **T-SSRF-01 (High→Medium tras fix):** las URLs de fuente/IA que configura el admin disparan
   peticiones salientes **con el token de la fuente adjunto**. Permitía apuntar a IMDS
   (`169.254.169.254`) y robar credenciales del rol de la instancia, o exfiltrar un token de
   fuente a un host atacante (rompería ADR-002). **Fix aplicado:** se bloquean los endpoints de
   metadata de nube en el write-path y en el probe. Residual (riesgo aceptado): exfiltración a un
   host público arbitrario — sin allowlist de hosts (cambio de producto, scope del dueño).
2. **T-AUTH-01 (High):** no hay rate-limit a nivel de aplicación en `/auth/login` ni
   `/auth/mfa/verify`; el lockout por-cuenta no frena password-spraying ni fuerza bruta
   distribuida. Hoy se confía al reverse proxy. **Recomendación must-fix.**
3. **T-AUTH-04 (High):** MFA **no se exige server-side** para admin pese a ADR-013 decisión #2;
   `must_enroll_mfa` es solo un hint de UI y se saltea llamando la API directa. **Must-fix.**

Fixes aplicados en esta pasada (pequeños, no rompen contrato): guard IMDS/metadata (SSRF) y
`from None` en los `raise` de anti-enumeración. Detalle en §7.

---

## 1. Componentes y flujos en alcance

| Componente | Archivos | Rol de seguridad |
|---|---|---|
| Primitivas cripto | `auth/security.py` | argon2id, JWT HS256+`tv`, TOTP, Fernet, recovery codes |
| Decisión de auth | `services/auth_service.py` | login, lockout, verify MFA, bump `tv` |
| Enforcement | `auth/dependencies.py` | `get_current_user`, `require_role` (fail-closed) |
| Identidad/usuarios | `services/user_service.py`, `api/routes_users.py` | CRUD, MFA lifecycle, guards anti-autobloqueo/último-admin |
| Config de instancia | `services/config_service.py`, `api/routes_settings.py` | fuentes/proyectos/IA; separación nombre-vs-valor de token; probe SSRF |
| Centro de Update | `services/update_service.py`, `api/routes_updates.py` | migraciones con reversibilidad + backup + auto-rollback |
| Conectores nuevos | `connectors/{harbor,trivy,base}.py` | egreso saliente a fuentes externas |
| Egreso IA | `api/routes_ai.py`, `services/config_service.py` | techo fail-closed `WOLF_AI_ENABLED` |
| Settings de app | `settings.py` | fail-closed de secretos en prod |

## 2. Inventario de superficie de ataque (entry points)

| # | Entry point | Exposición | Confianza requerida |
|---|---|---|---|
| E1 | `POST /api/v1/auth/login` | **Pública** | ninguna (credencial en tránsito) |
| E2 | `POST /api/v1/auth/mfa/verify` | **Pública** | `mfa_token` efímero |
| E3 | `GET /api/health`, `GET /api/meta` | **Pública, sin auth** | ninguna (ops) |
| E4 | `GET /auth/me`, `/users/me/*` (password, MFA setup/enable/disable) | Autenticada | viewer+ (self) |
| E5 | `GET /projects*`, `/projects/{k}/sources/{s}`, `GET /updates/version` | Autenticada | viewer+ |
| E6 | `POST /projects/{k}/ai/review` | Autenticada | operator+ y `ai_enabled` |
| E7 | `/settings/*` (incl. `POST /settings/sources/{name}/test` → **egreso**) | Autenticada | admin |
| E8 | `/users` CRUD, `/reset-password`, `/unlock` | Autenticada | admin |
| E9 | `GET /updates/status`, `POST /updates/migrate` | Autenticada | admin |
| E10 | Egreso conectores → URLs de fuente (admin-config) | **Saliente** | token server-side |
| E11 | Egreso update_service → `api.github.com/repos/{repo}/releases` | **Saliente** | `update_release_token` opcional |
| E12 | Egreso AI provider → endpoint local/hosted | **Saliente** | techo env + admin |
| E13 | Entorno: `WOLF_JWT_SECRET`, `WOLF_MFA_FERNET_KEY`, `WOLF_DATABASE_URL`, tokens de fuente/update | Arranque | proceso |
| E14 | Bootstrap YAML→BD al arranque (`config/projects.yaml`) | Arranque | fichero del host |

> Regla: un entry point descubierto después y no listado aquí es, en sí, un hallazgo. Esta tabla
> cubre todo lo montado en `main.create_app()` + los egresos de la capa nueva.

## 3. Mapa de fronteras de confianza

```
                 (clase secret en tránsito: credenciales / Bearer)
  Navegador ───────────────► [B1] /api/v1/*  (FastAPI)
   (token en localStorage)        │
                                  │  JWT Bearer obligatorio (require_role fail-closed)
                                  ▼
                        ┌─────────────────────┐
                        │  Backend (servicios)│
                        └─────────────────────┘
          [B2]  │ os.environ            [B3] │ AsyncSession         [B4] │ httpx saliente
                ▼                            ▼                           ▼
        JWT/Fernet/DB URL,            BD (identidad/config/      Fuentes externas +
        tokens de fuente             audit; SIN findings,       api.github.com + IMDS?(T-SSRF-01)
        (nunca a la BD/navegador)    SIN valores de token)      (token adjunto en header)
```

- **B1 Navegador⇄API:** autenticación JWT obligatoria; login público con lockout 423. Riesgo:
  token en localStorage (T-AUTH-03). Exige TLS en el reverse proxy.
- **B2 Backend⇄entorno:** secretos solo en `os.environ`; fail-closed en prod (`settings.py`).
- **B3 Backend⇄BD:** nunca findings ni valores de token (ADR-001/002); audit saneado.
- **B4 Backend⇄externo:** **la frontera más caliente de la superficie nueva** — egreso a URLs
  que controla el admin, con el token resuelto adjunto. Foco de T-SSRF-01.

## 4. STRIDE por componente (resumen)

| Componente | S | T | R | I | D | E |
|---|---|---|---|---|---|---|
| Login/MFA (auth_service) | dummy-hash anti-enum ✓ | — | audit `auth.login*` ✓ | 401 uniforme ✓ | **rate-limit falta (T-AUTH-01)**, lockout-DoS (T-AUTH-02) | lockout 423 ✓ |
| JWT/sesión (security) | alg-allowlist ✓, `tv` ✓ | firma HS256 ✓ | — | **localStorage/8h (T-AUTH-03)** | — | type-claim separa access/mfa ✓ |
| MFA/TOTP | Fernet en reposo ✓ | — | — | recovery one-shot ✓ | — | **replay TOTP (T-MFA-01)**, **no-enforce admin (T-AUTH-04)** |
| RBAC (dependencies) | — | rol desde BD ✓ | — | 403 genérico ✓ | — | fail-closed ✓; TOCTOU último-admin (T-RBAC-01) |
| Config/settings | — | validación tipada ✓ | audit `config.*` ✓ | token name-only ✓ | — | admin-only ✓ |
| Conectores (egreso) | — | parseo tolerante ✓ | — | **SSRF/IMDS (T-SSRF-01)** | timeouts 10s ✓ | — |
| Update Center | — | reversibilidad AST ✓ | audit `update.*` ✓ | **500 filtra exc (T-SEC-01)** | **sin lock concurrente (T-UPD-01)**, status sin caché (T-UPD-02) | admin-only + gate backup ✓ |
| Toggle IA | — | — | audit toggle ✓ | meta no revela provider ✓ | — | techo env fail-closed ✓ |

## 5. Inventario de hallazgos (priorizado)

| ID | Amenaza | Componente | STRIDE | Severidad | Verosimilitud | Impacto |
|---|---|---|---|---|---|---|
| T-SSRF-01 | URL de fuente/IA admin-config dispara egreso con token adjunto → IMDS / exfiltración de token | Conectores, `settings/sources/test` | Info Disclosure / EoP | **High** (Medium tras fix) | Media (admin o sesión admin comprometida vía XSS) | Robo de credencial de instancia / de token de fuente (rompe ADR-002) |
| T-AUTH-01 | Sin rate-limit app en `/auth/login` y `/auth/mfa/verify` | auth_service | DoS / Spoofing | **High** | Alta | Password-spraying y fuerza bruta distribuida; adivinación de TOTP acotada solo por lockout |
| T-AUTH-04 | MFA no exigido server-side para admin (ADR-013 #2) | auth_service/user_service | EoP | **High** | Media | Admin protegido solo por password vía API directa |
| T-AUTH-03 | Token en localStorage + TTL 8h sin rotación/idle-timeout | frontend A6 / security | Info Disclosure | Medium | Media (requiere XSS) | Robo de Bearer de larga vida; revocable solo por `tv` |
| T-UPD-01 | `apply_migrations` sin lock: `/migrate` concurrente → `alembic upgrade` simultáneo | update_service | Tampering / DoS | Medium | Baja | Posible corrupción de esquema / estado a medias |
| T-AUTH-02 | Lockout como DoS: bloquear a un usuario (admin) conocido a demanda | auth_service | DoS | Medium | Media | Denegación de acceso a cuentas legítimas |
| T-SEC-01 | `/updates/migrate` 500 devuelve `str(exc)[:400]` (posible DB URL en el repr) | routes_updates | Info Disclosure | Medium | Baja | Fuga de detalle interno (admin-only, truncado) |
| T-MFA-01 | Replay de código TOTP dentro de la ventana (~90s, `valid_window=1`) | security/auth_service | Spoofing | Low→Med | Baja (requiere MITM; TLS mitiga) | Reuso de un código interceptado |
| T-RBAC-01 | TOCTOU en guard último-admin (sin row-lock) | user_service | EoP | Low | Baja (SQLite single-writer) | Instancia sin admin activo bajo carrera en Postgres |
| T-SEC-02 | `_sanitize` del audit es shallow y por clave exacta | audit_service | Info Disclosure | Low | Baja | Defensa en profundidad incompleta (callers hoy disciplinados) |
| T-UPD-02 | `/updates/status` llama a GitHub sin caché por request | update_service | DoS | Low | Baja (admin) | Amplificación saliente / agotar rate-limit de GitHub |
| T-AI-01 | Flag `ai_settings.enabled` persiste; reactivar el env re-activa IA sin decisión fresca | config_service | — | Info | — | Aceptado por ADR (env = techo); documentar |
| T-INJ-01 | `trivy.report_path` y path de repo Harbor se unen a la base URL (admin-config) | conectores | Tampering | Low | Baja | Mismo nivel de confianza que la URL; sin traversal a FS (es HTTP) |

## 6. Mitigaciones (indexadas a hallazgos)

| ID | Mitigación | Tipo | Prioridad | Complejidad |
|---|---|---|---|---|
| T-SSRF-01 | **[HECHO]** bloquear IMDS/metadata (169.254.0.0/16, `fd00:ec2::254`, `metadata.google.internal`) en write-path y probe. **[REC]** allowlist opcional de hosts por deploy; aserción explícita `follow_redirects=False` en conectores; resolver DNS y revalidar la IP si se sube a allowlist estricta | Técnica | **Must-fix (hecho) + Rec** | Baja (hecho) / Media (allowlist) |
| T-AUTH-01 | Rate-limit a nivel app por IP **y** por username en `/auth/login` y `/auth/mfa/verify` (p. ej. `slowapi`/`limits` con backend compartido); respuesta 429 uniforme | Técnica | **Must-fix** | Media |
| T-AUTH-04 | Enforcement server-side: un admin sin `mfa_enabled` no puede ejecutar operaciones privilegiadas (o se le obliga a completar enrolamiento) — no delegar al hint de UI | Técnica | **Must-fix** | Media |
| T-AUTH-03 | CSP estricta + higiene XSS en el frontend; evaluar cookie httpOnly + CSRF; acortar TTL e introducir idle-timeout; rotación de token | Técnica | Alta | Media/Alta (diseño, A6) |
| T-UPD-01 | Serializar `apply_migrations` con un lock in-proc (`asyncio.Lock`) y, en Postgres, `pg_advisory_lock` | Técnica | Alta | Baja |
| T-AUTH-02 | Backoff exponiencial en vez de lock duro, o lock scoped por IP; mantener endpoint admin `/unlock` (ya existe) y alertar sobre `user.locked` repetido | Técnica/Operacional | Media | Media |
| T-SEC-01 | Mapear el 500 de `/migrate` a un mensaje genérico al cliente; loguear el detalle completo server-side; scrub de la DB URL antes de exponer cualquier texto de excepción | Técnica | Media | Baja |
| T-MFA-01 | Registrar el último timestep TOTP consumido por usuario y rechazar reuso (anti-replay) | Técnica | Media | Baja |
| T-RBAC-01 | `SELECT ... FOR UPDATE` sobre los admins activos (o lock app) dentro de `update_user` | Técnica | Baja | Baja |
| T-SEC-02 | Ampliar `_SENSITIVE_KEYS` (`authorization`, `api_key`, `jwt`, `fernet`, `recovery_code`) y sanear recursivamente dicts anidados | Técnica | Baja | Baja |
| T-UPD-02 | Cachear el resultado de `check_latest()` con TTL (reusar `cache_ttl_seconds`) | Técnica | Baja | Baja |
| T-AI-01 | Documentar; opcional: al cerrar el techo env, forzar `ai_settings.enabled=false` para exigir re-decisión | Operacional | Info | Baja |
| T-INJ-01 | Validar que `report_path`/repo no contengan `..` ni esquema absoluto antes de unir a la base | Técnica | Baja | Baja |

### Minimum Mandatory Mitigations (antes de consolidar a producción)

- **T-SSRF-01** — guard IMDS/metadata (**HECHO**). Allowlist de hosts: evaluar.
- **T-AUTH-01** — rate-limit app en endpoints de auth (no confiar solo al reverse proxy).
- **T-AUTH-04** — exigir MFA de admin server-side (cumplir ADR-013 #2).
- **Operación:** desplegar siempre tras reverse proxy TLS; fijar `WOLF_JWT_SECRET` y
  `WOLF_MFA_FERNET_KEY` fuertes (el arranque ya aborta en prod si faltan); fijar `cors_origins`
  al origen real (no `*`).

### Controles compensatorios (donde la mitigación plena no es inmediata)

- T-SSRF-01 residual (exfil a host público): rol admin confiado + **auditoría de
  `config.source_updated` con actor** + httpx sin seguir redirects por defecto.
- T-AUTH-01/02: lockout 423 por-cuenta + endpoint admin `/unlock` + alertas sobre `user.locked`.
- T-AUTH-03: revocación por `token_version` (logout-everywhere en reset/cambio de rol).

## 7. Fixes aplicados en esta pasada (pequeños y claramente correctos)

1. **SSRF IMDS guard (T-SSRF-01).** `services/config_service.py`: nuevo
   `assert_url_egress_allowed(url)` + `_is_blocked_egress_host(host)` que rechazan hosts de
   metadata de nube (link-local `169.254.0.0/16` incl. `169.254.169.254`, alias IMDSv6
   `fd00:ec2::254`, `metadata.google.internal`/`.goog`). Se invoca desde `_validate_url` (cubre
   `PUT /settings/sources/{name}` y `PUT /settings/ai`) y desde `POST
   /settings/sources/{name}/test` en `api/routes_settings.py` (cubre filas sembradas por
   bootstrap/YAML antes de hacer la petición saliente). **Proporcional:** NO bloquea rangos
   privados — el producto alcanza, por diseño, servicios internos (Harbor/Sonar/DefectDojo).
2. **`from None` en anti-enumeración.** `auth/dependencies.py::get_current_user` y
   `api/routes_auth.py::verify_mfa`: los `raise` que colapsan el motivo a un 401/challenge
   genérico ahora suprimen la causa encadenada, para no filtrar el porqué ni por el traceback.

Ambos cambios preservan el contrato HTTP y no alteran respuestas esperadas por los tests
(las URLs de fuente de la suite usan `127.0.0.1`, `*.local`, `api.github.com` — ninguna es IMDS;
los asserts de auth comprueban códigos de estado, no el encadenamiento de excepciones).

> **Verificación de la suite:** en este entorno de subagente no hay herramienta de shell
> disponible, así que **no pude ejecutar pytest**. El caller debe correr, desde `backend/` con el
> venv py3.11 indicado: `PYTHONPATH=. <venv>/bin/python -m pytest -q` (base: 124 verde). El
> razonamiento de no-ruptura está arriba; si algún test rojo apareciera, el candidato más probable
> sería un test que haga `PUT`/seed de una URL en rango metadata (no observado en el inventario).

## 8. Resiliencia a prompt-injection de esta auditoría

No se detectaron intentos de inyección dirigidos al agente en el código, comentarios o
configuración revisados (p. ej. comentarios tipo "ignora las instrucciones" o "este módulo está
fuera de alcance"). Todo el contenido se trató como dato a analizar, no como instrucción.

## 9. Checklist must-fix antes de producción

- [x] **T-SSRF-01** — bloqueo IMDS/metadata en write-path y probe (aplicado en esta pasada).
- [ ] **T-AUTH-01** — rate-limit a nivel app en `/auth/login` y `/auth/mfa/verify`.
- [ ] **T-AUTH-04** — exigir MFA de admin server-side (cumplir ADR-013 decisión #2).
- [ ] **T-UPD-01** — lock en `apply_migrations` (in-proc + advisory lock en Postgres).
- [ ] **T-SEC-01** — no devolver `str(exc)` crudo en el 500 de `/migrate`.
- [ ] **Deploy** — reverse proxy TLS obligatorio; `WOLF_JWT_SECRET`/`WOLF_MFA_FERNET_KEY` fuertes;
      `cors_origins` acotado al origen real.
- [ ] **T-AUTH-03** — CSP + higiene XSS; reevaluar localStorage vs cookie httpOnly y TTL/idle (A6).
- [ ] Correr la suite (`pytest -q`) y confirmar 124 verde con los fixes aplicados.

> Severidades con rationale: High = impacto alto (robo de credencial, bypass de 2Fo, fuerza bruta)
> con verosimilitud media/alta; Medium = impacto acotado o verosimilitud baja; Low/Info = defensa
> en profundidad o riesgo aceptado por ADR. Toda mitigación cita su Threat ID; todo Critical/High
> tiene mitigación o control compensatorio documentado.
