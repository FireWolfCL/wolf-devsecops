#!/usr/bin/env bash
# =============================================================================
# Wolf DevSecOps — instalador asistido / guided installer   (ES + EN)
# -----------------------------------------------------------------------------
# Un solo comando levanta TODA la suite: Postgres + backend (migrado y con el
# primer admin) + frontend + SonarQube + DefectDojo + Trivy. Opcionalmente cablea
# las fuentes y las deja en verde en el panel.
#
# One command brings up the WHOLE suite: Postgres + backend (migrated, with the
# first admin) + frontend + SonarQube + DefectDojo + Trivy. Optionally wires the
# sources and leaves them green in the panel.
#
#   bash deploy/install.sh                 # asistido / interactive
#   WOLF_LANG=en bash deploy/install.sh    # fuerza idioma / force language
#   bash deploy/install.sh --yes --lang es # no interactivo / non-interactive
#
# No pisa un deploy/.env existente (protege instalaciones ya hechas).
# Never overwrites an existing deploy/.env (protects existing installs).
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
COMPOSE_FILE="$HERE/docker-compose.install.yml"
ENV_FILE="$HERE/.env"
PROJECTS="$REPO/config/projects.yaml"
PROJECTS_SEED="$REPO/config/projects.install.yaml"

# ── idioma / language ────────────────────────────────────────────────────────
L="es"
ASSUME_YES="no"
for a in "$@"; do
  case "$a" in
    --lang=en|--en) L="en" ;;
    --lang=es|--es) L="es" ;;
    --lang) : ;;                       # el valor llega como siguiente arg (abajo)
    en|es) L="$a" ;;
    --yes|-y) ASSUME_YES="yes" ;;
  esac
done
# --lang <valor>
prev=""
for a in "$@"; do [ "$prev" = "--lang" ] && L="$a"; prev="$a"; done
[ -n "${WOLF_LANG:-}" ] && L="$WOLF_LANG"
[ "$L" = "en" ] || L="es"
[ "${WOLF_ASSUME_YES:-}" = "1" ] && ASSUME_YES="yes"
# sin TTY → no interactivo con defaults
[ -t 0 ] || ASSUME_YES="yes"

