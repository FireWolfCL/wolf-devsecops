#!/usr/bin/env bash
# =============================================================================
# connect-projects.sh — conecta proyectos a Wolf DevSecOps con datos Trivy REALES.
# -----------------------------------------------------------------------------
# Escanea cada entrada de deploy/projects.list:
#   - repo LOCAL (~/<nombre>)  -> `trivy fs`
#   - URL git (remoto)         -> `trivy repo`
# publica el reporte JSON en el volumen que sirve el servicio `trivy`, regenera
# config/projects.yaml y reinicia el backend.
#
# AGREGAR cualquiera (fácil):  bash deploy/connect-projects.sh <nombre-o-URL> [...]
#   (se añade a projects.list y se re-escanea todo; idempotente).
# =============================================================================
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LIST="$HERE/projects.list"
COMPOSE="$HERE/docker-compose.install.yml"
# Constantes compartidas (TRIVY_IMG, RVOL, CVOL, TRIVY_SKIP): ver scan-common.sh.
# shellcheck disable=SC1091
. "$HERE/scan-common.sh"

# Easy-add: cada argumento se agrega a la lista (sin duplicar).
for extra in "$@"; do
  grep -qxF "$extra" "$LIST" 2>/dev/null || { echo "$extra" >> "$LIST"; echo "+ agregado: $extra"; }
done

slug() { echo "$1" | sed 's#.*/##; s/\.git$//' | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]\+/-/g; s/^-//; s/-$//'; }

scan_local() {
  local n="$1" key ign=""; key="$(slug "$n")"
  if [ ! -d "$HOME/$n" ]; then echo "  skip $n (no existe en ~)"; return 0; fi
  # Waivers scoped por proyecto: si el repo trae su propio .trivyignore (riesgos
  # aceptados y documentados), se respeta SOLO para ese proyecto, sin contaminar
  # el escaneo de los demás. Trivy fs no lo auto-detecta (cwd=/), hay que pasarlo.
  [ -f "$HOME/$n/.trivyignore" ] && ign="--ignorefile /src/$n/.trivyignore"
  if docker run --rm -v "$HOME:/src:ro" -v "$RVOL:/reports" -v "$CVOL:/root/.cache" "$TRIVY_IMG" \
       fs --quiet --scanners vuln,secret,misconfig --skip-dirs "$TRIVY_SKIP" $ign --format json -o "/reports/$key.json" "/src/$n" >/dev/null 2>&1; then
    echo "  fs   $n -> $key.json"
  else echo "  (falló fs $n)"; fi
}
scan_remote() {
  local u="$1" key; key="$(slug "$u")"
  if docker run --rm -v "$RVOL:/reports" -v "$CVOL:/root/.cache" "$TRIVY_IMG" \
       repo --quiet --scanners vuln,secret,misconfig --skip-dirs "$TRIVY_SKIP" --format json -o "/reports/$key.json" "$u" >/dev/null 2>&1; then
    echo "  repo $u -> $key.json"
  else echo "  (falló repo $u)"; fi
}

echo "== Escaneando proyectos (Trivy) =="
while IFS= read -r raw; do
  line="${raw%%#*}"; line="$(echo "$line" | xargs || true)"; [ -z "$line" ] && continue
  case "$line" in http*|git@*) scan_remote "$line";; *) scan_local "$line";; esac
done < "$LIST"

echo "== Regenerando config/projects.yaml =="
python3 "$HERE/gen-projects.py" "$LIST" "$ROOT/config/projects.yaml"

echo "== Re-sincronizando config a la BD y reiniciando backend =="
# El bootstrap YAML→BD solo corre con BD vacía; sync-config fuerza el re-sync.
docker compose -f "$COMPOSE" exec -T backend wolf-devsecops sync-config >/dev/null 2>&1 \
  || echo "  (corre manual: docker compose -f $COMPOSE exec backend wolf-devsecops sync-config)"
docker compose -f "$COMPOSE" restart backend >/dev/null 2>&1 \
  || echo "  (reinicia manual: docker compose -f $COMPOSE restart backend)"
echo "Listo. Refresca http://localhost:8088"
