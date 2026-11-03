// Reporte documental por proyecto: un entregable imprimible (PDF vía navegador)
// o descargable como Markdown. Por cada herramienta: QUÉ está afectado y CÓMO
// remediarlo. Todo es DATO del backend; los TEXTOS se arman aquí en el idioma
// activo (el backend ya no redacta frases, para no mezclar idiomas).
import { Fragment } from "react";

import { fixHint } from "../components/FindingsDetail";
import { ExtLink, fmtDate } from "../components/util";
import { useReport } from "../hooks";
import { useT, type TFn } from "../i18n";
import type { ProjectReport, ReportSection, TrivyFinding } from "../api/types";

type T = TFn;

const SEV_KEY: Record<string, string> = {
  CRITICAL: "sev.critical", HIGH: "sev.high", MEDIUM: "sev.medium",
  LOW: "sev.low", UNKNOWN: "sev.unknown", INFO: "sev.info",
};
const SEV_CLASS: Record<string, string> = {
  CRITICAL: "crit", HIGH: "high", MEDIUM: "med", LOW: "low", UNKNOWN: "muted", INFO: "muted",
};
const sevLabel = (t: T, sev: string) => t(SEV_KEY[sev] ?? "sev.unknown");
const statusLabel = (t: T, st: string) => t(`status.${st}`);
const toolLabel = (t: T, source: string) => t(`report.label.${source}`);

function whereOf(f: TrivyFinding): string {
  return f.pkg ? `${f.pkg} ${f.installed ?? ""}`.trim() : (f.target ?? "—");
}

const LIVE = (st: string) => st === "ok" || st === "stale";

// ─── Línea humana (headline) de cada sección, en el idioma activo ────────────
function headlineOf(s: ReportSection, t: T): string {
  if (s.status === "not_configured") return t("report.notConnected");
  if (!LIVE(s.status)) return t("report.unavailable");
  if (s.kind === "quality" && s.quality) {
    const cov = s.quality.coverage != null ? `${s.quality.coverage.toFixed(0)}%` : "—";
    return t("report.quality", {
      gate: s.quality.quality_gate, bugs: s.quality.bugs,
      vulns: s.quality.vulnerabilities, cov,
    });
  }
  if (s.kind === "ci") {
    return s.ci
      ? t("report.ci", { status: s.ci.status, workflow: s.ci.workflow })
      : t("report.ciUnavailable");
  }
  // findings (contenedores / DefectDojo / Sonar)
  const c = s.counts;
  return t("report.counts", {
    total: s.total_active ?? 0, crit: c?.critical ?? 0, high: c?.high ?? 0,
    medium: c?.medium ?? 0, low: c?.low ?? 0,
  });
}

function noteOf(s: ReportSection, t: T): string | null {
  if (s.kind === "ci" && LIVE(s.status) && s.release) {
    const sbom = s.release.sbom_url ? t("report.sbom.attached") : t("report.sbom.missing");
    return t("report.release", { tag: s.release.tag || "—", sbom });
  }
  return null;
}

function postureLine(r: ProjectReport, t: T): string {
  if (!r.posture || r.posture.score == null) return t("report.noPosture");
  return t("report.posture", {
    score: r.posture.score, band: t(`band.${r.posture.band}`),
    coverage: r.posture.coverage, v: r.posture.rubric_version,
  });
}

