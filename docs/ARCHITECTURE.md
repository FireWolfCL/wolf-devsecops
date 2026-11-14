# Arquitectura — Wolf DevSecOps (MVP)

> Documento de arquitectura. Autoridad: wolf-chief-architect. Fecha: 2026-10-31.
> Alineado con `project-brief.md` y ADR-001..005 (no se re-litigan).
> Idioma: español. Código e identificadores en inglés.

## Resumen ejecutivo

Wolf DevSecOps es una **consola agregadora read-only**: un backend FastAPI lee las
APIs de GitHub Actions, DefectDojo y SonarQube, traduce cada fuente a un **contrato
común normalizado** (Pydantic), cachea el resultado y lo expone a un frontend
React/Vite por `/api/*`. Los tokens viven **solo en el servidor** (ADR-002) y se
cargan por variable de entorno; `config/projects.yaml` nunca contiene secretos.
La pieza central del diseño es la interfaz `Connector`: añadir una fuente nueva es
escribir una clase que implementa `fetch_summary()`/`fetch_detail()`, sin tocar ni el
agregador ni la API ni el frontend. La disponibilidad **degrada por fuente**: si
DefectDojo cae, su panel se marca `unavailable` y los demás siguen mostrándose.

El mismo seam cubre un subsistema de IA (§7), un conector opcional a Wolf Security Suite
(§12) y, a futuro, canales de notificación. El producto se distribuye en dos modos —
*connect* y *all-in-one* (§10)— y se instala con un wizard CLI (§11); cada conector es
resiliente a cambios de su fuente por versión soportada, parseo tolerante y tests de
contrato (§9).

**Lo que NO hace** (ADR-001): el panel/backend no escanea, no mantiene cola de trabajo,
no es fuente de verdad. Solo lee y unifica. (El instalador todo-en-uno sí puede correr
Trivy como conveniencia de arranque; esa frontera se detalla en §10.)

---

## 1. Contrato de datos normalizado (`backend/wolf_devsecops/models.py`)

Todo conector traduce su fuente a estos modelos. Son el único lenguaje que cruza la
frontera conector→agregador→API→frontend. Pydantic v2.

### Enums

```python
class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"

class CIStatus(str, Enum):
    success = "success"
    failure = "failure"
    running = "running"
    unknown = "unknown"          # fuente respondió pero sin dato interpretable

class SourceStatus(str, Enum):
    ok = "ok"                    # datos frescos
    stale = "stale"             # datos de caché servidos tras fallo de refresco
    unavailable = "unavailable"  # fuente configurada pero caída / error
    not_configured = "not_configured"  # fuente no definida para este proyecto
```

### Bloques por fuente

```python
class SeverityCount(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0
    @property
    def total(self) -> int: ...

class CISummary(BaseModel):            # desde GitHub Actions
    status: CIStatus
    workflow: str                       # p.ej. "ci.yml"
    branch: str | None = None
    run_number: int | None = None
    html_url: HttpUrl | None = None
    finished_at: datetime | None = None

class FindingsSummary(BaseModel):      # desde DefectDojo
    counts: SeverityCount
    total_active: int
    url: HttpUrl | None = None          # link al producto en DefectDojo

class QualitySummary(BaseModel):       # desde SonarQube
    quality_gate: Literal["OK", "WARN", "ERROR", "NONE"] = "NONE"
    bugs: int = 0
    vulnerabilities: int = 0
    code_smells: int = 0
    coverage: float | None = None       # porcentaje 0..100
    url: HttpUrl | None = None

class ReleaseSummary(BaseModel):       # derivado de GitHub
    tag: str | None = None
    name: str | None = None
    published_at: datetime | None = None
    html_url: HttpUrl | None = None
    sbom_url: HttpUrl | None = None     # asset del release si existe
```

### Envoltura de resultado por fuente (clave para la degradación)

Cada bloque viaja envuelto para que un fallo de una fuente no rompa el proyecto:

```python
T = TypeVar("T")

class SourceResult(BaseModel, Generic[T]):
    status: SourceStatus
    data: T | None = None
    error: str | None = None            # mensaje saneado, nunca stack ni token
    fetched_at: datetime | None = None  # cuándo se obtuvo el dato (o la caché)
```

### Agregados que consume el frontend

```python
class ProjectSummary(BaseModel):
    key: str                            # projects[].key de projects.yaml
    name: str
    ci: SourceResult[CISummary]
    findings: SourceResult[FindingsSummary]
    quality: SourceResult[QualitySummary]
    release: SourceResult[ReleaseSummary]
    generated_at: datetime

class ProjectListItem(BaseModel):       # versión liviana para el listado
    key: str
    name: str
    ci_status: CIStatus | None
    findings: SeverityCount | None
    quality_gate: str | None
    worst_source_status: SourceStatus   # peor estado entre sus fuentes (semáforo)

class SourceDetail(BaseModel):          # detalle crudo-normalizado por fuente
    project_key: str
    source: Literal["github", "defectdojo", "sonarqube", "wss"]
    status: SourceStatus
    fetched_at: datetime | None = None
    payload: dict                       # lista de findings, runs, issues, etc.
    error: str | None = None

class ProbeResult(BaseModel):           # salida de Connector.probe() — la usa el wizard (§12)
    source: str
    reachable: bool                     # salud: la fuente responde
    authenticated: bool                 # el token es válido
    version: str | None = None          # versión detectada de la fuente
    compatible: bool                    # version ∈ supported_versions (ADR-008)
    detail: str | None = None           # mensaje humano saneado (nunca token ni stacktrace)
```

