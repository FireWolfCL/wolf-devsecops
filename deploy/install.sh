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
#   bash deploy/install.sh --repo ~/mi-app # score real (Sonar+Trivy) de TU repo
#   bash deploy/install.sh --image nginx:1.27   # score Trivy de TU imagen
#   bash deploy/install.sh --demo          # escaneo demo (python:3.9-slim), opt-in
#
# No pisa un deploy/.env existente (protege instalaciones ya hechas).
# Never overwrites an existing deploy/.env (protects existing installs).
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
ENV_FILE="$HERE/.env"
PROJECTS="$REPO/config/projects.yaml"
FRESH_SEED="no"

# ── idioma / language · perfil / profile ─────────────────────────────────────
L="es"
ASSUME_YES="no"
PROFILE=""                             # vacío = sin fijar (se pregunta o default standard)
T_REPO=""; T_IMAGE=""; T_KEY=""; T_NAME=""; T_ORG=""; DEMO="no"; GITLEAKS="no"   # objetivo a escanear / scan target
for a in "$@"; do
  case "$a" in
    --lang=en|--en) L="en" ;;
    --lang=es|--es) L="es" ;;
    --lang) : ;;                       # el valor llega como siguiente arg (abajo)
    en|es) L="$a" ;;
    --yes|-y) ASSUME_YES="yes" ;;
    --profile=lite|--profile=standard|--profile=full) PROFILE="${a#--profile=}" ;;
    --profile) : ;;                    # --profile <valor> (abajo)
    --repo=*)  T_REPO="${a#--repo=}" ;;
    --image=*) T_IMAGE="${a#--image=}" ;;
    --key=*)   T_KEY="${a#--key=}" ;;
    --name=*)  T_NAME="${a#--name=}" ;;
    --org=*)   T_ORG="${a#--org=}" ;;
    --demo)    DEMO="yes" ;;
    --gitleaks) GITLEAKS="yes" ;;
    --repo|--image|--key|--name|--org) : ;;  # el valor llega como siguiente arg (abajo)
    lite|standard|full) PROFILE="$a" ;;
  esac
done
# --lang <valor>, --profile <valor>, --repo/--image/--key/--name <valor>
prev=""
for a in "$@"; do
  [ "$prev" = "--lang" ] && L="$a"
  [ "$prev" = "--profile" ] && PROFILE="$a"
  [ "$prev" = "--repo" ] && T_REPO="$a"
  [ "$prev" = "--image" ] && T_IMAGE="$a"
  [ "$prev" = "--key" ] && T_KEY="$a"
  [ "$prev" = "--name" ] && T_NAME="$a"
  [ "$prev" = "--org" ] && T_ORG="$a"
  prev="$a"
done
[ -n "${WOLF_REPO:-}" ] && T_REPO="$WOLF_REPO"
[ -n "${WOLF_IMAGE:-}" ] && T_IMAGE="$WOLF_IMAGE"
[ -n "${WOLF_ORG:-}" ] && T_ORG="$WOLF_ORG"
[ "${WOLF_GITLEAKS:-}" = "1" ] && GITLEAKS="yes"
[ "${WOLF_DEMO:-}" = "1" ] && DEMO="yes"
[ -n "${WOLF_LANG:-}" ] && L="$WOLF_LANG"
[ -n "${WOLF_PROFILE:-}" ] && PROFILE="$WOLF_PROFILE"
[ "$L" = "en" ] || L="es"
case "$PROFILE" in lite|standard|full|"") : ;; *) PROFILE="" ;; esac  # valor inválido → se trata como sin fijar
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

# ask_no ES EN → como ask pero default NO; con --yes responde no (no interactivo).
ask_no(){
  [ "$ASSUME_YES" = "yes" ] && return 1
  local q; q="$([ "$L" = en ] && echo "$2" || echo "$1")"
  local reply; printf "${Y}?${X} %s [s/N] " "$q"; read -r reply || true
  case "$reply" in [sSyY]*) return 0 ;; *) return 1 ;; esac
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

