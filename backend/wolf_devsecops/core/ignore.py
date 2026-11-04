"""Excepciones versionables por proyecto (MEJORAS 6.3.c).

Para escáneres SIN estados nativos (Trivy suelto, Gitleaks en CI) no existe un
"risk accepted" como el de DefectDojo. `config/wolf-ignore.yaml` cubre ese hueco:
lista, bajo control de versiones, los `{id|cve|rule, motivo, aprobado_por, fecha,
vence}` que el equipo decidió excluir del puntaje, con vencimiento obligatorio por
higiene (una excepción eterna es deuda escondida).

INVARIANTES:
- Solo LECTURA de fuentes (ADR-001): esto no escribe en ninguna herramienta; solo
  excluye del score que Wolf calcula y, aparte, genera `.trivyignore`/`.gitleaksignore`
  (archivos locales para el CI del equipo, no una fuente).
- Una entrada VENCIDA deja de suprimir: el hallazgo vuelve a contar y queda visible
  (es la señal de que hay que re-decidir).
- Falso positivo ≠ riesgo aceptado: el YAML no distingue el motivo en su efecto
  (ambos excluyen), pero `motivo` debe dejarlo claro para la auditoría.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

GLOBAL_SCOPE = "*"


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass(frozen=True)
class IgnoreEntry:
    match: str  # el id/cve/regla a excluir (p. ej. CVE-2024-1234, DS002)
    reason: str = ""
    approved_by: str = ""
    added: str | None = None  # fecha de alta (informativa)
    expires: str | None = None  # 'vence'; None = sin vencimiento (se desaconseja)
    tool: str = ""  # trivy | gitleaks | "" (ambos/auto) — solo para gen-ignore

    def is_expired(self, today: date) -> bool:
        d = _parse_date(self.expires)
        return d is not None and d < today

    @property
    def key(self) -> str:
        return self.match.strip().upper()


def _entry_from(raw: dict) -> IgnoreEntry | None:
    if not isinstance(raw, dict):
        return None
    match = raw.get("id") or raw.get("cve") or raw.get("rule")
    if not match:
        return None
    return IgnoreEntry(
        match=str(match),
        reason=str(raw.get("motivo") or raw.get("reason") or ""),
        approved_by=str(raw.get("aprobado_por") or raw.get("approved_by") or ""),
        added=raw.get("fecha") or raw.get("added"),
        expires=raw.get("vence") or raw.get("expires"),
        tool=str(raw.get("tool") or "").lower(),
    )


@dataclass(frozen=True)
class IgnoreFile:
    by_project: dict[str, list[IgnoreEntry]]  # incluye GLOBAL_SCOPE

    def entries_for(self, project_key: str) -> list[IgnoreEntry]:
        """Entradas aplicables a un proyecto: las suyas + las globales."""
        return [*self.by_project.get(GLOBAL_SCOPE, []), *self.by_project.get(project_key, [])]

    def active_ids(self, project_key: str, today: date | None = None) -> set[str]:
        """Ids actualmente suprimidos (no vencidos) para un proyecto, en MAYÚSCULAS."""
        d = today or date.today()
        return {e.key for e in self.entries_for(project_key) if not e.is_expired(d)}

    def expired(self, project_key: str, today: date | None = None) -> list[IgnoreEntry]:
        """Entradas vencidas aplicables a un proyecto (vuelven a contar + alerta)."""
        d = today or date.today()
        return [e for e in self.entries_for(project_key) if e.is_expired(d)]


def _parse(raw: dict) -> IgnoreFile:
    by_project: dict[str, list[IgnoreEntry]] = {}
    for item in raw.get("global") or []:
        e = _entry_from(item)
        if e:
            by_project.setdefault(GLOBAL_SCOPE, []).append(e)
    for key, items in (raw.get("projects") or {}).items():
        for item in items or []:
            e = _entry_from(item)
            if e:
                by_project.setdefault(str(key), []).append(e)
    return IgnoreFile(by_project=by_project)


# Caché por (ruta, mtime): el archivo cambia poco y se lee en cada agregación.
_CACHE: dict[str, tuple[float, IgnoreFile]] = {}


def load_ignore(path: str | Path) -> IgnoreFile:
    """Carga wolf-ignore.yaml. Ausente o inválido → vacío (no suprime nada)."""
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return IgnoreFile(by_project={})
    cached = _CACHE.get(str(p))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return IgnoreFile(by_project={})
    parsed = _parse(raw) if isinstance(raw, dict) else IgnoreFile(by_project={})
    _CACHE[str(p)] = (mtime, parsed)
    return parsed


def active_ids_for(project_key: str, path: str | Path | None = None) -> set[str]:
    """Atajo: ids suprimidos para un proyecto según la ruta por defecto de settings."""
    if path is None:
        from ..settings import get_settings

        path = get_settings().ignore_path
    return load_ignore(path).active_ids(project_key)
