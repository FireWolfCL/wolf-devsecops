"""P0-1 (wolf-ai-safety) — la proyección NO filtra rutas ni campos de código.

Caso hostil: un finding crudo de DefectDojo con file_path, line y snippet. Tras la
proyección con allow-list, lo que viaja al proveedor NO debe contener esos campos
ni la ubicación archivo:línea.
"""

from wolf_devsecops.ai.openai_compat import context_to_user
from wolf_devsecops.ai.projection import project_findings
from wolf_devsecops.models import AIReviewContext


def test_proyeccion_allowlist_no_filtra_rutas_ni_campos_de_codigo():
    raw = [
        {
            "title": "SQL injection",
            "severity": "Critical",
            "description": "Unsanitized input reaches query at app/db.py:42",
            "file_path": "app/db.py",
            "line": 42,
            "sast_source_file_path": "src/app/db.py",
            "mitigation": "use parametrized queries",
            "cwe": 89,
        }
    ]
    briefs = project_findings(raw)
    user = context_to_user(AIReviewContext(project_key="wolf-cloud", findings=briefs))

    # Campos de ubicación/código NO cruzan (allow-list estructural).
    assert "file_path" not in user
    assert "sast_source_file_path" not in user
    assert "mitigation" not in user
    assert "app/db.py:42" not in user  # ubicación archivo:línea saneada
    assert '"line"' not in user

    # Lo útil SÍ pasa.
    assert "SQL injection" in user
    assert briefs[0].severity.value == "critical"
    assert len(briefs[0].description) <= 240
