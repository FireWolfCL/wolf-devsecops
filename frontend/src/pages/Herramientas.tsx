// Herramientas — "single pane of glass": una fuente, TODOS los proyectos, sin
// salir de Wolf. Transpone lo que el backend ya agrega (GET /api/v1/tools/{tool}).
import { Fragment, useState } from "react";

import type { ToolName } from "../api/client";
import type { SourceStatus, ToolRow } from "../api/types";
import { FindingsDetail } from "../components/FindingsDetail";
import { ExtLink } from "../components/util";
import { useTool } from "../hooks";
import { useT } from "../i18n";

const TOOL_IDS: ToolName[] = ["containers", "sonarqube", "defectdojo", "github"];

const num = (v: unknown): number => (typeof v === "number" ? v : 0);
const asObj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" ? (v as Record<string, unknown>) : {};
const asStr = (v: unknown): string | null => (typeof v === "string" ? v : null);

function StatusChip({ status }: { status: SourceStatus }) {
  const t = useT();
  return (
    <span className="tool-status">
      <span className={`dot st-${status}`} /> {t(`status.${status}`)}
    </span>
  );
}

// ─── Encabezado de totales por herramienta ────────────────────────────────────
function Totals({ tool, totals, count }: { tool: ToolName; totals: Record<string, unknown>; count: number }) {
  const t = useT();
  const stat = (n: number | string, l: string, cls = "") => (
    <div className="tool-stat">
      <div className={`n ${cls}`}>{n}</div>
      <div className="l">{l}</div>
    </div>
  );
  if (tool === "containers" || tool === "defectdojo") {
    const c = asObj(totals.counts);
    return (
      <div className="tool-totals">
        {stat(count, t("tools.projects"))}
        {stat(num(c.critical), t("tools.critical"), num(c.critical) > 0 ? "crit" : "")}
        {stat(num(c.high), t("tools.high"), num(c.high) > 0 ? "high" : "")}
        {stat(num(totals.total_active), t("tools.activeFindings"))}
      </div>
    );
  }
  if (tool === "sonarqube") {
    const g = asObj(totals.gates);
    return (
      <div className="tool-totals">
        {stat(count, t("tools.projects"))}
        {stat(num(g.OK), t("tools.gateOk"), "ok")}
        {stat(num(g.ERROR), t("tools.gateError"), num(g.ERROR) > 0 ? "crit" : "")}
        {stat(num(totals.vulnerabilities), t("tools.vulns"))}
        {stat(num(totals.bugs), t("tools.bugs"))}
      </div>
    );
  }
  // github
  const ci = asObj(totals.ci);
  return (
    <div className="tool-totals">
      {stat(count, t("tools.projects"))}
      {stat(num(ci.success), t("tools.ciGreen"), "ok")}
      {stat(num(ci.failure), t("tools.ciRed"), num(ci.failure) > 0 ? "crit" : "")}
      {stat(num(totals.releases), t("tools.withRelease"))}
      {stat(num(totals.sbom), t("tools.withSbom"))}
    </div>
  );
}

// ─── Filas por proyecto ───────────────────────────────────────────────────────
function Row({
  tool, row, expandable, open, onToggle,
}: {
  tool: ToolName;
  row: ToolRow;
  expandable?: boolean;
  open?: boolean;
  onToggle?: () => void;
}) {
  const t = useT();
  const s = asObj(row.summary);
  const trProps = expandable
    ? { className: `expandable ${open ? "open" : ""}`, onClick: onToggle }
    : {};
  const name = (
    <td className="tool-proj">
      {expandable && <span className="caret" aria-hidden="true">{open ? "▾" : "▸"}</span>}
      {row.name}
    </td>
  );
  // Última celda: estado + enlace de salida a la herramienta (si lo hay).
  // stopPropagation: en filas expandibles, abrir el enlace no debe togglear.
  const statusCell = (
    <td className="tool-status-cell">
      <StatusChip status={row.status} />
      {row.link && (
        <span onClick={(e) => e.stopPropagation()}>
          <ExtLink href={row.link} className="ext-link sm">{t("tools.view")}</ExtLink>
        </span>
      )}
    </td>
  );
  if (tool === "containers" || tool === "defectdojo") {
    const c = asObj(s.counts);
    return (
      <tr {...trProps}>
        {name}
        <td className={num(c.critical) > 0 ? "num crit" : "num muted"}>{num(c.critical)}</td>
        <td className={num(c.high) > 0 ? "num high" : "num muted"}>{num(c.high)}</td>
        <td className="num muted">{num(c.medium)}</td>
        <td className="num muted">{num(c.low)}</td>
        <td className="num">{num(s.total_active)}</td>
        {statusCell}
      </tr>
    );
  }
  if (tool === "sonarqube") {
    const gate = asStr(s.quality_gate) ?? "NONE";
    const cov = s.coverage;
    const gcls = gate === "OK" ? "ok" : gate === "ERROR" ? "danger" : gate === "WARN" ? "warn" : "off";
    return (
      <tr {...trProps}>
        {name}
        <td><span className={`badge ${gcls}`}>{gate}</span></td>
        <td className="num muted">{num(s.bugs)}</td>
        <td className={num(s.vulnerabilities) > 0 ? "num high" : "num muted"}>{num(s.vulnerabilities)}</td>
        <td className="num muted">{typeof cov === "number" ? `${cov.toFixed(0)}%` : "—"}</td>
        {statusCell}
      </tr>
    );
  }
  // github: summary = { ci: {...}, release: {...} }
  const ci = asObj(s.ci);
  const rel = asObj(s.release);
  const ciStatus = asStr(ci.status) ?? "—";
  const ccls = ciStatus === "success" ? "ok" : ciStatus === "failure" ? "danger" : "off";
  return (
    <tr {...trProps}>
      {name}
      <td><span className={`badge ${ccls}`}>{ciStatus}</span></td>
      <td className="mono muted">{asStr(rel.tag) ?? "—"}</td>
      <td>{rel.sbom_url ? <span className="badge ok">SBOM</span> : <span className="muted">—</span>}</td>
      {statusCell}
    </tr>
  );
}

