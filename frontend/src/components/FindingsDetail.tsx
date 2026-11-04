// Detalle accionable de una fuente para un proyecto: QUÉ está afectado y CÓMO
// arreglarlo (Trivy/DefectDojo normalizados). Es DATO, nunca marcado: todo se
// renderiza como texto plano.
//
// Con `selectable` (proyectos Wolf, con edición) se habilita la selección por
// checkbox + "Remediar seleccionados", que genera un PLAN REVISABLE (tabla de
// upgrades + PR-draft descargable). NUNCA modifica el repo: aplicar queda a
// revisión humana (principio ai-safety; Wolf no escribe tu código solo).
import { useMemo, useState } from "react";

import { fetchSourceDetail } from "../api/client";
import type { RemediationPick, TrivyFinding } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useAsync, useMeta } from "../hooks";
import { useT, type TFn } from "../i18n";
import { MAX_PICKS, RemediationBranchModal } from "./RemediationBranchModal";

const SEV_CLASS: Record<string, string> = {
  CRITICAL: "crit", HIGH: "high", MEDIUM: "med", LOW: "low", UNKNOWN: "muted", INFO: "muted",
};
const SEV_KEY: Record<string, string> = {
  CRITICAL: "sev.critical", HIGH: "sev.high", MEDIUM: "sev.medium",
  LOW: "sev.low", UNKNOWN: "sev.unknown", INFO: "sev.info",
};

export function fixHint(f: TrivyFinding, t: TFn): string {
  // Remediación explícita (p. ej. mitigation de DefectDojo) gana sobre la heurística.
  if (f.fix) return f.fix;
  if (f.kind === "misconfig") {
    return f.fixed || t("fix.misconfig");
  }
  if (f.fixed) return t("fix.update", { pkg: f.pkg ?? t("fix.thePackage"), fixed: f.fixed });
  return t("fix.noPatch");
}

/** Un hallazgo es "remediable con plan" si trae una acción concreta: versión que
 * lo corrige o una mitigación explícita. */
function isFixable(f: TrivyFinding): boolean {
  return Boolean(f.fixed || f.fix);
}

