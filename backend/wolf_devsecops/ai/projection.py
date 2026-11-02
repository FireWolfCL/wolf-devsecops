"""Proyección de findings crudos → FindingBrief (P0-1, wolf-ai-safety).

La frontera de datos del subsistema de IA (ADR-007). Toma el finding crudo de una
fuente (DefectDojo, etc.) y deja pasar SOLO una allow-list de campos seguros:
title, severity y una description saneada y truncada. NUNCA file_path, line,
sast_source_file_path, código ni ubicaciones de secretos.
"""

from __future__ import annotations

import re

from ..models import FindingBrief, Severity

_SEV = {
    "critical": Severity.critical,
    "high": Severity.high,
    "medium": Severity.medium,
    "low": Severity.low,
    "info": Severity.info,
    "informational": Severity.info,
}

# Longitud máxima de description que dejamos salir (reduce fuga de snippets).
_MAX_DESC = 240


def _sanitize_description(raw: str) -> str:
    """Colapsa saltos de línea (los snippets de código son multilínea) y recorta.

    No intenta 'adivinar' si hay código: el control fuerte es la allow-list de
    campos (no incluimos file_path/line/source). Esto solo endurece description.
    """
    text = re.sub(r"\s+", " ", str(raw)).strip()
    # Quita rutas tipo /a/b/c.py:123 y referencias de archivo:línea evidentes.
    text = re.sub(r"\S+\.\w+:\d+", "[ubicación omitida]", text)
    return text[:_MAX_DESC]


def to_brief(raw: dict) -> FindingBrief:
    severity = _SEV.get(str(raw.get("severity", "")).lower(), Severity.info)
    title = str(raw.get("title", "sin título"))[:200]
    description = _sanitize_description(raw.get("description", ""))
    return FindingBrief(title=title, severity=severity, description=description)


def project_findings(raw_findings: list[dict], limit: int = 100) -> list[FindingBrief]:
    """Allow-list + cota de cantidad. Todo lo que no esté en to_brief NO sale."""
    return [to_brief(f) for f in raw_findings[:limit]]
