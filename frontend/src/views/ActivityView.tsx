import { useEffect, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";

import { fetchEventReport, fetchEvents } from "../api/client";
import { useI18n } from "../i18n";
import type { EventItem, EventReport } from "../api/types";

/**
 * Reporte de avance: cuánto hemos PARCHADO y MEJORADO en un período.
 * Agrega los eventos (vulns nuevas/resueltas, mejoras/empeoramientos) en tarjetas
 * de conteo, una tabla por proyecto y el detalle reciente. Bilingüe es/en.
 */

type Lang = "es" | "en";
const PERIODS = [7, 30, 90];

const KIND_COLOR: Record<string, string> = {
  improved: "#34d399", resolved_critical: "#34d399", resolved_high: "#34d399",
  worsened: "#f87171", new_critical: "#f87171", new_high: "#f5a524",
};

function label(kind: string, lang: Lang): string {
  const es = lang === "es";
  switch (kind) {
    case "improved": return es ? "Mejoras de postura" : "Posture improvements";
    case "worsened": return es ? "Retrocesos" : "Regressions";
    case "new_critical": return es ? "Críticos nuevos" : "New criticals";
    case "resolved_critical": return es ? "Críticos resueltos" : "Criticals resolved";
    case "new_high": return es ? "Altos nuevos" : "New highs";
    case "resolved_high": return es ? "Altos resueltos" : "Highs resolved";
    default: return kind;
  }
}

const ORDER = ["resolved_critical", "resolved_high", "improved", "new_critical", "new_high", "worsened"];

export function ActivityView() {
  const { lang: appLang } = useI18n();
  const lang: Lang = appLang === "en" ? "en" : "es";
  const es = lang === "es";
  const navigate = useNavigate();
  const [days, setDays] = useState(30);
  const [report, setReport] = useState<EventReport | null>(null);
  const [recent, setRecent] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchEventReport(days), fetchEvents(100)])
      .then(([r, e]) => { setReport(r); setRecent(e.events); })
      .catch(() => { setReport(null); setRecent([]); })
      .finally(() => setLoading(false));
  }, [days]);

  const totals = report?.totals ?? {};
  const resolved = (totals.resolved_critical ?? 0) + (totals.resolved_high ?? 0);
  const introduced = (totals.new_critical ?? 0) + (totals.new_high ?? 0);

  return (
    <div style={{ padding: "24px 32px", maxWidth: 980, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>{es ? "Reporte de avance" : "Progress report"}</h2>
          <p style={{ margin: "4px 0 0", color: "var(--muted)", fontSize: 13 }}>
            {es ? "Lo que hemos parchado y mejorado en el período." : "What we've patched and improved in the period."}
          </p>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {PERIODS.map((p) => (
            <button key={p} type="button" onClick={() => setDays(p)}
              style={{ ...chip, ...(days === p ? chipOn : {}) }}>
              {es ? `${p} días` : `${p} days`}
            </button>
          ))}
        </div>
      </div>

      {loading && <p style={{ color: "var(--muted)" }}>{es ? "Cargando…" : "Loading…"}</p>}

      {!loading && (
        <>
          {/* Resumen grande */}
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", margin: "18px 0" }}>
            <div style={{ ...big, borderColor: "#34d39955" }}>
              <span style={{ ...bigN, color: "#34d399" }}>{resolved}</span>
              <span style={bigL}>{es ? "hallazgos resueltos / parchados" : "findings resolved / patched"}</span>
            </div>
            <div style={{ ...big, borderColor: "#f8717155" }}>
              <span style={{ ...bigN, color: "#f87171" }}>{introduced}</span>
              <span style={bigL}>{es ? "hallazgos nuevos aparecidos" : "new findings appeared"}</span>
            </div>
            <div style={{ ...big, borderColor: "#6f9fcf55" }}>
              <span style={{ ...bigN, color: "#6f9fcf" }}>{totals.improved ?? 0}</span>
              <span style={bigL}>{es ? "proyectos que mejoraron de banda" : "projects that improved band"}</span>
            </div>
          </div>

          {/* Desglose por tipo */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 10 }}>
            {ORDER.filter((k) => (totals[k] ?? 0) > 0 || true).map((k) => (
              <div key={k} style={tile}>
                <span style={{ ...tileN, color: KIND_COLOR[k] }}>{totals[k] ?? 0}</span>
                <span style={tileL}>{label(k, lang)}</span>
              </div>
            ))}
          </div>

          {/* Por proyecto */}
          {report && report.by_project.length > 0 && (
            <>
              <h3 style={{ marginTop: 28 }}>{es ? "Por proyecto" : "By project"}</h3>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ color: "var(--muted)", textAlign: "left" }}>
                    <th style={th}>{es ? "Proyecto" : "Project"}</th>
                    <th style={th}>{es ? "Resueltos" : "Resolved"}</th>
                    <th style={th}>{es ? "Nuevos crít." : "New crit."}</th>
                    <th style={th}>{es ? "Mejoró" : "Improved"}</th>
                    <th style={th}>{es ? "Empeoró" : "Worsened"}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.by_project.map((p) => (
                    <tr key={p.project_key} style={{ borderTop: "1px solid var(--border)", cursor: "pointer" }}
                      onClick={() => navigate(`/projects/${encodeURIComponent(p.project_key)}`)}>
                      <td style={{ ...td, fontFamily: "var(--mono)" }}>{p.project_key}</td>
                      <td style={{ ...td, color: "#34d399" }}>{p.resolved_critical}</td>
                      <td style={{ ...td, color: "#f87171" }}>{p.new_critical}</td>
                      <td style={td}>{p.improved}</td>
                      <td style={td}>{p.worsened}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {/* Detalle reciente */}
          <h3 style={{ marginTop: 28 }}>{es ? "Detalle reciente" : "Recent detail"}</h3>
          {recent.length === 0 && <p style={{ color: "var(--muted)" }}>{es ? "Sin novedades en el período." : "Nothing new in the period."}</p>}
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {recent.map((ev) => (
              <li key={ev.id} style={{ display: "flex", gap: 10, padding: "8px 0", borderBottom: "1px solid var(--border)", fontSize: 13 }}>
                <span style={{ color: KIND_COLOR[ev.kind] ?? "#6f9fcf", fontFamily: "var(--mono)", minWidth: 150 }}>{label(ev.kind, lang)}</span>
                <span style={{ fontFamily: "var(--mono)", color: "var(--muted)", minWidth: 110 }}>{ev.project_key}</span>
                <span style={{ color: "var(--muted)" }}>
                  {ev.band_from && ev.band_to ? `${ev.band_from} → ${ev.band_to}` : ""}
                  {ev.crit_delta ? ` crit ${ev.crit_delta > 0 ? "+" : ""}${ev.crit_delta}` : ""}
                  {ev.high_delta ? ` high ${ev.high_delta > 0 ? "+" : ""}${ev.high_delta}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

const chip: CSSProperties = { background: "transparent", border: "1px solid var(--border)", color: "var(--muted)", padding: "5px 10px", borderRadius: 8, cursor: "pointer", fontSize: 12 };
const chipOn: CSSProperties = { background: "var(--accent)", color: "#0b1016", borderColor: "var(--accent)", fontWeight: 700 };
const big: CSSProperties = { flex: "1 1 200px", background: "var(--surface)", border: "1px solid", borderRadius: 12, padding: "16px 18px", display: "flex", flexDirection: "column", gap: 4 };
const bigN: CSSProperties = { fontSize: 34, fontWeight: 800, lineHeight: 1 };
const bigL: CSSProperties = { fontSize: 12.5, color: "var(--muted)" };
const tile: CSSProperties = { background: "var(--bg-alt)", border: "1px solid var(--border)", borderRadius: 10, padding: "12px 14px", display: "flex", flexDirection: "column", gap: 2 };
const tileN: CSSProperties = { fontSize: 22, fontWeight: 700 };
const tileL: CSSProperties = { fontSize: 11.5, color: "var(--muted)" };
const th: CSSProperties = { padding: "8px 10px", fontWeight: 600 };
const td: CSSProperties = { padding: "8px 10px" };