// ─── Export a Markdown (cliente): mismo contenido, en el idioma activo ────────
function toMarkdown(r: ProjectReport, t: T): string {
  const lines: string[] = [];
  lines.push(`# ${t("report.title")} — ${r.name}`);
  lines.push("");
  lines.push(`- ${r.org}`);
  lines.push(`- ${t("report.generated")}: ${fmtDate(r.generated_at)}`);
  lines.push(`- ${postureLine(r, t)}`);
  lines.push("");
  for (const s of r.sections) {
    lines.push(`## ${toolLabel(t, s.source)} — ${statusLabel(t, s.status)}`);
    lines.push("");
    lines.push(headlineOf(s, t));
    const note = noteOf(s, t);
    if (note) lines.push(`\n${note}`);
    if (s.link) lines.push(`\n${s.link}`);
    if (s.findings.length > 0) {
      lines.push("");
      lines.push(`| ${t("findings.sev")} | ${t("findings.id")} | ${t("findings.where")} | ${t("findings.problem")} | ${t("findings.howto")} |`);
      lines.push("|---|---|---|---|---|");
      for (const f of s.findings) {
        const cell = (x: string) => x.replace(/\|/g, "\\|").replace(/\n/g, " ");
        lines.push(
          `| ${sevLabel(t, f.severity)} | ${cell(f.id ?? "—")} | ${cell(whereOf(f))} | ${cell(f.title || "—")} | ${cell(fixHint(f, t))} |`,
        );
      }
      // Evidencia (issues de Sonar): bloque de código por hallazgo que la trae.
      for (const f of s.findings) {
        if (f.evidence && f.evidence.length > 0) {
          lines.push("");
          lines.push(`**${t("report.evidence")}** — \`${whereOf(f)}\``);
          lines.push("```");
          for (const e of f.evidence) lines.push(`${e.line}  ${e.code}`);
          lines.push("```");
        }
      }
    }
    lines.push("");
  }
  lines.push("---");
  lines.push(`_${t("report.footer")}_`);
  return lines.join("\n");
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

function Evidence({ f, t }: { f: TrivyFinding; t: T }) {
  if (!f.evidence || f.evidence.length === 0) return null;
  return (
    <tr className="evidence-row">
      <td />
      <td colSpan={4}>
        <div className="evidence">
          <div className="evidence-head muted">{t("report.evidence")} · <span className="mono">{whereOf(f)}</span></div>
          {/* Texto plano: cada línea se renderiza como {texto} (React escapa). */}
          <pre className="evidence-code">
            {f.evidence.map((e, i) => (
              <span key={i} className="ev-line">
                <span className="ev-num">{e.line}</span>{e.code === "" ? " " : e.code}{"\n"}
              </span>
            ))}
          </pre>
        </div>
      </td>
    </tr>
  );
}

function FindingsTable({ findings, t, withEvidence }: { findings: TrivyFinding[]; t: T; withEvidence: boolean }) {
  return (
    <table className="findings-table report-table">
      <thead>
        <tr>
          <th>{t("findings.sev")}</th><th>{t("findings.id")}</th><th>{t("findings.where")}</th>
          <th>{t("findings.problem")}</th><th>{t("findings.howto")}</th>
        </tr>
      </thead>
      <tbody>
        {findings.map((f, i) => (
          <Fragment key={`${f.id ?? "x"}-${i}`}>
            <tr>
              <td><span className={`sev-pill ${SEV_CLASS[f.severity] ?? "muted"}`}>{sevLabel(t, f.severity)}</span></td>
              <td className="mono">{f.id ?? "—"}</td>
              <td className="mono muted">{whereOf(f)}</td>
              <td>{f.title || "—"}</td>
              <td className="fix">{fixHint(f, t)}</td>
            </tr>
            {withEvidence && <Evidence f={f} t={t} />}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

function Section({ s, t }: { s: ReportSection; t: T }) {
  const note = noteOf(s, t);
  return (
    <section className="report-section">
      <h2>
        {toolLabel(t, s.source)}{" "}
        <span className={`tool-status`}><span className={`dot st-${s.status}`} /> {statusLabel(t, s.status)}</span>
        {s.link && <ExtLink href={s.link} className="ext-link sm report-noprint">{t("common.open")}</ExtLink>}
      </h2>
      <p className="report-headline">{headlineOf(s, t)}</p>
      {note && <p className="muted">{note}</p>}
      {s.findings.length > 0 && (
        <FindingsTable findings={s.findings} t={t} withEvidence={s.source === "sonarqube"} />
      )}
    </section>
  );
}

export function ReportView({ projectKey, onBack }: { projectKey: string; onBack: () => void }) {
  const { data: r, loading, error } = useReport(projectKey);
  const t = useT();

  if (loading) return <p className="state">{t("report.generating")}</p>;
  if (error || !r) return <p className="state">{t("report.loadError", { error: error ?? "—" })}</p>;

  return (
    <div className="report">
      <div className="report-actions report-noprint">
        <button type="button" className="btn-back" onClick={onBack}>{t("common.backToProject")}</button>
        <div className="spacer" />
        <button type="button" className="btn sm" onClick={() => download(`report-${r.key}.md`, toMarkdown(r, t))}>
          {t("common.downloadMd")}
        </button>
        <button type="button" className="btn sm ext-btn" onClick={() => window.print()}>
          {t("common.print")}
        </button>
      </div>

      <article className="report-doc">
        <header className="report-head">
          <h1>{t("report.title")} — {r.name}</h1>
          <p className="muted">
            {r.org} · {t("report.generated")} {fmtDate(r.generated_at)}
          </p>
          <p className="report-posture">{postureLine(r, t)}</p>
        </header>
        {r.sections.map((s) => <Section key={s.source} s={s} t={t} />)}
        <footer className="report-foot muted">{t("report.footer")}</footer>
      </article>
    </div>
  );
}
