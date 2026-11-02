#!/usr/bin/env bash
# =============================================================================
# import-trivy-to-dojo.sh — llena la pestaña "Hallazgos · DefectDojo" del panel
# importando los reportes Trivy (los mismos que alimentan "Contenedores") a
# DefectDojo, el agregador de hallazgos.
# -----------------------------------------------------------------------------
# Qué hace, idempotente y re-ejecutable:
#   1) Por cada proyecto con bloque `trivy` en config/projects.yaml, toma su
#      reporte JSON del volumen (contenedor `trivy`) y lo REIMPORTA a DefectDojo
#      (scan_type "Trivy Scan", auto_create_context: crea producto+engagement si
#      faltan y deduplica en reejecuciones).
#   2) Cablea un bloque `defectdojo: { product: <nombre> }` en projects.yaml para
#      los proyectos que aún no lo tengan (para que el panel los incluya en la
#      pestaña). No pisa los que ya existen.  [--no-wire para omitir]
#   3) Re-sincroniza YAML→BD con `wolf-devsecops sync-config`.  [--no-sync]
#
# Nota de semántica: estos CVEs de contenedor también aparecen en el pilar
# "Contenedores" del score. DefectDojo es el sistema de registro/triage de
# hallazgos; el solape es propio del diseño (Trivy = escáner, DefectDojo =
# agregador), no un error. Sin marca de tiempo ni credenciales en la salida.
#
#   Uso:  bash deploy/import-trivy-to-dojo.sh [--no-wire] [--no-sync]
# =============================================================================
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENV_FILE="$HERE/.env"
YAML="$ROOT/config/projects.yaml"
DD="${DD_URL:-http://localhost:8086}"
TRIVY_CTN="${TRIVY_CONTAINER:-wolf-devsecops-trivy-1}"
BACKEND_CTN="${BACKEND_CONTAINER:-wolf-devsecops-backend-1}"
SCAN_TYPE="Trivy Scan"
ENGAGEMENT="Trivy"
# Tipo de producto que usa el provisioner (deploy/provision-sources.sh). Debe
# coincidir con el de los productos ya existentes o DefectDojo rechaza (400) por
# conflicto de tipo. Configurable con DD_PRODUCT_TYPE.
PRODUCT_TYPE="${DD_PRODUCT_TYPE:-Research and Development}"

WIRE=1; SYNC=1
for arg in "$@"; do
  case "$arg" in
    --no-wire) WIRE=0 ;;
    --no-sync) SYNC=0 ;;
    *) echo "arg desconocido: $arg" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m[dojo]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[dojo]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[dojo]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || die "No existe $ENV_FILE (corre primero el deploy)."
[ -f "$YAML" ]     || die "No existe $YAML (corre connect-projects.sh)."
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

# ─── Token DefectDojo ────────────────────────────────────────────────────────
TOK="${DEFECTDOJO_TOKEN:-}"
if [ -z "$TOK" ]; then
  log "sin DEFECTDOJO_TOKEN en .env; pidiendo uno por API…"
  TOK="$(curl -s -X POST "$DD/api/v2/api-token-auth/" -H 'Content-Type: application/json' \
    -d "{\"username\":\"${DD_ADMIN_USER:-admin}\",\"password\":\"${DD_ADMIN_PASSWORD:-}\"}" \
    | grep -o '"token":"[^"]*"' | cut -d'"' -f4)"
fi
[ -n "$TOK" ] || die "No se pudo obtener token de DefectDojo."
H="Authorization: Token $TOK"

# Verifica que DefectDojo responde antes de empezar.
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$DD/api/v2/user_profile/" -H "$H" 2>/dev/null || echo 000)"
[ "$code" = "200" ] || die "DefectDojo no autenticó (HTTP $code) en $DD."
log "DefectDojo OK en $DD."