**Regla de contrato:** ningún modelo de salida contiene jamás tokens, cabeceras de
autenticación ni trazas internas. `error` es siempre un texto corto saneado.

---

## 2. Módulos backend

Layout canónico (confirma el del brief: `backend/wolf_devsecops/{connectors,api,core}`):

```
backend/
  pyproject.toml
  wolf_devsecops/
    __init__.py
    main.py              # app factory FastAPI: CORS, arranque, montaje de routers
    settings.py          # pydantic-settings: CACHE_TTL_SECONDS, CONFIG_PATH, HTTP_TIMEOUT
    models.py            # el contrato normalizado de la sección 1 (compartido)
    connectors/
      __init__.py
      base.py            # ABC Connector (interfaz común) + versiones soportadas + excepciones
      registry.py        # nombre de fuente -> clase de conector
      github.py          # GitHubConnector
      defectdojo.py      # DefectDojoConnector
      sonarqube.py       # SonarQubeConnector
      wss.py             # WSSConnector — OPCIONAL (ADR-011), vulns de infra/activos desde Wolf Security Suite
    core/
      __init__.py
      config.py          # carga y valida projects.yaml -> AppConfig
      credentials.py     # resuelve token_env -> token desde el entorno
      cache.py           # caché TTL en memoria, async-safe
      aggregator.py      # orquesta conectores -> ProjectSummary (concurrente + degradación)
    ai/                  # subsistema de revisión con IA (ADR-007) — seam paralelo a connectors/
      __init__.py
      base.py            # ABC AIProvider + excepciones
      registry.py        # tipo de proveedor -> clase ("local" | "hosted")
      local.py           # LocalAIProvider: endpoint compatible OpenAI (Ollama/LM Studio), sin egreso
      hosted.py          # HostedAIProvider: API token (Anthropic Claude / OpenAI)
    api/
      __init__.py
      deps.py            # DI: get_config, get_aggregator, get_ai_provider
      routes_projects.py # /api/v1/projects, /api/v1/projects/{key}
      routes_sources.py  # /api/v1/projects/{key}/sources/{source}
      routes_ai.py       # /api/v1/projects/{key}/ai/review
      routes_health.py   # /api/health, /api/meta (ops, sin versionar)
    cli/                 # wizard de instalación asistida (ADR-010)
      __init__.py
      main.py            # entrypoint `wolf-devsecops` (Typer/argparse)
      init.py            # orquesta el flujo `init`: modo -> probe fuentes -> proyectos -> escribir
      probe.py           # prueba de fuente: salud + token + versión soportada (reusa Connector, ADR-008)
      writer.py          # escribe config/projects.yaml y deploy/.env (sin secretos en el YAML)
  tests/
    conftest.py
    contract/            # tests de contrato por conector (ADR-008): CI falla si cambia el shape externo
      test_github.py
      test_defectdojo.py
      test_sonarqube.py
      test_wss.py
    fixtures/            # respuestas-ejemplo grabadas de cada API externa
      github/*.json
      defectdojo/*.json
      sonarqube/*.json
      wss/*.json
```

Árbol de despliegue (raíz del repo, ADR-009):

```
deploy/
  docker-compose.yml       # modo CONNECT: solo Wolf DevSecOps (backend + frontend)
  docker-compose.full.yml  # modo ALL-IN-ONE: + DefectDojo + SonarQube (versiones fijadas) + sus BD
  .env.example             # variables de entorno (tokens) — nunca el .env real
```

### Responsabilidad y contrato de cada módulo

| Módulo | Responsabilidad única | Depende de |
|---|---|---|
| `main.py` | Construir la app, montar routers, configurar CORS y eventos de arranque. **No lógica de negocio.** | `api/*`, `settings`, `core.config` |
| `settings.py` | Leer configuración de entorno de la app (no de las fuentes). | — |
| `models.py` | Definir el contrato normalizado. Sin I/O. | — |
| `connectors/base.py` | Definir la interfaz `Connector` y las excepciones de conector. | `models` |
| `connectors/github.py` etc. | Hablar con **una** API externa y traducirla al contrato. **No cachea, no agrega.** | `models`, `base`, `httpx` |
| `connectors/registry.py` | Mapear `"github" -> GitHubConnector`. Punto único de alta de fuentes. | conectores |
| `connectors/trivy.py` | Leer reportes Trivy JSON (CVEs/secretos/misconfig de filesystem o imagen) servidos por el bundle; traducir a `FindingsSummary`/contenedores. **Solo lee JSON ya generado; no ejecuta Trivy.** | `models`, `base`, `httpx` |
| `connectors/harbor.py` | Hablar con la API de Harbor (registry): CVEs de las imágenes que realmente se despliegan (ADR-006). Despliegue de Harbor es aparte del bundle. | `models`, `base`, `httpx` |
| `connectors/wss.py` | Conector **opcional** a la API REST de Wolf Security Suite (vulns de infra/activos). Mismo seam y contrato; sin dependencia dura. | `models`, `base`, `httpx` |
| `core/config.py` | Cargar/validar `projects.yaml` a modelos tipados. Rechazar secretos literales. | `pydantic`, `pyyaml` |
| `core/credentials.py` | Resolver `token_env` → valor desde `os.environ`. Nunca imprime el valor. | `config` |
| `core/cache.py` | Caché clave→(valor, expiración). TTL configurable. | — |
| `core/aggregator.py` | Para un proyecto: invocar sus conectores en paralelo, aplicar caché y degradación, ensamblar `ProjectSummary`. Hornea el **neto gobernado** del pilar Contenedores (resta los CVE con riesgo aceptado vía `GovernanceProvider`; ADR-023) antes de puntuar. Compone también la **procedencia** read-only (último commit de `main` + autor, vía `fetch_provenance` del forge; independiente de CI) y el **sello de frescura** (`_freshness`: el `fetched_at` más viejo entre fuentes con dato, nunca `generated_at`). ADR-001. | conectores, cache, config, credentials, models, `services.governance` |
| `services/governance.py` | `GovernanceProvider`: conjunto de CVE con aceptación de riesgo aprobada/vigente por proyecto (lee `source_exceptions`, SOLO LECTURA). Overlay cacheado e invalidable que alimenta el neto (ADR-023). No escribe a ninguna fuente; Harbor/Trivy siguen read-only (ADR-001/006). | `models_db.exceptions`, `cache`, `db` |
| `ai/base.py` | Definir la interfaz `AIProvider` y sus excepciones. | `models` |
| `ai/local.py` / `ai/hosted.py` | Hablar con **un** proveedor de IA y devolver `AIReviewResult`. `local` no egresa fuera de la red; `hosted` usa token por env. **No agrega, no decide alcance.** | `models`, `base` |
| `ai/registry.py` | Mapear `"local" -> LocalAIProvider`, `"hosted" -> HostedAIProvider`. Alta única de proveedores. | providers |
| `api/routes_*.py` | Traducir HTTP ⇄ agregador/provider. **Delegan; no implementan lógica.** | `aggregator`, `ai` vía `deps` |
| `cli/*` | Wizard `wolf-devsecops init`: detectar modo, probar fuentes, dar de alta proyectos, escribir config. **Reusa `Connector`, no duplica I/O.** | `connectors`, `core.config`, `core.credentials` |

