"""Carga y validación de config/projects.yaml → modelos tipados (ARCHITECTURE.md §2).

Regla de oro (ADR-002): el YAML NUNCA contiene secretos. Solo referencia el nombre
de la variable de entorno (`token_env`). Si se detecta un token literal, se rechaza
el arranque: una credencial versionada acaba en producción.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# Claves que delatan un secreto escrito a mano en el YAML (en vez de token_env).
_FORBIDDEN_SECRET_KEYS = {"token", "api_token", "password", "secret"}


class ConfigError(Exception):
    """Config inválida: aborta el arranque con un mensaje claro."""


class SourceConfig(BaseModel):
    url: str  # base de la API (de api_url o base_url en el YAML)
    token_env: str  # NOMBRE de la variable de entorno, nunca el valor
    # Ciphertext Fernet del token guardado en BD (0006, opt-in). Lo porta la config
    # en memoria para que el conector (vía `resolve_token`) le dé PRECEDENCIA sobre la
    # env var, sin tocar la BD en el hot-path. `exclude=True` + `repr=False`: jamás
    # entra en un model_dump(), un log ni un repr — es material de credencial cifrado.
    token_cipher: str | None = Field(default=None, exclude=True, repr=False)
    # Expiración conocida del token (no sensible): la expone el panel de Integraciones.
    token_expires_at: datetime | None = None


class GitHubProjectConfig(BaseModel):
    repo: str  # "owner/name"
    ci_workflow: str = "ci.yml"


class DefectDojoProjectConfig(BaseModel):
    product: str


class SonarProjectConfig(BaseModel):
    project_key: str


class WSSProjectConfig(BaseModel):
    product: str


class HarborProjectConfig(BaseModel):
    # Postura de imágenes de contenedor (CVEs del escaneo de Harbor, que usa Trivy
    # por dentro). `project` = proyecto Harbor; `repository` opcional acota a un repo.
    project: str
    repository: str | None = None


class TrivyProjectConfig(BaseModel):
    # Lector de un reporte Trivy JSON (trivy fs/image --format json) publicado como
    # artefacto. `report_path` es relativo a la URL base de la fuente `trivy`.
    report_path: str


class ProjectConfig(BaseModel):
    key: str
    name: str
    org: str = "Wolf"  # organización dueña (Wolf vs externas como lsst-it)
    github: GitHubProjectConfig | None = None
    defectdojo: DefectDojoProjectConfig | None = None
    sonarqube: SonarProjectConfig | None = None
    wss: WSSProjectConfig | None = None
    harbor: HarborProjectConfig | None = None
    trivy: TrivyProjectConfig | None = None


class AIConfig(BaseModel):
    provider: str  # "local" | "hosted"
    model: str
    base_url: str | None = None
    token_env: str | None = None  # NOMBRE de variable de entorno (solo hosted)


class AppConfig(BaseModel):
    sources: dict[str, SourceConfig] = {}
    projects: list[ProjectConfig] = []
    ai: AIConfig | None = None

    def project(self, key: str) -> ProjectConfig | None:
        return next((p for p in self.projects if p.key == key), None)


def _reject_literal_secrets(raw: dict) -> None:
    for name, source in (raw.get("sources") or {}).items():
        if not isinstance(source, dict):
            continue
        offending = _FORBIDDEN_SECRET_KEYS & set(source)
        if offending:
            raise ConfigError(
                f"La fuente '{name}' define {sorted(offending)} en projects.yaml. "
                "Los secretos nunca van en el YAML: usa 'token_env' con el NOMBRE "
                "de una variable de entorno."
            )


def _source_from_raw(raw: dict) -> SourceConfig:
    url = raw.get("api_url") or raw.get("base_url")
    if not url:
        raise ConfigError("Cada fuente necesita 'api_url' o 'base_url'.")
    token_env = raw.get("token_env")
    if not token_env:
        raise ConfigError("Cada fuente necesita 'token_env' (nombre de variable de entorno).")
    return SourceConfig(url=url, token_env=token_env)


def load_config(path: str | Path) -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            f"No existe {p}. Copia config/projects.yaml.example a config/projects.yaml "
            "y ajústalo (o usa el wizard: wolf-devsecops init)."
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    _reject_literal_secrets(raw)

    sources = {name: _source_from_raw(s) for name, s in (raw.get("sources") or {}).items()}
    projects = [ProjectConfig.model_validate(p) for p in (raw.get("projects") or [])]
    ai = AIConfig.model_validate(raw["ai"]) if raw.get("ai") else None
    return AppConfig(sources=sources, projects=projects, ai=ai)