function download(name: string, text: string) {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// ─── Plan de remediación revisable (sin tocar el repo) ───────────────────────
function RemediationPlan({
  projectKey, picked, t,
}: {
  projectKey: string;
  picked: TrivyFinding[];
  t: TFn;
}) {
  const prTitle = `fix(security): ${t("remediate.prSubject", { n: picked.length, key: projectKey })}`;

  const md = useMemo(() => {
    const lines: string[] = [];
    lines.push(`# ${t("remediate.plan")} — ${projectKey}`);
    lines.push("");
    lines.push(`**${t("remediate.prTitle")}:** ${prTitle}`);
    lines.push("");
    lines.push(`| ${t("remediate.pkg")} | ${t("remediate.to")} | ${t("remediate.fixes")} |`);
    lines.push("|---|---|---|");
    for (const f of picked) {
      const where = f.pkg ? `${f.pkg} ${f.installed ?? ""}`.trim() : (f.target ?? "—");
      const to = f.fixed || f.fix || "—";
      const cell = (x: string) => x.replace(/\|/g, "\\|").replace(/\n/g, " ");
      lines.push(`| ${cell(where)} | ${cell(to)} | ${cell(f.id ?? f.title ?? "—")} |`);
    }
    lines.push("");
    lines.push(`> ${t("remediate.note")}`);
    return lines.join("\n");
  }, [picked, projectKey, prTitle, t]);

  return (
    <div className="remediation-plan">
      <div className="rp-head">
        <strong>{t("remediate.plan")}</strong> · {picked.length}
        <button type="button" className="btn sm" onClick={() => download(`remediation-${projectKey}.md`, md)}>
          {t("remediate.download")}
        </button>
      </div>
      <table className="findings-table rp-table">
        <thead>
          <tr><th>{t("remediate.pkg")}</th><th>{t("remediate.to")}</th><th>{t("remediate.fixes")}</th></tr>
        </thead>
        <tbody>
          {picked.map((f, i) => (
            <tr key={`${f.id ?? "x"}-${i}`}>
              <td className="mono muted">{f.pkg ? `${f.pkg} ${f.installed ?? ""}`.trim() : (f.target ?? "—")}</td>
              <td className="fix">{f.fixed || f.fix || "—"}</td>
              <td className="mono">{f.id ?? f.title ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted rp-note">⚠︎ {t("remediate.note")}</p>
    </div>
  );
}

export function FindingsDetail({
  projectKey, source, selectable = false,
}: {
  projectKey: string;
  source: string;
  selectable?: boolean;
}) {
  const t = useT();
  const { data, loading, error } = useAsync(
    () => fetchSourceDetail(projectKey, source),
    [projectKey, source],
  );
  // 2.3 — Solape Trivy ↔ DefectDojo: en Contenedores, un CVE que también está en
  // DefectDojo se marca "en triage". El cruce se hace en la vista (no en el
  // conector Trivy) para no romper la independencia de fuentes (ADR-001). Si
  // DefectDojo no está configurado, el fetch cae a {} y no se muestra nada.
  const isContainers = source === "trivy" || source === "containers";
  const { data: triage } = useAsync<Record<string, string>>(
    () =>
      isContainers
        ? fetchSourceDetail(projectKey, "defectdojo")
            .then((d) => {
              const m: Record<string, string> = {};
              for (const g of ((d.payload?.findings as TrivyFinding[] | undefined) ?? [])) {
                if (g.id) m[g.id.toUpperCase()] = g.status ?? "active";
              }
              return m;
            })
            .catch(() => ({}))
        : Promise.resolve({}),
    [projectKey, isContainers],
  );
  const triageOf = (id: string | null): string | null =>
    isContainers && id ? (triage?.[id.toUpperCase()] ?? null) : null;
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [catFilter, setCatFilter] = useState<string | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const { user } = useAuth();
  const { data: meta } = useMeta();
  // Solo ergonomía: el servidor (operator/admin + edición) es la autoridad.
  const canWrite = user?.role === "operator" || user?.role === "admin";
  // El PR-draft/branch es feature Pro (edición); en Community se oculta el botón.
  const isProPR = meta?.features?.includes("remediation.pr") ?? false;
  const [showBranch, setShowBranch] = useState(false);
  const [writeDisabled, setWriteDisabled] = useState(false);

  if (loading) return <p className="muted findings-loading">{t("common.loading")}</p>;
  if (error) return <p className="muted">{error}</p>;

  const payload = data?.payload ?? {};
  const findings = (payload.findings as TrivyFinding[] | undefined) ?? [];
  const total = (payload.total as number | undefined) ?? findings.length;
  const shown = (payload.shown as number | undefined) ?? findings.length;

  if (findings.length === 0) {
    return <p className="muted">—</p>;
  }

  // 6.2: categorías por tipo de escaneo presentes (solo DefectDojo las trae). El
  // filtro es solo de VISTA: no re-indexa, para no romper la selección de remediación.
  const catCounts = findings.reduce<Record<string, number>>((acc, f) => {
    if (f.category) acc[f.category] = (acc[f.category] ?? 0) + 1;
    return acc;
  }, {});
  const cats = Object.keys(catCounts).sort();
  const showCatFilter = cats.length > 1;
  const activeCat = catFilter && catCounts[catFilter] ? catFilter : null;

  const fixableIdx = findings.map((f, i) => (isFixable(f) ? i : -1)).filter((i) => i >= 0);
  const toggle = (i: number) =>
    setSel((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  const allPicked = fixableIdx.length > 0 && fixableIdx.every((i) => sel.has(i));
  const toggleAll = () =>
    setSel(allPicked ? new Set() : new Set(fixableIdx));
  const picked = [...sel].sort((a, b) => a - b).map((i) => findings[i]).filter(Boolean);

  const picks: RemediationPick[] = picked
    .map((f): RemediationPick | null => {
      const ref = f.id ?? (f.title || null);
      if (!ref) return null;
      const pick: RemediationPick = { ref: ref.slice(0, 300) };
      if (f.title) pick.title = f.title.slice(0, 300);
      if (f.severity) pick.severity = f.severity.slice(0, 20);
      pick.recommended_action = fixHint(f, t).slice(0, 1000);
      return pick;
    })
    .filter((x): x is RemediationPick => x !== null);

  return (
    <div className="findings-detail">
      <div className="findings-head muted">
        {shown} / {total} — {t("report.howFix")}
      </div>

      {showCatFilter && (
        <div className="cat-filter" role="group" aria-label={t("findings.cat.filter")}>
          <button
            type="button"
            className={`btn sm chip${activeCat === null ? " on" : ""}`}
            aria-pressed={activeCat === null}
            onClick={() => setCatFilter(null)}
          >
            {t("findings.cat.all")} ({findings.length})
          </button>
          {cats.map((c) => (
            <button
              key={c}
              type="button"
              className={`btn sm chip${activeCat === c ? " on" : ""}`}
              aria-pressed={activeCat === c}
              onClick={() => setCatFilter(activeCat === c ? null : c)}
            >
              {t(`findings.cat.${c}`)} ({catCounts[c]})
            </button>
          ))}
        </div>
      )}

      {selectable && fixableIdx.length > 0 && (
        <div className="remediate-bar">
          <label className="rm-all">
            <input type="checkbox" checked={allPicked} onChange={toggleAll} />
            {t("remediate.selectAll")} ({fixableIdx.length})
          </label>
          <button
            type="button"
            className="btn sm ext-btn"
            disabled={sel.size === 0}
            onClick={() => setShowPlan(true)}
          >
            {t("remediate.button")} ({sel.size})
          </button>
          {canWrite && isProPR && (
            <span title={writeDisabled ? t("rbr.disabledTip") : sel.size > MAX_PICKS ? t("rbr.tooMany", { max: MAX_PICKS }) : undefined}>
              <button
                type="button"
                className="btn sm ext-btn"
                disabled={sel.size === 0 || sel.size > MAX_PICKS || writeDisabled}
                onClick={() => setShowBranch(true)}
              >
                {t("rbr.button")} ({sel.size})
              </button>
            </span>
          )}
        </div>
      )}

      <table className="findings-table">
        <thead>
          <tr>
            {selectable && <th aria-label={t("remediate.select")} />}
            <th>{t("findings.sev")}</th>
            <th>{t("findings.id")}</th>
            <th>{t("findings.where")}</th>
            <th>{t("findings.problem")}</th>
            <th>{t("findings.howto")}</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f, i) => (
            activeCat && f.category !== activeCat ? null : (
            <tr key={`${f.id ?? "x"}-${i}`} className={selectable && sel.has(i) ? "picked" : undefined}>
              {selectable && (
                <td className="rm-check">
                  {isFixable(f) && (
                    <input
                      type="checkbox"
                      aria-label={t("remediate.select")}
                      checked={sel.has(i)}
                      onChange={() => toggle(i)}
                    />
                  )}
                </td>
              )}
              <td>
                <span className={`sev-pill ${SEV_CLASS[f.severity] ?? "muted"}`}>
                  {t(SEV_KEY[f.severity] ?? "sev.unknown")}
                </span>
              </td>
              <td className="mono">
                {f.id ?? "—"}
                {(() => {
                  const st = triageOf(f.id);
                  return st ? (
                    <span className="triage-badge" title={t("findings.triage.tip", { state: t(`findings.triage.state.${st}`) })}>
                      {t("findings.triage.badge")}
                    </span>
                  ) : null;
                })()}
              </td>
              <td className="mono muted">
                {f.pkg ? `${f.pkg} ${f.installed ?? ""}`.trim() : (f.target ?? "—")}
              </td>
              <td>{f.title || "—"}</td>
              <td className="fix">{fixHint(f, t)}</td>
            </tr>
            )
          ))}
        </tbody>
      </table>

      {selectable && showPlan && picked.length > 0 && (
        <RemediationPlan projectKey={projectKey} picked={picked} t={t} />
      )}

      {selectable && canWrite && isProPR && showBranch && picks.length > 0 && (
        <RemediationBranchModal
          projectKey={projectKey}
          picks={picks}
          onClose={() => setShowBranch(false)}
          onWriteDisabled={() => setWriteDisabled(true)}
        />
      )}
    </div>
  );
}
