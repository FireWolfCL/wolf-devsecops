#!/usr/bin/env bash
# =============================================================================
# setup-harbor.sh — genera Harbor desde 0 con su instalador OFICIAL (online).
# -----------------------------------------------------------------------------
# Harbor no se despliega con un compose a mano: su herramienta `prepare` genera
# el compose y la config a partir de harbor.yml. Este script automatiza eso:
#   1) descarga el online installer (versión fijada),
#   2) escribe harbor.yml (HTTP, puerto del host, pass admin desde deploy/.env),
#   3) corre ./prepare y `docker compose up -d`.
#
# Publicado en http://localhost:${HARBOR_HTTP_PORT:-8085}. Wolf lo alcanza por
# host.docker.internal:ese-puerto. admin / HARBOR_ADMIN_PASSWORD (deploy/.env).
#
#   Uso:  bash deploy/harbor/setup-harbor.sh
# =============================================================================
set -eu
HARBOR_VERSION="v2.11.1"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HERE/../.env"
WORK="$HERE/harbor"          # dir de trabajo (gitignored)

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
PORT="${HARBOR_HTTP_PORT:-8085}"
ADMIN_PW="${HARBOR_ADMIN_PASSWORD:?falta HARBOR_ADMIN_PASSWORD en deploy/.env}"

mkdir -p "$WORK"
cd "$WORK"

TGZ="harbor-online-installer-${HARBOR_VERSION}.tgz"
if [ ! -f "$TGZ" ]; then
  echo "[harbor] descargando installer ${HARBOR_VERSION}…"
  curl -fL -o "$TGZ" \
    "https://github.com/goharbor/harbor/releases/download/${HARBOR_VERSION}/${TGZ}"
fi
# Extrae (crea ./harbor/).
tar xzf "$TGZ"
cd harbor

# harbor.yml a partir de la PLANTILLA OFICIAL (trae todos los campos que `prepare`
# exige, p. ej. job_loggers). Solo ajustamos lo mínimo para HTTP local.
cp harbor.yml.tmpl harbor.yml
# HTTP-only: elimina el bloque https (si no, prepare exige certificados).
sed -i.bak '/^https:/,/private_key:/d' harbor.yml
sed -i.bak "s|^hostname:.*|hostname: localhost|" harbor.yml
sed -i.bak "s|^  port: 80|  port: ${PORT}|" harbor.yml
sed -i.bak "s|^harbor_admin_password:.*|harbor_admin_password: ${ADMIN_PW}|" harbor.yml
sed -i.bak "s|^data_volume:.*|data_volume: ${WORK}/data|" harbor.yml
grep -q "^external_url:" harbor.yml || printf '\nexternal_url: http://localhost:%s\n' "${PORT}" >> harbor.yml
rm -f harbor.yml.bak

echo "[harbor] generando config (prepare)… incluye el Trivy-adapter para escaneo de imágenes."
# En Docker Desktop no se necesita sudo (prepare corre en un contenedor).
./prepare --with-trivy

echo "[harbor] levantando Harbor…"
docker compose up -d

echo "[harbor] listo → http://localhost:${PORT}  (admin / HARBOR_ADMIN_PASSWORD)"
echo "[harbor] luego corre: bash deploy/provision-sources.sh  (crea el proyecto y pone Harbor en verde)"
