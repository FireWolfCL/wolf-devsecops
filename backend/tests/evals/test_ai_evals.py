"""Suite de evals del subsistema de IA (P0-3, wolf-ai-safety).

Evalúa el manejo SERVER-SIDE de la salida del modelo (input no confiable) y la
frontera de datos de entrada. No requiere un modelo real: ejercita la proyección
y `parse_review`, que es donde viven los controles de seguridad.

Cobertura:
- golden: salida bien formada → AIReviewResult correcto.
- hostile H2: la entrada nunca filtra rutas/código (allow-list).
- hostile H3: salida con HTML/JS se conserva como TEXTO inerte (no se interpreta;
  el render seguro es responsabilidad del frontend, P0-4).
- hostile: no-JSON → degrada; demasiados items → capado; severity inválida → descartada.
"""

from wolf_devsecops.ai.openai_compat import context_to_user, parse_review
from wolf_devsecops.ai.projection import project_findings
from wolf_devsecops.models import AIReviewContext


def _ctx():
    return AIReviewContext(project_key="p", findings=[])


# ─── golden ───────────────────────────────────────────────────────────────────
def test_golden_salida_bien_formada():
    raw = (
        '{"summary":"Dos hallazgos relevantes.","prioritized":['
        '{"title":"SQLi","severity":"critical","rationale":"inyección","recommended_action":"params"},'
        '{"title":"Dep vieja","severity":"high","rationale":"CVE","recommended_action":"bump"}]}'
    )
    r = parse_review(raw, "local", "llama3", egress=False, context=_ctx())
    assert r.summary.startswith("Dos hallazgos")
    assert len(r.prioritized) == 2
    assert r.prioritized[0].severity.value == "critical"
    assert r.egress is False


# ─── hostile H2 — fuga de código/rutas ──────────────────────────────────────────
def test_hostile_h2_entrada_sin_rutas_ni_codigo():
    raw = [{"title": "X", "severity": "High", "description": "bug at app/x.py:10",
            "file_path": "app/x.py", "line": 10, "source_code": "exec(user)"}]
    user = context_to_user(AIReviewContext(project_key="p", findings=project_findings(raw)))
    assert "file_path" not in user and "source_code" not in user
    assert "app/x.py:10" not in user


# ─── hostile H3 — XSS/HTML en la salida del modelo ──────────────────────────────
def test_hostile_h3_html_se_conserva_como_texto_inerte():
    raw = '{"summary":"<script>alert(1)</script>","prioritized":[]}'
    r = parse_review(raw, "local", "m", egress=False, context=_ctx())
    # El backend NO ejecuta ni des-escapa: lo guarda como string (dato).
    assert isinstance(r.summary, str)
    assert "<script>" in r.summary  # intacto como texto; el frontend lo renderiza plano


# ─── hostile — robustez del parser ──────────────────────────────────────────────
def test_hostile_no_json_degrada():
    r = parse_review("lo siento, no puedo", "local", "m", egress=False, context=_ctx())
    assert r.prioritized == []
    assert r.summary  # algo de texto, no revienta


def test_hostile_demasiados_items_capado():
    items = ",".join(
        '{"title":"t","severity":"low","rationale":"r","recommended_action":"a"}'
        for _ in range(200)
    )
    r = parse_review(f'{{"summary":"s","prioritized":[{items}]}}', "local", "m", False, _ctx())
    assert len(r.prioritized) <= 50


def test_hostile_severity_invalida_descartada():
    raw = (
        '{"summary":"s","prioritized":['
        '{"title":"ok","severity":"high","rationale":"r","recommended_action":"a"},'
        '{"title":"mala","severity":"apocalyptic","rationale":"r","recommended_action":"a"}]}'
    )
    r = parse_review(raw, "local", "m", False, _ctx())
    assert len(r.prioritized) == 1
    assert r.prioritized[0].title == "ok"
