#!/usr/bin/env bash
# =============================================================================
# scan-common.sh — constantes y helpers compartidos por connect-projects.sh,
# analyze-sonar.sh y scan-target.sh. SE HACE `source`, no se ejecuta.
# Shared constants/helpers for the scan scripts. Sourced, not executed.
# Requiere que el script que lo carga defina HERE (dir de deploy/).
# =============================================================================

TRIVY_IMG="${TRIVY_IMG:-aquasec/trivy:0.56.2}"
RVOL="${RVOL:-wolf-devsecops_trivy_reports}"   # volumen que sirve el nginx `trivy`
CVOL="${CVOL:-wolf-devsecops_trivy_cache}"
SCANNER_IMG="${SCANNER_IMG:-sonarsource/sonar-scanner-cli:11}"
GITLEAKS_IMG="${GITLEAKS_IMG:-ghcr.io/gitleaks/gitleaks:v8.21.2}"
SCAN_NET="${SCAN_NET:-wolf-devsecops_default}"

# Trivy fs NO respeta .gitignore: sin esto escanea caches/artefactos/volúmenes de
# datos (p. ej. .pytest_cache con fixtures dummy de tests de secretos, o la llave
# que Harbor se autogenera en su volumen) y los reporta como "secretos" críticos.
TRIVY_SKIP='**/.pytest_cache,**/node_modules,**/.venv,**/venv,**/__pycache__,**/dist,**/build,**/.git,**/coverage,**/harbor/harbor,**/defectdojo/media,**/.mypy_cache,**/.ruff_cache'

# Exclusiones del sonar-scanner (dependencias/artefactos).
SONAR_EXCLUSIONS='**/node_modules/**,**/dist/**,**/build/**,**/.venv/**,**/venv/**,**/__pycache__/**,**/*.min.js,**/migrations/**,**/vendor/**,**/.git/**,**/coverage/**,**/target/**'

# slug <texto> → minúsculas [a-z0-9-] (basename, sin .git).
scan_slug() {
  echo "$1" | sed 's#.*/##; s/\.git$//' | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]\{1,\}/-/g; s/^-//; s/-$//'
}

# scan_valid_key <key> → 0 si es seguro como nombre de archivo y project_key.
scan_valid_key() {
  printf '%s' "$1" | grep -Eq '^[a-z0-9]([a-z0-9._-]{0,98}[a-z0-9])?$'
}

# scan_helper <cmd> <yaml> [opts…] → ejecuta sonar_projects.py. Usa python3 del
# host si tiene PyYAML; si no (macOS de fábrica), lo corre dentro de la imagen
# del backend (que ya trae PyYAML) con el directorio del YAML montado.
# Fallback vars: SCAN_BACKEND_IMAGE (opcional, si no se deduce del compose).
scan_helper() {
  local cmd="$1" yaml="$2"; shift 2
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    python3 "$HERE/sonar_projects.py" "$cmd" "$yaml" "$@"
    return $?
  fi
  local img="${SCAN_BACKEND_IMAGE:-}" cid
  if [ -z "$img" ] && [ -n "${WOLF_COMPOSE_FILE:-}" ]; then
    cid="$(docker compose -f "$WOLF_COMPOSE_FILE" ps -q backend 2>/dev/null | head -1)"
    [ -n "$cid" ] && img="$(docker inspect --format '{{.Config.Image}}' "$cid" 2>/dev/null)"
  fi
  [ -n "$img" ] || { echo "no hay python3+PyYAML en el host ni imagen de backend para el fallback" >&2; return 1; }
  docker run --rm --entrypoint python \
    -v "$HERE/sonar_projects.py:/h/sonar_projects.py:ro" \
    -v "$(dirname "$yaml"):/cfg" \
    "$img" /h/sonar_projects.py "$cmd" "/cfg/$(basename "$yaml")" "$@"
}
