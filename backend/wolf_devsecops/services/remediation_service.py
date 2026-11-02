"""Remediación asistida (Feature D, ADR-017): crea branch aislado + PR DRAFT.

Convierte el panel de visor a asistente de remediación SIN ceder el control del
código: GitHub sigue siendo la fuente de verdad y un HUMANO sigue siendo quien
mergea. Límites no negociables (ADR-017), aplicados aquí y en GitHubWriteClient:
- Branch con prefijo reservado `wolf-sec/fix-<fecha>-<slug>` (determinista).
- NUNCA mergea, nunca toca main/default/otros branches, nunca force-push.
- Idempotente: si el branch/PR ya existe, no duplica — devuelve el existente.
- Todo bajo confirmación explícita por acción (lo exige el router).

El PAT de escritura lo resuelve el router (token_env dedicado, ADR-002) y se lo pasa
en el `write_client`; este servicio nunca ve el valor del token.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..connectors.github import GitHubWriteClient, GitHubWriteError
from ..core.config import ProjectConfig
from ..models import RemediationBranchResult, RemediationFinding
from . import audit_service


class RemediationError(Exception):
    """Fallo de remediación (GitHub rechazó, o config inválida). Mensaje saneado."""


def _slug(project_key: str, refs: list[str]) -> str:
    """Slug corto y DETERMINISTA del conjunto de findings: misma selección → mismo
    branch (base de la idempotencia). sha1 solo como identificador, no es seguridad."""
    seed = "|".join([project_key, *sorted(refs)])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]  # noqa: S324


def _proposal_markdown(
    project: ProjectConfig, findings: list[RemediationFinding], branch: str
) -> str:
    lines = [
        f"# Propuesta de remediación — {project.name}",
        "",
        "> Generada por Wolf DevSecOps (ADR-017). Branch aislado + PR **draft**.",
        "> **Un humano revisa y mergea**: el panel nunca mergea ni toca el branch por defecto.",
        "",
        f"- Proyecto: `{project.key}`",
        f"- Branch: `{branch}`",
        f"- Hallazgos seleccionados: {len(findings)}",
        "",
        "## Hallazgos",
        "",
    ]
    for f in findings:
        sev = (f.severity or "n/d").upper()
        lines.append(f"### [{sev}] {f.title or f.ref}")
        lines.append(f"- Referencia: `{f.ref}`")
        if f.recommended_action:
            lines.append(f"- Acción recomendada: {f.recommended_action}")
        lines.append("")
    return "\n".join(lines)


async def create_remediation_branch(
    session: AsyncSession,
    *,
    project: ProjectConfig,
    picked_findings: list[RemediationFinding],
    write_client: GitHubWriteClient,
    actor_user_id: str | None,
    actor_username: str | None,
) -> RemediationBranchResult:
    if project.github is None:
        raise RemediationError("El proyecto no define un repositorio github.")
    repo = project.github.repo
    refs = [f.ref for f in picked_findings]
    slug = _slug(project.key, refs)
    date = datetime.now(UTC).strftime("%Y%m%d")
    branch = f"wolf-sec/fix-{date}-{slug}"
    proposal_path = f"wolf-sec/remediation-{date}-{slug}.md"
    proposal = _proposal_markdown(project, picked_findings, branch)

    try:
        default_branch, head_sha = await write_client.default_branch_and_sha(repo)
        created_branch = await write_client.create_branch(repo, branch, head_sha)
        existing_sha = await write_client.get_file_sha(repo, proposal_path, branch)
        await write_client.put_file(
            repo,
            proposal_path,
            proposal,
            message=f"wolf-sec: propuesta de remediación {slug}",
            branch=branch,
            sha=existing_sha,
        )
        pr = await write_client.find_open_pull(repo, branch, default_branch)
        pr_created = False
        if pr is None:
            try:
                pr = await write_client.open_pull(
                    repo,
                    branch,
                    default_branch,
                    title=f"wolf-sec: remediación ({len(refs)} hallazgo(s))",
                    body=proposal,
                    draft=True,
                )
                pr_created = True
            except GitHubWriteError as exc:
                if str(exc) == "already_exists":
                    pr = await write_client.find_open_pull(repo, branch, default_branch)
                    if pr is None:
                        raise
                else:
                    raise
    except GitHubWriteError as exc:
        # GitHub rechazó: audita el motivo (saneado) y propaga como RemediationError.
        await audit_service.record(
            session,
            action="remediation.rejected",
            result="failure",
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            target_type="project",
            target_id=project.key,
            detail={"branch": branch, "reason": str(exc), "findings": refs},
        )
        raise RemediationError(str(exc)) from exc

    already_existed = not pr_created
    applied = created_branch or pr_created
    pr_url = pr.get("html_url") if isinstance(pr, dict) else None
    pr_number = pr.get("number") if isinstance(pr, dict) else None

    await audit_service.record(
        session,
        action="remediation.branch_pr_created",
        actor_user_id=actor_user_id,
        actor_username=actor_username,
        target_type="project",
        target_id=project.key,
        detail={
            "branch": branch,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "findings": refs,
            "idempotency_key": branch,
            "already_existed": already_existed,
        },
    )
    return RemediationBranchResult(
        branch=branch, pr_url=pr_url, applied=applied, already_existed=already_existed
    )
