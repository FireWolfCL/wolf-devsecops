# Changelog

Todas las versiones notables de Wolf DevSecOps. Formato basado en
[Conventional Commits](https://www.conventionalcommits.org/); se genera con `git cliff`.

## [No publicado]

### Features
- Consola agregadora de postura de seguridad de código: SonarQube, DefectDojo,
  Harbor/Trivy y GitHub en un solo panel (rúbrica v1.0).
- Reviewer de código con IA (opcional, gated) y remediación asistida que abre
  branch/PR draft y nunca mergea.
- i18n en 5 idiomas (es/en/ru/zh/ja).

### Seguridad
- Pipeline CI/Release con gates: lint+tests, SAST (Bandit), escaneo de
  dependencias (pip-audit + npm audit), secretos (gitleaks), SBOM (syft) y
  reversibilidad de migraciones (Alembic).
