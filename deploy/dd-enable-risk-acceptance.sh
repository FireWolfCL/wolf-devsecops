#!/usr/bin/env bash
# =============================================================================
# dd-enable-risk-acceptance.sh — habilita "Simple Risk Acceptance" en TODOS los
# productos de DefectDojo.
# -----------------------------------------------------------------------------
# Por qué: DefectDojo deshabilita por defecto la "aceptación simple de riesgo".
# Sin esto, el write-back de "riesgo aceptado" de Wolf (ADR-021) se rechaza con
# HTTP 400 ("Simple risk acceptance is disabled for this product"), así que
# aprobar un riesgo aceptado falla. Idempotente: corre las veces que quieras,
# y re-córrelo tras importar productos nuevos.
#
# Uso:  bash deploy/dd-enable-risk-acceptance.sh
# =============================================================================
set -eu
CTN="${WOLF_BACKEND_CTN:-wolf-devsecops-backend-1}"

docker exec -i "$CTN" python - <<'PY'
import os, httpx
tok = os.environ.get("DEFECTDOJO_TOKEN", "")
if not tok:
    raise SystemExit("Falta DEFECTDOJO_TOKEN en el backend (revisa deploy/.env).")
H = {"Authorization": f"Token {tok}"}
base = "http://defectdojo-nginx:8080"
r = httpx.get(f"{base}/api/v2/products/?limit=500", headers=H, timeout=30)
r.raise_for_status()
prods = r.json().get("results", [])
done = skipped = failed = 0
for p in prods:
    if p.get("enable_simple_risk_acceptance"):
        skipped += 1
        continue
    resp = httpx.patch(
        f"{base}/api/v2/products/{p['id']}/", headers=H,
        json={"enable_simple_risk_acceptance": True}, timeout=20,
    )
    if resp.status_code in (200, 202):
        done += 1
    else:
        failed += 1
        print(f"  ! producto '{p.get('name')}' (id {p['id']}): HTTP {resp.status_code}")
print(f"Simple Risk Acceptance -> habilitados: {done}, ya estaban: {skipped}, "
      f"fallidos: {failed}  (total {len(prods)} productos)")
PY