### Interfaz común `Connector` (`connectors/base.py`)

```python
class Connector(ABC):
    source_name: ClassVar[str]          # "github" | "defectdojo" | "sonarqube" | "wss"
    supported_versions: ClassVar[str]   # rango semver soportado de la fuente, p.ej. ">=2.0,<3"

    def __init__(self, source_config: SourceConfig, token: str | None): ...

    @abstractmethod
    async def probe(self) -> ProbeResult:
        """Salud + autenticación + versión de la fuente. Base del wizard (§12) y del
        check de compatibilidad (ADR-008). No lanza: devuelve un ProbeResult tipado."""

    @abstractmethod
    async def fetch_summary(self, project: ProjectConfig) -> BaseModel:
        """Devuelve CISummary | FindingsSummary | QualitySummary | ReleaseSummary.
        Lanza ConnectorError en fallo; NUNCA devuelve un SourceResult (eso lo
        construye el agregador). NUNCA filtra el token en la excepción."""

    @abstractmethod
    async def fetch_detail(self, project: ProjectConfig) -> dict:
        """Payload normalizado para la vista de detalle (lista de runs/findings/issues)."""
```

`ConnectorError` / `ConnectorAuthError` / `ConnectorTimeout` / `ConnectorIncompatible`
(versión fuera de `supported_versions`) son las únicas excepciones que el agregador
captura para construir un `SourceResult` degradado con mensaje claro, nunca stacktrace.
**Parseo tolerante (ADR-008):** cada conector lee solo los campos que necesita; los
modelos externos usan `model_config = ConfigDict(extra="ignore")`, de modo que un campo
nuevo en la API externa no rompe el parseo.

**Nota de diseño:** GitHub produce *dos* bloques (`CISummary` y `ReleaseSummary`). Se
resuelve con dos métodos finos en `GitHubConnector` (`fetch_ci`, `fetch_release`) que
`fetch_summary` compone, o registrando el conector una vez y dejando que el agregador
pida ambos bloques. Para el MVP: `GitHubConnector` expone `fetch_ci()` y
`fetch_release()`; el agregador sabe que la fuente `github` alimenta los campos `ci` y
`release`. DefectDojo→`findings`, SonarQube→`quality`. Este mapeo vive en el agregador,
no disperso.

---

## 3. Credenciales, caché y degradación

### Credenciales (frontera de secretos)

- `projects.yaml` declara `sources.<x>.token_env: "GITHUB_TOKEN"` — **el nombre de la
  variable, nunca el valor** (ya es así en `projects.yaml.example`).
- Al arrancar, `core/credentials.py` resuelve cada `token_env` contra `os.environ` y
  construye un mapa `fuente → token` en memoria.
- Si falta la variable de una fuente, esa fuente queda **`not_configured`**: no se
  intenta llamar, no se rompe el arranque. Se registra un warning (sin el nombre del
  secreto, solo "fuente github sin credencial").
- **Clasificación `secret`:** el token jamás entra en logs, respuestas JSON, caché
  serializada ni mensajes de `error`. `core/config.py` **rechaza el arranque** si
  detecta en el YAML un valor con pinta de token (p.ej. clave `token:` en vez de
  `token_env:`), para que nadie lo comitee por error.

### Caché (`core/cache.py`)

- Caché **en memoria**, TTL configurable `CACHE_TTL_SECONDS` (defecto **300 s**).
- Clave: `(project_key, source)` para summaries y `(project_key, source, "detail")`
  para detalles. Async-safe (lock por clave o `asyncio.Lock`).
- Suficiente para el MVP mono-instancia. Migrar a Redis es un cambio aislado tras la
  interfaz de caché, no un rediseño (crecimiento previsto, sin reescritura — ADR-005).

### Degradación por fuente (fail-open en disponibilidad, read-only)

El agregador llama a los conectores de un proyecto **en paralelo** con
`asyncio.gather(..., return_exceptions=True)` y un **timeout por llamada**
(`HTTP_TIMEOUT`, defecto 10 s). Para cada fuente:

| Situación | `SourceResult.status` | `data` |
|---|---|---|
| Éxito | `ok` | datos frescos, se cachea |
| Fuente no configurada para el proyecto / sin token | `not_configured` | `null` |
| Error o timeout, hay caché previa | `stale` | último valor cacheado + `error` saneado |
| Error o timeout, sin caché | `unavailable` | `null` + `error` saneado |

Así una fuente caída **nunca** tumba el proyecto ni el dashboard: los demás paneles se
renderizan y el afectado muestra su estado. Esto es correcto porque el producto es
read-only (ADR-001); no hay acción destructiva que un dato parcial pueda disparar.
La degradación de *seguridad* (secretos, config) sí es fail-closed: sin token no se
llama; config inválida aborta el arranque.

---

## 4. Frontend (`frontend/`)

Solo estructura y flujo de datos. El diseño visual es de wolf-web-designer-agent /
wolf-frontend-agent.

```
frontend/
  index.html
  vite.config.ts
  src/
    main.tsx
    App.tsx               # router: "/" lista, "/projects/:key" detalle
    api/
      client.ts           # wrapper fetch sobre /api (base configurable)
      types.ts            # espejo TS del contrato (sección 1)
    hooks/
      useProjects.ts      # GET /api/projects
      useProjectSummary.ts# GET /api/projects/:key
      useSourceDetail.ts  # GET /api/projects/:key/sources/:source (lazy)
    views/
      ProjectListView.tsx # grilla de tarjetas (dashboard)
      ProjectDetailView.tsx # cabecera + pestañas por fuente
    components/
      ProjectCard.tsx     # resumen de un proyecto (semáforo + contadores)
      SourceStatusPill.tsx# ok/stale/unavailable/not_configured
      CiStatusBadge.tsx
      SeverityBars.tsx    # barras por severidad
      QualityTile.tsx
```

### Data-flow

1. **ProjectListView** monta → `useProjects()` → `GET /api/projects` → renderiza una
   `ProjectCard` por `ProjectListItem`. El `worst_source_status` da el color de
   semáforo de la tarjeta.
2. Click en tarjeta → navega a `/projects/:key` → **ProjectDetailView** →
   `useProjectSummary(key)` → `GET /api/projects/:key` → pinta los cuatro bloques
   (`ci`, `findings`, `quality`, `release`) leyendo `SourceResult.status` para decidir
   si muestra datos, "sin configurar" o "fuente caída".
3. Pestaña de detalle (p.ej. "Findings") → `useSourceDetail(key, "defectdojo")` →
   `GET /api/projects/:key/sources/defectdojo` → lista cruda-normalizada. **Lazy**: no
   se pide hasta abrir la pestaña.

El frontend **nunca** ve tokens ni conoce las URLs de las fuentes: solo habla con
`/api/*`. Toda la forma de los datos está fijada por `api/types.ts`, que es traducción
1:1 de `models.py`.

---

## 5. Contratos de los endpoints HTTP

**Versionado (ADR-006):** los endpoints de recursos viven bajo el namespace versionado
`/api/v1`. Los endpoints de operación (`/api/health`, `/api/meta`) quedan sin versionar
porque describen al despliegue, no al contrato de datos. Regla: cambios **aditivos**
(campo nuevo, fuente nueva) se quedan en `v1`; un cambio **incompatible** estrena
`/api/v2` sin romper a los clientes de `v1`. Todas las respuestas son JSON de los
modelos de la sección 1.

| Método | Ruta | Respuesta | Códigos |
|---|---|---|---|
| GET | `/api/health` | `{ "status": "ok", "version": "x.y.z" }` | 200 |
| GET | `/api/meta` | `{ "project_count": int, "sources_configured": [...], "ai_provider": "local"\|"hosted"\|null }` | 200 |
| GET | `/api/v1/projects` | `ProjectListItem[]` | 200 |
| GET | `/api/v1/projects/{key}` | `ProjectSummary` | 200, 404 (key desconocida) |
| GET | `/api/v1/projects/{key}/sources/{source}` | `SourceDetail` | 200, 404 (key o source desconocida) |
| POST | `/api/v1/projects/{key}/ai/review` | `AIReviewResult` (ver §7) | 200, 404, 409 (IA no configurada), 502 (proveedor caído) |

- `source` ∈ `{github, defectdojo, sonarqube}`.
- **Degradación nunca es error HTTP:** una fuente caída devuelve 200 con
  `SourceResult.status = unavailable`. 4xx/5xx se reservan para errores del propio
  backend (key inexistente = 404; fallo interno = 500 con cuerpo saneado).
- Caché: el backend puede enviar `Cache-Control` coherente con `CACHE_TTL_SECONDS`;
  el valor de verdad es la caché server-side.

### Trust boundaries y clasificación de datos

| Frontera | Qué cruza | Autenticación |
|---|---|---|
| Navegador ⇄ Backend `/api/*` | Datos agregados (CI, findings, calidad) — clase **internal** | **MVP sin auth de usuario** (ver decisión abierta) |
| Backend ⇄ GitHub/DefectDojo/Sonar | Peticiones autenticadas con token — token clase **secret** | Token por fuente, solo server-side (ADR-002) |
| Backend ⇄ `projects.yaml` | Repos, URLs, product keys — clase **internal** | Fichero local; sin secretos |
| Backend ⇄ entorno | Tokens — clase **secret** | `os.environ`, nunca en disco de la app |
| Backend ⇄ Proveedor IA **local** | Texto de findings (clase **internal**) | Red del equipo; **sin egreso** fuera de la red. Default seguro |
| Backend ⇄ Proveedor IA **hosted** | Texto de findings (clase **internal**) sale a un tercero | **Decisión de privacidad EXPLÍCITA del operador** (ADR-007), nunca default silencioso |