# ── 0b) perfil interactivo si no vino fijado ─────────────────────────────────
# lite = panel + Sonar + Trivy (≈2 GB) · standard = + DefectDojo (≈4-5 GB) ·
# full = standard + Harbor (su instalador aparte).
if [ -z "$PROFILE" ] && [ "$ASSUME_YES" != "yes" ]; then
  say "\n  Perfil de instalación:" "\n  Install profile:"
  say "  1) lite      — panel + SonarQube + Trivy        (~2 GB RAM, arranque rápido)" \
      "  1) lite      — panel + SonarQube + Trivy        (~2 GB RAM, quick start)"
  say "  2) standard  — + DefectDojo (gestión de hallazgos) (~4-5 GB RAM)" \
      "  2) standard  — + DefectDojo (findings management)  (~4-5 GB RAM)"
  say "  3) full      — standard + Harbor (registry, su instalador aparte)" \
      "  3) full      — standard + Harbor (registry, its own installer)"
  printf "  [2]: "; read -r pf || true
  case "$pf" in 1) PROFILE="lite" ;; 3) PROFILE="full" ;; *) PROFILE="standard" ;; esac
fi
[ -n "$PROFILE" ] || PROFILE="standard"   # default no interactivo

# Resuelve compose + semilla de proyectos según el perfil.
case "$PROFILE" in
  lite)
    COMPOSE_FILE="$HERE/docker-compose.lite.yml"
    PROJECTS_SEED="$REPO/config/projects.lite.yaml"
    ;;
  standard|full)
    COMPOSE_FILE="$HERE/docker-compose.install.yml"
    PROJECTS_SEED="$REPO/config/projects.install.yaml"
    ;;
esac
ok "Perfil: $PROFILE" "Profile: $PROFILE"

# ── 0c) objetivo a escanear (tu repo / tu imagen) ────────────────────────────
# Sin --repo/--image/--demo: pregunta (interactivo) o no escanea nada (--yes / sin TTY).
# NUNCA cae al demo salvo --demo.
if [ -z "$T_REPO" ] && [ -z "$T_IMAGE" ] && [ "$DEMO" != "yes" ] && [ "$ASSUME_YES" != "yes" ]; then
  say "\n  ¿Qué quieres analizar? (Enter = omitir; luego: bash deploy/scan-target.sh)" \
      "\n  What do you want to analyze? (Enter = skip; later: bash deploy/scan-target.sh)"
  printf "  %s " "$([ "$L" = en ] && echo 'Local repo path:' || echo 'Ruta local de tu repo:')"; read -r T_REPO || true
  printf "  %s " "$([ "$L" = en ] && echo 'Container image (name[:tag]):' || echo 'Imagen de contenedor (nombre[:tag]):')"; read -r T_IMAGE || true
  if [ -n "$T_REPO" ] || [ -n "$T_IMAGE" ]; then
    printf "  %s " "$([ "$L" = en ] && echo 'Organization (to group it in the panel) [Local]:' || echo 'Organización (para agruparlo en el panel) [Local]:')"; read -r T_ORG || true
  else
    ask_no "¿Correr el escaneo DEMO (python:3.9-slim) para ver el panel con datos de ejemplo?" \
           "Run the DEMO scan (python:3.9-slim) to see the panel with sample data?" && DEMO="yes"
  fi
fi
case "$T_REPO" in "~"|"~/"*) T_REPO="$HOME${T_REPO#\~}" ;; esac
if [ -n "$T_REPO" ]; then
  [ -d "$T_REPO" ] || die "El repo no existe o no es un directorio: $T_REPO" "Repo does not exist or is not a directory: $T_REPO"
  T_REPO="$(cd "$T_REPO" && pwd -P)"
fi
if [ -n "$T_IMAGE" ] && ! printf '%s' "$T_IMAGE" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/:@-]*$'; then
  die "Nombre de imagen inválido: $T_IMAGE" "Invalid image name: $T_IMAGE"
fi
HAS_TARGET="no"; { [ -n "$T_REPO" ] || [ -n "$T_IMAGE" ]; } && HAS_TARGET="yes"

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
# Secretos obligatorios de DefectDojo (standard/full) — el initializer los exige con \${...:?}.
# Si faltan, el 'up' de standard aborta antes de levantar DefectDojo.
DD_SECRET_KEY=$(rand_secret)
DD_AES_KEY=$(rand_pw 32)
DD_ADMIN_PASSWORD=$(rand_pw 18)
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
  FRESH_SEED="yes"
  ok "config/projects.yaml creado desde la plantilla install." "config/projects.yaml seeded from the install template."
