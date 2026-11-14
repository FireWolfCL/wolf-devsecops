# Cola de features — Panel Wolf DevSecOps

Rama: `feature/panel-devsecops-hardening` (local, sin remoto). Último commit entregable: `167ed2f`.
Estado al 2026-11-02. ✅ hecho y vivo · 🔄 en curso · ⬜ pendiente · 🛑 bloqueado.

## ✅ Entregado y vivo en el panel
- **Dogfooding/hardening CICD** — Trivy acotado, DefectDojo reset limpio, frontend no-root+HEALTHCHECK, k8s securityContext + `automountServiceAccountToken:false` (J), backend runAsNonRoot. CICD: 38% → **0 crit / 0 high / 0 med / 0 low** (Trivy + DefectDojo). Cerrado: react-router **6.30.4 → 7.18.5** (CVE-2026-53666/53668/53669; build verde sin cambios de código, bundle desplegado y verificado), Dockerfile frontend a `npm ci` (build reproducible pineado al lock), y waiver scoped KSV020/KSV021 de postgres vía `.trivyignore` del repo (uid 70 lo exige la imagen; `connect-projects.sh` lo pasa solo a este proyecto).
- **i18n 100%** — toda la UI en 5 idiomas (es/en/ru/zh/ja), sin texto fijo.
- **Suite de configuración de IA** (`/admin/ai`) — presets Local/Claude/OpenAI, modelo, endpoint, API key por env (ADR-002).
- **A — Inventario de dependencias** (`/projects/:key/deps`) — tabla filtrable.
- **C — Mapa de código** (`/projects/:key/codemap`) — árbol colapsable (requiere GitHub).
- **F — Tendencia histórica** (`/projects/:key/history`) — gráfica SVG, acumula un punto por corrida de escaneo.
- **B — Revisión de código con IA + diff** (`/projects/:key/code-review`) — consola filtrable + diff side-by-side, selector proveedor/modelo, badge de egreso. (Requiere operator/admin + IA activa + proyecto con GitHub.)
- **D — Reparar → branch + PR draft** (botón en el detalle de findings) — modal de confirmación; crea branch `wolf-sec/fix-…` + PR draft, **nunca mergea**. (Requiere operator/admin + PAT de escritura + `WOLF_REMEDIATION_WRITE_ENABLED=true`.)
- **E — Harbor** — imágenes del panel pusheadas a Harbor (`wolf-devsecops/{backend,frontend}`) + escaneo Trivy real visible en `:8085`.
- **G — Coverage en Sonar** — CICD 0% → 43.1% (coverage de los 149 tests subido).

## ⬜ Cola restante (quick-wins / mejoras)
| # | Feature | Nota |
|---|---------|------|
| K | Audit trail del panel (review IA / cambio config / remediación) | ya existe tabla `audit_log`; falta cablear eventos + vista |
| L | Gate de release (bloquea/avisa si supera umbrales, p. ej. cualquier crítico) | convierte el panel en gate real |
| M | Escaneos programados (cron) + aviso Telegram ante crítico nuevo | usa `telegram-notify.sh` |
| N | Mapa de la rama/cambios git (dónde se intervino por seguridad) | se apoya en git log |
| O | Watcher de código nuevo por revisar (badge + Telegram) | vía connector GitHub; solo metadata (sha/fecha) |
| I | Reporte consolidado de la flota (export único "estado Wolf") | para gerencia |
| P | Actualizar SonarQube a una versión activa (hoy avisa "no longer active, upgrade") | infra: bump de imagen en docker-compose + revalidar quality gate |
| 🛑 H | Conectar track GitHub (CI/Release) de CICD | **bloqueado:** CICD no tiene remoto GitHub |

## Decisiones ya tomadas (ADRs)
- ADR-017 (D: branch/PR, nunca merge), ADR-018 (B: proveedor/modelo por petición + egreso), ADR-019 (F: solo métricas agregadas). Registradas en `_shared/decisions.md`.

## Pendiente de activación manual (fail-closed por diseño)
- **D (escritura a repos):** poner un PAT fine-grained en `GITHUB_WRITE_TOKEN` (scope contents:write + pull_requests:write) en `deploy/.env`, `WOLF_REMEDIATION_WRITE_ENABLED=true`, reiniciar backend. Por defecto apagado.
- **B hosted (Claude/OpenAI):** poner `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` en `deploy/.env` + elegir proveedor hosted en `/admin/ai`. Local no necesita nada.
