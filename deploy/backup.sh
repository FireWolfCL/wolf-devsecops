#!/usr/bin/env bash
# =============================================================================
# backup.sh — respaldo del stack Wolf DevSecOps / Wolf DevSecOps stack backup  (ES + EN)
# -----------------------------------------------------------------------------
# Empaqueta en UN tarball  wolf-backup-YYYYmmdd-HHMM.tgz :
#   - pg_dump de los 3 Postgres:  db (BD del backend) · sonar-db · defectdojo-db
#   - deploy/.env (permisos 600) y config/projects.yaml (+ wolf-ignore.yaml si existe)
#   - opcional (--volumes): volúmenes sonarqube_data y sonarqube_ext
# NO imprime secretos. El tarball CONTIENE secretos (deploy/.env): protégelo (600).
#
# Packs ONE tarball wolf-backup-YYYYmmdd-HHMM.tgz with: pg_dump of the 3 Postgres
# (db = backend DB, sonar-db, defectdojo-db), deploy/.env (600) and config/, plus
# optionally (--volumes) the sonarqube_data / sonarqube_ext volumes.
# Prints no secrets. The tarball DOES contain secrets (deploy/.env): protect it.
#
#   bash deploy/backup.sh                              # compose install.yml, ./backups
#   bash deploy/backup.sh --out /mnt/respaldos         # destino / destination
#   bash deploy/backup.sh --compose deploy/docker-compose.lite.yml
#   bash deploy/backup.sh --volumes                    # + volúmenes de SonarQube
#   WOLF_LANG=en bash deploy/backup.sh                 # mensajes en inglés / English messages
#
# Requiere el stack LEVANTADO (pg_dump corre dentro de los contenedores).
# Requires the stack to be UP (pg_dump runs inside the containers).
# Los volúmenes dd_redis (broker/cola de DefectDojo) y trivy_* (cache/reportes)
# NO se respaldan: son regenerables. / dd_redis and trivy_* are regenerable: not backed up.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
ENV_FILE="$HERE/.env"
COMPOSE="$HERE/docker-compose.install.yml"
OUT="./backups"
WITH_VOLUMES="no"
L="${WOLF_LANG:-es}"