# ── helpers bilingües ────────────────────────────────────────────────────────
B="\033[1m"; G="\033[32m"; Y="\033[33m"; R="\033[31m"; C="\033[36m"; X="\033[0m"
say(){ [ "$L" = "en" ] && printf '%b\n' "$2" || printf '%b\n' "$1"; }
step(){ printf "\n${C}${B}▸ %s${X}\n" "$([ "$L" = en ] && echo "$2" || echo "$1")"; }
ok(){   printf "${G}✓${X} %s\n" "$([ "$L" = en ] && echo "$2" || echo "$1")"; }
warn(){ printf "${Y}!${X} %s\n" "$([ "$L" = en ] && echo "$2" || echo "$1")"; }
die(){  printf "${R}✗ %s${X}\n" "$([ "$L" = en ] && echo "$2" || echo "$1")" >&2; exit 1; }
# ask ES EN  → devuelve 0 (sí) / 1 (no). Default sí. Con --yes responde sí.
ask(){
  [ "$ASSUME_YES" = "yes" ] && return 0
  local q; q="$([ "$L" = en ] && echo "$2" || echo "$1")"
  local reply; printf "${Y}?${X} %s [S/n] " "$q"; read -r reply || true
  case "$reply" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# ── generadores de secretos (sin depender de python) ────────────────────────
rand_pw(){ LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-24}"; }
rand_secret(){ LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 64; }
gen_fernet(){ openssl rand -base64 32 | tr '+/' '-_'; }   # 32 bytes → clave Fernet urlsafe válida

banner(){
  printf "${B}${C}\n"
  printf "   ┌──────────────────────────────────────────────┐\n"
  printf "   │   🐺  Wolf DevSecOps — %s   │\n" "$([ "$L" = en ] && echo 'guided install ' || echo 'instalador     ')"
  printf "   └──────────────────────────────────────────────┘${X}\n"
}

# ── 0) idioma interactivo si no vino fijado ──────────────────────────────────
banner
if [ "$ASSUME_YES" != "yes" ] && [ -z "${WOLF_LANG:-}" ]; then
  printf "\n  1) Español   2) English\n"
  printf "  Idioma / Language [1]: "; read -r li || true
  [ "$li" = "2" ] && L="en"
fi

# ── 1) preflight ─────────────────────────────────────────────────────────────
step "Requisitos previos" "Prerequisites"
command -v docker >/dev/null 2>&1 || die \
  "Falta Docker. Instala Docker Desktop: https://docs.docker.com/get-docker/" \
  "Docker is missing. Install Docker Desktop: https://docs.docker.com/get-docker/"
if docker compose version >/dev/null 2>&1; then DC="docker compose";
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose";
else die "Falta Docker Compose v2 (docker compose)." "Docker Compose v2 (docker compose) is missing."; fi
docker info >/dev/null 2>&1 || die \
  "El demonio de Docker no responde. ¿Está Docker Desktop abierto?" \
  "The Docker daemon is not responding. Is Docker Desktop running?"
command -v openssl >/dev/null 2>&1 || die "Falta openssl (para generar secretos)." "openssl is required (to generate secrets)."
ok "Docker + Compose OK ($DC)" "Docker + Compose OK ($DC)"
warn "SonarQube pide ~2–3 GB de RAM libres en Docker. Si falla, sube la RAM en Docker Desktop → Resources." \
     "SonarQube needs ~2–3 GB free RAM in Docker. If it fails, raise RAM in Docker Desktop → Resources."

# ── 2) deploy/.env ───────────────────────────────────────────────────────────
step "Secretos (deploy/.env)" "Secrets (deploy/.env)"
if [ -f "$ENV_FILE" ]; then
  ok "Ya existe deploy/.env — no se toca (se conserva tu configuración)." \
     "deploy/.env already exists — left untouched (your config is kept)."
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
  ADMIN_PW="${WOLF_ADMIN_PASSWORD:-?}"
else
  ADMIN_PW="$(rand_pw 18)"
  umask 077
  cat > "$ENV_FILE" <<EOF
# Generado por install.sh — NO se comitea (gitignored). / Generated by install.sh — not committed.
# Secretos obligatorios del backend / mandatory backend secrets:
WOLF_JWT_SECRET=$(rand_secret)
WOLF_MFA_FERNET_KEY=$(gen_fernet)
WOLF_ADMIN_USERNAME=admin
WOLF_ADMIN_PASSWORD=$ADMIN_PW
WOLF_ADMIN_EMAIL=
# Contraseñas de las BD internas / internal DB passwords:
POSTGRES_PASSWORD=$(rand_pw 24)
SONAR_DB_PASSWORD=$(rand_pw 24)
DD_DB_PASSWORD=$(rand_pw 24)
# Reviewer IA desactivado por defecto (se activa en /admin/ia) / AI reviewer off by default:
WOLF_AI_ENABLED=false
WOLF_CORS_ORIGINS=http://localhost:8088
# Tokens de las fuentes — vacíos: los rellena el panel o provision-sources.sh.
# Source tokens — empty: filled by the panel or provision-sources.sh.
GITHUB_TOKEN=
DEFECTDOJO_TOKEN=
SONAR_TOKEN=
HARBOR_TOKEN=
TRIVY_TOKEN=
EOF
  chmod 600 "$ENV_FILE"
  ok "deploy/.env creado con secretos aleatorios (permisos 600)." \
     "deploy/.env created with random secrets (600 perms)."
fi

# ── 3) semilla de proyectos ──────────────────────────────────────────────────
step "Catálogo de proyectos" "Projects catalog"
if [ -f "$PROJECTS" ]; then
  ok "config/projects.yaml ya existe — se conserva." "config/projects.yaml already exists — kept."
elif [ -f "$PROJECTS_SEED" ]; then
  cp "$PROJECTS_SEED" "$PROJECTS"
  ok "config/projects.yaml creado desde la plantilla install." "config/projects.yaml seeded from the install template."
else
  warn "No hay plantilla de proyectos; el panel arrancará vacío (agrégalos en /admin/settings)." \
       "No projects template; the panel starts empty (add them in /admin/settings)."
fi

# ── 4) levantar la suite ─────────────────────────────────────────────────────
step "Levantando la suite (build + up)" "Bringing the suite up (build + up)"
say "Esto construye las imágenes y descarga Sonar/DefectDojo/Trivy. La 1ª vez tarda varios minutos." \
    "This builds the images and pulls Sonar/DefectDojo/Trivy. The first run takes several minutes."
( cd "$REPO" && $DC -f "$COMPOSE_FILE" up -d --build )

# ── 5) esperar salud ─────────────────────────────────────────────────────────
step "Esperando a que el panel responda" "Waiting for the panel to come up"
URL="http://localhost:8088"
tries=0; max=90
until curl -fsS "$URL/api/health" >/dev/null 2>&1; do
  tries=$((tries+1)); [ "$tries" -ge "$max" ] && { warn "El backend tardó demasiado; revisa: $DC -f $COMPOSE_FILE logs backend" "Backend took too long; check: $DC -f $COMPOSE_FILE logs backend"; break; }
  printf "."; sleep 2
done
printf "\n"
curl -fsS "$URL/api/health" >/dev/null 2>&1 && ok "Backend saludable (migrado + admin creado)." "Backend healthy (migrated + admin created)."
curl -fsS "$URL/" >/dev/null 2>&1 && ok "Frontend sirviendo en $URL" "Frontend serving at $URL"

# ── 6) conectar fuentes (opcional) ───────────────────────────────────────────
step "Conectar fuentes demo" "Connect demo sources"
if ask "¿Emitir tokens y cablear SonarQube/DefectDojo/Trivy ahora? (provision-sources.sh)" \
       "Issue tokens and wire SonarQube/DefectDojo/Trivy now? (provision-sources.sh)"; then
  if [ -x "$HERE/provision-sources.sh" ]; then
    bash "$HERE/provision-sources.sh" || warn "provision-sources.sh terminó con avisos; puedes reintentarlo luego." "provision-sources.sh finished with warnings; you can re-run it later."
  else
    warn "No encontré provision-sources.sh ejecutable." "provision-sources.sh not found/executable."
  fi
else
  say "Sáltalo por ahora: conecta las fuentes desde el panel → Admin → Integraciones." \
      "Skipped: connect sources later from the panel → Admin → Integrations."
fi

# ── 7) resumen ───────────────────────────────────────────────────────────────
step "Listo" "Done"
printf "${B}%s${X}\n" "$([ "$L" = en ] && echo 'Open the panel and sign in:' || echo 'Abre el panel e inicia sesión:')"
printf "   ${C}%s${X}\n" "$URL"
printf "   %s  ${B}admin${X}\n" "$([ "$L" = en ] && echo 'User:    ' || echo 'Usuario: ')"
if [ "$ADMIN_PW" != "?" ]; then
  printf "   %s  ${B}%s${X}\n" "$([ "$L" = en ] && echo 'Password:' || echo 'Clave:   ')" "$ADMIN_PW"
else
  printf "   %s  %s\n" "$([ "$L" = en ] && echo 'Password:' || echo 'Clave:   ')" "$([ "$L" = en ] && echo 'see deploy/.env (WOLF_ADMIN_PASSWORD)' || echo 'ver deploy/.env (WOLF_ADMIN_PASSWORD)')"
fi
echo
say "Primer login (flujo endurecido): 1) cambia la contraseña, 2) enrola MFA (QR + código TOTP)." \
    "First login (hardened flow): 1) change the password, 2) enroll MFA (QR + TOTP code)."
say "Otros servicios: SonarQube :9000 · DefectDojo :8086 · Harbor :8085 (su instalador aparte)." \
    "Other services: SonarQube :9000 · DefectDojo :8086 · Harbor :8085 (its own installer)."
echo
say "Parar (conserva datos): $DC -f deploy/docker-compose.install.yml down" \
    "Stop (keeps data):      $DC -f deploy/docker-compose.install.yml down"
say "Borrar todo (¡datos!):  $DC -f deploy/docker-compose.install.yml down -v" \
    "Wipe everything (data!): $DC -f deploy/docker-compose.install.yml down -v"

if ask "¿Abrir el panel en el navegador?" "Open the panel in the browser?"; then
  (command -v open >/dev/null 2>&1 && open "$URL") || (command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL") || true
fi
