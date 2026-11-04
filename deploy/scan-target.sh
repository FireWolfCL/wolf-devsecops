#!/usr/bin/env bash
# =============================================================================
# scan-target.sh — escanea UN objetivo arbitrario (repo local y/o imagen) y lo
# deja visible en el panel con su score real (Trivy + Sonar).
# -----------------------------------------------------------------------------
#   bash deploy/scan-target.sh --repo <ruta> [--key <k>] [--name <n>]
#   bash deploy/scan-target.sh --image <imagen[:tag]> [--key <k>] [--name <n>]
#   (ambos a la vez: el repo usa <key>, la imagen usa <key>-image)
#
# Opciones:
#   --no-sonar   solo Trivy            --no-trivy   solo Sonar (requiere --repo)
#   --no-sync    no sincroniza/reinicia el backend (el caller lo hará)
#   --config <yaml>        projects.yaml a actualizar (def. config/projects.yaml)
#   --compose-file <yml>   compose del stack (def. $WOLF_COMPOSE_FILE o install.yml)
#
# Reglas: el código del operador se monta SOLO-LECTURA; Trivy fs siempre con
# --skip-dirs y respetando el .trivyignore del repo; ningún secreto se imprime.
# ADR-001: el panel/backend no escanea; esto corre en el bundle (conveniencia).
# Sale 0 si TODO lo pedido funcionó; 1 si algo falló (el instalador lo toma como aviso).
# =============================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENV_FILE="$HERE/.env"
# shellcheck disable=SC1091
. "$HERE/scan-common.sh"

log()  { printf '\033[1;36m[scan]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[scan]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[scan]\033[0m %s\n' "$*" >&2; exit 1; }

REPO=""; IMAGE=""; KEY=""; NAME=""; DO_SONAR="yes"; DO_TRIVY="yes"; DO_SYNC="yes"
YAML="$ROOT/config/projects.yaml"
COMPOSE="${WOLF_COMPOSE_FILE:-$HERE/docker-compose.install.yml}"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)         [ $# -ge 2 ] || die "--repo requiere una ruta."; REPO="$2"; shift 2 ;;
    --image)        [ $# -ge 2 ] || die "--image requiere un nombre."; IMAGE="$2"; shift 2 ;;
    --key)          [ $# -ge 2 ] || die "--key requiere un valor."; KEY="$2"; shift 2 ;;
    --name)         [ $# -ge 2 ] || die "--name requiere un valor."; NAME="$2"; shift 2 ;;
    --config)       [ $# -ge 2 ] || die "--config requiere una ruta."; YAML="$2"; shift 2 ;;
    --compose-file) [ $# -ge 2 ] || die "--compose-file requiere una ruta."; COMPOSE="$2"; shift 2 ;;
    --no-sonar)     DO_SONAR="no"; shift ;;
    --no-trivy)     DO_TRIVY="no"; shift ;;
    --no-sync)      DO_SYNC="no"; shift ;;
    -h|--help)      sed -n '2,22p' "$0"; exit 0 ;;
    *) die "argumento desconocido: $1" ;;
  esac
done
export WOLF_COMPOSE_FILE="$COMPOSE"

[ -n "$REPO$IMAGE" ] || die "Indica --repo <ruta> y/o --image <imagen>."
command -v docker >/dev/null 2>&1 || die "Falta Docker."
docker info >/dev/null 2>&1 || die "El demonio de Docker no responde."

# ── validación de entradas ───────────────────────────────────────────────────
if [ -n "$REPO" ]; then
  case "$REPO" in "~"|"~/"*) REPO="$HOME${REPO#\~}" ;; esac
  [ -d "$REPO" ] || die "El repo no existe o no es un directorio: $REPO"
  REPO="$(cd "$REPO" && pwd -P)"
  [ "$REPO" != "/" ] || die "Me niego a escanear '/'."
fi
if [ -n "$IMAGE" ]; then
  printf '%s' "$IMAGE" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/:@-]*$' \
    || die "Nombre de imagen inválido: '$IMAGE'."
fi
if [ -z "$KEY" ]; then
  if [ -n "$REPO" ]; then KEY="$(scan_slug "$REPO")"
  else KEY="$(scan_slug "${IMAGE%%[:@]*}")"; fi
fi
scan_valid_key "$KEY" || die "key inválida ('$KEY'): minúsculas, dígitos, '.', '_' o '-'."
[ -n "$NAME" ] || NAME="$KEY"
IMG_KEY="$KEY"; [ -n "$REPO" ] && IMG_KEY="${KEY}-image"
[ "$DO_TRIVY" = "yes" ] || [ -n "$REPO" ] || die "--no-trivy solo tiene sentido con --repo (Sonar no analiza imágenes)."

docker volume inspect "$RVOL" >/dev/null 2>&1 \
  || die "No existe el volumen $RVOL: levanta el stack primero (install.sh)."