m() { if [ "$L" = "en" ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
log()  { printf '\033[1;36m[backup]\033[0m %s\n' "$(m "$1" "$2")"; }
warn() { printf '\033[1;33m[backup]\033[0m %s\n' "$(m "$1" "$2")" >&2; }
die()  { printf '\033[1;31m[backup]\033[0m %s\n' "$(m "$1" "$2")" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --compose) [ $# -ge 2 ] || die "--compose requiere una ruta." "--compose needs a path."; COMPOSE="$2"; shift 2 ;;
    --out)     [ $# -ge 2 ] || die "--out requiere un directorio." "--out needs a directory."; OUT="$2"; shift 2 ;;
    --volumes) WITH_VOLUMES="yes"; shift ;;
    --lang)    [ $# -ge 2 ] || die "--lang requiere es|en." "--lang needs es|en."; L="$2"; shift 2 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) die "Argumento desconocido: $1" "Unknown argument: $1" ;;
  esac
done
[ "$L" = "en" ] || L="es"

# ── preflight ────────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "Falta Docker." "Docker is missing."
if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "Falta Docker Compose (docker compose / docker-compose)." "Docker Compose (docker compose / docker-compose) is missing."; fi
docker info >/dev/null 2>&1 || die "El demonio de Docker no responde." "The Docker daemon is not responding."
[ -f "$COMPOSE" ] || die "No existe el compose: $COMPOSE" "Compose file not found: $COMPOSE"
COMPOSE="$(cd "$(dirname "$COMPOSE")" && pwd)/$(basename "$COMPOSE")"
dc() { ( cd "$REPO" && $DC -f "$COMPOSE" "$@" ); }

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
STAMP="$(date +%Y%m%d-%H%M)"
NAME="wolf-backup-$STAMP"
umask 077
# El staging va dentro de OUT (no en /tmp): los bind-mounts de Docker Desktop lo ven.
STAGE="$(mktemp -d "$OUT/.wolf-backup-stage.XXXXXX")"
RESTART_SONAR="no"
cleanup() {
  rc=$?
  rm -rf "$STAGE"
  if [ "$RESTART_SONAR" = "yes" ]; then dc start sonarqube >/dev/null 2>&1 || true; fi
  [ $rc -eq 0 ] || warn "Respaldo ABORTADO: no se generó tarball parcial." "Backup ABORTED: no partial tarball was produced."
}
trap cleanup EXIT
D="$STAGE/$NAME"; mkdir -p "$D/config" "$D/volumes"

svc_exists()  { dc config --services 2>/dev/null | grep -qx "$1"; }
svc_running() { [ -n "$(dc ps -q "$1" 2>/dev/null)" ] && [ "$(docker inspect -f '{{.State.Running}}' "$(dc ps -q "$1" | head -n1)" 2>/dev/null)" = "true" ]; }

# ── 1) pg_dump de cada Postgres ──────────────────────────────────────────────
# Las credenciales se leen DENTRO del contenedor (POSTGRES_USER/PASSWORD/DB de su
# propio entorno): no pasan por la línea de comandos del host ni se imprimen.
# Credentials are read INSIDE the container: they never hit the host command line.
DUMPED=()
dump_pg() { # servicio  archivo
  local svc="$1" file="$2"
  if ! svc_exists "$svc"; then
    warn "Servicio '$svc' no existe en $(basename "$COMPOSE") — se omite." "Service '$svc' is not in $(basename "$COMPOSE") — skipped."
    return 0
  fi
  svc_running "$svc" || die "El servicio '$svc' no está corriendo. Levanta el stack: $DC -f $COMPOSE up -d" \
                            "Service '$svc' is not running. Bring the stack up: $DC -f $COMPOSE up -d"
  log "pg_dump de '$svc' …" "pg_dump of '$svc' …"
  dc exec -T "$svc" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --clean --if-exists' \
    | gzip -c > "$D/$file"
  [ -s "$D/$file" ] || die "El dump de '$svc' salió vacío." "The dump of '$svc' is empty."
  DUMPED+=("$svc")
}
if svc_exists db; then
  dump_pg db db-wolf.sql.gz
else
  warn "No hay servicio 'db': si el backend usa SQLite (WOLF_DATABASE_URL sin definir), respalda el archivo/volumen de la BD aparte." \
       "No 'db' service: if the backend uses SQLite (WOLF_DATABASE_URL unset), back up that DB file/volume separately."
fi
dump_pg sonar-db db-sonar.sql.gz
dump_pg defectdojo-db db-defectdojo.sql.gz
[ "${#DUMPED[@]}" -gt 0 ] || die "No se respaldó ninguna base de datos." "No database was backed up."

# ── 2) deploy/.env + config (sin imprimir contenido) ─────────────────────────
if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "$D/deploy.env"; chmod 600 "$D/deploy.env"
  log "deploy/.env incluido (600)." "deploy/.env included (600)."
else
  warn "No hay deploy/.env: el respaldo NO podrá reconstruir las claves (incluida WOLF_MFA_FERNET_KEY)." \
       "No deploy/.env: the backup will NOT be able to rebuild the keys (including WOLF_MFA_FERNET_KEY)."
fi
for f in projects.yaml wolf-ignore.yaml; do
  [ -f "$REPO/config/$f" ] && cp "$REPO/config/$f" "$D/config/$f"
done

# ── 3) volúmenes de SonarQube (opcional) ─────────────────────────────────────
# Se resuelve el nombre REAL del volumen desde los mounts del contenedor.
vol_of() { # servicio  ruta-destino-en-contenedor  → nombre del volumen
  local cid; cid="$(dc ps -aq "$1" 2>/dev/null | head -n1)"
  [ -n "$cid" ] || return 1
  docker inspect -f '{{range .Mounts}}{{if eq .Destination "'"$2"'"}}{{.Name}}{{end}}{{end}}' "$cid"
}
VOLS=()
if [ "$WITH_VOLUMES" = "yes" ]; then
  if svc_exists sonarqube; then
    V_DATA="$(vol_of sonarqube /opt/sonarqube/data || true)"
    V_EXT="$(vol_of sonarqube /opt/sonarqube/extensions || true)"
    if [ -n "$V_DATA" ] && [ -n "$V_EXT" ]; then
      # Consistencia: SonarQube (Elasticsearch embebido) se detiene durante la copia.
      if svc_running sonarqube; then
        warn "Deteniendo SonarQube unos segundos para copiar sus volúmenes (se reinicia solo)." \
             "Stopping SonarQube for a few seconds to copy its volumes (it restarts by itself)."
        RESTART_SONAR="yes"; dc stop sonarqube >/dev/null
      fi
      docker image inspect busybox >/dev/null 2>&1 || docker pull -q busybox >/dev/null
      for pair in "sonarqube_data:$V_DATA" "sonarqube_ext:$V_EXT"; do
        label="${pair%%:*}"; vol="${pair#*:}"
        log "Volumen $label ($vol) …" "Volume $label ($vol) …"
        docker run --rm -v "$vol":/src:ro -v "$D/volumes":/out busybox tar czf "/out/$label.tgz" -C /src .
        VOLS+=("$label")
      done
      if [ "$RESTART_SONAR" = "yes" ]; then dc start sonarqube >/dev/null; RESTART_SONAR="no"; fi
    else
      warn "No pude resolver los volúmenes de sonarqube (¿contenedor creado?). Se omiten." \
           "Could not resolve the sonarqube volumes (container created?). Skipped."
    fi
  else
    warn "El compose no tiene 'sonarqube': --volumes no aplica." "The compose has no 'sonarqube': --volumes does not apply."
  fi
fi

# ── 4) manifiesto (sin secretos) + empaquetado atómico ───────────────────────
{
  echo "wolf-backup: $NAME"
  echo "created_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "compose: $(basename "$COMPOSE")"
  echo "databases: ${DUMPED[*]}"
  echo "volumes: ${VOLS[*]:-none}"
  echo "env_included: $([ -f "$D/deploy.env" ] && echo yes || echo no)"
} > "$D/MANIFEST.txt"

FINAL="$OUT/$NAME.tgz"
tar czf "$STAGE/$NAME.tgz" -C "$STAGE" "$NAME"
mv "$STAGE/$NAME.tgz" "$FINAL"
chmod 600 "$FINAL"

SIZE="$(du -h "$FINAL" | cut -f1)"
log "Listo: $FINAL ($SIZE)" "Done: $FINAL ($SIZE)"
warn "El tarball contiene deploy/.env (secretos): guárdalo cifrado / fuera del servidor. Restaurar: bash deploy/restore.sh $FINAL" \
     "The tarball contains deploy/.env (secrets): store it encrypted / off-host. Restore: bash deploy/restore.sh $FINAL"
