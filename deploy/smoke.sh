#!/usr/bin/env bash
# Smoke test post-instalación de Wolf DevSecOps (MEJORAS 3.4).
# Verifica, sin credenciales, que el stack quedó sano: /api/health, /api/meta,
# el frontend, y que los contenedores de cada fuente están arriba. Imprime un
# semáforo (🟢 ok · 🟡 aviso · 🔴 fallo) y sale con código 0/1/2.
#
# Uso:  bash deploy/smoke.sh [--url http://localhost:8088] [--compose <file>] [--lang es|en]
# Es solo-lectura (ADR-001): no toca nada, solo consulta estado.
set -u

URL="http://localhost:8088"
COMPOSE_FILE=""
L="${WOLF_LANG:-es}"
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --compose) COMPOSE_FILE="$2"; shift 2 ;;
    --lang) L="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"
[ -n "$COMPOSE_FILE" ] || COMPOSE_FILE="$HERE/docker-compose.install.yml"

C='\033[36m'; G='\033[32m'; Y='\033[33m'; R='\033[31m'; X='\033[0m'; B='\033[1m'
say(){ [ "$L" = en ] && printf '%b\n' "$2" || printf '%b\n' "$1"; }

# Peor estado observado: 0 ok, 1 aviso, 2 fallo.
WORST=0
bump(){ [ "$1" -gt "$WORST" ] && WORST="$1"; return 0; }
line_ok(){   printf "  ${G}🟢${X} %s\n" "$1"; }
line_warn(){ printf "  ${Y}🟡${X} %s\n" "$1"; bump 1; }
line_bad(){  printf "  ${R}🔴${X} %s\n" "$1"; bump 2; }

command -v curl >/dev/null 2>&1 || { echo "Falta curl." >&2; exit 2; }

printf "${B}${C}"
say "  Smoke test — Wolf DevSecOps" "  Smoke test — Wolf DevSecOps"
printf "${X}  %s\n\n" "$URL"

# 1) /api/health ──────────────────────────────────────────────────────────────
HEALTH="$(curl -fsS "$URL/api/health" 2>/dev/null)" || HEALTH=""
if printf '%s' "$HEALTH" | grep -q '"status"'; then
  VER="$(printf '%s' "$HEALTH" | sed -n 's/.*"version"[^"]*"\([^"]*\)".*/\1/p')"
  line_ok "$(say "/api/health responde (versión ${VER:-?})" "/api/health responds (version ${VER:-?})")"
else
  line_bad "$(say "/api/health no responde — el backend no está arriba" "/api/health is down — backend not up")"
fi

# 2) /api/meta ──────────────────────────────────────────────────────────────
META="$(curl -fsS "$URL/api/meta" 2>/dev/null)" || META=""
if [ -n "$META" ]; then
  PC="$(printf '%s' "$META" | sed -n 's/.*"project_count"[^0-9]*\([0-9]*\).*/\1/p')"
  ED="$(printf '%s' "$META" | sed -n 's/.*"edition"[^"]*"\([^"]*\)".*/\1/p')"
  SRCS="$(printf '%s' "$META" | sed -n 's/.*"sources_configured"[^]]*\[\([^]]*\)\].*/\1/p' | tr -d '" ' )"
  line_ok "$(say "/api/meta OK — edición ${ED:-?}, proyectos: ${PC:-0}" "/api/meta OK — edition ${ED:-?}, projects: ${PC:-0}")"
  if printf '%s' "$META" | grep -q '"config_error"'; then
    line_warn "$(say "projects.yaml con error de configuración (revisa Admin → Integraciones)" "projects.yaml has a config error (see Admin → Integrations)")"
  fi
  if [ -n "$SRCS" ]; then
    line_ok "$(say "fuentes configuradas: $SRCS" "configured sources: $SRCS")"
  else
    line_warn "$(say "ninguna fuente configurada todavía (conéctalas en el panel)" "no sources configured yet (connect them in the panel)")"
  fi
else
  line_bad "$(say "/api/meta no responde" "/api/meta is down")"
fi

# 3) frontend ────────────────────────────────────────────────────────────────
if curl -fsS "$URL/" >/dev/null 2>&1; then
  line_ok "$(say "el frontend sirve en $URL" "frontend serving at $URL")"
else
  line_warn "$(say "el frontend no respondió en $URL/" "frontend did not respond at $URL/")"
fi

# 4) contenedores de las fuentes (el 'probe' sin credenciales) ─────────────────
DC=""
if docker compose version >/dev/null 2>&1; then DC="docker compose";
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"; fi
if [ -n "$DC" ] && [ -f "$COMPOSE_FILE" ]; then
  REPO="$(cd "$HERE/.." && pwd)"
  PS="$( cd "$REPO" && $DC -f "$COMPOSE_FILE" ps --format '{{.Service}} {{.State}}' 2>/dev/null )"
  if [ -n "$PS" ]; then
    DOWN="$(printf '%s\n' "$PS" | awk '$2!="running" && $2!="" {print $1"("$2")"}' | paste -sd ' ' -)"
    UPN="$(printf '%s\n' "$PS" | awk '$2=="running"' | wc -l | tr -d ' ')"
    if [ -z "$DOWN" ]; then
      line_ok "$(say "contenedores arriba: $UPN" "containers up: $UPN")"
    else
      line_warn "$(say "contenedores no 'running': $DOWN" "containers not 'running': $DOWN")"
    fi
  else
    line_warn "$(say "no pude leer el estado de los contenedores" "could not read container state")"
  fi
else
  line_warn "$(say "sin docker compose o sin $COMPOSE_FILE: omito chequeo de contenedores" "no docker compose or missing $COMPOSE_FILE: skipping container check")"
fi

# Semáforo final ───────────────────────────────────────────────────────────────
echo
case "$WORST" in
  0) printf "  ${G}${B}🟢 %s${X}\n" "$(say "Todo sano." "All healthy.")" ;;
  1) printf "  ${Y}${B}🟡 %s${X}\n" "$(say "Operativo con avisos (revisa arriba)." "Operational with warnings (see above).")" ;;
  2) printf "  ${R}${B}🔴 %s${X}\n" "$(say "Hay fallos: el stack no quedó sano." "Failures found: the stack is not healthy.")" ;;
esac
exit "$WORST"
