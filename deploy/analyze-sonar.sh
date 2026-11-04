#!/usr/bin/env bash
# =============================================================================
# analyze-sonar.sh — corre `sonar-scanner` sobre TODA la flota Wolf y llena la
# pestaña "Código · Sonar" del panel con datos reales (gate, bugs, vulns…).
# -----------------------------------------------------------------------------
# Por cada proyecto con org=Wolf en config/projects.yaml:
#   1) resuelve el repo local como ~/<nombre> (p. ej. Wolf-Contable),
#   2) lo analiza con el scanner oficial (Docker) apuntando al Sonar interno,
#      excluyendo dependencias/artefactos (node_modules, venv, dist…),
#   3) al final cablea `sonarqube: {project_key}` en los que falten y
#      re-sincroniza (sync-config) + reinicia el backend.
#
# SOLO proyectos Wolf. NUNCA toca externos (lsst-it) ni material ALMA. El código
# se monta SOLO-LECTURA; el scanner escribe su work-dir en /tmp del contenedor.
# Secuencial a propósito (RAM acotada): un scanner a la vez.
#
#   Uso (flota, legacy):  bash deploy/analyze-sonar.sh
#
# MODO SINGLE-TARGET (cualquier repo, sin asumir ~/<nombre> ni org=Wolf):
#   bash deploy/analyze-sonar.sh --repo <ruta> --key <project_key> [--name <nombre>] [--no-sync]
#   Analiza SOLO ese repo (montado :ro), crea/actualiza su entrada en
#   config/projects.yaml (sonarqube.project_key) y sincroniza + reinicia el backend
#   (salvo --no-sync, que usa scan-target.sh para sincronizar una sola vez).
# =============================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENV_FILE="$HERE/.env"
YAML="$ROOT/config/projects.yaml"
SONAR_INTERNAL="http://sonarqube:9000"   # el scanner corre en la red del stack
BACKEND_CTN="${BACKEND_CONTAINER:-wolf-devsecops-backend-1}"
# Constantes compartidas (SCANNER_IMG, SCAN_NET, SONAR_EXCLUSIONS…): scan-common.sh.
# shellcheck disable=SC1091
. "$HERE/scan-common.sh"
NET="$SCAN_NET"
EXCLUSIONS="$SONAR_EXCLUSIONS"

log()  { printf '\033[1;36m[sonar]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[sonar]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[sonar]\033[0m %s\n' "$*" >&2; exit 1; }

# ─── Argumentos (single-target) ───────────────────────────────────────────────
T_REPO=""; T_KEY=""; T_NAME=""; NO_SYNC="no"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)    [ $# -ge 2 ] || die "--repo requiere una ruta."; T_REPO="$2"; shift 2 ;;
    --key)     [ $# -ge 2 ] || die "--key requiere un valor."; T_KEY="$2"; shift 2 ;;
    --name)    [ $# -ge 2 ] || die "--name requiere un valor."; T_NAME="$2"; shift 2 ;;
    --no-sync) NO_SYNC="yes"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) die "argumento desconocido: $1 (usa --repo/--key/--name o nada para la flota)" ;;
  esac
done
if [ -n "$T_REPO$T_KEY$T_NAME" ] && { [ -z "$T_REPO" ] || [ -z "$T_KEY" ]; }; then
  die "modo single-target: --repo y --key van juntos."
fi

[ -f "$ENV_FILE" ] || die "No existe $ENV_FILE."
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
[ -n "${SONAR_TOKEN:-}" ] || die "Falta SONAR_TOKEN en .env (corre provision-sources.sh)."

# analyze_one <repo> <key> <name> → 0 ok / 1 falla. El código se monta SOLO-LECTURA.
# La salida del scanner se captura; si falla se muestran sus últimas líneas (sin
# secretos: el token viaja por -e y el scanner no lo imprime).
analyze_one() {
  local repo="$1" key="$2" name="$3" out rc
  out="$(docker run --rm --network "$NET" \
      -v "$repo:/usr/src:ro" \
      -e SONAR_TOKEN="$SONAR_TOKEN" \
      "$SCANNER_IMG" \
      -Dsonar.projectKey="$key" \
      -Dsonar.projectName="$name" \
      -Dsonar.sources=. \
      -Dsonar.host.url="$SONAR_INTERNAL" \
      -Dsonar.working.directory=/tmp/scannerwork \
      -Dsonar.scm.disabled=true \
      -Dsonar.exclusions="$EXCLUSIONS" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '%s\n' "$out" | tail -n 15 | sed 's/^/    | /' >&2
    return 1
  fi
  return 0
}

# ─── Modo single-target ──────────────────────────────────────────────────────
if [ -n "$T_REPO" ]; then
  [ -d "$T_REPO" ] || die "El repo no existe o no es un directorio: $T_REPO"
  T_REPO="$(cd "$T_REPO" && pwd -P)"
  scan_valid_key "$T_KEY" || die "--key inválido ('$T_KEY'): usa minúsculas, dígitos, '.', '_' o '-'."
  [ -n "$T_NAME" ] || T_NAME="$T_KEY"
  log "analizando $T_NAME  ($T_REPO)…"
  analyze_one "$T_REPO" "$T_KEY" "$T_NAME" || die "✗ $T_NAME: el scanner falló (¿SonarQube UP? ¿token válido?)."
  log "✔ $T_NAME analizado."
  scan_helper upsert "$YAML" --key "$T_KEY" --name "$T_NAME" --sonar >/dev/null \
    || die "No pude registrar $T_KEY en $YAML."
  log "config: proyecto '$T_KEY' cableado a Sonar."
  if [ "$NO_SYNC" = "no" ]; then
    if docker exec "$BACKEND_CTN" wolf-devsecops sync-config >/dev/null 2>&1; then
      docker restart "$BACKEND_CTN" >/dev/null 2>&1 || true
      log "sync-config OK + backend reiniciado."
    else
      warn "sync-config falló; corre: docker exec $BACKEND_CTN wolf-devsecops sync-config && docker restart $BACKEND_CTN"
    fi
  fi
  exit 0
fi

# ─── Modo flota (legacy, sin argumentos) ─────────────────────────────────────
[ -f "$YAML" ] || die "No existe $YAML."
ok=0; fail=0; total=0
while IFS=$'\t' read -r key name; do
  [ -n "$key" ] || continue
  repo="$HOME/$name"
  total=$((total+1))
  if [ ! -d "$repo" ]; then
    warn "$name: no existe el repo local ($repo); se salta."; fail=$((fail+1)); continue
  fi
  log "analizando $name  ($repo)…"
  if analyze_one "$repo" "$key" "$name"; then
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
