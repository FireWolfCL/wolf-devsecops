# Pipelines de escaneo → DefectDojo → Wolf DevSecOps

> Cómo alimentar el pilar **Hallazgos** del panel con escáneres reales. Wolf
> DevSecOps **no corre estos escáneres** (ADR-001): corren en **tu CI** y reportan
> a **DefectDojo**; el panel los **lee** y los agrupa por tipo (6.2):
> `secrets · sast · sca · container · iac · dast`.

La única excepción es **Trivy**, que el instalador puede correr como conveniencia
de arranque (ver `INSTALL.md` y `deploy/scan-target.sh`). Todo lo demás vive en el
CI del proyecto auditado.

## El patrón, en una frase

Cada job: **(1)** corre un escáner y produce un reporte en un formato que DefectDojo
entiende → **(2)** lo sube a DefectDojo con `reimport-scan` (dedup + cierre de lo
resuelto) indicando su `scan_type` → **(3)** el panel lo muestra, categorizado.

El paso de subida es el mismo para todos (cambia `scan_type` y el archivo):

```bash
curl -sS -X POST "$DEFECTDOJO_URL/api/v2/reimport-scan/" \
  -H "Authorization: Token $DEFECTDOJO_TOKEN" \
  -F "scan_type=<SCAN_TYPE>" \
  -F "file=@<reporte>" \
  -F "product_name=<TU_PRODUCTO>" \
  -F "engagement_name=CI" \
  -F "auto_create_context=true" \
  -F "active=true" -F "verified=false" \
  -F "close_old_findings=true" -F "do_not_reactivate=false"
```

- `DEFECTDOJO_URL` / `DEFECTDOJO_TOKEN`: **secrets del CI**, nunca en el YAML.
- `product_name`: el **mismo** nombre que configuraste en el panel para ese proyecto
  (Admin → Integraciones → DefectDojo → `product`). Así el panel lo correlaciona.
- `reimport-scan` con `close_old_findings=true` es idempotente: re-subir cierra lo
  que ya no aparece y no acumula (mismo criterio que `deploy/import-trivy-to-dojo.sh`).

## Qué escáner para qué categoría

| Categoría (panel) | Escáner recomendado | `scan_type` de DefectDojo | Formato |
|---|---|---|---|
| **secrets** | **Gitleaks** (historial git completo) | `Gitleaks Scan` | `gitleaks detect --report-format json` |
| **sast** | **Semgrep** | `Semgrep JSON Report` | `semgrep --json` |
| **sca** | **Grype** (+ **Syft** para el SBOM) | `Anchore Grype` | `grype -o json` |
| **container** | **Trivy** sobre la imagen | `Trivy Scan` | `trivy image -f json` |
| **iac** | **Checkov** (o `trivy config`) | `Checkov Scan` | `checkov -o json` |
| **dast** | **OWASP ZAP** (entorno desplegado) | `ZAP Scan` | reporte XML de ZAP |

> El panel deriva la categoría del **nombre del `scan_type`** (conector
> `defectdojo.py`). Si usas otro escáner, cualquiera cuyo nombre contenga una
> palabra conocida (p. ej. "semgrep", "grype", "gitleaks", "zap", "checkov") cae en
> la categoría correcta; el resto queda en `other`.

## Ejemplos listos para copiar

Workflows de GitHub Actions en **[`deploy/ci-examples/github/`](../deploy/ci-examples/github/)**,
uno por categoría. Cada uno:

- fija versiones (sin `@latest`/`@main`),
- corre el escáner y **no falla el build** por hallazgos (eso lo gobierna tu política
  de release, no el reporte),
- sube a DefectDojo solo si `DEFECTDOJO_URL`/`DEFECTDOJO_TOKEN` están configurados.

Define en tu repo los secrets `DEFECTDOJO_URL`, `DEFECTDOJO_TOKEN` y la variable
`DEFECTDOJO_PRODUCT` (el `product_name` del panel). Prioridad de adopción sugerida:
**Gitleaks → Grype/Syft → Semgrep → Checkov → ZAP** (de lo más barato y de mayor
señal a lo más caro).

## Gestión de hallazgos: falsos positivos y riesgo aceptado

No inventes un sistema de exclusiones propio. Marca el estado **en DefectDojo**
(False Positive / Risk Accepted / Out of Scope / Mitigated): el panel ya cuenta solo
los **activos**, así que un hallazgo aceptado deja de restar en el score. Para
escáneres sin estado (Gitleaks en CI), usa su archivo de ignore versionado
(`.gitleaksignore`) con justificación en el commit. (Pendiente de producto: la vista
de "riesgos aceptados" y el `wolf-ignore.yaml`, ver `MEJORAS.md` 6.3.)
