"""Mapa de pipeline "usted está aquí" (solo lectura, ADR-001).

Arma un `PipelineMap` de 6 etapas (commit → ci → quality → security → registry →
release) desde el ProjectSummary YA agregado + los últimos runs de GitHub + la
presencia de imagen en Harbor. No escanea ni ejecuta nada.

Regla de la casa: nunca falsear estado operacional. Fuente ausente o caída sin dato
=> state="none" (gris, "sin datos"), JAMÁS verde. El backend entrega estados y
`detail_code` estables; los textos viven en i18n del frontend.

Algoritmo del pin (`current_index`):
  1. Recorrer las etapas en orden; el pin es el índice de la PRIMERA etapa con
     state en {fail, pending, warn} (ahí el proyecto está parado/trabajando).
  2. Si ninguna bloquea, el pin es la ÚLTIMA etapa con state == "ok" (lo más lejos
     que se llegó).
  3. Las etapas "none" NO capturan el pin (se saltan). Si no hay ninguna etapa con
     dato, current_index = -1 (sin pin).
"""

from __future__ import annotations

from ..core.aggregator import Aggregator
from ..core.config import ProjectConfig
from ..models import (
    CIStatus,
    PipelineMap,
    PipelineStage,
    PipelineStageKey,
    PipelineStageState,
    ProjectSummary,
    PushItem,
    RegistryImage,
    RunInfo,
    SourceResult,
    SourceStatus,
)

_BLOCKING: set[str] = {"fail", "pending", "warn"}


def _has_data(block: SourceResult) -> bool:
    return block.status in (SourceStatus.ok, SourceStatus.stale) and block.data is not None


def _no_data_code(block: SourceResult) -> str:
    """Por qué no hay dato: fuente no configurada vs. configurada pero caída."""
    return "no_source" if block.status == SourceStatus.not_configured else "source_unavailable"


def _push(r: RunInfo) -> PushItem:
    return PushItem(
        actor=r.actor,
        when=r.created_at.isoformat() if r.created_at else None,
        ci_state=r.ci_state,
        sha_short=r.sha_short,
        message=r.message,
        html_url=r.html_url,
    )


def _stage(key: PipelineStageKey, state: PipelineStageState, code: str,
           url: str | None = None, **meta) -> PipelineStage:
    return PipelineStage(key=key, state=state, detail_code=code, url=url,
                         meta={k: v for k, v in meta.items() if v is not None})


def compute_current_index(stages: list[PipelineStage]) -> int:
    for i, st in enumerate(stages):
        if st.state in _BLOCKING:
            return i
    last_ok = -1
    for i, st in enumerate(stages):
        if st.state == "ok":
            last_ok = i
    return last_ok


