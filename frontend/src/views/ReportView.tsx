// Reporte documental por proyecto: un entregable imprimible (PDF vía navegador)
// o descargable como Markdown. Por cada herramienta: QUÉ está afectado y CÓMO
// remediarlo. Todo es DATO del backend, renderizado como texto plano.
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

function whereOf(f: TrivyFinding): string {
  return f.pkg ? `${f.pkg} ${f.installed ?? ""}`.trim() : (f.target ?? "—");
}

// ─── Export a Markdown (cliente): mismo contenido, en el idioma activo ────────
function toMarkdown(r: ProjectReport, t: T): string {
  const lines: string[] = [];
  lines.push(`# ${t("report.title")} — ${r.name}`);
  lines.push("");
  lines.push(`- ${r.org}`);
  lines.push(`- ${t("report.generated")}: ${fmtDate(r.generated_at)}`);
  lines.push(`- ${r.posture_summary}`);
  lines.push("");
  for (const s of r.sections) {
    lines.push(`## ${s.tool_label} — ${statusLabel(t, s.status)}`);
    lines.push("");
    lines.push(s.headline);
    if (s.note) lines.push(`\n${s.note}`);
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

function FindingsTable({ findings, t }: { findings: TrivyFinding[]; t: T }) {
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
          <tr key={`${f.id ?? "x"}-${i}`}>
            <td><span className={`sev-pill ${SEV_CLASS[f.severity] ?? "muted"}`}>{sevLabel(t, f.severity)}</span></td>
            <td className="mono">{f.id ?? "—"}</td>
            <td className="mono muted">{whereOf(f)}</td>
            <td>{f.title || "—"}</td>
            <td className="fix">{fixHint(f, t)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Section({ s, t }: { s: ReportSection; t: T }) {
  return (
    <section className="report-section">
      <h2>
        {s.tool_label}{" "}
        <span className={`tool-status`}><span className={`dot st-${s.status}`} /> {statusLabel(t, s.status)}</span>
        {s.link && <ExtLink href={s.link} className="ext-link sm report-noprint">{t("common.open")}</ExtLink>}
      </h2>
      <p className="report-headline">{s.headline}</p>
      {s.note && <p className="muted">{s.note}</p>}
      {s.findings.length > 0 && <FindingsTable findings={s.findings} t={t} />}
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
          <p className="report-posture">{r.posture_summary}</p>
        </header>
        {r.sections.map((s) => <Section key={s.source} s={s} t={t} />)}
        <footer className="report-foot muted">{t("report.footer")}</footer>
      </article>
    </div>
  );
}
