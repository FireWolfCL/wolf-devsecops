# Pipelines de escaneo de ejemplo

Workflows listos para copiar a **tu** proyecto, uno por tipo de escaneo. Corren en
tu CI y suben a DefectDojo; el panel Wolf DevSecOps los lee y los agrupa por
categoría (ver [`docs/PIPELINES.md`](../../docs/PIPELINES.md)).

| Archivo | Categoría | Escáner |
|---|---|---|
| `github/secrets-gitleaks.yml` | secrets | Gitleaks (historial git) |
| `github/sca-grype.yml` | sca | Grype + Syft (SBOM) |
| `github/sast-semgrep.yml` | sast | Semgrep |
| `github/iac-checkov.yml` | iac | Checkov |
| `github/dast-zap.yml` | dast | OWASP ZAP |

## Uso

1. Copia el/los `.yml` que quieras a `.github/workflows/` de tu repo.
2. Define en tu repo: secrets **`DEFECTDOJO_URL`** y **`DEFECTDOJO_TOKEN`**, y la
   variable **`DEFECTDOJO_PRODUCT`** (el mismo `product_name` que configuraste en el
   panel para ese proyecto).
3. Sin esos valores, el escáner igual corre pero **omite** la subida (no falla).

Versiones fijadas a propósito (sin `@latest`). El reporte **no** hace fallar el
build por hallazgos; la política de release la gobierna tu pipeline, no el escáner.

> GitLab/Jenkins/otros: el patrón es idéntico — correr el escáner y hacer
> `POST /api/v2/reimport-scan/` con el `scan_type` correcto. Ver `docs/PIPELINES.md`.