def build_pipeline_map(
    summary: ProjectSummary,
    runs: list[RunInfo],
    registry: SourceResult,
    github_configured: bool,
) -> PipelineMap:
    links = summary.links or {}
    stages: list[PipelineStage] = []

    # commit — procedencia del forge (último commit de main + autor), INDEPENDIENTE de
    # que haya corrido CI. Fallback al run más reciente si no hay procedencia (compat).
    prov = summary.provenance.data if (summary.provenance and summary.provenance.data) else None
    if prov is not None:
        stages.append(_stage(
            "commit", "ok", "last_commit", prov.html_url or links.get("github"),
            actor=prov.author, message=prov.message, sha=prov.sha_short,
            branch=prov.default_branch, when=prov.authored_at.isoformat() if prov.authored_at else None,
            author_is_login=prov.author_is_login,
        ))
    elif runs:
        r = runs[0]
        stages.append(_stage(
            "commit", "ok", "last_push", r.html_url or links.get("github"),
            actor=r.actor, message=r.message, sha=r.sha_short, branch=r.branch,
            when=r.created_at.isoformat() if r.created_at else None,
        ))
    else:
        stages.append(_stage("commit", "none", "no_runs" if github_configured else "no_source",
                             links.get("github")))

    # ci
    ci = summary.ci
    if _has_data(ci):
        d = ci.data
        url = str(d.html_url) if d.html_url else None
        meta = {"run_number": d.run_number, "branch": d.branch,
                "stale": True if ci.status == SourceStatus.stale else None}
        if d.status == CIStatus.success:
            stages.append(_stage("ci", "ok", "ci_passing", url, **meta))
        elif d.status == CIStatus.failure:
            stages.append(_stage("ci", "fail", "ci_failing", url, **meta))
        elif d.status == CIStatus.running:
            stages.append(_stage("ci", "pending", "awaiting_ci", url, **meta))
        else:
            stages.append(_stage("ci", "none", "no_runs", url))
    else:
        stages.append(_stage("ci", "none", _no_data_code(ci)))

    # quality
    q = summary.quality
    if _has_data(q):
        d = q.data
        url = str(d.url) if d.url else links.get("sonarqube")
        meta = {"bugs": d.bugs, "vulnerabilities": d.vulnerabilities, "coverage": d.coverage}
        gate = d.quality_gate
        if gate == "OK":
            stages.append(_stage("quality", "ok", "gate_ok", url, **meta))
        elif gate == "WARN":
            stages.append(_stage("quality", "warn", "gate_warn", url, **meta))
        elif gate == "ERROR":
            stages.append(_stage("quality", "fail", "gate_error", url, **meta))
        else:
            stages.append(_stage("quality", "none", "no_gate", url))
    else:
        stages.append(_stage("quality", "none", _no_data_code(q), links.get("sonarqube")))

    # security (hallazgos DefectDojo + CVEs de contenedor Harbor/Trivy)
    sec_blocks = [summary.findings, summary.containers]
    configured = [b for b in sec_blocks if b.status != SourceStatus.not_configured]
    with_data = [b for b in configured if _has_data(b)]
    crit = sum(b.data.counts.critical for b in with_data)
    high = sum(b.data.counts.high for b in with_data)
    sec_url = links.get("defectdojo") or links.get("harbor")
    if crit > 0:
        stages.append(_stage("security", "fail", "crit_unmanaged", sec_url, critical=crit, high=high))
    elif high > 0:
        stages.append(_stage("security", "warn", "high_present", sec_url, critical=0, high=high))
    elif not configured:
        stages.append(_stage("security", "none", "no_source"))
    elif len(with_data) == len(configured):
        stages.append(_stage("security", "ok", "clean", sec_url, critical=0, high=0))
    else:
        # Alguna fuente de seguridad configurada no respondió: no se puede afirmar "limpio".
        stages.append(_stage("security", "none", "source_unavailable", sec_url))

    # registry
    if _has_data(registry):
        img: RegistryImage = registry.data
        if img.present:
            stages.append(_stage(
                "registry", "ok", "image_published", links.get("harbor"), tag=img.tag,
                when=img.pushed_at.isoformat() if img.pushed_at else None,
            ))
        else:
            stages.append(_stage("registry", "pending", "awaiting_image", links.get("harbor")))
    else:
        stages.append(_stage("registry", "none", _no_data_code(registry), links.get("harbor")))

    # release
    rel = summary.release
    if _has_data(rel):
        d = rel.data
        if d.tag or d.name:
            stages.append(_stage(
                "release", "ok", "released", str(d.html_url) if d.html_url else None,
                name=d.name, version=d.tag, sbom=bool(d.sbom_url),
            ))
        else:
            stages.append(_stage("release", "pending", "awaiting_release", links.get("github")))
    else:
        stages.append(_stage("release", "none", _no_data_code(rel)))

    pushes = [_push(r) for r in runs]
    return PipelineMap(
        stages=stages,
        current_index=compute_current_index(stages),
        last_push=pushes[0] if pushes else None,
        recent_pushes=pushes,
    )


async def get_pipeline_map(aggregator: Aggregator, project: ProjectConfig) -> PipelineMap:
    """Orquesta las lecturas (todas cacheadas/degradables) y arma el mapa."""
    import asyncio

    summary, runs, registry = await asyncio.gather(
        aggregator.get_summary(project),
        aggregator.get_recent_runs(project, 5),
        aggregator.get_registry_image(project),
    )
    return build_pipeline_map(summary, runs, registry, github_configured=project.github is not None)
