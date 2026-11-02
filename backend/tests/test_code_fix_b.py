"""Feature B — propuesta de fix con código (ADR-018, nivel assistive).

Cubre los CONTROLES de seguridad que posee wolf-security-crypto-agent:
- validación anti path-traversal DURA de la ruta del finding,
- ventana ACOTADA vía Contents API (symlink/dir/404/archivo grande rechazados),
- egreso computado server-side (local=False / hosted=True) y redacción de secretos
  antes de cualquier egreso hosted,
- allow-list de modelo por proveedor (422 fuera de lista),
- orden fail-closed del endpoint (401 -> 403 -> 503 gate -> lógica).
"""

from __future__ import annotations

import base64

import httpx
import pytest

from wolf_devsecops.ai import code_context, code_fix
from wolf_devsecops.ai.code_context import CodeContextError, fetch_code_window, validate_repo_path
from wolf_devsecops.ai.openai_compat import parse_code_fix
from wolf_devsecops.api.deps import get_aggregator, get_config
from wolf_devsecops.auth import security
from wolf_devsecops.core.config import (
    AIConfig,
    AppConfig,
    GitHubProjectConfig,
    ProjectConfig,
    SourceConfig,
)
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models import SourceDetail, SourceStatus
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import config_service
from wolf_devsecops.settings import Settings, get_settings


# ─── Unit: validación de ruta (anti path-traversal) ─────────────────────────
@pytest.mark.parametrize(
    "bad",
    ["", "   ", "../etc/passwd", "/etc/passwd", "a/../../x", "x/../../../y",
     "C:/windows", "a\\b", "foo/\x00bar", ".."],
)
def test_validate_repo_path_rejects_traversal(bad):
    with pytest.raises(CodeContextError):
        validate_repo_path(bad)


@pytest.mark.parametrize("ok", ["a/b/c.py", "src/app/main.py", "README.md", "a/./b.py"])
def test_validate_repo_path_accepts_clean(ok):
    assert validate_repo_path(ok)  # no lanza; normaliza


# ─── Unit: fetch_code_window (Contents API, transporte simulado) ─────────────
def _contents_transport(obj, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=obj)

    return httpx.MockTransport(handler)


def _file_payload(text: str) -> dict:
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text.encode()).decode(),
    }


async def test_fetch_window_slices_around_line():
    content = "\n".join(f"line{i}" for i in range(1, 101))
    w = await fetch_code_window(
        api_base="https://api.github.com", repo="o/r", file_path="a.py", line=50,
        token="read", window_lines=5, transport=_contents_transport(_file_payload(content)),
    )
    assert w.start_line == 45 and w.end_line == 55
    assert "line50" in w.text and "line44" not in w.text and "line56" not in w.text
    assert w.path == "a.py"


async def test_fetch_window_rejects_symlink():
    with pytest.raises(CodeContextError):
        await fetch_code_window(
            api_base="https://api.github.com", repo="o/r", file_path="a.py", line=1,
            token=None, window_lines=5,
            transport=_contents_transport({"type": "symlink", "target": "/etc/passwd"}),
        )


async def test_fetch_window_rejects_directory_and_404():
    with pytest.raises(CodeContextError):
        await fetch_code_window(
            api_base="https://api.github.com", repo="o/r", file_path="a", line=1,
            token=None, window_lines=5, transport=_contents_transport([{"type": "file"}]),
        )
    with pytest.raises(CodeContextError):
        await fetch_code_window(
            api_base="https://api.github.com", repo="o/r", file_path="a.py", line=1,
            token=None, window_lines=5, transport=_contents_transport({}, status=404),
        )


async def test_fetch_window_rejects_too_large():
    # Sin content/encoding base64: la Contents API no incrusta archivos >1MB.
    with pytest.raises(CodeContextError):
        await fetch_code_window(
            api_base="https://api.github.com", repo="o/r", file_path="big.bin", line=1,
            token=None, window_lines=5, transport=_contents_transport({"type": "file", "size": 9_000_000}),
        )


# ─── Unit: pre-scan + redacción de secretos ─────────────────────────────────
def test_redacts_private_key_and_token():
    text = "-----BEGIN PRIVATE KEY-----\nabc\napi_key = 'supersecretvalue123'"
    red, n = code_context.scan_and_redact_secrets(text)
    assert n >= 2
    assert "BEGIN PRIVATE KEY" not in red and "supersecretvalue123" not in red