Clasificación: **no hay PII**. Los contadores de findings y el estado de CI son
**internal** — no deben exponerse públicamente. Los tokens son **secret** y quedan
confinados al proceso backend. `projects.yaml` es **internal** y, por contrato del
loader, libre de secretos.

---

## 6. Orden de implementación del MVP

Principio: tener el **circuito completo end-to-end con una sola fuente (GitHub)** antes
de multiplicar fuentes. Cada fase es verificable por sí sola.

| Fase | Entregable | Archivos | Criterio de "hecho" (verificable) |
|---|---|---|---|
| **F0 — Esqueleto** | App arranca y responde | `settings.py`, `models.py`, `core/config.py`, `main.py`, `api/routes_health.py`, `deploy/docker-compose.yml` | `docker compose up` y `GET /api/health` → 200; `GET /api/meta` lista proyectos de `projects.yaml` |
| **F1 — Circuito GitHub end-to-end** | Un proyecto real muestra estado de CI vía API | `connectors/base.py`, `connectors/registry.py`, `connectors/github.py` (`fetch_ci`), `core/credentials.py`, `core/cache.py`, `core/aggregator.py` (solo github), `api/routes_projects.py` | `GET /api/projects` y `GET /api/projects/{key}` devuelven `CISummary` real de un repo; segunda llamada servida de caché; sin token → `not_configured` |
| **F2 — Frontend mínimo** | Dashboard funcional sobre GitHub | `frontend/` (client, types, `useProjects`, `ProjectListView`, `ProjectCard`, `ProjectDetailView`) | El dashboard lista el proyecto y su semáforo de CI; detalle abre sin error |
| **F3 — DefectDojo** | Vulnerabilidades agregadas | `connectors/defectdojo.py`, `api/routes_sources.py`, `SeverityBars.tsx`, `useSourceDetail` | Panel de findings con contadores por severidad; detalle lista findings; DefectDojo caído → `unavailable`, el resto sigue |
| **F4 — SonarQube** | Calidad y cobertura | `connectors/sonarqube.py`, `QualityTile.tsx` | Quality gate, bugs y cobertura reales en el panel |
| **F5 — Releases/SBOM + endurecimiento** | Release derivado de GitHub + degradación pulida | `github.py` (`fetch_release`), `ReleaseSummary` en UI, caché `stale`-on-error | Última versión + link a SBOM; prueba de caos: matar una fuente y confirmar `stale`/`unavailable` sin romper el dashboard |
| **F6 — Reviewer IA (explicar/priorizar findings)** | Panel que explica y prioriza los findings YA agregados vía `AIProvider` (ver §7) | `ai/{base,registry,local,hosted}.py`, `api/routes_ai.py`, panel en `ProjectDetailView` | `POST /api/v1/projects/{key}/ai/review` devuelve `AIReviewResult` con `local` (default, sin egreso); sin IA configurada → 409; **bloqueada hasta visto bueno de wolf-ai-safety-agent** |
| **F7 — Distribución all-in-one (ADR-009)** | `deploy/docker-compose.full.yml` que levanta Wolf + DefectDojo + SonarQube fijados, con URLs internas preconfiguradas | `deploy/docker-compose.full.yml`, `deploy/.env.example`, `config/projects.yaml` de ejemplo | `docker compose -f docker-compose.full.yml up` deja el dashboard leyendo DefectDojo/Sonar internos sin config manual; **no corre escáneres** (ADR-001) |
| **F8 — Wizard CLI `wolf-devsecops init` (ADR-010)** | Instalación asistida headless que detecta modo, prueba fuentes y escribe config | `cli/{main,init,probe,writer}.py` | `wolf-devsecops init` detecta modo, usa `Connector.probe()` por fuente (salud+token+versión, ADR-008), valida proyectos y escribe `projects.yaml`/`.env` válidos |
| **F9 — Conector WSS opcional (ADR-011)** | Vista de vulns de infra/activos desde Wolf Security Suite, como upsell | `connectors/wss.py`, fixtures + `tests/contract/test_wss.py` | Con WSS configurada aparece su panel; sin ella → `not_configured`, el Community corre igual |

**F0–F6 no cambian de contrato.** Se añaden **F7, F8 y F9**. Dos matices de secuenciación
(los decide wolf-product-orchestrator, §Decisiones abiertas):
- **Resiliencia (ADR-008) es transversal, no una fase:** `supported_versions`, parseo
  tolerante y el test de contrato con su fixture se construyen **junto con cada conector**
  (desde F1). El arnés `tests/contract/` + `tests/fixtures/` se monta en F0.
- **F7/F8 son independientes de F6** (que está gated por ai-safety). Para el lanzamiento
  Community, la distribución y el wizard (F7, F8) pesan más que la IA: pueden ir antes de
  F6 si el orchestrator así lo prioriza. F9 (WSS) es opcional y puede ir en cualquier
  momento tras F3, por ser otro `Connector`.

Dos seams hacen que el crecimiento sea **mecánico** (ADR-006): la interfaz `Connector`
(fijada en F1) y la interfaz `AIProvider` (fijada en F6). Añadir una fuente o un
proveedor = una clase nueva + una línea en su `registry.py`; no se toca el core ni la
API ni —salvo un componente de presentación— el frontend. Ver §8.

---

## 7. Subsistema de revisión de código con IA (ADR-007)

Subsistema **opcional** que explica y prioriza los findings ya agregados usando un
modelo de lenguaje, con un proveedor **seleccionable por el operador**. Sigue el mismo
patrón de seam que los conectores: interfaz común + registro + implementaciones.

