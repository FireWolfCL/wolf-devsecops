# Rúbrica de postura ("% de cumplimiento") — Wolf DevSecOps

> Rúbrica v1.0 · 2026-11-01 · Especificación para implementar.
> Respeta ADR-001 (solo lectura de fuentes), ADR-002 (sin secretos) y ADR-012 (el score **no se persiste**: se calcula al vuelo).
> Entrada única: `ProjectSummary` (`backend/wolf_devsecops/models.py`). Sin llamadas extra a las fuentes.

## 0. Qué es y qué NO es

Es un indicador **relativo, de la evidencia que agregamos** (5 señales). No es un nivel de madurez DSOMM, ni una atestación SSDF, ni una certificación. Los mapeos indican a qué práctica **aporta evidencia** cada check; ninguna práctica queda "cumplida" solo por pasar su check.

## 1. Convenciones de evaluación

Por cada check, `S` = la fuente (`SourceResult`). Regla común de **elegibilidad**, aplicada antes de la lógica propia:

| `S.status` | Efecto |
|---|---|
| `ok` | Se evalúa. |
| `stale` | Se evalúa con el dato en caché; el proyecto se marca `stale=true` (el UI lo muestra, el puntaje no cambia). |
| `not_configured` | **NA** — la fuente no aplica. |
| `unavailable` | **NA** (sin evidencia), marcado `degraded=true`. No castiga al proyecto por una caída de la fuente, pero **sí reduce la cobertura** (§3) y por tanto el techo de banda. |
| `ok`/`stale` pero `S.data is None` | Tratar como `unavailable`. |

Resultado de un check: `PASS` (valor 1), `PARTIAL` (0.5, solo `quality-gate`), `FAIL` (0), `NA` (excluido).

## 2. Checks (peso total = 100)

| id | Título | Señal exacta | PASS / FAIL / NA | Peso | OWASP DSOMM (dimensión → actividad) | NIST SSDF |
|---|---|---|---|---|---|---|
| `ci-green` | Último CI en verde | `ci.data.status` | PASS si `success`; FAIL si `failure`; NA si `running` o `unknown` (sin veredicto) o fuente no elegible | 10 | Build and Deployment → Build (proceso de build definido/automatizado) | PO.3.2 (toolchain automatizada), PW.8.2 (pruebas ejecutadas) |
| `findings-no-critical` | Sin hallazgos críticos activos | `findings.data.counts.critical` | PASS si `==0`; FAIL si `>=1`; NA si no elegible | 20 | Test and Verification → Consolidation (tratamiento de defectos por severidad) | RV.2.1 (priorizar), RV.2.2 (remediar) |
| `findings-no-high` | Sin hallazgos altos activos | `findings.data.counts.high` | PASS si `==0`; FAIL si `>=1`; NA si no elegible | 10 | Test and Verification → Consolidation (tratamiento de defectos por severidad) | RV.2.1, RV.2.2 |
| `containers-no-critical` | Imágenes sin CVE críticos | `containers.data.counts_net.critical` (neto gobernado, ADR-023) | PASS si `==0`; FAIL si `>=1`; NA si no elegible. Si pasa solo porque había críticos pero con riesgo aceptado aprobado, `reason_code="accepted_risk"` | 20 | Build and Deployment → Patch Management; Test and Verification → Static depth (análisis de dependencias/imagen) | PW.4.4 (verificar componentes de terceros), RV.1.1 (identificar vulnerabilidades) |
| `containers-no-high` | Imágenes sin CVE altos | `containers.data.counts_net.high` (neto gobernado, ADR-023) | PASS si `==0`; FAIL si `>=1`; NA si no elegible | 10 | Ídem `containers-no-critical` | PW.4.4, RV.1.1 |
| `quality-gate` | Quality gate de código | `quality.data.quality_gate` | PASS si `OK`; PARTIAL (0.5) si `WARN`; FAIL si `ERROR`; NA si `NONE` o no elegible | 10 | Test and Verification → Static depth (análisis estático) | PW.7.2 (analizar código), PW.8.2 |
| `quality-no-vulns` | Sin vulnerabilidades en análisis estático | `quality.data.vulnerabilities` | PASS si `==0`; FAIL si `>=1`; NA si no elegible | 5 | Test and Verification → Static depth (SAST) | PW.7.2, PW.5.1 (prácticas de código seguro, evidencia indirecta) |
| `quality-coverage` | Cobertura de pruebas mínima | `quality.data.coverage` | PASS si `>= coverage_min` (def. 60.0); FAIL si `< coverage_min`; NA si `coverage is None` o no elegible | 5 | Test and Verification → Test-intensity (cobertura de pruebas) | PW.8.2 |
| `release-sbom` | Release con SBOM adjunto | `release.data.sbom_url` | PASS si `tag` no nulo y `sbom_url` no nulo; FAIL si `tag` no nulo y `sbom_url` nulo; NA si `tag` nulo (aún sin release) o no elegible | 10 | Build and Deployment → Build (inventario/trazabilidad de componentes) | PS.3.2 (recopilar y proteger datos de procedencia, SBOM), PW.4.1 |

