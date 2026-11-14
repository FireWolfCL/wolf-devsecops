import { useEffect, useState } from "react";

import { fetchPipeline } from "../api/client";
import type { CIStatus, PipelineMap as PipelineMapData, PipelineStage, PipelineStageState, PushItem } from "../api/types";
import { useI18n, useT, type TFn } from "../i18n";
import { Skel } from "./posture";
import { errText, safeHref } from "./util";

/**
 * Mapa de pipeline "usted está aquí": 6 etapas (commit → release) con el pin en la
 * etapa donde está parado el proyecto, más los últimos pushes. SOLO LECTURA (ADR-001).
 * Estados honestos: una etapa sin fuente se pinta gris ("sin datos"), nunca verde.
 * Sin emojis: glifos geométricos sobre un chip teñido (mismo vocabulario que la campana).
 */

const COLOR: Record<PipelineStageState, string> = {
  ok: "#34d399", fail: "#f87171", warn: "#f5a524", pending: "#f5a524", none: "#9fb0c0",
};

function StateGlyph({ state }: { state: PipelineStageState }) {
  switch (state) {
    case "ok": return <path d="M3.5 8.5 L6.5 11.5 L12.5 5" />;
    case "fail": return <path d="M4.5 4.5 L11.5 11.5 M11.5 4.5 L4.5 11.5" />;
    case "warn": return <path d="M8 3.5 L13 12.5 H3 Z M8 7 V9.5" />;
    case "pending": return <path d="M8 3 V8 L11 10" />;
    default: return <path d="M4 8 H12" />;
  }
}

function Marker({ state, size = 22 }: { state: PipelineStageState; size?: number }) {
  const c = COLOR[state];
  return (
    <span className="pipeline-glyph" aria-hidden="true"
      style={{ color: c, background: `${c}1f`, borderColor: `${c}59`, width: size, height: size }}>
      <svg viewBox="0 0 16 16" width={size - 6} height={size - 6} fill="none" stroke={c}
        strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <StateGlyph state={state} />
      </svg>
    </span>
  );
}

function ciToState(s: CIStatus): PipelineStageState {
  return s === "success" ? "ok" : s === "failure" ? "fail" : s === "running" ? "pending" : "none";
}

function metaParams(st: PipelineStage): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(st.meta)) {
    if (typeof v === "string" || typeof v === "number") out[k] = v;
  }
  return out;
}

function detailText(st: PipelineStage, t: TFn): string {
  const key = `pipeline.code.${st.detail_code}`;
  const txt = t(key, metaParams(st));
  return txt === key ? t("pipeline.code.unknown") : txt;
}

function useRelative() {
  const { lang } = useI18n();
  return (iso: string | null): string => {
    if (!iso) return "—";
    const ms = new Date(iso).getTime();
    if (Number.isNaN(ms)) return "—";
    const secs = Math.round((ms - Date.now()) / 1000);
    const units: [Intl.RelativeTimeFormatUnit, number][] = [["day", 86400], ["hour", 3600], ["minute", 60]];
    try {
      const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
      for (const [u, n] of units) if (Math.abs(secs) >= n) return rtf.format(Math.round(secs / n), u);
      return rtf.format(0, "second");
    } catch {
      return new Date(iso).toLocaleString();
    }
  };
}

function Arrow() {
  return (
    <span className="pipeline-arrow" aria-hidden="true">
      <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor"
        strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 8 H13 M9 4 L13 8 L9 12" />
      </svg>
    </span>
  );
}

