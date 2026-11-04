#!/usr/bin/env bash
# =============================================================================
# restore.sh — restaura un respaldo de backup.sh / restores a backup from backup.sh  (ES + EN)
# -----------------------------------------------------------------------------
#   bash deploy/restore.sh backups/wolf-backup-20261104-1530.tgz
#   bash deploy/restore.sh <tgz> --compose deploy/docker-compose.lite.yml
#   WOLF_LANG=en bash deploy/restore.sh <tgz>
#
# ⚠ SOBRESCRIBE las bases de datos (y volúmenes de SonarQube, si el respaldo los
#   trae) del stack actual. Pregunta y/N antes de tocar nada. Sin TTY se niega.
# ⚠ REQUIERE el stack LEVANTADO: los Postgres (db, sonar-db, defectdojo-db) deben
#   estar corriendo. En un host nuevo: copia primero deploy/.env del respaldo,
#   corre `docker compose -f <compose> up -d` y recién entonces restaura.
# ⚠ OVERWRITES the databases (and SonarQube volumes, if the backup has them) of the
#   current stack. Asks y/N first. Refuses without a TTY.
# ⚠ REQUIRES the stack UP: the Postgres services must be running. On a new host:
#   copy deploy/.env from the backup first, run `docker compose -f <compose> up -d`,
#   then restore.
#
# deploy/.env y config/ NO se pisan: si difieren, quedan como *.from-backup para
# que los compares tú. / deploy/.env and config/ are NOT overwritten: if they
# differ they are saved as *.from-backup for you to compare.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
ENV_FILE="$HERE/.env"
COMPOSE="$HERE/docker-compose.install.yml"
ARCHIVE=""
L="${WOLF_LANG:-es}"