Notas de implementación:
- **Un hallazgo `info`/`medium`/`low` no puntúa** (evita castigar ruido; ver §6). Los umbrales numéricos (`coverage_min`) viven en config YAML no secreta (`posture:` en `config/projects.yaml` o `system_config`), con default en código. Cambiar pesos/umbrales exige subir `rubric_version`.
- `findings` y `quality.vulnerabilities` pueden solaparse (mismo SAST visto por dos vías). Se asume y se acepta: peso de `quality-no-vulns` bajo (5) para limitar el doble conteo.
- **Referencias DSOMM:** dimensiones/sub-dimensiones según DSOMM v4; los nombres de actividad son indicativos y deben contrastarse con la versión de DSOMM fijada antes de publicar la matriz. **SSDF:** tareas de SP 800-218 v1.1.
- **Neto gobernado vs. crudo (ADR-023).** Los checks de contenedores y la regla de banda crítica puntúan sobre `counts_net = counts − accepted`. `accepted` = CVE con aceptación de riesgo **aprobada y vigente** (`source_exceptions.status='applied'` + `kind='risk_accepted'`, cruzados por `cve_id`). La resta ocurre en la capa de agregación de Wolf (nunca se escribe a Harbor/Trivy; ADR-001/006). Solo se resta lo que **Trivy** enumera por CVE; **Harbor** no expone CVE por hallazgo → su exposición no se resta (**sub-resta honesta, nunca sobre-resta**). El cruce se hace **después** de `wolf-ignore.yaml` (6.3.c). **Fail-closed:** una excepción `proposed`/`write_failed`/`expired`/`expire_failed` NO entra ⇒ su CVE sigue contando y sigue capando. En **Hallazgos (DefectDojo)** `accepted` es siempre 0 (el write-back de ADR-021 ya los sacó de `counts`): el neto solo difiere del crudo en Contenedores. `counts` crudo **nunca se muta** (wolf-genjutsu).

## 3. Fórmula del % por proyecto

```
valor(c)   = 1 | 0.5 | 0            para PASS | PARTIAL | FAIL
E          = checks con resultado != NA
peso_E     = Σ peso(c)   c ∈ E
ganado     = Σ peso(c) * valor(c)   c ∈ E
score      = round_half_up(100 * ganado / peso_E)        si peso_E > 0
cobertura  = round_half_up(100 * peso_E / 100)           (100 = suma total de pesos)
```

- **NA fuera del denominador.** No suma ni resta.
- **Todo NA** (`peso_E == 0`): `score = null`, `band = "sin_datos"`, `cobertura = 0`. Nunca 0 % ni 100 %.
- **Banda** = banda por `score` (entero ya redondeado) con **tres límites**, aplicados en este orden:
  1. `cobertura < 40` → banda `Evidencia insuficiente` (el `score` se informa como indicativo, sin color de riesgo).
  2. `cobertura < 70` → banda máxima `Aceptable` (no puede ser `Sólido`).
  3. Cualquier `findings.counts_net.critical >= 1` o `containers.counts_net.critical >= 1` (elegibles) → banda máxima `En riesgo`. Un crítico abierto no se "compensa" con otros PASS. **Neto gobernado (ADR-023):** un crítico con **riesgo aceptado aprobado y vigente** (excepción `applied`+`risk_accepted`, cruzada por `cve_id`) no cuenta como abierto y no capa la banda; uno no aceptado (o con aceptación vencida/fallida) sí. La exposición **cruda** (`counts`) nunca se altera — sigue en el panel/PDF con el badge "✓ aceptado".

## 4. Bandas y etiquetas

| Rango (`score`) | Etiqueta | Color sugerido | Lectura |
|---|---|---|---|
| 90–100 | **Sólido** | verde | Sin brechas relevantes en lo medido. |
| 70–89 | **Aceptable** | amarillo-verde | Brechas menores; plan de mejora. |
| 50–69 | **En riesgo** | naranja | Brechas relevantes; priorizar. |
| 0–49 | **Crítico** | rojo | Exposición alta; acción inmediata. |
| `null` o cobertura < 40 | **Evidencia insuficiente** / **Sin datos** | gris | No hay base para juzgar; configurar o restaurar fuentes. |