function StageBox({ st, here }: { st: PipelineStage; here: boolean }) {
  const t = useT();
  const href = safeHref(st.url);
  const c = COLOR[st.state];
  const body = (
    <>
      {here && (
        <span className="pipeline-pin" style={{ color: c, borderColor: `${c}59`, background: `${c}1f` }}>
          <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true" fill="currentColor">
            <path d="M8 14 L2.5 5 H13.5 Z" />
          </svg>
          {t("pipeline.here")}
        </span>
      )}
      <Marker state={st.state} />
      <span className="pipeline-label">{t(`pipeline.stage.${st.key}`)}</span>
      <span className="pipeline-state" style={{ color: c }}>{t(`pipeline.state.${st.state}`)}</span>
    </>
  );
  const cls = `pipeline-box pipeline-${st.state}${here ? " here" : ""}`;
  const aria = `${t(`pipeline.stage.${st.key}`)}: ${t(`pipeline.state.${st.state}`)}${here ? `, ${t("pipeline.here")}` : ""}`;
  return href ? (
    <a className={cls} href={href} target="_blank" rel="noopener noreferrer" aria-label={aria}
      style={{ borderColor: `${c}59` }}>{body}</a>
  ) : (
    <div className={cls} role="group" aria-label={aria} style={{ borderColor: `${c}59` }}>{body}</div>
  );
}

function PushRow({ p, rel }: { p: PushItem; rel: (iso: string | null) => string }) {
  const t = useT();
  const state = ciToState(p.ci_state);
  const href = safeHref(p.html_url);
  const inner = (
    <>
      <Marker state={state} size={20} />
      <span className="pipeline-push-actor">{p.actor ?? t("pipeline.unknownActor")}</span>
      <span className="pipeline-push-when muted">{rel(p.when)}</span>
      {p.sha_short && <code className="pipeline-push-sha">{p.sha_short}</code>}
      <span className="pipeline-push-msg" title={p.message ?? undefined}>{p.message ?? ""}</span>
      <span className="sr-only">{t(`pipeline.ci.${p.ci_state}`)}</span>
    </>
  );
  return (
    <li>
      {href ? (
        <a className="pipeline-push" href={href} target="_blank" rel="noopener noreferrer">{inner}</a>
      ) : (
        <div className="pipeline-push">{inner}</div>
      )}
    </li>
  );
}

export function PipelineMap({ projectKey }: { projectKey: string }) {
  const t = useT();
  const rel = useRelative();
  const [data, setData] = useState<PipelineMapData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    fetchPipeline(projectKey)
      .then((d) => active && setData(d))
      .catch((e: unknown) => active && setError(errText(e)));
    return () => { active = false; };
  }, [projectKey, tick]);

  if (error && !data) {
    return (
      <section className="card pipeline" aria-label={t("pipeline.title")}>
        <div className="alert err" role="alert">
          {t("pipeline.loadError", { error })}{" "}
          <button type="button" className="btn sm" onClick={() => setTick((n) => n + 1)}>{t("common.retry")}</button>
        </div>
      </section>
    );
  }
  if (!data) {
    return (
      <section className="card pipeline" aria-busy="true" aria-label={t("pipeline.title")}>
        <Skel w={180} h={14} />
        <div className="pipeline-strip" style={{ marginTop: 16 }}>
          {Array.from({ length: 6 }).map((_, i) => <Skel key={i} h={84} r={10} style={{ minWidth: 110 }} />)}
        </div>
      </section>
    );
  }

  const here = data.current_index >= 0 ? data.stages[data.current_index] : undefined;
  return (
    <section className="card pipeline" aria-label={t("pipeline.title")}>
      <h3 className="pipeline-title">{t("pipeline.title")}</h3>
      <ol className="pipeline-strip">
        {data.stages.map((st, i) => (
          <li key={st.key} className="pipeline-step">
            {i > 0 && <Arrow />}
            <StageBox st={st} here={i === data.current_index} />
          </li>
        ))}
      </ol>
      <p className="pipeline-sub" role="status">
        {here
          ? <><strong>{t(`pipeline.stage.${here.key}`)}</strong>{" — "}{detailText(here, t)}</>
          : t("pipeline.noPin")}
      </p>
      {data.stages.some((s) => s.state === "none") && (
        <p className="pipeline-hint muted">{t("pipeline.noneHint")}</p>
      )}
      <h4 className="pipeline-sub-title">{t("pipeline.pushes")}</h4>
      {data.recent_pushes.length === 0 ? (
        <p className="muted">{t("pipeline.noPushes")}</p>
      ) : (
        <ul className="pipeline-pushes">
          {data.recent_pushes.map((p, i) => <PushRow key={`${p.sha_short}-${i}`} p={p} rel={rel} />)}
        </ul>
      )}
    </section>
  );
}