def test_redaction_noop_on_clean_code():
    red, n = code_context.scan_and_redact_secrets("def add(a, b):\n    return a + b\n")
    assert n == 0


# ─── Unit: parse_code_fix (salida del modelo = input no confiable) ───────────
def test_parse_code_fix_degrades_without_json():
    r = parse_code_fix("no soy json", "local", "m", egress=False, original_snippet="x", max_chars=100)
    assert r.proposed_snippet == "" and r.explanation
    assert r.egress is False and r.original_snippet == "x"


def test_parse_code_fix_bounds_length():
    raw = '{"proposed_snippet":"%s","explanation":"ok"}' % ("A" * 500)
    r = parse_code_fix(raw, "hosted", "m", egress=True, original_snippet="B" * 500, max_chars=10)
    assert len(r.proposed_snippet) == 10 and len(r.original_snippet) == 10 and r.egress is True


# ─── Unit: resolve_runtime + egreso/redacción en propose_code_fix ────────────
def _cfg_local() -> AppConfig:
    return AppConfig(
        sources={"github": SourceConfig(url="https://api.github.com", token_env="GH")},
        projects=[ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))],
        ai=AIConfig(provider="local", model="qwen2.5:14b", base_url="http://ollama:11434/v1"),
    )


def test_resolve_runtime_allowlists():
    s = Settings()
    rl = code_fix.resolve_runtime("local", _cfg_local(), s)
    assert "qwen2.5:14b" in rl.allowed_models and rl.token is None
    rh = code_fix.resolve_runtime("hosted", _cfg_local(), s)
    assert "claude-sonnet-4-5" in rh.allowed_models  # default hosted allow-list


async def test_propose_rejects_model_outside_allowlist(monkeypatch):
    with pytest.raises(code_fix.CodeFixError):
        await code_fix.propose_code_fix(
            config=_cfg_local(), settings=Settings(), provider_type="local",
            model="evil-model", github_api_base="https://api.github.com", read_token=None,
            repo="o/r", file_path="a.py", line=1, finding_title="t",
        )


async def test_propose_local_no_egress_sends_original(monkeypatch):
    sent = {}

    async def fake_window(**kw):
        return code_context.CodeWindow(path="a.py", start_line=1, end_line=3, text="api_key='leak12345'")

    async def fake_chat(base_url, token, model, user, system=None):
        sent["user"] = user
        return '{"proposed_snippet":"fix","explanation":"ok"}'

    monkeypatch.setattr(code_fix, "fetch_code_window", fake_window)
    monkeypatch.setattr(code_fix, "chat", fake_chat)
    result, redacted = await code_fix.propose_code_fix(
        config=_cfg_local(), settings=Settings(), provider_type="local",
        model="qwen2.5:14b", github_api_base="https://api.github.com", read_token=None,
        repo="o/r", file_path="a.py", line=1, finding_title="t",
    )
    assert result.egress is False and redacted == 0
    assert "leak12345" in sent["user"]  # local = sin egreso, el secreto NO se redacta


async def test_propose_hosted_redacts_before_egress(monkeypatch):
    sent = {}

    async def fake_window(**kw):
        return code_context.CodeWindow(path="a.py", start_line=1, end_line=3, text="api_key='leak12345'")

    async def fake_chat(base_url, token, model, user, system=None):
        sent["user"] = user
        return '{"proposed_snippet":"fix","explanation":"ok"}'

    monkeypatch.setattr(code_fix, "fetch_code_window", fake_window)
    monkeypatch.setattr(code_fix, "chat", fake_chat)
    monkeypatch.setenv("HOSTED_KEY", "sk-abc")
    s = Settings()
    s.ai_hosted_base_url = "https://api.anthropic-compat/v1"
    s.ai_hosted_token_env = "HOSTED_KEY"
    result, redacted = await code_fix.propose_code_fix(
        config=_cfg_local(), settings=s, provider_type="hosted",
        model="claude-sonnet-4-5", github_api_base="https://api.github.com", read_token=None,
        repo="o/r", file_path="a.py", line=1, finding_title="t",
    )
    assert result.egress is True and redacted >= 1
    assert "leak12345" not in sent["user"]  # hosted = CON egreso: secreto redactado


async def test_propose_hosted_unconfigured_raises(monkeypatch):
    s = Settings()  # sin ai_hosted_base_url ni token_env, y config.ai es local
    with pytest.raises(code_fix.CodeFixError):
        await code_fix.propose_code_fix(
            config=_cfg_local(), settings=s, provider_type="hosted",
            model="claude-sonnet-4-5", github_api_base="https://api.github.com", read_token=None,
            repo="o/r", file_path="a.py", line=1, finding_title="t",
        )