## 5. % global del dashboard

**Recomendación: promedio ponderado por evidencia evaluada**, no promedio simple.

```
global_score = round_half_up(100 * Σ_p ganado_p / Σ_p peso_E_p)      p ∈ proyectos con peso_E_p > 0
global_cobertura = round_half_up(100 * Σ_p peso_E_p / (100 * N))     N = todos los proyectos
```

Justificación:
- El promedio simple trata igual a un proyecto con 1 de 9 checks medidos que a uno con 9 de 9: un proyecto casi sin evidencia movería el global tanto como uno completo.
- Ponderar por `peso_E` hace que cada punto del global se base en evidencia efectivamente medida, y es determinista y sin parámetros nuevos.
- No ponderamos por tamaño del proyecto: no medimos LOC ni criticidad de negocio, y no inventamos señales.
- Proyectos con `score = null` se excluyen del numerador y el denominador, pero sí cuentan en `N` (bajan la cobertura global).
- Las mismas reglas de banda de §3 aplican al global (`global_cobertura` < 40 / < 70). Regla de crítico global: si algún proyecto tiene crítico abierto, el global muestra banda máxima `Aceptable`.
- **Un promedio oculta al peor proyecto**: la API debe devolver además la distribución por banda (n por etiqueta) y el `key` de los proyectos en `Crítico`/`En riesgo`. El % global nunca se muestra sin esa distribución.

## 6. Nota de honestidad

1. **Solo lo medido.** El % refleja únicamente CI, DefectDojo, SonarQube, Harbor+Trivy y metadata de release. No mide: tiempos de remediación (SLA/MTTR), secretos en repositorio, firma de artefactos, revisión por pares, DAST, cultura ni procesos. Un 100 % no significa "seguro".
2. **NA no infla ni hunde.** Fuente `not_configured` queda fuera del denominador (no se castiga a quien no usa una herramienta ni se premia su ausencia con un 0 o un 100). Para que excluir no infle un puntaje con poca base, el sistema **siempre** informa `cobertura` junto al `score` y la limita por banda (§3.1–3.2): un proyecto con solo CI en verde muestra "100 % · cobertura 10 % · Evidencia insuficiente", no "Sólido".
3. **Caída ≠ falla.** `unavailable` es NA con marca `degraded`: no hunde el puntaje por un problema de infraestructura, pero tampoco lo deja pasar como completo (cobertura baja). `stale` se evalúa y se rotula como dato en caché.
4. **Sin veredicto, sin puntaje.** CI `running`/`unknown` y Sonar `NONE` son NA, no PASS ni FAIL.
5. **Críticos no se compensan** (§3.3): el promedio no puede esconder una vulnerabilidad crítica abierta.
6. **Sin fuga.** Cada resultado expone solo `{id, result, weight, earned, reason_code}` con códigos fijos (`ok`, `source_not_configured`, `source_unavailable`, `no_verdict`, `threshold_not_met`, `missing_data`). Nunca rutas, líneas, código, mensajes de error de fuente ni URLs internas. El YAML de config contiene solo umbrales; nunca secretos.

## 7. Contrato de implementación

```python
def score_project(p: ProjectSummary, cfg: PostureConfig) -> PostureScore: ...
def score_global(scores: list[PostureScore]) -> GlobalPosture: ...
# PostureScore: rubric_version, score: int|None, coverage: int, band: str,
#   stale: bool, degraded: bool, capped_by: Literal["coverage","critical"] | None,
#   checks: list[CheckResult{id, result, weight, earned, reason_code}]
```

- Funciones **puras y deterministas**: mismo `ProjectSummary` + misma `cfg` ⇒ mismo resultado; sin reloj, sin I/O, sin aleatoriedad (`generated_at` no interviene).
- Redondeo half-up explícito (no `round()` de Python, que usa banker's rounding).
- Tests mínimos: todo NA; solo CI; crítico con otros PASS (tope `En riesgo`); `WARN` = 0.5; `unavailable` vs `not_configured` (mismo score, distinta marca); `coverage=None`; límites 90/70/50 y cobertura 40/70; global con un proyecto sin datos.
- Endpoint sugerido: añadir `posture: PostureScore` a la respuesta de resumen y `posture` global al listado, bajo `/api` (ADR-006).
