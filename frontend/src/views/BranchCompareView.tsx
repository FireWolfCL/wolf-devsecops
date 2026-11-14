// Comparador de postura entre ramas (ADR-024). Responde "¿cuál rama está mejor?"
// comparando el estado AL HEAD de cada rama (no es una línea de tiempo: eso es la
// Tendencia). Read-only y al vuelo: el backend NO escanea ni persiste. MVP: solo
// el check ci-green es consciente de rama; el resto queda `na` y se declara en la
// nota. Todo valor del backend se renderiza como texto plano.
import { useEffect, useMemo, useState } from "react";

import { ApiError } from "../api/http";
import { compareRefs, fetchProject } from "../api/client";
import type { PostureComparison, RefPosture } from "../api/types";
import { BandPill, bandOf } from "../components/posture";
import { fmtDate } from "../components/util";
import { useAsync } from "../hooks";
import { useT, type TFn } from "../i18n";

const MAX_REFS = 6; // espejo de _MAX_COMPARE_REFS del backend

// Título legible de un check (reusa las claves de la rúbrica); id desconocido → id.
function checkTitle(id: string, t: TFn): string {
  const k = `detail.check.${id}`;
  const v = t(k);
  return v === k ? id : v;
}

// Validación de una rama a agregar, espejo de _parse_refs del backend: sin
// espacios/control, ≤255, no vacía. Devuelve clave i18n del error o null si OK.
function refError(ref: string, existing: string[]): string | null {
  if (!ref) return "cmp.err.empty";
  if ([...ref].some((ch) => /\s/.test(ch)) || ref.length > 255) return "cmp.err.bad";
  if (existing.includes(ref)) return "cmp.err.dup";
  if (existing.length >= MAX_REFS) return "cmp.err.max";
  return null;
}

function pct(n: number | null): string {
  return n == null ? "—" : `${n}%`;
}

function DeltaCell({ d }: { d: number | null }) {
  const t = useT();
  if (d == null) return <span className="muted">—</span>;
  const dir = d > 0 ? "up" : d < 0 ? "down" : "flat";
  const sign = d > 0 ? "+" : "";
  return (
    <span className={`mono trend-${dir}`} title={t("cmp.deltaTip")}>
      {sign}{d}
    </span>
  );
}

// Lista de checks (regresiones o arreglos) como chips; vacío → guión discreto.
function CheckTags({ ids, kind }: { ids: string[]; kind: "bad" | "good" }) {
  const t = useT();
  if (ids.length === 0) return <span className="muted">—</span>;
  return (
    <span className="cmp-tags">
      {ids.map((id) => (
        <span key={id} className={`cmp-tag cmp-tag-${kind}`}>{checkTitle(id, t)}</span>
      ))}
    </span>
  );
}

