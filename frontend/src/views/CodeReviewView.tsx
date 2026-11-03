// Revisión IA de código (Feature B · ADR-018, assistive). El operador elige un
// finding con ubicación, un proveedor/modelo y pulsa "Proponer fix": el backend
// lee una ventana ACOTADA del archivo y pide una propuesta al modelo.
//
// Invariantes de seguridad de este archivo:
//  1. TEXTO PLANO: original/propuesta/explicación son DATO no confiable. Se
//     renderizan solo como `{texto}` (React escapa). PROHIBIDO dangerouslySetInnerHTML.
//  2. TRANSPARENCIA: badge de egreso obligatorio (CON EGRESO / SIN EGRESO (local)).
//  3. Jamás se pide ni se muestra base_url/token/API-key: el backend los resuelve.
//  4. Nada se aplica al repo: es solo una propuesta para revisión humana.
import { useEffect, useMemo, useState } from "react";

import { fetchSourceDetail, proposeCodeFix } from "../api/client";
import { settingsApi } from "../api/admin";
import { ApiError } from "../api/http";
import type { AICodeFixResult, AIProviderKind, TrivyFinding } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useAsync, useMeta } from "../hooks";
import { useT } from "../i18n";

// Allow-list de modelos hosted (espejo del backend; el servidor valida igual).
const HOSTED_MODELS = ["claude-sonnet-4-5", "claude-opus-4-8", "gpt-4o"] as const;

// Fuentes con findings ubicados (archivo:línea) que el backend puede resolver.
type FindingSource = "defectdojo" | "sonarqube";
// Un finding con su fuente: el backend lo resuelve en esa fuente (finding_ref = id/título).
type Item = { f: TrivyFinding; source: FindingSource };

const SEV_CLASS: Record<string, string> = {
  CRITICAL: "crit", HIGH: "high", MEDIUM: "med", LOW: "low", UNKNOWN: "muted", INFO: "muted",
};
const SEV_KEY: Record<string, string> = {
  CRITICAL: "sev.critical", HIGH: "sev.high", MEDIUM: "sev.medium",
  LOW: "sev.low", UNKNOWN: "sev.unknown", INFO: "sev.info",
};

/** El backend identifica el finding por `id` o, si no, por `title`. */
function refOf(f: TrivyFinding): string | null {
  return f.id ?? (f.title || null);
}

type Failure = { kind: "403" | "503" | "404" | "422" | "other"; msg: string };

function toFailure(e: unknown): Failure {
  if (e instanceof ApiError) {
    if (e.status === 403) return { kind: "403", msg: e.message };
    if (e.status === 503) return { kind: "503", msg: e.message };
    if (e.status === 404) return { kind: "404", msg: e.message };
    if (e.status === 422) return { kind: "422", msg: e.message };
    return { kind: "other", msg: e.message };
  }
  return { kind: "other", msg: e instanceof Error ? e.message : String(e) };
}

type Mark = "same" | "del" | "add";

/** Diff por línea simple (sin LCS): una línea del original ausente en la
 *  propuesta se marca "del"; una de la propuesta ausente en el original, "add".
 *  Es una ayuda visual; la verdad son los dos textos completos. */
function markLines(original: string, proposed: string) {
  const o = original.split("\n");
  const p = proposed.split("\n");
  const oSet = new Set(o.map((l) => l.trimEnd()));
  const pSet = new Set(p.map((l) => l.trimEnd()));
  return {
    left: o.map((l) => ({ text: l, mark: (pSet.has(l.trimEnd()) ? "same" : "del") as Mark })),
    right: p.map((l) => ({ text: l, mark: (oSet.has(l.trimEnd()) ? "same" : "add") as Mark })),
  };
}

function Transparencia({ r }: { r: AICodeFixResult }) {
  const t = useT();
  return (
    <div className="ai-meta">
      <span className="badge ai-badge">{t("cr.byAI")}</span>
      <span className="badge off">{t("ai.provider", { v: r.provider_type })}</span>
      <span className="badge off">{t("ai.model", { v: r.model })}</span>
      {r.egress ? (
        <span className="badge danger" title={t("cr.egressTip")}>{t("cr.egress")}</span>
      ) : (
        <span className="badge ok" title={t("cr.noEgressTip")}>{t("cr.noEgress")}</span>
      )}
    </div>
  );
}

function CodeColumn({
  label, lines, ariaLabel,
}: {
  label: string;
  lines: { text: string; mark: Mark }[];
  ariaLabel: string;
}) {
  return (
    <div className="cr-col">
      <div className="cr-col-head">{label}</div>
      {/* Texto plano por línea: nada se interpreta como marcado. */}
      <pre className="cr-code" tabIndex={0} aria-label={ariaLabel}>
        {lines.map((l, i) => (
          <span key={i} className={`cr-line ${l.mark}`}>{l.text === "" ? " " : l.text}{"\n"}</span>
        ))}
      </pre>
    </div>
  );
}