# reimport-scan acumula un test NUEVO por corrida y este DefectDojo IGNORA los
# filtros de /tests (p. ej. engagement__product), así que no hay forma fiable de
# reusar el test correcto: intentarlo contamina hallazgos entre productos. Enfoque
# a prueba de balas: antes de importar se BORRA el engagement Trivy del producto
# (el filtro por `product` SÍ se respeta) y se reimporta fresco → siempre 1
# engagement + 1 test con hallazgos ACTIVOS, sin duplicados ni contaminación.
purge_trivy_engagement() {
  local product="$1" pid eid
  pid="$(curl -s "$DD/api/v2/products/?name=$product" -H "$H" \
    | python3 -c 'import sys,json;r=json.load(sys.stdin).get("results") or [];print(r[0]["id"] if r else "")' 2>/dev/null)"
  [ -n "$pid" ] || return 0
  for eid in $(curl -s "$DD/api/v2/engagements/?product=$pid&name=$ENGAGEMENT&limit=50" -H "$H" \
      | python3 -c 'import sys,json;[print(e["id"]) for e in (json.load(sys.stdin).get("results") or [])]' 2>/dev/null); do
    curl -s -o /dev/null -X DELETE "$DD/api/v2/engagements/$eid/" -H "$H"
  done
}

# ─── Paso 2 (cablear config) antes de listar, para que todos queden incluidos ─
if [ "$WIRE" -eq 1 ]; then
  added="$(python3 "$HERE/dojo_products.py" wire "$YAML")" || die "Falló el cableado de config."
  log "config: $added proyecto(s) nuevos cableados a DefectDojo."
fi

# ─── Paso 1: importar cada reporte Trivy ──────────────────────────────────────
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ok=0; fail=0; total=0
while IFS=$'\t' read -r key name report product wired; do
  [ -n "$report" ] || continue
  total=$((total+1))
  dest="$TMP/$report"
  # Lee el JSON del volumen Trivy (no está publicado al host; via docker exec).
  if ! docker exec "$TRIVY_CTN" cat "/usr/share/nginx/html/$report" > "$dest" 2>/dev/null; then
    warn "$name: no se pudo leer el reporte $report del contenedor Trivy; se salta."
    fail=$((fail+1)); continue
  fi
  if [ ! -s "$dest" ]; then
    warn "$name: reporte $report vacío; se salta."; fail=$((fail+1)); continue
  fi
  # Reset limpio: borra el engagement Trivy previo del producto y reimporta fresco
  # (auto_create_context lo recrea) → 1 test con hallazgos activos, sin acumular.
  purge_trivy_engagement "$product"
  http="$(curl -s -o "$TMP/resp.json" -w '%{http_code}' -X POST "$DD/api/v2/reimport-scan/" \
    -H "$H" \
    -F "scan_type=$SCAN_TYPE" \
    -F "auto_create_context=true" \
    -F "product_type_name=$PRODUCT_TYPE" \
    -F "product_name=$product" \
    -F "engagement_name=$ENGAGEMENT" \
    -F "active=true" -F "verified=false" \
    -F "close_old_findings=true" \
    -F "file=@$dest;type=application/json")"
  if [ "$http" = "201" ] || [ "$http" = "200" ]; then
    log "✔ $name → producto '$product' (HTTP $http)"
    ok=$((ok+1))
  else
    warn "✗ $name → '$product' falló (HTTP $http): $(head -c 180 "$TMP/resp.json")"
    fail=$((fail+1))
  fi
done < <(python3 "$HERE/dojo_products.py" list "$YAML")

log "importados: $ok/$total  (fallos: $fail)"

# ─── Paso 3: re-sincronizar YAML→BD ──────────────────────────────────────────
if [ "$SYNC" -eq 1 ] && [ "$WIRE" -eq 1 ]; then
  log "re-sincronizando config a la BD (sync-config)…"
  if docker exec "$BACKEND_CTN" wolf-devsecops sync-config >/dev/null 2>&1; then
    log "sync-config OK — la pestaña DefectDojo ya refleja los proyectos."
  else
    warn "sync-config falló; corre a mano: docker exec $BACKEND_CTN wolf-devsecops sync-config"
  fi
fi

# ─── Paso 4: snapshot de postura (tendencia histórica, F/ADR-019) ─────────────
# Tras refrescar los datos, guarda un punto de postura por proyecto para la gráfica
# de tendencia. Solo métricas agregadas (no findings). No aborta si falla.
if docker exec "$BACKEND_CTN" wolf-devsecops snapshot-posture >/dev/null 2>&1; then
  log "snapshot de postura guardado (tendencia histórica)."
else
  warn "no se pudo guardar el snapshot de postura (¿migración 0003 aplicada?)."
fi

[ "$fail" -eq 0 ] || exit 1