FAIL=0
TMPDIR_SCAN=""
cleanup() { [ -n "$TMPDIR_SCAN" ] && rm -rf "$TMPDIR_SCAN"; }
trap cleanup EXIT

# ── Trivy: repo (fs) ─────────────────────────────────────────────────────────
trivy_repo() {
  local ign=()
  # Trivy fs no auto-detecta .trivyignore (cwd=/): se pasa explícito, solo de este repo.
  [ -f "$REPO/.trivyignore" ] && ign=(--ignorefile /src/.trivyignore)
  log "Trivy fs  $REPO -> $KEY.json"
  docker run --rm -v "$REPO:/src:ro" -v "$RVOL:/reports" -v "$CVOL:/root/.cache" "$TRIVY_IMG" \
    fs --quiet --scanners vuln,secret,misconfig --skip-dirs "$TRIVY_SKIP" \
    ${ign[@]+"${ign[@]}"} --format json -o "/reports/$KEY.json" /src >/dev/null 2>&1
}

# ── Trivy: imagen ────────────────────────────────────────────────────────────
# Si la imagen existe en el daemon local se exporta a tar (:ro) en vez de montar
# docker.sock en el contenedor de Trivy; si no, Trivy la descarga del registry.
trivy_image() {
  log "Trivy image  $IMAGE -> $IMG_KEY.json"
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    TMPDIR_SCAN="$(mktemp -d)"
    docker save -o "$TMPDIR_SCAN/image.tar" "$IMAGE" || return 1
    docker run --rm -v "$TMPDIR_SCAN/image.tar:/image.tar:ro" -v "$RVOL:/reports" -v "$CVOL:/root/.cache" "$TRIVY_IMG" \
      image --quiet --scanners vuln --input /image.tar --format json -o "/reports/$IMG_KEY.json" >/dev/null 2>&1
  else
    docker run --rm -v "$RVOL:/reports" -v "$CVOL:/root/.cache" "$TRIVY_IMG" \
      image --quiet --scanners vuln --format json -o "/reports/$IMG_KEY.json" "$IMAGE" >/dev/null 2>&1
  fi
}

# ── Ejecución ────────────────────────────────────────────────────────────────
if [ "$DO_TRIVY" = "yes" ]; then
  if [ -n "$REPO" ]; then
    if trivy_repo; then
      log "✔ Trivy repo OK."
      scan_helper upsert "$YAML" --key "$KEY" --name "$NAME" --trivy-report "$KEY.json" >/dev/null \
        || { warn "No pude registrar $KEY en $YAML."; FAIL=1; }
    else warn "✗ Trivy falló sobre el repo."; FAIL=1; fi
  fi
  if [ -n "$IMAGE" ]; then
    INAME="$NAME"; [ -n "$REPO" ] && INAME="$NAME (image)"
    if trivy_image; then
      log "✔ Trivy imagen OK."
      scan_helper upsert "$YAML" --key "$IMG_KEY" --name "$INAME" --trivy-report "$IMG_KEY.json" >/dev/null \
        || { warn "No pude registrar $IMG_KEY en $YAML."; FAIL=1; }
    else warn "✗ Trivy falló sobre la imagen (¿existe/pública? las privadas requieren docker login previo y no se soportan aquí)."; FAIL=1; fi
  fi
fi

if [ "$DO_SONAR" = "yes" ] && [ -n "$REPO" ]; then
  if grep -Eq '^SONAR_TOKEN=.+' "$ENV_FILE" 2>/dev/null; then
    bash "$HERE/analyze-sonar.sh" --repo "$REPO" --key "$KEY" --name "$NAME" --no-sync \
      || { warn "✗ Sonar falló sobre el repo."; FAIL=1; }
  else
    warn "SONAR_TOKEN vacío en deploy/.env: se omite Sonar (corre provision-sources.sh y luego este script)."
    FAIL=1
  fi
fi

# ── Sincronizar config → BD y reiniciar backend (una sola vez) ───────────────
if [ "$DO_SYNC" = "yes" ]; then
  if docker compose -f "$COMPOSE" exec -T backend wolf-devsecops sync-config >/dev/null 2>&1; then
    docker compose -f "$COMPOSE" restart backend >/dev/null 2>&1 \
      || warn "reinicia manual: docker compose -f $COMPOSE restart backend"
    log "sync-config OK + backend reiniciado — el panel ya muestra '$KEY'."
  else
    warn "sync-config falló; corre: docker compose -f $COMPOSE exec backend wolf-devsecops sync-config && docker compose -f $COMPOSE restart backend"
    FAIL=1
  fi
fi

[ "$FAIL" -eq 0 ] && log "LISTO." || warn "Terminó con avisos (ver arriba)."
exit "$FAIL"
