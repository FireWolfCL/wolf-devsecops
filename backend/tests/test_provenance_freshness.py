"""Procedencia del último commit + sello de frescura (read-only, ADR-001).

Cubre: fetch_provenance GitHub/GitLab (golden + hostil), Aggregator._freshness,
Aggregator._provenance_block y la etapa `commit` del pipeline map. Regla
wolf-genjutsu: jamás se inventa un autor (fail-closed → None / "desconocido").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from wolf_devsecops.connectors.base import ConnectorUnavailable
from wolf_devsecops.connectors.github import GitHubConnector
from wolf_devsecops.connectors.gitlab import GitLabConnector
from wolf_devsecops.core.aggregator import Aggregator
from wolf_devsecops.core.cache import TTLCache
from wolf_devsecops.core.config import (
    AppConfig,
    GitHubProjectConfig,
    GitLabProjectConfig,
    JenkinsProjectConfig,
    ProjectConfig,
    SourceConfig,
)
from wolf_devsecops.models import (
    CIStatus,
    CISummary,
    FindingsSummary,
    ProjectSummary,
    ProvenanceInfo,
    QualitySummary,
    RegistryImage,
    ReleaseSummary,
    SeverityCount,
    SourceResult,
    SourceStatus,
)
from wolf_devsecops.services.pipeline_service import build_pipeline_map

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
NC = SourceResult(status=SourceStatus.not_configured)


# ─── GitHub ──────────────────────────────────────────────────────────────────

def _gh(commit_body, *, repo_status=200, commit_status=200, repo_body=None, raw_commit=None):
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.method} {req.url.path}")
        if req.url.path == "/repos/o/r":
            return httpx.Response(repo_status, json=repo_body or {"default_branch": "trunk"})
        if req.url.path == "/repos/o/r/commits/trunk":
            if raw_commit is not None:
                return httpx.Response(commit_status, content=raw_commit)
            return httpx.Response(commit_status, json=commit_body)
        return httpx.Response(404, json={})

    c = GitHubConnector(SourceConfig(url="https://api.github.com", token_env="T"),
                        token="test-token-INVALID", transport=httpx.MockTransport(handler))
    return c, seen


def _gh_project():
    return ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r", ci_workflow="ci.yml"))


def _commit(*, author=None, committer=None, name="Git Name", sha="0123456789abcdef", msg="feat: x\n\ncuerpo"):
    return {
        "sha": sha, "html_url": "https://github.com/o/r/commit/" + sha,
        "author": author, "committer": committer,
        "commit": {"message": msg,
                   "author": {"name": name, "date": "2026-11-01T10:00:00Z"} if name else {"date": "2026-11-01T10:00:00Z"},
                   "committer": {"date": "2026-11-01T10:05:00Z"}},
    }


async def test_gh_author_login_golden():
    c, seen = _gh(_commit(author={"login": "felipe"}, committer={"login": "web-flow"}))
    p = await c.fetch_provenance(_gh_project())
    assert (p.author, p.author_is_login) == ("felipe", True)
    assert p.default_branch == "trunk" and p.source == "github"
    assert p.sha_short == "0123456" and len(p.sha_short) == 7
    assert p.message == "feat: x"  # solo primera línea
    assert p.authored_at == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    assert seen == ["GET /repos/o/r", "GET /repos/o/r/commits/trunk"]  # 2 GET read-only


async def test_gh_cae_a_committer_login():
    c, _ = _gh(_commit(author=None, committer={"login": "web-flow"}))
    p = await c.fetch_provenance(_gh_project())
    assert (p.author, p.author_is_login) == ("web-flow", True)


async def test_gh_cae_a_nombre_git_sin_login():
    c, _ = _gh(_commit(author=None, committer=None, name="Ana Gómez"))
    p = await c.fetch_provenance(_gh_project())
    assert (p.author, p.author_is_login) == ("Ana Gómez", False)


async def test_gh_login_null_en_objetos_cae_a_nombre():
    # author {} / {"login": None} (cuenta no vinculada) no debe contar como login.
    c, _ = _gh(_commit(author={"login": None}, committer={}, name="Ana"))
    p = await c.fetch_provenance(_gh_project())
    assert (p.author, p.author_is_login) == ("Ana", False)


async def test_gh_sin_autor_alguno_es_none_nunca_inventado():
    c, _ = _gh(_commit(author=None, committer=None, name=None))
    p = await c.fetch_provenance(_gh_project())
    assert p.author is None and p.author_is_login is False


async def test_gh_nombre_vacio_es_none():
    c, _ = _gh(_commit(author=None, committer=None, name=""))
    p = await c.fetch_provenance(_gh_project())
    assert p.author is None


async def test_gh_sha_corto_no_revienta_y_sha_ausente_es_none():
    c, _ = _gh(_commit(sha="abc"))
    assert (await c.fetch_provenance(_gh_project())).sha_short == "abc"
    c2, _ = _gh({"commit": {}})
    p2 = await c2.fetch_provenance(_gh_project())
    assert p2.sha_short is None and p2.message is None and p2.author is None


async def test_gh_sin_default_branch_usa_head():
    seen: list[str] = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, json={} if req.url.path == "/repos/o/r" else _commit(author={"login": "x"}))

    c = GitHubConnector(SourceConfig(url="https://api.github.com", token_env="T"),
                        token="test-token-INVALID", transport=httpx.MockTransport(handler))
    p = await c.fetch_provenance(_gh_project())
    assert p.default_branch == "HEAD" and seen[-1] == "/repos/o/r/commits/HEAD"


@pytest.mark.parametrize("st", [401, 403, 404, 429, 500])
async def test_gh_repo_no_200_es_unavailable(st):
    c, _ = _gh(None, repo_status=st)
    with pytest.raises(ConnectorUnavailable):
        await c.fetch_provenance(_gh_project())


@pytest.mark.parametrize("st", [404, 409, 422, 429, 500])
async def test_gh_commit_no_200_es_unavailable(st):
    c, _ = _gh(_commit(author={"login": "x"}), commit_status=st)
    with pytest.raises(ConnectorUnavailable):
        await c.fetch_provenance(_gh_project())


async def test_gh_json_invalido_es_unavailable():
    c, _ = _gh(None, raw_commit=b"<html>no json</html>")
    with pytest.raises(ConnectorUnavailable):
        await c.fetch_provenance(_gh_project())


async def test_gh_error_de_red_es_unavailable():
    def boom(req):
        raise httpx.ConnectError("down")

    c = GitHubConnector(SourceConfig(url="https://api.github.com", token_env="T"),
                        token="test-token-INVALID", transport=httpx.MockTransport(boom))
    with pytest.raises(ConnectorUnavailable) as ei:
        await c.fetch_provenance(_gh_project())
    assert "test-token-INVALID" not in str(ei.value)  # mensaje saneado


async def test_gh_sin_bloque_github_es_unavailable():
    c, _ = _gh(None)
    with pytest.raises(ConnectorUnavailable):
        await c.fetch_provenance(ProjectConfig(key="p", name="P"))


# ─── GitLab ──────────────────────────────────────────────────────────────────

def _gl(commits, *, proj_status=200, commit_status=200, raw=None, ref="main"):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/repository/commits"):
            if raw is not None:
                return httpx.Response(commit_status, content=raw)
            return httpx.Response(commit_status, json=commits)
        if "/projects/" in req.url.path:
            return httpx.Response(proj_status, json={"default_branch": "develop"})
        return httpx.Response(404, json={})

    c = GitLabConnector(SourceConfig(url="https://gitlab.example.com", token_env="T"),
                        token="test-token-INVALID", transport=httpx.MockTransport(handler))
    p = ProjectConfig(key="p", name="P", gitlab=GitLabProjectConfig(project="g/p", ref=ref))
    return c, p, seen


async def test_gl_golden_mapeo():
    c, p, seen = _gl([{
        "id": "deadbeefcafe1234", "short_id": "deadbee", "title": "fix: y",
        "author_name": "Beto", "authored_date": "2026-11-01T09:00:00.000Z",
        "web_url": "https://gitlab.example.com/g/p/-/commit/deadbee",
    }])
    r = await c.fetch_provenance(p)
    assert (r.sha_short, r.message, r.author) == ("deadbee", "fix: y", "Beto")
    assert r.author_is_login is False and r.source == "gitlab"
    assert r.html_url.endswith("/commit/deadbee")
    assert r.authored_at == datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert r.default_branch == "main"  # gl.ref gana
    q = seen[-1].url.params
    assert q["per_page"] == "1" and q["ref_name"] == "main"
    assert all(s.method == "GET" for s in seen)


async def test_gl_sin_ref_usa_default_branch_del_proyecto():
    c, p, seen = _gl([{"short_id": "abc1234", "title": "t", "author_name": "A"}], ref=None)
    r = await c.fetch_provenance(p)
    assert r.default_branch == "develop" and seen[-1].url.params["ref_name"] == "develop"


async def test_gl_lista_vacia_no_revienta():
    c, p, _ = _gl([])
    r = await c.fetch_provenance(p)
    assert r == ProvenanceInfo(default_branch="main", source="gitlab")
    assert r.author is None and r.sha_short is None


async def test_gl_sin_author_name_es_none():
    c, p, _ = _gl([{"short_id": "abc1234", "title": "t", "author_name": "", "author_email": "x@y.z"}])
    r = await c.fetch_provenance(p)
    assert r.author is None and r.author_is_login is False  # no se usa el email


async def test_gl_respuesta_no_lista_degrada_sin_inventar():
    c, p, _ = _gl({"message": "weird"})
    r = await c.fetch_provenance(p)
    assert r.author is None and r.sha_short is None and r.source == "gitlab"


@pytest.mark.parametrize("kw", [{"proj_status": 404}, {"commit_status": 500}, {"commit_status": 401}])
async def test_gl_no_200_es_unavailable(kw):
    c, p, _ = _gl([], **kw)
    with pytest.raises(ConnectorUnavailable):
        await c.fetch_provenance(p)


async def test_gl_json_invalido_es_unavailable():
    c, p, _ = _gl(None, raw=b"not json")
    with pytest.raises(ConnectorUnavailable):
        await c.fetch_provenance(p)


# ─── _freshness ──────────────────────────────────────────────────────────────

def _ok(data, at):
    return SourceResult(status=SourceStatus.ok, data=data, fetched_at=at)


def _summary(**over):
    base = dict(key="p", name="P", ci=NC, findings=NC, quality=NC, release=NC, containers=NC,
                generated_at=NOW)
    base.update(over)
    return ProjectSummary(**base)


def test_freshness_elige_el_mas_viejo():
    t_old, t_mid, t_new = NOW - timedelta(hours=5), NOW - timedelta(hours=2), NOW - timedelta(minutes=1)
    s = _summary(
        ci=_ok(CISummary(status=CIStatus.success, workflow="ci"), t_new),
        findings=_ok(FindingsSummary(counts=SeverityCount(), total_active=0), t_old),
        quality=_ok(QualitySummary(quality_gate="OK"), t_mid),
        provenance=_ok(ProvenanceInfo(default_branch="main"), t_new),
    )
    f = Aggregator._freshness(s)
    assert f.as_of == t_old and f.stale is False


def test_freshness_incluye_procedencia_si_es_la_mas_vieja():
    t_old = NOW - timedelta(days=1)
    s = _summary(ci=_ok(CISummary(status=CIStatus.success, workflow="ci"), NOW),
                 provenance=_ok(ProvenanceInfo(), t_old))
    assert Aggregator._freshness(s).as_of == t_old


def test_freshness_ignora_not_configured_y_sin_fetched_at():
    s = _summary(ci=_ok(CISummary(status=CIStatus.success, workflow="ci"), NOW),
                 findings=SourceResult(status=SourceStatus.unavailable, error="x"))
    f = Aggregator._freshness(s)
    assert f.as_of == NOW and f.stale is False


def test_freshness_stale_si_algun_bloque_stale():
    s = _summary(
        ci=_ok(CISummary(status=CIStatus.success, workflow="ci"), NOW),
        quality=SourceResult(status=SourceStatus.stale, data=QualitySummary(quality_gate="OK"),
                             fetched_at=NOW - timedelta(hours=3)),
    )
    f = Aggregator._freshness(s)
    assert f.stale is True and f.as_of == NOW - timedelta(hours=3)


def test_freshness_stale_en_procedencia_cuenta():
    s = _summary(provenance=SourceResult(status=SourceStatus.stale, data=ProvenanceInfo(),
                                         fetched_at=NOW - timedelta(hours=1)))
    assert Aggregator._freshness(s).stale is True


def test_freshness_sin_datos_as_of_none():
    f = Aggregator._freshness(_summary())
    assert f.as_of is None and f.stale is False


def test_freshness_no_usa_generated_at():
    gen = NOW + timedelta(days=30)
    t = NOW - timedelta(hours=9)
    s = _summary(generated_at=gen, ci=_ok(CISummary(status=CIStatus.success, workflow="ci"), t))
    assert Aggregator._freshness(s).as_of == t
    # y sin datos NO cae a generated_at
    assert Aggregator._freshness(_summary(generated_at=gen)).as_of is None


# ─── _provenance_block / get_summary ─────────────────────────────────────────

class _Prov:
    def __init__(self, info=None, exc=None):
        self.info, self.exc, self.calls = info, exc, 0

    async def fetch_provenance(self, project):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.info


class _NoProv:  # p. ej. Jenkins: sin fetch_provenance
    pass


def _agg(project, sources, connector):
    cfg = AppConfig(sources=sources, projects=[project])
    a = Aggregator(cfg, TTLCache(60))
    a._build_connector = lambda name: connector  # type: ignore[method-assign]
    return a


GH_SRC = {"github": SourceConfig(url="https://api.github.com", token_env="T")}


async def test_block_sin_forge_not_configured():
    a = _agg(ProjectConfig(key="p", name="P"), GH_SRC, _Prov(ProvenanceInfo()))
    r = await a._provenance_block(a._config.projects[0])
    assert r.status == SourceStatus.not_configured and r.data is None


async def test_block_forge_sin_fuente_global_not_configured():
    p = ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))
    a = _agg(p, {}, _Prov(ProvenanceInfo()))
    assert (await a._provenance_block(p)).status == SourceStatus.not_configured


async def test_block_jenkins_only_not_configured():
    p = ProjectConfig(key="p", name="P", jenkins=JenkinsProjectConfig(job="j"))
    a = _agg(p, {"jenkins": SourceConfig(url="http://j", token_env="T")}, _NoProv())
    r = await a._provenance_block(p)
    assert r.status == SourceStatus.not_configured and r.data is None


async def test_block_ok_y_cacheado():
    p = ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))
    conn = _Prov(ProvenanceInfo(default_branch="main", author="x", source="github"))
    a = _agg(p, GH_SRC, conn)
    r1 = await a._provenance_block(p)
    r2 = await a._provenance_block(p)
    assert r1.status == SourceStatus.ok and r1.data.author == "x" and r1.fetched_at is not None
    assert conn.calls == 1 and r2.data == r1.data  # cacheado bajo {key}:github:provenance
    assert await a._cache.get("p:github:provenance") is not None


async def test_block_fuente_caida_unavailable_sin_datos():
    p = ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))
    a = _agg(p, GH_SRC, _Prov(exc=ConnectorUnavailable("GitHub respondió HTTP 500.")))
    r = await a._provenance_block(p)
    assert r.status == SourceStatus.unavailable and r.data is None
    assert "test-token" not in (r.error or "")


async def test_get_summary_no_explota_con_procedencia_caida():
    p = ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))
    a = _agg(p, GH_SRC, _Prov(exc=ConnectorUnavailable("boom")))

    async def fb(project, name):
        return SourceResult(status=SourceStatus.not_configured)

    async def blk(project):
        return SourceResult(status=SourceStatus.not_configured)

    a._fetch_block = fb  # type: ignore[method-assign]
    a._release_block = blk  # type: ignore[method-assign]
    a._containers_block = blk  # type: ignore[method-assign]
    s = await a.get_summary(p)
    assert s.provenance.status == SourceStatus.unavailable and s.provenance.data is None
    assert s.freshness is not None and s.freshness.as_of is None  # nada con dato
    assert s.posture is not None


async def test_get_summary_freshness_as_of_es_el_mas_viejo_no_generated_at():
    p = ProjectConfig(key="p", name="P", github=GitHubProjectConfig(repo="o/r"))
    a = _agg(p, GH_SRC, _Prov(ProvenanceInfo(default_branch="main", source="github")))
    old = NOW - timedelta(hours=7)

    async def fb(project, name):
        if name == "github":
            return SourceResult(status=SourceStatus.ok, fetched_at=old,
                                data=CISummary(status=CIStatus.success, workflow="ci"))
        return SourceResult(status=SourceStatus.not_configured)

    async def blk(project):
        return SourceResult(status=SourceStatus.not_configured)

    a._fetch_block = fb  # type: ignore[method-assign]
    a._release_block = blk  # type: ignore[method-assign]
    a._containers_block = blk  # type: ignore[method-assign]
    summ = await a.get_summary(p)
    assert summ.provenance.status == SourceStatus.ok
    assert summ.freshness.as_of == old  # el más viejo (CI), no la procedencia recién traída
    assert summ.freshness.as_of != summ.generated_at


# ─── build_pipeline_map: etapa commit ────────────────────────────────────────

def _prov(author="felipe", is_login=True):
    return _ok(ProvenanceInfo(default_branch="main", sha_short="abc1234", message="feat: z",
                              author=author, author_is_login=is_login, authored_at=NOW,
                              html_url="https://gh/c/abc1234", source="github"), NOW)


def _full(**over):
    base = dict(
        ci=_ok(CISummary(status=CIStatus.success, workflow="ci.yml", run_number=7), NOW),
        findings=_ok(FindingsSummary(counts=SeverityCount(), total_active=0), NOW),
        quality=_ok(QualitySummary(quality_gate="OK"), NOW),
        release=_ok(ReleaseSummary(tag="v1"), NOW),
    )
    base.update(over)
    return _summary(**base)


def _image():
    return _ok(RegistryImage(present=True, tag="1", pushed_at=NOW, artifacts=1), NOW)


def _run():
    from wolf_devsecops.models import RunInfo
    return RunInfo(actor="run-actor", sha_short="r000000", message="run msg", branch="main",
                   event="push", ci_state=CIStatus.success, run_number=7,
                   html_url="https://gh/run/7", created_at=NOW)


def _commit_stage(s, runs, gh=True):
    m = build_pipeline_map(s, runs, _image(), github_configured=gh)
    return next(st for st in m.stages if st.key == "commit")


def test_map_push_sin_run_ci_muestra_commit_y_autor():
    st = _commit_stage(_full(provenance=_prov()), runs=[])  # el bug original
    assert st.state == "ok" and st.detail_code == "last_commit"
    assert st.meta["actor"] == "felipe" and st.meta["sha"] == "abc1234"
    assert st.meta["branch"] == "main" and st.meta["author_is_login"] is True
    assert st.meta["message"] == "feat: z"


def test_map_provenance_gana_sobre_runs():
    st = _commit_stage(_full(provenance=_prov("otro")), runs=[_run()])
    assert st.detail_code == "last_commit" and st.meta["actor"] == "otro"


def test_map_sin_procedencia_con_runs_last_push():
    st = _commit_stage(_full(), runs=[_run()])
    assert st.state == "ok" and st.detail_code == "last_push" and st.meta["actor"] == "run-actor"


def test_map_procedencia_unavailable_cae_a_runs():
    prov = SourceResult(status=SourceStatus.unavailable, error="x")
    st = _commit_stage(_full(provenance=prov), runs=[_run()])
    assert st.detail_code == "last_push"


def test_map_ambos_ausentes_none():
    assert _commit_stage(_full(), runs=[], gh=True).state == "none"
    assert _commit_stage(_full(), runs=[], gh=True).detail_code == "no_runs"
    assert _commit_stage(_full(), runs=[], gh=False).detail_code == "no_source"


def test_map_autor_desconocido_no_se_inventa():
    st = _commit_stage(_full(provenance=_prov(author=None, is_login=False)), runs=[_run()])
    assert st.detail_code == "last_commit"
    assert st.meta.get("actor") is None  # NO cae al actor del run
