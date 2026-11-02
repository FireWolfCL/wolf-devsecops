<div align="center">

# 🐺 Wolf DevSecOps — Roadmap

*by Wolf Industries · Apache-2.0*

</div>

Este roadmap describe cómo crece Wolf DevSecOps. Es una **guía de dirección, no
un compromiso de fechas**: el orden puede ajustarse según prioridades. Cada
horizonte es aditivo — construido sobre los seams de extensión definidos en
el propio código, de modo que crecer **no exige rediseñar**.

> **Principio rector:** la herramienta debe poder sumar fuentes, proveedores de
> IA y canales nuevos añadiendo *una clase + su registro*, sin tocar el core.

---

## v0.1.0 — MVP · El panel funcional

**Objetivo:** ver en un dashboard el estado real de uno o más proyectos,
empezando por un circuito end-to-end y sumando fuentes de a una.

| Fase | Entrega | Verificación |
|---|---|---|
| F0 | Esqueleto: settings, models, config, `main`, `/api/health`, docker-compose | `docker compose up` → `/api/health` 200 |
| F1 | Circuito **GitHub** end-to-end (CI del último run) + caché + agregador | `/api/v1/projects` con CI real; sin token → `not_configured` |
| F2 | **Frontend mínimo**: dashboard sobre GitHub | lista + detalle de proyecto en el navegador |
| F3 | Conector **DefectDojo**: findings por severidad + degradación visible | fuente caída → el resto sigue mostrándose |
| F4 | Conector **SonarQube**: calidad y cobertura | panel de calidad por proyecto |
| F5 | **Releases + SBOM** (derivados de GitHub) + endurecimiento (caché stale-on-error, prueba de caos) | release visible; tolera fuentes intermitentes |

**Resultado v0.1.0:** single pane of glass multi-proyecto, read-only,
self-hostable con un `docker compose up` y un `config/projects.yaml`.

---

## v0.2.0 — Inteligencia asistida (revisión con IA)

**Objetivo:** que el panel no solo muestre, sino que **ayude a entender y
priorizar** — con IA local o por API, a elección del operador.

- **F6 — Reviewer IA (explicar / priorizar findings).** Proveedor configurable:
  **local** (Ollama / endpoint OpenAI-compatible, *sin egreso de código*) o
  **hosted** (API token: Claude / OpenAI). En este corte la IA recibe **solo el
  texto de los findings ya agregados — nunca código ni diffs**.
- 🔒 **Gate obligatorio:** no se implementa sin el visto bueno de
  `wolf-ai-safety-agent` (clasificación por consecuencia, validación de la salida
  del modelo como input no confiable, transparencia de que el texto es IA).
- La salida registra `egress: true/false` para que quede trazado cuándo un dato
  salió a un tercero.

---

## v0.3.0 — Más fuentes

**Objetivo:** cubrir más pipelines sin tocar el core (cada una es un `Connector`
nuevo).

- GitLab CI · Jenkins · Azure DevOps (estado de CI)
- Snyk · Trivy Operator / Grype — SCA y vulnerabilidades. Cada uno es un
  `Connector` nuevo, sin tocar el core.
- **Wolf Security Suite (WSS)** — integración opcional del ecosistema Wolf
  (ADR-011): aporta vulnerabilidades de infraestructura/activos cuando el cliente
  tiene la suite. El Community corre sin WSS; es el upsell comercial, no un
  requisito.

---

## v0.4.0 — Operación en equipo

**Objetivo:** pasar de herramienta mono-operador a uso compartido.

- **Autenticación de usuarios** y **multi-tenant** (hoy el MVP asume red interna).
- **Notificaciones** (nuevo seam `NotificationChannel`): email, Slack, Teams,
  webhook — p. ej. "nuevo finding CRITICAL en el proyecto X".

---

## v0.5.0 — Escala e histórico

**Objetivo:** memoria y operación a mayor volumen.

- **Histórico y tendencias** (¿mejora o empeora la postura en el tiempo?).
- **Caché Redis + multi-instancia** (hoy la caché es en memoria, ya aislada tras
  `core/cache.py` para que este salto sea local, no un rediseño).

---

## Gobernanza del código (dos niveles, no confundir)

- **Recomendaciones de postura** *(roadmap cercano, ligero)*: un score de madurez
  por proyecto y qué le falta — "no tiene SCA conectado", "releases sin firmar",
  "cobertura baja". Wolf no solo muestra, aconseja. Barato y diferenciador.
- **Motor de políticas / SLAs** *(futuro, capa propia)*: reglas que gobiernan —
  licencias prohibidas, SLA de remediación por severidad, cumplimiento medido.
  Valioso pero es casi un sub-producto; **fuera del MVP a propósito**.

## Futuro / investigación

- **Reviewer IA avanzado:** revisión de **diffs / Pull Requests con código**
  (requiere otra vuelta de `wolf-ai-safety-agent` por el egreso de código).
- Exportes/informes de postura para auditoría.

---

## Cómo crecer (para quien contribuya)

Añadir una integración es siempre el mismo patrón:

1. Implementa la interfaz en su `base.py` (`Connector`, `AIProvider` o
   `NotificationChannel`).
2. Regístrala en el `registry.py` correspondiente.
3. Traduce al **contrato normalizado** (`models.py`) — el único lenguaje que
   cruza entre capas.
4. Core, API y frontend quedan intactos.

---

## Decisiones abiertas (gobierna `wolf-product-orchestrator`)

1. **Auth en `/api/v1/*`:** ¿red interna basta para el MVP, o auth mínima ya?
   (define si el deploy lleva reverse proxy; bloquea F2) — + `wolf-security-crypto-agent`.
2. **Caché:** memoria vs Redis (aislada, diferible a v0.5).
3. **Releases/SBOM (F5):** ¿entra en el primer corte del MVP o se difiere?
4. **`not_configured` vs ocultar fuente:** tensión con el brief ("si no la
   defines, no se muestra").
5. **F6 (reviewer IA):** ¿entra en el primer corte o se difiere? — +
   `wolf-ai-safety-agent`.
