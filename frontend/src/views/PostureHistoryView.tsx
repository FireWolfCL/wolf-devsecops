// Tendencia histórica de postura de un proyecto: un punto por corrida de
// escaneo (GET /api/v1/projects/{key}/history). Responde: ¿el cumplimiento
// sube o baja? Gráfica SVG inline (sin dependencias) con tokens CSS del tema.
// Con 0-1 puntos no se dibuja una línea rota: se muestra el valor actual y el
// motivo. Todo valor del backend se renderiza como texto plano.
import { useMemo, useState } from "react";

import { ApiError } from "../api/http";
import { fetchHistory } from "../api/client";
import type { PostureSnapshot } from "../api/types";
import { BandPill, bandOf } from "../components/posture";
import { fmtDate } from "../components/util";
import { useAsync } from "../hooks";
import { useT, type TFn } from "../i18n";

type ViewId = "compliance" | "severity" | "coverage";

const W = 640;
const H = 260;
const PAD = { l: 40, r: 16, t: 14, b: 28 };

interface Series {
  id: string;
  label: string;
  color: string;
  values: (number | null)[];
}

function fmtPct(n: number | null | undefined): string {
  return n == null ? "—" : `${Number.isInteger(n) ? n : n.toFixed(1)}%`;
}

function shortDate(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function LineChart({ points, series, yMax, ariaLabel, fromTo }: {
  points: PostureSnapshot[];
  series: Series[];
  yMax: number;
  ariaLabel: string;
  fromTo: string;
}) {
  const times = points.map((p) => new Date(p.captured_at).getTime());
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const span = t1 - t0 || 1;
  const x = (i: number) => PAD.l + ((times[i] - t0) / span) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - Math.min(Math.max(v, 0), yMax) / yMax) * (H - PAD.t - PAD.b);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * yMax));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
      {ticks.map((tk) => (
        <g key={tk}>
          <line className="grid" x1={PAD.l} x2={W - PAD.r} y1={y(tk)} y2={y(tk)} />
          <text className="axis-t" x={PAD.l - 6} y={y(tk) + 4} textAnchor="end">{tk}</text>
        </g>
      ))}
      <text className="axis-t" x={PAD.l} y={H - 8} textAnchor="start">{shortDate(t0)}</text>
      <text className="axis-t" x={W - PAD.r} y={H - 8} textAnchor="end">{shortDate(t1)}</text>
      <title>{fromTo}</title>
      {series.map((s) => {
        // La línea se corta donde no hay dato (coverage null), no se interpola.
        const segs: string[] = [];
        let cur = "";
        s.values.forEach((v, i) => {
          if (v == null) {
            if (cur) segs.push(cur);
            cur = "";
          } else {
            cur += `${cur ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
          }
        });
        if (cur) segs.push(cur);
        return (
          <g key={s.id} style={{ color: s.color }}>
            {segs.map((d, k) => <path key={k} className="line" d={d} stroke="currentColor" />)}
            {s.values.map((v, i) =>
              v == null ? null : (
                <circle key={i} cx={x(i)} cy={y(v)} r={3.5} fill="currentColor" stroke="var(--surface)" strokeWidth={1.5}>
                  <title>{`${s.label}: ${Number.isInteger(v) ? v : v.toFixed(1)} · ${fmtDate(points[i].captured_at)}`}</title>
                </circle>
              ),
            )}
          </g>
        );
      })}
    </svg>
  );
}

function sevSeries(points: PostureSnapshot[], t: TFn): Series[] {
  return [
    { id: "crit", label: t("hist.crit"), color: "var(--s-critical)", values: points.map((p) => p.crit) },
    { id: "high", label: t("hist.high"), color: "var(--s-high)", values: points.map((p) => p.high) },
    { id: "med", label: t("hist.med"), color: "var(--s-medium)", values: points.map((p) => p.med) },
    { id: "low", label: t("hist.low"), color: "var(--s-low)", values: points.map((p) => p.low) },
  ];
}

export function PostureHistoryView({ projectKey, onBack }: { projectKey: string; onBack: () => void }) {
  const t = useT();
  const res = useAsync(
    () =>
      fetchHistory(projectKey)
        .then((h) => ({ h, notFound: false }))
        .catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 404) return { h: null, notFound: true };
          throw e;
        }),
    [projectKey],
  );
  const { loading, error, reload } = res;
  const data = res.data?.h ?? null;
  const notFound = res.data?.notFound === true;
  const [view, setView] = useState<ViewId>("compliance");

  const points = useMemo(
    () => [...(data?.snapshots ?? [])].sort((a, b) => a.captured_at.localeCompare(b.captured_at)),
    [data],
  );
  const last = points.length ? points[points.length - 1] : null;
  const prev = points.length > 1 ? points[points.length - 2] : null;
  const delta = last && prev ? last.compliance_pct - prev.compliance_pct : null;

  const errMsg = error;

  let series: Series[] = [];
  let yMax = 100;
  let what = t("hist.compliance");
  if (view === "compliance" && last) {
    series = [{ id: "c", label: t("hist.compliance"), color: `var(--b-${bandOf(last.band) === "sin_datos" || bandOf(last.band) === "evidencia_insuficiente" ? "neutral" : bandOf(last.band)})`, values: points.map((p) => p.compliance_pct) }];
  } else if (view === "coverage") {
    what = t("hist.coverage");
    series = [{ id: "v", label: t("hist.coverage"), color: "var(--accent)", values: points.map((p) => p.coverage_pct) }];
  } else if (view === "severity") {
    what = t("hist.severity");
    series = sevSeries(points, t);
    yMax = Math.max(4, ...points.flatMap((p) => [p.crit, p.high, p.med, p.low]));
  }

  const views: { id: ViewId; label: string }[] = [
    { id: "compliance", label: t("hist.compliance") },
    { id: "severity", label: t("hist.severity") },
    { id: "coverage", label: t("hist.coverage") },
  ];

  return (
    <div className="detail hist-view">
      <button type="button" className="btn-back" onClick={onBack}>{t("common.backToProject")}</button>
      <div className="detail-title-row">
        <h2 className="detail-title">{t("hist.title")}</h2>
      </div>
      <p className="muted">{t("hist.subtitle", { name: projectKey })}</p>

      {loading && !res.data && <p className="state" aria-busy="true">{t("hist.loadingAria")}…</p>}
      {notFound && <div className="alert err" role="alert">{t("hist.notFound", { key: projectKey })}</div>}
      {errMsg && (
        <div className="alert err" role="alert">
          {t("hist.loadError", { key: projectKey, error: errMsg })}{" "}
          <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button>
        </div>
      )}

      {data && points.length === 0 && <div className="alert info" role="status">{t("hist.empty")}</div>}

      {data && last && (
        <>
          <div className="hist-stats">
            <div className={`hist-stat band-${bandOf(last.band)}`}>
              <div className="k">{t("hist.current")} · {t("hist.compliance")}</div>
              <div className="v">{fmtPct(last.compliance_pct)}</div>
              <BandPill band={last.band} />
              {delta != null && (
                <div className="d">{t("hist.delta", { d: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}` })}</div>
              )}
            </div>
            <div className="hist-stat">
              <div className="k">{t("hist.coverage")}</div>
              <div className="v">{last.coverage_pct == null ? t("hist.noCoverage") : fmtPct(last.coverage_pct)}</div>
            </div>
            <div className="hist-stat">
              <div className="k">{t("hist.severity")}</div>
              <div className="v" style={{ fontSize: 18 }}>
                <span style={{ color: "var(--s-critical)" }}>{last.crit}</span> /{" "}
                <span style={{ color: "var(--s-high)" }}>{last.high}</span> /{" "}
                <span style={{ color: "var(--s-medium)" }}>{last.med}</span> /{" "}
                <span style={{ color: "var(--s-low)" }}>{last.low}</span>
              </div>
              <div className="d">{t("hist.crit")} / {t("hist.high")} / {t("hist.med")} / {t("hist.low")}</div>
            </div>
            <div className="hist-stat">
              <div className="k">{t("hist.capturedAt")}</div>
              <div className="d" style={{ fontSize: 13 }}>{fmtDate(last.captured_at)}</div>
              <div className="d">{t("hist.points", { n: points.length })}</div>
            </div>
          </div>

          {points.length < 2 ? (
            <div className="alert info" role="status">{t("hist.fewPoints")}</div>
          ) : (
            <>
              <div className="hist-views" role="group" aria-label={t("hist.viewAria")}>
                {views.map((v) => (
                  <button key={v.id} type="button" className="btn sm" aria-pressed={view === v.id} onClick={() => setView(v.id)}>
                    {v.label}
                  </button>
                ))}
              </div>
              <div className="hist-chart">
                <LineChart
                  points={points}
                  series={series}
                  yMax={yMax}
                  ariaLabel={t("hist.chartAria", { what, key: projectKey })}
                  fromTo={t("hist.xRange", { from: fmtDate(points[0].captured_at), to: fmtDate(last.captured_at) })}
                />
                {view === "severity" && (
                  <div className="hist-legend">
                    {series.map((s) => (
                      <span key={s.id}><i style={{ background: s.color }} aria-hidden="true" />{s.label}</span>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          <h3 style={{ marginTop: 20 }}>{t("hist.tableTitle")}</h3>
          <div className="hist-scroll">
            <table className="hist-table">
              <thead>
                <tr>
                  <th>{t("hist.capturedAt")}</th>
                  <th className="num">{t("hist.compliance")}</th>
                  <th>{/* banda */}</th>
                  <th className="num">{t("hist.coverage")}</th>
                  <th className="num">{t("hist.crit")}</th>
                  <th className="num">{t("hist.high")}</th>
                  <th className="num">{t("hist.med")}</th>
                  <th className="num">{t("hist.low")}</th>
                </tr>
              </thead>
              <tbody>
                {[...points].reverse().slice(0, 15).map((p) => (
                  <tr key={p.captured_at}>
                    <td>{fmtDate(p.captured_at)}</td>
                    <td className="num">{fmtPct(p.compliance_pct)}</td>
                    <td><BandPill band={p.band} /></td>
                    <td className="num">{fmtPct(p.coverage_pct)}</td>
                    <td className="num">{p.crit}</td>
                    <td className="num">{p.high}</td>
                    <td className="num">{p.med}</td>
                    <td className="num">{p.low}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
