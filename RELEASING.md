# Cómo se publica Wolf DevSecOps

Runbook de release. El pipeline vive en `.github/workflows/ci.yml` (GitHub Actions).

## Qué corre y cuándo

| Disparo | Qué corre |
|---|---|
| Pull request | Todos los gates (`lint-tests`, `sast`, `dependency-scan`, `secret-scan`, `sbom`, `migration-check`). |
| Push a `main` | Ídem. |
| Tag `vX.Y.Z` | Los gates **+** `release` (build + SBOM + publicación). `release` depende de que TODOS los gates pasen. |

### Gates (qué prueba cada uno)
- **lint-tests** — `ruff check` + `pytest` (backend, SQLite async) y `npm ci && npm run typecheck && npm run build` (frontend).
- **sast** — Bandit sobre `backend/wolf_devsecops` (severidad media+). Ruff ya aplica el set `S` de seguridad. *Pendiente JS/TS: añadir semgrep o `eslint-plugin-security`.*
- **dependency-scan** — `pip-audit` (Python) + `npm audit --audit-level=high` (frontend).
- **secret-scan** — `gitleaks` (historial completo). Allowlist de fixtures de test en `.gitleaks.toml`.
- **sbom** — `syft` → CycloneDX JSON, subido como artefacto del run.
- **migration-check** — Alembic `upgrade head → downgrade base → upgrade head`: falla si una migración no es reversible.

## Cortar un release

1. Asegúrate de que `main` está verde (todos los gates).
2. Sube la versión en **ambos** componentes:
   - `backend/pyproject.toml` → `version`
   - `frontend/package.json` → `version`
   (mantener sincronizadas salvo que diverjan a propósito; documentarlo si divergen).
3. Actualiza el changelog (ver abajo).
4. Crea un tag **anotado** en el commit exacto ya verificado por CI y súbelo:
   ```bash
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```
5. El job `release` corre, construye el wheel del backend + el `dist` del frontend, regenera el SBOM y publica el GitHub Release con esos artefactos.

Trazabilidad: cada tag es anotado y apunta al commit que el pipeline construyó y verificó. **No existe release sin un run verde sobre ese commit exacto.**

## Changelog (conventional commits)

Los mensajes siguen [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`…). El changelog se genera con **git-cliff** (config en `cliff.toml`):

```bash
# instalar una vez:  cargo install git-cliff   (o binario pineado de releases)
git cliff --tag v0.2.0 -o CHANGELOG.md
```

## Firma de artefactos (PENDIENTE)

El paso de firma (`cosign`) está en el workflow **comentado con TODO**: aún no hay llave. Antes del primer release a producción, `wolf-security-crypto-agent` debe definir tipo de llave, almacenamiento y rotación, y la llave se inyecta como secreto de CI (`COSIGN_KEY`), nunca en el repo.

## Política de gates

La mecánica vive aquí; la **política** (qué gate puede waivearse, umbrales de severidad, veredicto de release-readiness) la dueña es `wolf-release-devsecops`. Un gate waiveado se deja en el workflow con un comentario `# WAIVED: <motivo, fecha, quién>`, nunca se borra.