m() { if [ "$L" = "en" ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
log()  { printf '\033[1;36m[restore]\033[0m %s\n' "$(m "$1" "$2")"; }
warn() { printf '\033[1;33m[restore]\033[0m %s\n' "$(m "$1" "$2")" >&2; }
die()  { printf '\033[1;31m[restore]\033[0m %s\n' "$(m "$1" "$2")" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --compose) [ $# -ge 2 ] || die "--compose requiere una ruta." "--compose needs a path."; COMPOSE="$2"; shift 2 ;;
    --lang)    [ $# -ge 2 ] || die "--lang requiere es|en." "--lang needs es|en."; L="$2"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    -*) die "Argumento desconocido: $1" "Unknown argument: $1" ;;
    *)  [ -z "$ARCHIVE" ] || die "Solo un tarball." "Only one tarball."; ARCHIVE="$1"; shift ;;
  esac
done
[ "$L" = "en" ] || L="es"
[ -n "$ARCHIVE" ] || die "Uso: bash deploy/restore.sh <wolf-backup-*.tgz> [--compose <file>]" "Usage: bash deploy/restore.sh <wolf-backup-*.tgz> [--compose <file>]"
[ -f "$ARCHIVE" ] || die "No existe el tarball: $ARCHIVE" "Tarball not found: $ARCHIVE"
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

# ── preflight ────────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "Falta Docker." "Docker is missing."
if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "Falta Docker Compose (docker compose / docker-compose)." "Docker Compose (docker compose / docker-compose) is missing."; fi
docker info >/dev/null 2>&1 || die "El demonio de Docker no responde." "The Docker daemon is not responding."
[ -f "$COMPOSE" ] || die "No existe el compose: $COMPOSE" "Compose file not found: $COMPOSE"
COMPOSE="$(cd "$(dirname "$COMPOSE")" && pwd)/$(basename "$COMPOSE")"
dc() { ( cd "$REPO" && $DC -f "$COMPOSE" "$@" ); }
svc_exists()  { dc config --services 2>/dev/null | grep -qx "$1"; }
svc_running() { local c; c="$(dc ps -q "$1" 2>/dev/null | head -n1)"; [ -n "$c" ] && [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = "true" ]; }

# Seguridad del tarball: sin rutas absolutas ni '..'. / Tarball safety: no absolute paths or '..'.
if tar tzf "$ARCHIVE" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  die "El tarball tiene rutas inseguras (absolutas o '..'). No se restaura." "The tarball has unsafe paths (absolute or '..'). Not restoring."
fi

umask 077
# Extracción junto al tarball (visible para los bind-mounts de Docker Desktop).
WORK="$(mktemp -d "$(dirname "$ARCHIVE")/.wolf-restore.XXXXXX")"
STOPPED=()
cleanup() {
  rc=$?
  for s in "${STOPPED[@]:-}"; do [ -n "$s" ] && dc start "$s" >/dev/null 2>&1 || true; done
  rm -rf "$WORK"
  [ $rc -eq 0 ] || warn "Restauración INTERRUMPIDA. Revisa el estado; puedes repetirla (es idempotente)." "Restore INTERRUPTED. Check the state; you can re-run it (it is idempotent)."
}
trap cleanup EXIT
tar xzf "$ARCHIVE" -C "$WORK"
D="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d -name 'wolf-backup-*' | head -n1)"
[ -n "$D" ] && [ -f "$D/MANIFEST.txt" ] || die "El tarball no es un respaldo de backup.sh (falta MANIFEST.txt)." "Not a backup.sh tarball (MANIFEST.txt missing)."

log "Respaldo: $(basename "$D")" "Backup: $(basename "$D")"
sed 's/^/    /' "$D/MANIFEST.txt"

# ── qué se va a restaurar y qué falta ────────────────────────────────────────
PLAN=()  # "servicio:archivo"
for pair in "db:db-wolf.sql.gz" "sonar-db:db-sonar.sql.gz" "defectdojo-db:db-defectdojo.sql.gz"; do
  svc="${pair%%:*}"; file="${pair#*:}"
  [ -f "$D/$file" ] || continue
  svc_exists "$svc" || { warn "Servicio '$svc' no existe en $(basename "$COMPOSE") — se omite." "Service '$svc' is not in $(basename "$COMPOSE") — skipped."; continue; }
  svc_running "$svc" || die "El servicio '$svc' no está corriendo. Levanta el stack primero: $DC -f $COMPOSE up -d" \
                            "Service '$svc' is not running. Bring the stack up first: $DC -f $COMPOSE up -d"
  PLAN+=("$pair")
done
[ "${#PLAN[@]}" -gt 0 ] || die "Nada que restaurar en este compose." "Nothing to restore for this compose."
HAS_VOLS="no"; ls "$D/volumes"/*.tgz >/dev/null 2>&1 && HAS_VOLS="yes"

# ── confirmación y/N ─────────────────────────────────────────────────────────
echo
warn "ESTO SOBRESCRIBE: ${PLAN[*]%%:*}$([ "$HAS_VOLS" = yes ] && echo ' + volúmenes de SonarQube')." \
     "THIS OVERWRITES: ${PLAN[*]%%:*}$([ "$HAS_VOLS" = yes ] && echo ' + SonarQube volumes')."
[ -t 0 ] || die "Sin terminal interactiva: no se puede confirmar. Abortando." "No interactive terminal: cannot confirm. Aborting."
printf '\033[1;33m?\033[0m %s [y/N] ' "$(m "¿Continuar y sobrescribir los datos actuales?" "Continue and overwrite the current data?")"
read -r reply || reply=""
case "$reply" in [yYsS]|[yY][eE][sS]|[sS][iI]) : ;; *) log "Cancelado. No se tocó nada." "Cancelled. Nothing was touched."; exit 0 ;; esac

# ── detener los consumidores de las BD (las BD siguen arriba) ────────────────
for s in backend scheduler bot sonarqube defectdojo-nginx defectdojo-uwsgi defectdojo-celeryworker defectdojo-celerybeat; do
  if svc_exists "$s" && svc_running "$s"; then
    log "Deteniendo $s …" "Stopping $s …"
    dc stop "$s" >/dev/null; STOPPED+=("$s")
  fi
done

# ── restaurar dumps (credenciales leídas dentro del contenedor) ──────────────
for pair in "${PLAN[@]}"; do
  svc="${pair%%:*}"; file="${pair#*:}"
  log "Restaurando '$svc' desde $file …" "Restoring '$svc' from $file …"
  gunzip -c "$D/$file" | dc exec -T "$svc" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null'
done

# ── restaurar volúmenes de SonarQube (si vienen) ─────────────────────────────
vol_of() {
  local cid; cid="$(dc ps -aq "$1" 2>/dev/null | head -n1)"
  [ -n "$cid" ] || return 1
  docker inspect -f '{{range .Mounts}}{{if eq .Destination "'"$2"'"}}{{.Name}}{{end}}{{end}}' "$cid"
}
if [ "$HAS_VOLS" = "yes" ]; then
  if svc_exists sonarqube; then
    docker image inspect busybox >/dev/null 2>&1 || docker pull -q busybox >/dev/null
    for pair in "sonarqube_data:/opt/sonarqube/data" "sonarqube_ext:/opt/sonarqube/extensions"; do
      label="${pair%%:*}"; dest="${pair#*:}"
      [ -f "$D/volumes/$label.tgz" ] || continue
      vol="$(vol_of sonarqube "$dest" || true)"
      [ -n "$vol" ] || { warn "No pude resolver el volumen $label; se omite." "Could not resolve volume $label; skipped."; continue; }
      log "Restaurando volumen $label ($vol) …" "Restoring volume $label ($vol) …"
      docker run --rm -v "$vol":/dst -v "$D/volumes":/in:ro busybox \
        sh -c 'find /dst -mindepth 1 -maxdepth 1 -exec rm -rf {} + && tar xzf "/in/'"$label"'.tgz" -C /dst'
    done
  else
    warn "El respaldo trae volúmenes de SonarQube pero este compose no tiene 'sonarqube'." "The backup has SonarQube volumes but this compose has no 'sonarqube'."
  fi
fi

# ── deploy/.env y config: nunca se pisan ─────────────────────────────────────
keep_or_stage() { # origen destino
  local src="$1" dst="$2"
  [ -f "$src" ] || return 0
  if [ ! -f "$dst" ]; then
    cp "$src" "$dst"; [ "$dst" = "$ENV_FILE" ] && chmod 600 "$dst"
    log "Creado $(basename "$dst") desde el respaldo." "Created $(basename "$dst") from the backup."
  elif ! cmp -s "$src" "$dst"; then
    cp "$src" "$dst.from-backup"; chmod 600 "$dst.from-backup"
    warn "$(basename "$dst") ACTUAL difiere del respaldado → guardado como $dst.from-backup (compara; ojo con WOLF_MFA_FERNET_KEY)." \
         "CURRENT $(basename "$dst") differs from the backed-up one → saved as $dst.from-backup (compare; mind WOLF_MFA_FERNET_KEY)."
  fi
}
keep_or_stage "$D/deploy.env" "$ENV_FILE"
for f in projects.yaml wolf-ignore.yaml; do keep_or_stage "$D/config/$f" "$REPO/config/$f"; done

# ── reiniciar lo detenido (también lo hace el trap si algo falla) ────────────
for s in "${STOPPED[@]:-}"; do [ -n "$s" ] && dc start "$s" >/dev/null; done
STOPPED=()
log "Restauración terminada. Verifica: bash deploy/smoke.sh --compose $COMPOSE" \
    "Restore finished. Verify: bash deploy/smoke.sh --compose $COMPOSE"