# ─── Endpoint: RBAC + gate + resolución de finding ──────────────────────────
_FINDINGS = [
    {"kind": "vuln", "id": "CVE-2024-1", "title": "SQLi", "severity": "CRITICAL",
     "target": "src/app/db.py:42", "description": "x"},
]


class _StubAggregator:
    async def get_source_detail(self, project, source):
        return SourceDetail(project_key="p", source="defectdojo", status=SourceStatus.ok,
                            payload={"findings": _FINDINGS})


@pytest.fixture
async def env(tmp_path, monkeypatch):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/codefix_b.db"
    settings.ai_enabled = False  # por defecto: el gate frena (503)
    # Las rutas llaman get_config()/get_cached_config() directo (no vía Depends), así
    # que se sustituye la fuente de config en lugar de dependency_overrides.
    monkeypatch.setattr(config_service, "get_cached_config", lambda: _cfg_local())
    import wolf_devsecops.api.routes_ai as r_ai
    monkeypatch.setattr(r_ai, "get_aggregator", lambda: _StubAggregator())
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    headers: dict[str, dict[str, str]] = {}
    async with db_engine.get_sessionmaker()() as s:
        for role in Role:
            u = User(username=f"u-{role.value}", role=role, is_active=True, token_version=0)
            s.add(u)
            await s.flush()
            tok = security.create_access_token(subject=u.id, role=u.role.value, token_version=0)
            headers[role.value] = {"Authorization": f"Bearer {tok}"}
        await s.commit()
    app = create_app()
    app.dependency_overrides[get_aggregator] = lambda: _StubAggregator()
    app.dependency_overrides[get_config] = lambda: _cfg_local()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, headers
    settings.ai_enabled = False
    await db_engine.dispose_engine()


URL = "/api/v1/projects/p/ai/code-fix"
_BODY = {"finding_ref": "CVE-2024-1", "provider": "local", "model": "qwen2.5:14b"}


async def test_code_fix_no_token_401(env):
    client, _ = env
    assert (await client.post(URL, json=_BODY)).status_code == 401


async def test_code_fix_viewer_forbidden(env):
    client, h = env
    assert (await client.post(URL, json=_BODY, headers=h["viewer"])).status_code == 403


async def test_code_fix_gate_503_when_ai_off(env):
    client, h = env
    r = await client.post(URL, json=_BODY, headers=h["operator"])
    assert r.status_code == 503


async def test_code_fix_happy_path_local(env, monkeypatch):
    client, h = env
    get_settings().ai_enabled = True  # gate: env permite + config.ai presente
    import wolf_devsecops.api.routes_ai as r_ai
    from wolf_devsecops.models import AICodeFixResult

    async def fake_propose(**kw):
        assert kw["provider_type"] == "local"
        assert kw["file_path"] == "src/app/db.py" and kw["line"] == 42  # target parseado
        return (
            AICodeFixResult(provider_type="local", model=kw["model"], egress=False,
                            original_snippet="o", proposed_snippet="p", explanation="e"),
            0,
        )

    monkeypatch.setattr(r_ai, "propose_code_fix", fake_propose)
    r = await client.post(URL, json=_BODY, headers=h["operator"])
    assert r.status_code == 200
    body = r.json()
    assert body["egress"] is False and body["provider_type"] == "local"


async def test_code_fix_finding_not_found_404(env, monkeypatch):
    client, h = env
    get_settings().ai_enabled = True
    bad = {"finding_ref": "nope", "provider": "local", "model": "qwen2.5:14b"}
    r = await client.post(URL, json=bad, headers=h["operator"])
    assert r.status_code == 404


async def test_code_fix_model_not_allowed_422(env):
    client, h = env
    get_settings().ai_enabled = True
    bad = {"finding_ref": "CVE-2024-1", "provider": "local", "model": "evil"}
    r = await client.post(URL, json=bad, headers=h["operator"])
    assert r.status_code == 422  # allow-list real (propose_code_fix) rechaza


async def test_code_fix_bad_provider_422(env):
    client, h = env
    get_settings().ai_enabled = True
    bad = {"finding_ref": "CVE-2024-1", "provider": "remote", "model": "x"}
    r = await client.post(URL, json=bad, headers=h["operator"])
    assert r.status_code == 422  # Literal de Pydantic
