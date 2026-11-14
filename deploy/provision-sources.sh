#!/usr/bin/env bash
# =============================================================================
# provision-sources.sh — cablea las fuentes generadas desde 0 hacia Wolf DevSecOps.
# -----------------------------------------------------------------------------
# Espera a Sonar/DefectDojo/Harbor, emite tokens por API, crea el proyecto demo
# en cada uno, escribe los tokens en deploy/.env (gitignored) y reinicia el
# backend para que el panel muestre las fuentes en VERDE.
#
# Idempotente y tolerante: puede re-ejecutarse; si una fuente aún no está lista,
# la salta con un aviso (no aborta el resto).
#
#   Uso:  bash deploy/provision-sources.sh
# =============================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HERE/.env"
COMPOSE="$HERE/docker-compose.install.yml"

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

SONAR="http://localhost:9000"
DD="http://localhost:8086"
HARBOR="http://localhost:${HARBOR_HTTP_PORT:-8085}"

log()  { printf '\033[1;36m[provision]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[provision]\033[0m %s\n' "$*"; }

# Reescribe (o añade) KEY=VALUE en deploy/.env sin tocar el resto.
set_env() {
  key="$1"; val="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$val" '$0 ~ "^"k"=" {print k"="v; next} {print}' "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

wait_http() { # url, nombre, intentos
  url="$1"; name="$2"; tries="${3:-60}"
  log "esperando a $name ($url)…"
  i=0
  while [ "$i" -lt "$tries" ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)"
    [ "$code" != "000" ] && { log "$name responde (HTTP $code)."; return 0; }
    i=$((i+1)); sleep 5
  done
  warn "$name no respondió tras $((tries*5))s; se salta."
  return 1
}

# ─── SonarQube ───────────────────────────────────────────────────────────────
provision_sonar() {
  # Espera a status UP (no solo al puerto).
  i=0
  while [ "$i" -lt 60 ]; do
    st="$(curl -s --max-time 5 "$SONAR/api/system/status" 2>/dev/null | grep -o '"status":"[A-Z]*"' | cut -d'"' -f4)"
    [ "$st" = "UP" ] && break
    i=$((i+1)); sleep 5
  done
  [ "$st" = "UP" ] || { warn "SonarQube no llegó a UP; se salta."; return 1; }
  log "SonarQube UP."

  # Cambia la pass admin (admin→SONAR_ADMIN_PASSWORD). Si ya se cambió, 401 → ok.
  curl -s -o /dev/null -u "admin:admin" -X POST \
    "$SONAR/api/users/change_password" \
    --data-urlencode "login=admin" \
    --data-urlencode "previousPassword=admin" \
    --data-urlencode "password=${SONAR_ADMIN_PASSWORD}" || true

  AUTH="admin:${SONAR_ADMIN_PASSWORD}"
  # Proyecto demo (ignora si ya existe).
  curl -s -o /dev/null -u "$AUTH" -X POST \
    "$SONAR/api/projects/create" \
    --data-urlencode "project=wolf-cloud" \
    --data-urlencode "name=Wolf-Cloud" || true

  # Token: revoca el previo y genera uno nuevo.
  curl -s -o /dev/null -u "$AUTH" -X POST \
    "$SONAR/api/user_tokens/revoke" --data-urlencode "name=wolf-devsecops" || true
  tok="$(curl -s -u "$AUTH" -X POST \
    "$SONAR/api/user_tokens/generate" --data-urlencode "name=wolf-devsecops" \
    | grep -o '"token":"[^"]*"' | cut -d'"' -f4)"
  if [ -n "$tok" ]; then
    set_env SONAR_TOKEN "$tok"
    log "SonarQube: token emitido y escrito en .env ✔"
  else
    warn "SonarQube: no se pudo emitir token."
  fi
}

# ─── DefectDojo ──────────────────────────────────────────────────────────────
provision_defectdojo() {
  wait_http "$DD/login" "DefectDojo" 72 || return 1
  tok="$(curl -s -X POST "$DD/api/v2/api-token-auth/" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${DD_ADMIN_USER:-admin}\",\"password\":\"${DD_ADMIN_PASSWORD}\"}" \
    | grep -o '"token":"[^"]*"' | cut -d'"' -f4)"
  if [ -z "$tok" ]; then warn "DefectDojo: no se pudo obtener token (¿initializer terminó?)."; return 1; fi
  set_env DEFECTDOJO_TOKEN "$tok"
  log "DefectDojo: token obtenido y escrito en .env ✔"

  H="Authorization: Token $tok"
  # Tipo de producto (usa el primero existente o crea uno).
  pt="$(curl -s "$DD/api/v2/product_types/?limit=1" -H "$H" | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)"
  if [ -z "$pt" ]; then
    pt="$(curl -s -X POST "$DD/api/v2/product_types/" -H "$H" -H 'Content-Type: application/json' \
      -d '{"name":"Research and Development","description":"auto"}' | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)"
  fi
  # Producto demo "Wolf-Cloud" (ignora si ya existe).
  curl -s -o /dev/null -X POST "$DD/api/v2/products/" -H "$H" -H 'Content-Type: application/json' \
    -d "{\"name\":\"Wolf-Cloud\",\"description\":\"Proyecto demo\",\"prod_type\":${pt:-1}}" || true
  log "DefectDojo: producto 'Wolf-Cloud' asegurado."
}

# ─── Harbor (opcional: solo si su stack está arriba) ─────────────────────────
provision_harbor() {
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HARBOR/api/v2.0/systeminfo" 2>/dev/null || echo 000)"
  if [ "$code" != "200" ]; then
    warn "Harbor no está arriba en $HARBOR (systeminfo=$code). Levántalo con deploy/harbor/setup-harbor.sh y re-ejecuta este script."
    return 1
  fi
  log "Harbor arriba (systeminfo 200)."
  AUTH="admin:${HARBOR_ADMIN_PASSWORD}"
  # Proyecto demo (ignora si ya existe).
  curl -s -o /dev/null -u "$AUTH" -X POST "$HARBOR/api/v2.0/projects" \
    -H 'Content-Type: application/json' \
    -d '{"project_name":"wolf-cloud","metadata":{"public":"true"}}' || true
  log "Harbor: proyecto 'wolf-cloud' asegurado (probe por systeminfo público = verde)."
}

log "== Provisionando fuentes de Wolf DevSecOps =="
provision_sonar       || true
provision_defectdojo  || true
provision_harbor      || true

log "Reiniciando backend para tomar los tokens nuevos…"
# Re-lee el .env ACTUALIZADO: las vars de shell tienen prioridad sobre el archivo
# en la interpolación de compose, así que sin esto el backend tomaría los valores
# vacíos cargados al inicio. --force-recreate garantiza que el env nuevo se aplique.
set -a; . "$ENV_FILE"; set +a
docker compose -f "$COMPOSE" up -d --force-recreate backend >/dev/null 2>&1 || warn "No se pudo reiniciar el backend automáticamente; hazlo con: docker compose -f $COMPOSE up -d --force-recreate backend"

log "Listo. Abre http://localhost:8088 → Integraciones y pulsa 'Probar conexión'."