const HEADERS: Record<ToolName, string[]> = {
  containers: ["project", "crit", "high", "medium", "low", "total", "status"],
  defectdojo: ["project", "crit", "high", "medium", "low", "total", "status"],
  sonarqube: ["project", "gate", "bugs", "vulns", "coverage", "status"],
  github: ["project", "ci", "release", "sbom", "status"],
};

export function Herramientas() {
  const [tool, setTool] = useState<ToolName>("containers");
  const [open, setOpen] = useState<string | null>(null); // proyecto expandido
  const { data, loading, error, reload } = useTool(tool);
  const t = useT();
  const label = t(`tools.${tool}.label`);
  const blurb = t(`tools.${tool}.blurb`);
  // Contenedores (Trivy) y Hallazgos (DefectDojo) traen lista accionable → expandibles.
  const canExpand = tool === "containers" || tool === "defectdojo";
  const detailSource = tool === "containers" ? "trivy" : "defectdojo";

  return (
    <section className="tools-page">
      <div className="tools-head">
        <h2>{t("nav.tools")}</h2>
        <p className="muted">{t("tools.subtitle")}</p>
      </div>

      <div className="tool-tabs" role="tablist">
        {TOOL_IDS.map((id) => (
          <button
            key={id}
            role="tab"
            aria-selected={id === tool}
            className={`tool-tab ${id === tool ? "active" : ""}`}
            onClick={() => { setTool(id); setOpen(null); }}
          >
            {t(`tools.${id}.label`)}
          </button>
        ))}
      </div>
      <div className="tool-blurb-row">
        <p className="muted tool-blurb">{blurb}</p>
        {data?.tool_url && (
          <ExtLink href={data.tool_url} className="btn sm ext-btn">{t("tools.open", { label })}</ExtLink>
        )}
      </div>

      {loading && <p className="state">{t("tools.loading", { label })}</p>}
      {error && (
        <p className="state">
          {t("tools.error", { error })} <button className="btn sm" onClick={reload}>{t("common.retry")}</button>
        </p>
      )}
      {data && !loading && (
        data.configured_count === 0 ? (
          <p className="state">{t("tools.none")}</p>
        ) : (
          <>
            <Totals tool={tool} totals={data.totals} count={data.configured_count} />
            {canExpand && (
              <p className="muted tool-hint">{t("tools.hint")}</p>
            )}
            <table className="tool-table">
              <thead>
                <tr>{HEADERS[tool].map((h) => <th key={h}>{t(`tools.h.${h}`)}</th>)}</tr>
              </thead>
              <tbody>
                {data.rows.map((r) => {
                  const isOpen = open === r.key;
                  return (
                    <Fragment key={r.key}>
                      <Row
                        tool={tool}
                        row={r}
                        expandable={canExpand}
                        open={isOpen}
                        onToggle={() => setOpen(isOpen ? null : r.key)}
                      />
                      {canExpand && isOpen && (
                        <tr className="detail-row">
                          <td colSpan={HEADERS[tool].length}>
                            <FindingsDetail projectKey={r.key} source={detailSource} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </>
        )
      )}
    </section>
  );
}