### Alcance por madurez (no negociable, ADR-007)

- **MVP (F6):** el backend envía al modelo el **texto de los findings ya normalizados**
  (severidad, título, descripción, componente) y pide una explicación priorizada y
  accionable. **NO se envía código fuente ni diffs.** Salida = ayuda para leer la cola
  de findings, nunca una acción automática.
- **Futuro (fuera de MVP):** revisión de diffs/PR completos enviando código. Es un salto
  de la frontera de egreso y de consecuencia; va al ROADMAP y re-pasa por
  wolf-ai-safety-agent.

### Interfaz común `AIProvider` (`ai/base.py`)

```python
class AIProvider(ABC):
    provider_type: ClassVar[str]        # "local" | "hosted"

    def __init__(self, ai_config: AIConfig, token: str | None): ...

    @abstractmethod
    async def review(self, context: AIReviewContext) -> AIReviewResult:
        """Recibe el contexto de findings normalizados y devuelve una revisión
        priorizada. Lanza AIProviderError en fallo. NUNCA filtra el token.
        La implementación `local` no egresa datos fuera de la red del equipo."""
```

`AIProviderError` / `AIProviderAuthError` / `AIProviderTimeout`: únicas excepciones que
`routes_ai` traduce a 502/409.

### Modelos Pydantic (en `models.py`)

```python
class AIReviewContext(BaseModel):       # ENTRADA al modelo — SOLO findings, nunca código
    project_key: str
    findings: list[FindingBrief]        # {severity, title, description, component}
    quality: QualitySummary | None = None
    # Invariante de diseño: este modelo no tiene campo para código fuente ni diffs.

class PrioritizedFinding(BaseModel):
    title: str
    severity: Severity
    rationale: str                      # por qué importa / por qué priorizar
    suggested_action: str

class AIReviewResult(BaseModel):        # SALIDA — se valida antes de renderizar (ai-safety)
    project_key: str
    summary: str
    prioritized: list[PrioritizedFinding]
    provider_type: Literal["local", "hosted"]
    model: str
    egress: bool                        # True si los datos salieron a un tercero (hosted)
    generated_at: datetime
```

### Configuración (`config/projects.yaml`)

Un bloque `ai:` de nivel superior (análogo a `sources:`). `local` es el default seguro;
`hosted` exige que el operador lo elija y provea el token por env:

```yaml
ai:
  provider: "local"                     # local | hosted  (default: local, sin egreso)
  model: "qwen2.5-coder"
  base_url: "http://localhost:11434/v1" # endpoint compatible OpenAI
  # Para hosted, además:
  # provider: "hosted"
  # token_env: "AI_TOKEN"               # nombre de var, nunca el valor
```

Si no hay bloque `ai:`, el subsistema queda **`not_configured`** y
`POST /ai/review` responde 409. El token `hosted` es clase **secret** (misma frontera
que los tokens de fuentes): solo `os.environ`, jamás en logs/JSON/`error`.

### Encaje en la API

Endpoint nuevo `POST /api/v1/projects/{key}/ai/review` en `api/routes_ai.py`. Es POST
porque invoca un modelo (coste/efecto en `hosted`), no una lectura cacheada. El router
delega en el `AIProvider` resuelto por `deps.get_ai_provider()`; no implementa lógica.

### Frontera de egreso de datos (resumen)

| Proveedor | Qué sale | A dónde | Gobierno |
|---|---|---|---|
| `local` (default) | Texto de findings | Red del equipo | Sin egreso externo. Default seguro |
| `hosted` | Texto de findings | API de un tercero (Anthropic/OpenAI) | Elección **explícita** del operador; `AIReviewResult.egress = true` lo deja trazado |

### Gate de seguridad

**Este subsistema no se implementa sin pasar por wolf-ai-safety-agent**, que debe:
clasificar la feature por consecuencia (es *assistive*: explica, no decide ni actúa),
definir la validación de la salida del modelo (schema `AIReviewResult`, saneo, límites)
antes de renderizarla, y confirmar los requisitos de transparencia (marcar que el texto
es generado por IA y qué proveedor/modelo lo produjo). La salida del modelo es **input
no confiable**: se valida contra el esquema y se sanea antes de llegar al frontend.

---

## 8. Extensibilidad de primer orden (ADR-006)

La integración de cualquier capacidad nueva es un **seam formal con registro**: una
clase que implementa una interfaz + una línea en su `registry.py`. El core, la API y el
frontend **no se tocan**. Puntos de extensión documentados:

| Seam | Interfaz | Registro | Implementaciones MVP | Crecimiento |
|---|---|---|---|---|
| **Fuentes de datos** | `Connector` (`connectors/base.py`) | `connectors/registry.py` | github, sonarqube, defectdojo, trivy, harbor, **wss** (opcional, ADR-011) | GitLab CI, Jenkins, Bitbucket, Azure DevOps, Gitea, Snyk… |
| **Proveedores de IA** | `AIProvider` (`ai/base.py`) | `ai/registry.py` | local, hosted | nuevos proveedores/modelos |
| **Canales de notificación** (futuro) | `NotificationChannel` (previsto, no MVP) | `notifications/registry.py` | — | email, Slack, Teams, webhook |

Reglas del patrón:
1. La interfaz vive en `base.py`; las implementaciones son intercambiables y no se
   conocen entre sí.
2. El alta ocurre **solo** en el `registry.py` del seam; el core descubre por nombre
   desde `config/projects.yaml`.
3. El contrato de datos (sección 1) es el único lenguaje que cruza el seam; una
   implementación nueva traduce a los modelos existentes, no inventa los suyos.
