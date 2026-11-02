#!/usr/bin/env bash
# =============================================================================
# analyze-sonar.sh — corre `sonar-scanner` sobre TODA la flota Wolf y llena la
# pestaña "Código · Sonar" del panel con datos reales (gate, bugs, vulns…).
# -----------------------------------------------------------------------------
# Por cada proyecto con org=Wolf en config/projects.yaml:
#   1) resuelve el repo local como ~/<nombre> (p. ej. mi-proyecto),
#   2) lo analiza con el scanner oficial (Docker) apuntando al Sonar interno,
#      excluyendo dependencias/artefactos (node_modules, venv, dist…),
#   3) al final cablea `sonarqube: {project_key}` en los que falten y
#      re-sincroniza (sync-config) + reinicia el backend.
#
# SOLO proyectos Wolf. NUNCA toca externos (lsst-it) ni material ALMA. El código
# se monta SOLO-LECTURA; el scanner escribe su work-dir en /tmp del contenedor.
# Secuencial a propósito (RAM acotada): un scanner a la vez.
#
#   Uso:  bash deploy/analyze-sonar.sh
# =============================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENV_FILE="$HERE/.env"
YAML="$ROOT/config/projects.yaml"
SONAR_INTERNAL="http://sonarqube:9000"   # el scanner corre en la red del stack
NET="wolf-devsecops_default"
SCANNER_IMG="sonarsource/sonar-scanner-cli:11"
BACKEND_CTN="${BACKEND_CONTAINER:-wolf-devsecops-backend-1}"
EXCLUSIONS='**/node_modules/**,**/dist/**,**/build/**,**/.venv/**,**/venv/**,**/__pycache__/**,**/*.min.js,**/migrations/**,**/vendor/**,**/.git/**,**/coverage/**,**/target/**'

log()  { printf '\033[1;36m[sonar]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[sonar]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[sonar]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || die "No existe $ENV_FILE."
[ -f "$YAML" ]     || die "No existe $YAML."
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
[ -n "${SONAR_TOKEN:-}" ] || die "Falta SONAR_TOKEN en .env (corre provision-sources.sh)."

ok=0; fail=0; total=0
while IFS=$'\t' read -r key name; do
  [ -n "$key" ] || continue
  repo="$HOME/$name"
  total=$((total+1))
  if [ ! -d "$repo" ]; then
    warn "$name: no existe el repo local ($repo); se salta."; fail=$((fail+1)); continue
  fi
  log "analizando $name  ($repo)…"
  if docker run --rm --network "$NET" \
      -v "$repo:/usr/src:ro" \
      -e SONAR_TOKEN="$SONAR_TOKEN" \
      "$SCANNER_IMG" \
      -Dsonar.projectKey="$key" \
      -Dsonar.projectName="$name" \
      -Dsonar.sources=. \
      -Dsonar.host.url="$SONAR_INTERNAL" \
      -Dsonar.working.directory=/tmp/scannerwork \
      -Dsonar.scm.disabled=true \
      -Dsonar.exclusions="$EXCLUSIONS" >/dev/null 2>&1; then
    log "✔ $name analizado."
    ok=$((ok+1))
  else
    warn "✗ $name: el scanner falló (revisa a mano con -X si hace falta)."
    fail=$((fail+1))
  fi
done < <(python3 "$HERE/sonar_projects.py" list "$YAML")

log "análisis: $ok/$total  (fallos: $fail)"

# ─── Cablear config + sincronizar ─────────────────────────────────────────────
added="$(python3 "$HERE/sonar_projects.py" wire "$YAML")" || die "Falló el cableado."
log "config: $added proyecto(s) Wolf cableados a Sonar."
if docker exec "$BACKEND_CTN" wolf-devsecops sync-config >/dev/null 2>&1; then
  docker restart "$BACKEND_CTN" >/dev/null 2>&1 || true
  log "sync-config OK + backend reiniciado — la pestaña Sonar ya refleja los análisis."
else
  warn "sync-config falló; corre: docker exec $BACKEND_CTN wolf-devsecops sync-config && docker restart $BACKEND_CTN"
fi
log "LISTO."