else
  warn "No hay plantilla de proyectos; el panel arrancará vacío (agrégalos en /admin/settings)." \
       "No projects template; the panel starts empty (add them in /admin/settings)."
fi

# ── 4) levantar la suite ─────────────────────────────────────────────────────
step "Levantando la suite (build + up)" "Bringing the suite up (build + up)"
if [ "$PROFILE" = "lite" ]; then
  say "Perfil lite: construye el panel + SonarQube + Trivy (sin DefectDojo). La 1ª vez tarda varios minutos." \
      "lite profile: builds the panel + SonarQube + Trivy (no DefectDojo). The first run takes several minutes."
else
  say "Esto construye las imágenes y descarga Sonar/DefectDojo/Trivy. La 1ª vez tarda varios minutos." \
      "This builds the images and pulls Sonar/DefectDojo/Trivy. The first run takes several minutes."
fi
( cd "$REPO" && $DC -f "$COMPOSE_FILE" up -d --build )

# ── 4b) escaneo DEMO (opt-in con --demo) ─────────────────────────────────────
# Idéntico al comportamiento histórico: trivy image python:3.9-slim → wolf-cloud.json.
if [ "$DEMO" = "yes" ]; then
  step "Escaneo demo (python:3.9-slim)" "Demo scan (python:3.9-slim)"
  ( cd "$REPO" && $DC -f "$COMPOSE_FILE" --profile demo run --rm trivy-scan ) \
    && ok "Reporte demo publicado (wolf-cloud.json)." "Demo report published (wolf-cloud.json)." \
    || warn "El escaneo demo falló; reintenta: $DC -f deploy/$(basename "$COMPOSE_FILE") --profile demo run --rm trivy-scan" \
            "Demo scan failed; retry: $DC -f deploy/$(basename "$COMPOSE_FILE") --profile demo run --rm trivy-scan"
fi

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
if [ "$PROFILE" = "lite" ]; then
  Q_ES="¿Emitir tokens y cablear SonarQube/Trivy ahora? (provision-sources.sh)"
  Q_EN="Issue tokens and wire SonarQube/Trivy now? (provision-sources.sh)"
else
  Q_ES="¿Emitir tokens y cablear SonarQube/DefectDojo/Trivy ahora? (provision-sources.sh)"
  Q_EN="Issue tokens and wire SonarQube/DefectDojo/Trivy now? (provision-sources.sh)"
fi
if ask "$Q_ES" "$Q_EN"; then
  if [ -x "$HERE/provision-sources.sh" ]; then
    # WOLF_PROFILE permite que provision-sources.sh omita DefectDojo en lite.
    WOLF_PROFILE="$PROFILE" bash "$HERE/provision-sources.sh" || warn "provision-sources.sh terminó con avisos; puedes reintentarlo luego." "provision-sources.sh finished with warnings; you can re-run it later."
  else
    warn "No encontré provision-sources.sh ejecutable." "provision-sources.sh not found/executable."
  fi
else
  say "Sáltalo por ahora: conecta las fuentes desde el panel → Admin → Integraciones." \
      "Skipped: connect sources later from the panel → Admin → Integrations."
fi

