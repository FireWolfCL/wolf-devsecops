"""Feature D — remediación: branch + PR draft, NUNCA merge (ADR-017).

Cubre los invariantes no negociables:
- branch con prefijo reservado wolf-sec/fix- (el cliente rechaza cualquier otro),
- PR en estado DRAFT (draft=True) y NUNCA merge (no existe el método),
- idempotencia (branch/PR existente → no se duplica, se devuelve el existente),
- audit trail (branch_pr_created / rejected) sin secretos,
- orden fail-closed del endpoint (401 -> 403 -> 503 gate -> 400 confirm -> lógica),
- separación del PAT de escritura (token_env dedicado, resuelto server-side).
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from wolf_devsecops.api.deps import get_config
from wolf_devsecops.auth import security
from wolf_devsecops.connectors.github import GitHubWriteClient, GitHubWriteError
from wolf_devsecops.core.config import (
    AppConfig,
    GitHubProjectConfig,
    ProjectConfig,
    SourceConfig,
)
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models import RemediationFinding
from wolf_devsecops.models_db.audit import AuditLog
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import config_service, remediation_service
from wolf_devsecops.services.remediation_service import RemediationError, _slug
from wolf_devsecops.settings import get_settings


def _project() -> ProjectConfig:
    return ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))


def _cfg() -> AppConfig:
    return AppConfig(
        sources={"github": SourceConfig(url="https://api.github.com", token_env="GH_READ")},
        projects=[_project()],
    )


# ─── Unit: slug determinista + GitHubWriteClient ─────────────────────────────
def test_slug_deterministic_and_order_independent():
    a = _slug("p", ["CVE-1", "CVE-2"])
    b = _slug("p", ["CVE-2", "CVE-1"])
    assert a == b and len(a) == 8


def _wc_transport(routes):
    """routes: función (method, path) -> (status, json)."""
    def handler(request: httpx.Request):
        status, body = routes(request.method, request.url.path)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


async def test_write_client_create_branch_idempotent():
    created = GitHubWriteClient("https://api.github.com", "wtok",
                                _wc_transport(lambda m, p: (201, {})))
    assert await created.create_branch("o/r", "wolf-sec/fix-x", "sha") is True
    exists = GitHubWriteClient("https://api.github.com", "wtok",
                               _wc_transport(lambda m, p: (422, {"message": "Reference already exists"})))
    assert await exists.create_branch("o/r", "wolf-sec/fix-x", "sha") is False


async def test_write_client_create_branch_rejects_foreign_prefix():
    wc = GitHubWriteClient("https://api.github.com", "wtok", _wc_transport(lambda m, p: (201, {})))
    for forbidden in ("main", "develop", "feature/x", "wolf-sec/other"):
        with pytest.raises(GitHubWriteError):
            await wc.create_branch("o/r", forbidden, "sha")


async def test_write_client_open_pull_draft_and_conflict():
    ok = GitHubWriteClient("https://api.github.com", "wtok",
                           _wc_transport(lambda m, p: (201, {"html_url": "u", "number": 3})))
    pr = await ok.open_pull("o/r", "wolf-sec/fix-x", "main", "t", "b", draft=True)
    assert pr["number"] == 3
    conflict = GitHubWriteClient("https://api.github.com", "wtok",
                                 _wc_transport(lambda m, p: (422, {"message": "A pull request already exists"})))
    with pytest.raises(GitHubWriteError) as e:
        await conflict.open_pull("o/r", "wolf-sec/fix-x", "main", "t", "b")
    assert str(e.value) == "already_exists"


def test_write_client_has_no_merge_method():
    # ADR-017: el panel JAMÁS mergea. El cliente de escritura no expone merge/force-push.
    assert not hasattr(GitHubWriteClient, "merge")
    assert not hasattr(GitHubWriteClient, "merge_pull")


# ─── Fake write client para probar el servicio ──────────────────────────────
class _FakeWriteClient:
    def __init__(self, *, branch_exists=False, pr_existing=None, fail=None):
        self.branch_exists = branch_exists
        self.pr_existing = pr_existing
        self.fail = fail
        self.calls: list = []

    async def default_branch_and_sha(self, repo):
        if self.fail == "sha":
            raise GitHubWriteError("GitHub respondió HTTP 403 al leer el repo.")
        return ("main", "basesha")

    async def create_branch(self, repo, new_branch, from_sha):
        self.calls.append(("create_branch", new_branch, from_sha))
        return not self.branch_exists

    async def get_file_sha(self, repo, path, branch):
        return None

    async def put_file(self, repo, path, content, message, branch, sha=None):
        self.calls.append(("put_file", path, branch))

    async def find_open_pull(self, repo, head_branch, base):
        return self.pr_existing

    async def open_pull(self, repo, head_branch, base, title, body, draft=True):
        self.calls.append(("open_pull", head_branch, base, draft))
        return {"html_url": "https://github.com/o/r/pull/9", "number": 9}


@pytest.fixture
async def db_session(tmp_path):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/remed_d.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        yield s
    await db_engine.dispose_engine()


_PICKED = [RemediationFinding(ref="CVE-1", title="SQLi", severity="critical",
                             recommended_action="usar params")]


async def test_service_happy_creates_branch_and_draft_pr(db_session):
    wc = _FakeWriteClient()
    res = await remediation_service.create_remediation_branch(
        db_session, project=_project(), picked_findings=_PICKED, write_client=wc,
        actor_user_id="u1", actor_username="op",
    )
    assert res.branch.startswith("wolf-sec/fix-")
    assert res.applied is True and res.already_existed is False
    assert res.pr_url == "https://github.com/o/r/pull/9"
    # el PR se abrió en DRAFT
    assert any(c[0] == "open_pull" and c[3] is True for c in wc.calls)
    # audit: branch_pr_created, sin secretos
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    actions = [r.action for r in rows]
    assert "remediation.branch_pr_created" in actions
    created = next(r for r in rows if r.action == "remediation.branch_pr_created")
    assert created.detail["branch"] == res.branch and created.detail["idempotency_key"] == res.branch


async def test_service_idempotent_when_pr_exists(db_session):
    wc = _FakeWriteClient(branch_exists=True,
                          pr_existing={"html_url": "https://github.com/o/r/pull/9", "number": 9})
    res = await remediation_service.create_remediation_branch(
        db_session, project=_project(), picked_findings=_PICKED, write_client=wc,
        actor_user_id="u1", actor_username="op",
    )
    assert res.already_existed is True and res.applied is False
    # NO se intentó abrir un PR nuevo (idempotente)
    assert not any(c[0] == "open_pull" for c in wc.calls)


async def test_service_rejection_audits_and_raises(db_session):
    wc = _FakeWriteClient(fail="sha")
    with pytest.raises(RemediationError):
        await remediation_service.create_remediation_branch(
            db_session, project=_project(), picked_findings=_PICKED, write_client=wc,
            actor_user_id="u1", actor_username="op",
        )
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert [r.action for r in rows] == ["remediation.rejected"]
    assert rows[0].result == "failure"


# ─── Endpoint: fail-closed (RBAC -> gate -> confirm) ─────────────────────────
@pytest.fixture
async def env(tmp_path, monkeypatch):
    settings = get_settings()
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/remed_ep.db"
    settings.remediation_write_enabled = False
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    headers: dict[str, dict[str, str]] = {}
    async with db_engine.get_sessionmaker()() as s:
        for role in Role:
            # operator sin MFA (require_admin_action solo exige MFA a admin)
            u = User(username=f"u-{role.value}", role=role, is_active=True, token_version=0)
            s.add(u)
            await s.flush()
            tok = security.create_access_token(subject=u.id, role=u.role.value, token_version=0)
            headers[role.value] = {"Authorization": f"Bearer {tok}"}
        await s.commit()
    app = create_app()
    # Las rutas llaman get_config() directo (no vía Depends): se sustituye la fuente.
    monkeypatch.setattr(config_service, "get_cached_config", lambda: _cfg())
    app.dependency_overrides[get_config] = lambda: _cfg()
    # El cliente de escritura se sustituye: no se toca red.
    import wolf_devsecops.api.routes_remediation as r_rem
    monkeypatch.setattr(r_rem, "_build_write_client", lambda gh_url, token: _FakeWriteClient())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, headers
    settings.remediation_write_enabled = False
    await db_engine.dispose_engine()


URL = "/api/v1/projects/p/remediation/branch"
_BODY = {"picked_findings": [{"ref": "CVE-1", "title": "SQLi", "severity": "critical"}], "confirm": True}


async def test_endpoint_no_token_401(env):
    client, _ = env
    assert (await client.post(URL, json=_BODY)).status_code == 401


async def test_endpoint_viewer_403(env):
    client, h = env
    assert (await client.post(URL, json=_BODY, headers=h["viewer"])).status_code == 403


async def test_endpoint_gate_503_when_flag_off(env):
    client, h = env
    # flag off por defecto: aunque operator y confirm=true, 503
    r = await client.post(URL, json=_BODY, headers=h["operator"])
    assert r.status_code == 503


async def test_endpoint_confirm_required_400(env):
    client, h = env
    get_settings().remediation_write_enabled = True
    body = {"picked_findings": [{"ref": "CVE-1"}], "confirm": False}
    r = await client.post(URL, json=body, headers=h["operator"])
    assert r.status_code == 400


async def test_endpoint_happy_operator_200(env, monkeypatch):
    client, h = env
    get_settings().remediation_write_enabled = True
    monkeypatch.setenv("GITHUB_WRITE_TOKEN", "write-pat-xyz")
    r = await client.post(URL, json=_BODY, headers=h["operator"])
    assert r.status_code == 200
    body = r.json()
    assert body["branch"].startswith("wolf-sec/fix-") and body["applied"] is True


async def test_endpoint_missing_write_token_503(env):
    client, h = env
    get_settings().remediation_write_enabled = True
    # sin GITHUB_WRITE_TOKEN en el entorno → 503 (config incompleta), nunca escribe
    import os
    os.environ.pop("GITHUB_WRITE_TOKEN", None)
    r = await client.post(URL, json=_BODY, headers=h["operator"])
    assert r.status_code == 503