export function CodeReviewView({ projectKey, onBack }: { projectKey: string; onBack: () => void }) {
  const t = useT();
  const { user } = useAuth();
  const { data: meta } = useMeta();
  const aiEnabled = meta?.ai_enabled ?? false;

  const dd = useAsync(() => fetchSourceDetail(projectKey, "defectdojo"), [projectKey]);
  const sq = useAsync(() => fetchSourceDetail(projectKey, "sonarqube"), [projectKey]);
  const loading = dd.loading || sq.loading;
  const error = dd.error ?? sq.error;
  // Lista unificada: cada finding sabe de qué fuente viene (para el code-fix).
  const items = useMemo<Item[]>(() => {
    const ddF = (dd.data?.payload?.findings as TrivyFinding[] | undefined) ?? [];
    const sqF = (sq.data?.payload?.findings as TrivyFinding[] | undefined) ?? [];
    return [
      ...ddF.map((f): Item => ({ f, source: "defectdojo" })),
      ...sqF.map((f): Item => ({ f, source: "sonarqube" })),
    ];
  }, [dd.data, sq.data]);

  const [query, setQuery] = useState("");
  const [selIdx, setSelIdx] = useState<number | null>(null);
  const [provider, setProvider] = useState<AIProviderKind>("local");
  const [hostedModel, setHostedModel] = useState<string>(HOSTED_MODELS[0]);
  const [localModel, setLocalModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AICodeFixResult | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);

  // Solo admin puede leer la config IA: prellena el NOMBRE del modelo local.
  // Nunca se lee ni muestra base_url/token (ADR-002).
  useEffect(() => {
    if (user?.role !== "admin") return;
    let active = true;
    settingsApi.getAI().then((c) => active && c.model && setLocalModel((m) => m || c.model)).catch(() => undefined);
    return () => { active = false; };
  }, [user?.role]);

  const q = query.trim().toLowerCase();
  const rows = useMemo(
    () =>
      items
        .map((it, i) => ({ ...it, i }))
        .filter(({ f }) => {
          if (!q) return true;
          return [f.id, f.title, f.target, f.pkg, f.severity]
            .filter(Boolean)
            .some((v) => String(v).toLowerCase().includes(q));
        }),
    [items, q],
  );

  const selected = selIdx !== null ? items[selIdx]?.f : undefined;
  const selectedSource = selIdx !== null ? items[selIdx]?.source : undefined;
  const selectedRef = selected ? refOf(selected) : null;
  const model = provider === "hosted" ? hostedModel : localModel.trim();
  const canRun = aiEnabled && !busy && !!selectedRef && !!selected?.target && model.length > 0;

  async function run() {
    if (!selectedRef || !selectedSource) return;
    setBusy(true);
    setResult(null);
    setFailure(null);
    try {
      setResult(await proposeCodeFix(projectKey, { finding_ref: selectedRef, provider, model, source: selectedSource }));
    } catch (e: unknown) {
      setFailure(toFailure(e));
    } finally {
      setBusy(false);
    }
  }

  const select = (i: number) => {
    setSelIdx(i);
    setResult(null);
    setFailure(null);
  };

  const marked = result ? markLines(result.original_snippet, result.proposed_snippet) : null;

  return (
    <div className="detail code-review-view">
      <button type="button" className="btn-back" onClick={onBack}>{t("common.backToProject")}</button>
      <div className="detail-title-row">
        <h2 className="detail-title">
          {t("cr.title")}
          <span className="badge ai-badge" style={{ marginLeft: 10 }}>{t("ai.badge")}</span>
        </h2>
      </div>
      <p className="muted">{t("cr.subtitle", { name: projectKey })}</p>
      <p className="muted" style={{ fontSize: 13 }}>{t("cr.notice")}</p>

      <div className="cr-layout">
        {/* ── Lista de findings ─────────────────────────────────────────── */}
        <section className="cr-list" aria-label={t("cr.findings")}>
          <div className="list-bar">
            <h3 style={{ margin: 0, fontSize: 15 }}>{t("cr.findings")} ({items.length})</h3>
            <input
              type="search"
              className="search"
              placeholder={t("cr.filter")}
              aria-label={t("cr.filter")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          {loading && <p className="state" aria-busy="true">{t("common.loading")}</p>}
          {error && <div className="alert err" role="alert">{t("cr.findingsError", { error })}</div>}
          {!loading && !error && items.length === 0 && <p className="empty">{t("cr.noFindings")}</p>}
          {!loading && items.length > 0 && rows.length === 0 && <p className="empty">{t("cr.noMatch")}</p>}
          <ul className="cr-items">
            {rows.map(({ f, source, i }) => (
              <li key={`${source}-${f.id ?? "x"}-${i}`}>
                <button
                  type="button"
                  className={`cr-item${selIdx === i ? " active" : ""}`}
                  aria-pressed={selIdx === i}
                  onClick={() => select(i)}
                >
                  <span className={`sev-pill ${SEV_CLASS[f.severity] ?? "muted"}`}>
                    {t(SEV_KEY[f.severity] ?? "sev.unknown")}
                  </span>{" "}
                  <span className="badge off" style={{ fontSize: 10 }}>{t(`cr.source.${source}`)}</span>{" "}
                  <span className="mono">{f.id ?? "—"}</span>
                  <span className="cr-item-title">{f.title || "—"}</span>
                  <span className="mono muted cr-item-where">{f.target ?? t("cr.noWhere")}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {/* ── Controles + resultado ─────────────────────────────────────── */}
        <section className="cr-main">
          <div className="cr-controls">
            <label className="cr-field">
              <span>{t("cr.provider")}</span>
              <select value={provider} onChange={(e) => setProvider(e.target.value as AIProviderKind)}>
                <option value="local">{t("cr.providerLocal")}</option>
                <option value="hosted">{t("cr.providerHosted")}</option>
              </select>
            </label>
            <label className="cr-field">
              <span>{t("cr.model")}</span>
              {provider === "hosted" ? (
                <select value={hostedModel} onChange={(e) => setHostedModel(e.target.value)}>
                  {HOSTED_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <input
                  type="text"
                  value={localModel}
                  maxLength={120}
                  placeholder={t("cr.modelLocalPlaceholder")}
                  onChange={(e) => setLocalModel(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
              )}
            </label>
            <button
              type="button"
              className="btn primary"
              disabled={!canRun}
              title={aiEnabled ? undefined : t("ai.tipDisabled")}
              onClick={run}
            >
              {busy ? t("cr.proposing") : t("cr.propose")}
            </button>
          </div>

          <p className={`cr-egress-hint ${provider === "hosted" ? "warn" : "muted"}`}>
            {provider === "hosted" ? t("cr.hostedWarn") : t("cr.localHint")}
          </p>
          {!aiEnabled && <p className="state">{t("ai.disabledState")}</p>}
          {selected ? (
            <p className="muted" style={{ fontSize: 13 }}>
              {t("cr.selected", { id: selectedRef ?? "—" })}
              {!selected.target && ` — ${t("cr.noWhere")}`}
            </p>
          ) : (
            <p className="muted" style={{ fontSize: 13 }}>{t("cr.selectPrompt")}</p>
          )}

          {busy && <p className="state" aria-busy="true">{t("ai.loading")}</p>}

          {/* Estados esperados (no errores rotos). */}
          {failure?.kind === "403" && <div className="alert info" role="status"><strong>{t("cr.err403")}</strong></div>}
          {failure?.kind === "503" && <div className="alert info" role="status"><strong>{t("cr.err503")}</strong></div>}
          {failure?.kind === "404" && (
            <div className="alert info" role="status">
              <strong>{t("cr.err404")}</strong>
              <div className="muted">{failure.msg}</div>
            </div>
          )}
          {failure?.kind === "422" && <div className="alert warn" role="alert">{t("cr.err422", { msg: failure.msg })}</div>}
          {failure?.kind === "other" && <div className="alert err" role="alert">{t("cr.errGeneric", { msg: failure.msg })}</div>}

          {result && marked && (
            <div className="ai-result">
              <Transparencia r={result} />
              {result.proposed_snippet.trim() === "" ? (
                <div className="alert info" role="status">
                  <strong>{t("cr.noProposal")}</strong>
                </div>
              ) : null}
              <p className="muted cr-legend">
                <span className="cr-key del">−</span> {t("cr.legendDel")}{" "}
                <span className="cr-key add">+</span> {t("cr.legendAdd")}
              </p>
              <div className="cr-diff">
                <CodeColumn label={t("cr.original")} lines={marked.left} ariaLabel={t("cr.original")} />
                <CodeColumn label={t("cr.proposed")} lines={marked.right} ariaLabel={t("cr.proposed")} />
              </div>
              <h4 className="cr-expl-title">{t("cr.explanation")}</h4>
              {/* explanation: TEXTO PLANO (React escapa; pre-wrap). */}
              <p className="ai-text">{result.explanation || "—"}</p>
              <p className="muted" style={{ fontSize: 12 }}>{t("cr.reviewNote")}</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