# ── 6a) escanear TU objetivo (tras provision-sources.sh: necesita SONAR_TOKEN) ─
if [ "$HAS_TARGET" = "yes" ]; then
  step "Escaneando tu proyecto (Trivy + Sonar)" "Scanning your project (Trivy + Sonar)"
  # El demo de la semilla (wolf-cloud) sobra si el operador trae su objetivo.
  if [ "$FRESH_SEED" = "yes" ] && [ "$DEMO" != "yes" ] && [ -f "$PROJECTS" ]; then
    ( # shellcheck disable=SC1091
      . "$HERE/scan-common.sh"
      WOLF_COMPOSE_FILE="$COMPOSE_FILE" scan_helper drop "$PROJECTS" --key wolf-cloud >/dev/null 2>&1 ) || true
  fi
  SCAN_ARGS=()
  [ -n "$T_REPO" ]  && SCAN_ARGS+=(--repo "$T_REPO")
  [ -n "$T_IMAGE" ] && SCAN_ARGS+=(--image "$T_IMAGE")
  [ -n "$T_KEY" ]   && SCAN_ARGS+=(--key "$T_KEY")
  [ -n "$T_NAME" ]  && SCAN_ARGS+=(--name "$T_NAME")
  [ -n "$T_ORG" ]   && SCAN_ARGS+=(--org "$T_ORG")
  [ "$GITLEAKS" = "yes" ] && SCAN_ARGS+=(--gitleaks)
  if WOLF_COMPOSE_FILE="$COMPOSE_FILE" bash "$HERE/scan-target.sh" "${SCAN_ARGS[@]}"; then
    ok "Tu score real ya está en el panel." "Your real score is now in the panel."
  else
    warn "El escaneo terminó con avisos; reintenta: bash deploy/scan-target.sh ${SCAN_ARGS[*]}" \
         "Scan finished with warnings; retry: bash deploy/scan-target.sh ${SCAN_ARGS[*]}"
  fi
elif [ "$DEMO" != "yes" ]; then
  say "No se escaneó nada (sin --repo/--image/--demo). Cuando quieras: bash deploy/scan-target.sh --repo <ruta>" \
      "Nothing was scanned (no --repo/--image/--demo). Whenever you want: bash deploy/scan-target.sh --repo <path>"
fi

# ── 6b) Harbor (solo perfil full) ────────────────────────────────────────────
if [ "$PROFILE" = "full" ]; then
  step "Harbor (registry de imágenes)" "Harbor (image registry)"
  if [ -x "$HERE/harbor/setup-harbor.sh" ]; then
    if ask "¿Instalar Harbor ahora con su instalador oficial? (deploy/harbor/setup-harbor.sh)" \
           "Install Harbor now with its official installer? (deploy/harbor/setup-harbor.sh)"; then
      bash "$HERE/harbor/setup-harbor.sh" || warn "setup-harbor.sh terminó con avisos; puedes reintentarlo luego." "setup-harbor.sh finished with warnings; you can re-run it later."
    else
      say "Sáltalo: luego corre  bash deploy/harbor/setup-harbor.sh" \
          "Skipped: later run  bash deploy/harbor/setup-harbor.sh"
    fi
  else
    warn "No encontré deploy/harbor/setup-harbor.sh; instala Harbor por separado (ver deploy/harbor/README.md)." \
         "deploy/harbor/setup-harbor.sh not found; install Harbor separately (see deploy/harbor/README.md)."
  fi
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
# Ruta del compose del perfil, relativa al repo, para los comandos de abajo.
CF_REL="deploy/$(basename "$COMPOSE_FILE")"
if [ "$PROFILE" = "lite" ]; then
  say "Otros servicios: SonarQube :9000. DefectDojo y Harbor: no incluidos en lite (sube a standard/full)." \
      "Other services: SonarQube :9000. DefectDojo and Harbor: not in lite (move up to standard/full)."
else
  say "Otros servicios: SonarQube :9000 · DefectDojo :8086 · Harbor :8085 (su instalador aparte)." \
      "Other services: SonarQube :9000 · DefectDojo :8086 · Harbor :8085 (its own installer)."
fi
echo
say "Parar (conserva datos): $DC -f $CF_REL down" \
    "Stop (keeps data):      $DC -f $CF_REL down"
say "Borrar todo (¡datos!):  $DC -f $CF_REL down -v" \
    "Wipe everything (data!): $DC -f $CF_REL down -v"

# ── 8) smoke test (semáforo) ─────────────────────────────────────────────────
step "Smoke test" "Smoke test"
if [ -f "$HERE/smoke.sh" ]; then
  WOLF_LANG="$L" bash "$HERE/smoke.sh" --url "$URL" --compose "$COMPOSE_FILE" --lang "$L" || \
    warn "El smoke test reportó avisos/fallos (ver arriba); revisa: $DC -f $CF_REL logs" \
         "Smoke test reported warnings/failures (see above); check: $DC -f $CF_REL logs"
fi

if ask "¿Abrir el panel en el navegador?" "Open the panel in the browser?"; then
  (command -v open >/dev/null 2>&1 && open "$URL") || (command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL") || true
fi