function ResultTable({ cmp }: { cmp: PostureComparison }) {
  const t = useT();
  const order = useMemo(() => {
    const idx = new Map(cmp.ranking.map((r, i) => [r, i]));
    return [...cmp.refs].sort((a, b) => (idx.get(a.ref) ?? 99) - (idx.get(b.ref) ?? 99));
  }, [cmp]);

  return (
    <div className="hist-scroll">
      <table className="hist-table cmp-table">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>{t("cmp.col.ref")}</th>
            <th className="num">{t("cmp.col.score")}</th>
            <th>{/* banda */}</th>
            <th className="num">{t("cmp.col.delta")}</th>
            <th>{t("cmp.col.regressions")}</th>
            <th>{t("cmp.col.resolved")}</th>
          </tr>
        </thead>
        <tbody>
          {order.map((r: RefPosture, i) => {
            const isBase = r.ref === cmp.baseline;
            const noData = r.posture.score == null;
            return (
              <tr key={r.ref} className={`cmp-row band-${bandOf(r.posture.band)}${noData ? " cmp-nodata" : ""}`}>
                <td className="num cmp-rank">{i + 1}</td>
                <td>
                  <code className="cmp-ref">{r.ref}</code>
                  {isBase && <span className="cmp-base" title={t("cmp.baselineTip")}>{t("cmp.base")}</span>}
                  {r.ci_branch && r.ci_branch !== r.ref && (
                    <span className="muted cmp-cibranch" title={t("cmp.ciBranchTip")}>→ {r.ci_branch}</span>
                  )}
                </td>
                <td className="num cmp-score">{pct(r.posture.score)}</td>
                <td><BandPill band={r.posture.band} /></td>
                <td className="num"><DeltaCell d={r.delta_vs_baseline} /></td>
                <td><CheckTags ids={r.new_fails} kind="bad" /></td>
                <td><CheckTags ids={r.resolved_fails} kind="good" /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function BranchCompareView({ projectKey, onBack }: { projectKey: string; onBack: () => void }) {
  const t = useT();
  // Resumen del proyecto (cacheado): nombre + rama por defecto para sembrar la 1ª rama.
  const summary = useAsync(
    () => fetchProject(projectKey).catch(() => null),
    [projectKey],
  );
  const projectName = summary.data?.name ?? projectKey;
  const defaultBranch = summary.data?.provenance?.data?.default_branch ?? null;

  const [refs, setRefs] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [baseline, setBaseline] = useState<string>("");
  const [addErr, setAddErr] = useState<string | null>(null);
  // Parámetros con los que se disparó la comparación (null = aún no se comparó).
  const [query, setQuery] = useState<{ refs: string[]; baseline: string } | null>(null);
  // Siembra la rama por defecto una sola vez cuando llega el summary, sin pisar al
  // usuario si ya agregó algo (de ahí la guarda refs.length === 0 + dep en seeded).
  const [seeded, setSeeded] = useState(false);
  useEffect(() => {
    if (seeded || !defaultBranch) return;
    setSeeded(true);
    setRefs((cur) => (cur.length === 0 ? [defaultBranch] : cur));
    setBaseline((cur) => cur || defaultBranch);
  }, [seeded, defaultBranch]);

  const res = useAsync(
    () =>
      query == null
        ? Promise.resolve<{ cmp: PostureComparison | null; unsupported: boolean }>({ cmp: null, unsupported: false })
        : compareRefs(projectKey, query.refs, query.baseline)
            .then((cmp) => ({ cmp, unsupported: false }))
            .catch((e: unknown) => {
              // 422 = el forge del proyecto no es comparable por rama (no es GitHub).
              if (e instanceof ApiError && e.status === 422) return { cmp: null, unsupported: true };
              throw e;
            }),
    [projectKey, query],
  );
  const cmp = res.data?.cmp ?? null;
  const unsupported = res.data?.unsupported === true;

  function addRef() {
    const ref = input.trim();
    const err = refError(ref, refs);
    if (err) { setAddErr(err); return; }
    const next = [...refs, ref];
    setRefs(next);
    if (!baseline) setBaseline(ref);
    setInput("");
    setAddErr(null);
  }

  function removeRef(ref: string) {
    const next = refs.filter((r) => r !== ref);
    setRefs(next);
    if (baseline === ref) setBaseline(next[0] ?? "");
    setAddErr(null);
  }

  const canCompare = refs.length >= 2 && refs.includes(baseline);

  function runCompare() {
    if (!canCompare) return;
    setQuery({ refs, baseline });
  }

  return (
    <div className="detail cmp-view">
      <button type="button" className="btn-back" onClick={onBack}>{t("common.backToProject")}</button>
      <div className="detail-title-row">
        <h2 className="detail-title">{t("cmp.title")}</h2>
      </div>
      <p className="muted">{t("cmp.subtitle", { name: projectName })}</p>

      {/* Selector de ramas: chips + input + baseline + comparar. */}
      <section className="card cmp-picker" aria-label={t("cmp.pickerAria")}>
        {refs.length > 0 && (
          <div className="cmp-chips">
            {refs.map((r) => (
              <span key={r} className={`cmp-chip${r === baseline ? " is-base" : ""}`}>
                <code>{r}</code>
                <button type="button" className="cmp-chip-x" aria-label={t("cmp.remove", { ref: r })} onClick={() => removeRef(r)}>×</button>
              </span>
            ))}
          </div>
        )}
        <div className="cmp-form">
          <input
            className="cmp-input"
            type="text"
            value={input}
            placeholder={t("cmp.inputPlaceholder")}
            aria-label={t("cmp.inputAria")}
            disabled={refs.length >= MAX_REFS}
            onChange={(e) => { setInput(e.target.value); setAddErr(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addRef(); } }}
          />
          <button type="button" className="btn sm" onClick={addRef} disabled={refs.length >= MAX_REFS}>
            {t("cmp.add")}
          </button>
          <label className="cmp-baseline">
            <span className="muted">{t("cmp.baselineLabel")}</span>
            <select value={baseline} onChange={(e) => setBaseline(e.target.value)} disabled={refs.length === 0}>
              {refs.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <button type="button" className="btn primary" onClick={runCompare} disabled={!canCompare || res.loading}>
            {res.loading ? t("cmp.comparing") : t("cmp.compare")}
          </button>
        </div>
        {addErr && <p className="cmp-hint err" role="alert">{t(addErr, { n: MAX_REFS })}</p>}
        {!addErr && <p className="cmp-hint muted">{t("cmp.pickerHint", { n: MAX_REFS })}</p>}
      </section>

      {res.error && (
        <div className="alert err" role="alert">
          {t("cmp.loadError", { error: res.error })}{" "}
          <button type="button" className="btn sm" onClick={res.reload}>{t("common.retry")}</button>
        </div>
      )}
      {unsupported && (
        <div className="alert info" role="status">{t("cmp.unsupported")}</div>
      )}

      {cmp && (
        <>
          {/* Veredicto: la mejor rama con datos (primera del ranking con score). */}
          {(() => {
            const best = cmp.refs.find((r) => r.ref === cmp.ranking[0] && r.posture.score != null);
            if (!best) return <div className="alert info" role="status">{t("cmp.noWinner")}</div>;
            const tied = cmp.refs.filter((r) => r.posture.score === best.posture.score).length > 1;
            return (
              <div className={`cmp-winner band-${bandOf(best.posture.band)}`} role="status">
                <span className="cmp-winner-k">{tied ? t("cmp.winnerTied") : t("cmp.winner")}</span>
                <code className="cmp-winner-ref">{best.ref}</code>
                <span className="cmp-winner-score mono">{pct(best.posture.score)}</span>
                <BandPill band={best.posture.band} />
              </div>
            );
          })()}

          <ResultTable cmp={cmp} />

          <p className="muted cmp-scope">
            {t("cmp.comparable", { checks: cmp.comparable_checks.map((c) => checkTitle(c, t)).join(", ") || "—" })}
          </p>
          {cmp.note && <div className="alert info cmp-note" role="note">{cmp.note}</div>}
          <p className="muted mono deps-meta">{t("cmp.generatedAt", { when: fmtDate(cmp.generated_at) })}</p>
        </>
      )}
    </div>
  );
}