4. **Versionado de API confirmado:** los recursos viven bajo `/api/v1`; cambios
   aditivos se quedan en `v1`, incompatibles estrenan `/api/v2` (ver §5). El seam deja
   crecer sin romper clientes.

El canal de notificaciones se lista como seam **previsto** para fijar su forma desde
ahora; no se implementa en el MVP.

---

## 9. Resiliencia frente a cambios de las fuentes externas (ADR-008)

Las APIs externas cambian; el objetivo es que un cambio se arregle **en una clase**,
nunca en cascada, y que CI lo detecte antes del deploy.

1. **Versiones soportadas + check de compatibilidad.** Cada `Connector` declara
   `supported_versions` (rango semver). En `probe()` (y, si la fuente expone versión en
   runtime, al primer `fetch`) compara la versión real contra el rango. Si no coincide,
   degrada a `unavailable` con un mensaje claro (`ConnectorIncompatible`: "DefectDojo
   3.x no soportado; soportado >=2.0,<3") — **nunca un stacktrace**.
2. **Parseo tolerante a campos desconocidos.** Los modelos de parseo de respuestas
   externas usan `ConfigDict(extra="ignore")` y cada conector lee solo los campos que
   necesita. Un campo nuevo en la API externa no rompe nada.
3. **Tests de contrato con fixtures grabados.** Por conector, respuestas-ejemplo reales
   guardadas en `backend/tests/fixtures/<source>/*.json` y un test en
   `backend/tests/contract/test_<source>.py` que alimenta el conector con el fixture y
   verifica que produce el modelo normalizado esperado. Si la API externa cambia su
   shape (se re-graba el fixture o se actualiza la real), **CI falla** y obliga a tocar
   solo esa clase. Estos tests no hacen red: corren contra los fixtures.

Este arnés se monta en F0 y cada conector nace con su fixture + test de contrato (F1
GitHub, F3 DefectDojo, F4 Sonar, F9 WSS).

---

## 10. Distribución en dos modos (ADR-009)

Un mismo conector, dos formas de desplegar. El bundle **preconfigura URLs internas** y no
cambia la arquitectura. El **panel/backend nunca escanea** (ADR-001): esa frontera no se
mueve. Lo que sí hace el bundle todo-en-uno, como conveniencia de arranque, es ejecutar
**Trivy** sobre el código/imagen que le indiques y dejar el JSON para que el conector
`trivy.py` lo lea — así el operador ve un primer puntaje el día uno sin montar un CI aún.

| Modo | Fichero | Qué levanta | Para quién |
|---|---|---|---|
| **connect** (default) | `deploy/docker-compose.yml` | Solo Wolf DevSecOps (backend + frontend), apuntando a DefectDojo/Sonar/GitHub **existentes** vía `projects.yaml` | Equipos que ya tienen sus herramientas |
| **all-in-one** | `deploy/docker-compose.full.yml` | Wolf DevSecOps **+** DefectDojo **+** SonarQube (versiones fijadas y probadas) **+** sus bases de datos | Evaluar / arrancar de cero sin montar herramientas aparte |

**Cómo conviven:** es la **misma imagen y el mismo backend** en ambos modos. La única
diferencia es qué servicios externos están en la red de Compose y a qué URLs apunta el
`projects.yaml`:
- En **all-in-one**, el `projects.yaml` de ejemplo ya trae `defectdojo.base_url:
  http://defectdojo:8080` y `sonarqube.base_url: http://sonarqube:9000` (nombres de
  servicio internos de Compose). El operador solo pega sus tokens en `.env`.
- En **connect**, el operador pone las URLs reales de sus instancias.

**El matiz sobre escáneres:** el bundle todo-en-uno **sí** trae y ejecuta **Trivy**
(servicio `trivy-scan` en `docker-compose.install.yml`) como conveniencia de arranque, y
sirve su JSON para que el conector lo lea. Lo que **no** empaqueta ni orquesta son los
demás escáneres (semgrep, gitleaks, SBOM…): esos viven en el CI del proyecto auditado y
alimentan a DefectDojo; el backend solo **lee** (ADR-001). Harbor, cuando se usa, se
despliega con su propio instalador, fuera del bundle. Las versiones de DefectDojo/Sonar/Trivy
del bundle se fijan por tag y deben caer dentro del `supported_versions` de sus conectores
(ADR-008): el bundle es, de hecho, la combinación de versiones garantizada.

---

## 11. Wizard de instalación asistida (ADR-010)

Instalación **CLI primero** (`cli/`), porque debe funcionar en servidores headless y
**antes** de que exista `projects.yaml`. Onboarding **web = fase posterior**
(wolf-ux-kit), solo se menciona aquí. Especialista de implementación:
wolf-installer-packaging-agent.

### Flujo de `wolf-devsecops init`

1. **Detección de modo** (`cli/init.py`): pregunta/detecta all-in-one vs. connect
   (ADR-009). En all-in-one propone las URLs internas de Compose como defaults.
2. **Prueba de cada fuente** (`cli/probe.py`): por cada fuente que el operador quiere
   habilitar, invoca `Connector.probe()` → `ProbeResult`: salud (¿responde?),
   autenticación (¿token válido?) y **versión soportada** (ADR-008). Reusa el conector
   real, no reimplementa el I/O. Reporta cada check con ✓/✗ y mensaje saneado.
3. **Alta de proyectos**: por cada proyecto, valida que exista en las fuentes elegidas
   (p.ej. el repo en GitHub, el `product` en DefectDojo, el `project_key` en Sonar)
   usando el mismo conector; no escribe un proyecto que no se pudo verificar.
4. **Escritura de config** (`cli/writer.py`): genera `config/projects.yaml` (estructura
   validada, **referenciando `token_env`, nunca el valor**) y `deploy/.env` con los
   nombres de variable. El wizard **nunca** escribe el token dentro del YAML.

El wizard es un cliente más del seam `Connector`: añadir una fuente nueva al producto la
hace aparecer en el wizard sin tocar `cli/`, salvo el prompt de alta. Depende de que los
conectores (F1–F4) y el config loader existan; por eso es F8.

---

## 12. Integración opcional con Wolf Security Suite (ADR-011)

Wolf DevSecOps es **Community open-source autónomo** (seguridad de *código*). WSS es el
**upsell comercial** que añade la vista de vulnerabilidades de **infraestructura/activos**.
Modelo **open core**: el Community vale por sí solo; WSS enriquece, no condiciona.

- `connectors/wss.py` implementa **exactamente el mismo seam `Connector`** y traduce la
  API REST de WSS al contrato normalizado (sección 1) — sus vulns de infra encajan como
  un bloque de findings (`FindingsSummary`/`SourceDetail`), igual que DefectDojo.
- **Sin dependencia dura:** si no hay bloque `wss:` en `sources` o falta su token, la
  fuente queda `not_configured` y simplemente no se muestra — idéntico comportamiento al
  de cualquier otra fuente. El Community **corre sin WSS**.
- Se da de alta en `connectors/registry.py` como una fuente más; no hay rama especial en
  el core, el agregador ni el frontend. Trae su fixture + test de contrato (ADR-008).

Así el open-source es la puerta de entrada (lead-gen / adopción de marca) y WSS el camino
comercial, sin fragmentar la arquitectura ni la licencia (Apache-2.0, ADR-004/011).

---

## 13. Insumos para el ROADMAP (crecimiento fuera del MVP)

Lista de lo que queda **deliberadamente fuera del MVP** y es crecimiento futuro. No es
el ROADMAP (lo compone wolf-product-orchestrator); es el insumo:

- **Más fuentes de datos:** GitLab CI, Jenkins, Bitbucket Pipelines, Azure DevOps,
  Gitea/Forgejo, Snyk… (cada una = un `Connector` nuevo, §8).
- **Autenticación y multi-tenant:** login de usuario en `/api/v1`, RBAC, aislamiento por
  equipo/organización (hoy decisión abierta #1).
- **Histórico y tendencias:** persistencia de snapshots para ver evolución de findings,
  cobertura y CI en el tiempo (el MVP es solo estado actual, en memoria).
- **Notificaciones:** alertas por email/Slack/Teams/webhook ante regresiones (nuevo
  finding crítico, quality gate roto, CI en rojo) — seam `NotificationChannel`, §8.
- **Reviewer de IA avanzado:** revisión de diffs/PR completos enviando código (salto de
  frontera de egreso y de consecuencia; re-pasa por wolf-ai-safety-agent), sugerencias
  de remediación y PRs automáticos de fix.
- **Caché compartida (Redis)** y despliegue multi-instancia (hoy decisión abierta #2).
- **Onboarding web** (wizard desde la UI, wolf-ux-kit) — el MVP trae solo el wizard CLI
  (ADR-010, §11).
- **Histórico de versiones de fuentes / tendencias de compatibilidad** sobre el arnés de
  resiliencia (ADR-008, §9).

---

## Decisiones abiertas (requieren a wolf-product-orchestrator / seguridad)

1. **Autenticación de usuario en `/api/*`.** El MVP se diseña sin login. Como los datos
   son clase *internal*, esto obliga a desplegar el backend en red interna o tras un
   reverse proxy con auth. **Decisión pendiente:** ¿basta el supuesto de red interna
   para el MVP, o se añade auth mínima (basic/API key) ya? Dueño sugerido:
   wolf-product-orchestrator + wolf-security-crypto-agent. Hasta resolver, el deploy
   por defecto **no** debe exponer el puerto del backend a Internet.
2. **Caché en memoria vs. Redis.** En memoria para MVP mono-instancia; Redis si se
   quiere multi-instancia o caché compartida. Aislado tras `core/cache.py`.
3. **Mapeo fuente→bloques en el agregador.** GitHub alimenta `ci` y `release`. Confirmar
   si `release`/SBOM entra en MVP (F5) o se difiere. Dueño: wolf-product-orchestrator.
4. **`not_configured` vs. ocultar.** Hoy una fuente no configurada se muestra como
   `not_configured`. ¿Se oculta del todo (brief: "si no defines una fuente, no se
   muestra") o se muestra atenuada? Afecta a `ProjectSummary` y a las tarjetas.
5. **Reviewer IA (F6) en el corte del MVP.** El diseño está (§7) pero F6 está
   **bloqueada hasta el visto bueno de wolf-ai-safety-agent** (clasificación por
   consecuencia + validación de salida). ¿Entra F6 en el primer corte o se difiere a
   post-MVP? Dueños: wolf-product-orchestrator + wolf-ai-safety-agent. El default de
   proveedor es `local` (sin egreso); `hosted` queda como opt-in explícito del operador.
6. **Secuenciación F6 vs. F7/F8.** Para el lanzamiento Community, distribución (F7) y
   wizard (F8) pueden pesar más que la IA (F6), que además está gated. Orden de ejecución
   a decidir por wolf-product-orchestrator (§6).
7. **Contrato real de la API de WSS.** El conector `wss.py` (§12) asume que WSS expone
   sus vulns de infra/activos por REST con versión consultable. Falta confirmar el shape
   real y el `supported_versions` con el equipo de Wolf Security Suite antes de F9.
