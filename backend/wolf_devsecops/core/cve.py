"""Extracción estricta de ids CVE (seguimiento de parche de excepciones, ADR-021).

Solo reconoce el formato canónico `CVE-AAAA-NNNN+`. No hace matching difuso: si un
texto no contiene un CVE bien formado, devuelve None y el hallazgo simplemente NO se
rastrea (no-op honesto).
"""

from __future__ import annotations

import re

_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,19}\b", re.IGNORECASE)


def extract_cve(*candidates: object) -> str | None:
    """Primer CVE bien formado (en MAYÚSCULAS) entre los candidatos, en orden.
    Acepta str o listas/dicts típicos de `vulnerability_ids` de DefectDojo."""
    for cand in candidates:
        if isinstance(cand, dict):
            cand = cand.get("vulnerability_id")
        if isinstance(cand, (list, tuple)):
            found = extract_cve(*cand)
            if found:
                return found
            continue
        if isinstance(cand, str):
            m = _CVE_RE.search(cand)
            if m:
                return m.group(0).upper()
    return None
