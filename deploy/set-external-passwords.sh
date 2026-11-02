#!/usr/bin/env bash
# =============================================================================
# set-external-passwords.sh — fija UNA MISMA password de admin en las herramientas
# externas (SonarQube, DefectDojo, Harbor) del stack local.
# -----------------------------------------------------------------------------
# LO CORRE EL USUARIO, no Claude. La nueva clave se toma de la variable de entorno
# NEW_EXTERNAL_PASSWORD (nunca se escribe en el repo ni se pasa como argumento, que
# quedaría en el historial del shell). Las claves ACTUALES se leen de deploy/.env
# para autenticar el cambio. Tras cada cambio exitoso, actualiza deploy/.env.
#
#   Uso (recomendado, sin dejar la clave en el history):
#     read -rs NEW_EXTERNAL_PASSWORD; export NEW_EXTERNAL_PASSWORD
#     bash deploy/set-external-passwords.sh
#     unset NEW_EXTERNAL_PASSWORD
#
# Nota de seguridad: reutilizar una sola password (y más si es la de tu admin de
# Wolf) en varias apps es un anti-patrón. Preferible una clave distinta y fuerte
# solo para estas herramientas. Los TOKENS de API que usa el backend NO cambian
# con esto (Sonar/DefectDojo), así que el panel sigue funcionando sin re-provisión.
# =============================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HERE/.env"
# La nueva clave se obtiene (en orden): de la variable NEW_EXTERNAL_PASSWORD si ya
# viene exportada, o si no, se PIDE por consola de forma interactiva (no se muestra
# y no queda en el history). NP se asigna más abajo, tras definir die().

SONAR="${SONAR_URL:-http://localhost:9000}"
DD="${DD_URL:-http://localhost:8086}"
HARBOR="${HARBOR_URL:-http://localhost:${HARBOR_HTTP_PORT:-8085}}"
DD_CTN="${DD_CONTAINER:-wolf-devsecops-defectdojo-uwsgi-1}"

log()  { printf '\033[1;36m[pass]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[pass]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[pass]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || die "No existe $ENV_FILE."
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

# ─── Nueva contraseña: variable exportada o pedida por consola ─────────────────
if [ -n "${NEW_EXTERNAL_PASSWORD:-}" ]; then
  NP="$NEW_EXTERNAL_PASSWORD"
elif [ -t 0 ]; then
  printf 'Nueva contraseña para las herramientas (no se mostrará): '
  read -rs NP; echo
  printf 'Confírmala: '
  read -rs NP2; echo
  [ "$NP" = "$NP2" ] || die "Las contraseñas no coinciden; nada se cambió."
  [ -n "$NP" ]        || die "Contraseña vacía; nada se cambió."
else
  die "Sin terminal interactiva: exporta NEW_EXTERNAL_PASSWORD o corre el script en una consola."
fi

# Reescribe KEY=VALUE en deploy/.env sin tocar el resto.
set_env() {
  key="$1"; val="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    tmp="$(mktemp)"; awk -v k="$key" -v v="$val" '$0 ~ "^"k"=" {print k"="v; next} {print}' "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

# ─── SonarQube ────────────────────────────────────────────────────────────────
log "SonarQube…"
code="$(curl -s -o /dev/null -w '%{http_code}' -u "admin:${SONAR_ADMIN_PASSWORD}" -X POST \
  "$SONAR/api/users/change_password" \
  --data-urlencode "login=admin" \
  --data-urlencode "previousPassword=${SONAR_ADMIN_PASSWORD}" \
  --data-urlencode "password=${NP}")"
if [ "$code" = "204" ] || [ "$code" = "200" ]; then
  set_env SONAR_ADMIN_PASSWORD "$NP"; log "✔ Sonar admin cambiada."
else
  warn "✗ Sonar respondió HTTP $code (¿clave actual distinta? ¿política de complejidad?)."
fi

# ─── Harbor ───────────────────────────────────────────────────────────────────
log "Harbor…"
uid="$(curl -s -u "admin:${HARBOR_ADMIN_PASSWORD}" "$HARBOR/api/v2.0/users/current" | grep -o '"user_id":[0-9]*' | head -1 | cut -d: -f2)"
if [ -n "$uid" ]; then
  code="$(curl -s -o /dev/null -w '%{http_code}' -u "admin:${HARBOR_ADMIN_PASSWORD}" -X PUT \
    "$HARBOR/api/v2.0/users/$uid/password" -H 'Content-Type: application/json' \
    --data "{\"old_password\":\"${HARBOR_ADMIN_PASSWORD}\",\"new_password\":\"${NP}\"}")"
  if [ "$code" = "200" ]; then
    set_env HARBOR_ADMIN_PASSWORD "$NP"; log "✔ Harbor admin cambiada."
  else
    warn "✗ Harbor respondió HTTP $code (política Harbor: 8-128 chars con mayúscula+minúscula+número)."
  fi
else
  warn "✗ Harbor no autenticó con la clave actual; se salta."
fi

# ─── DefectDojo (vía Django set_password; la API REST no expone cambio de clave) ─
log "DefectDojo…"
if docker exec -e NP="$NP" -e DDU="${DD_ADMIN_USER:-admin}" "$DD_CTN" \
     python manage.py shell -c \
     "import os;from django.contrib.auth import get_user_model as G;u=G().objects.get(username=os.environ['DDU']);u.set_password(os.environ['NP']);u.save();print('ok')" 2>/dev/null | grep -q ok; then
  set_env DD_ADMIN_PASSWORD "$NP"; log "✔ DefectDojo admin cambiada."
else
  warn "✗ DefectDojo: no se pudo cambiar (¿contenedor $DD_CTN?, ¿usuario ${DD_ADMIN_USER:-admin}?)."
fi

log "Listo. Los tokens de API del backend NO cambian; el panel sigue igual."
log "Revisa deploy/.env (gitignored) para confirmar los valores actualizados."
