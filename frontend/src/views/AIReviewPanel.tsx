// Panel del Reviewer IA (F6 · ADR-007 · P0-4 de docs/AI-SAFETY.md).
//
// Dos invariantes de seguridad viven en este archivo:
//
//  1. TRANSPARENCIA: todo lo que produce el panel va rotulado "generado por IA",
//     y se muestran proveedor, modelo, si hubo egreso de datos y la hora. El
//     usuario nunca confunde una salida del modelo con un dato medido.
//
//  2. RENDER TEXTO-PLANO: la salida del modelo es DATO no confiable, nunca
//     marcado. React escapa `{texto}` por defecto, así que un `<script>` que
//     devuelva el modelo se muestra como texto inerte. Queda PROHIBIDO
//     introducir dangerouslySetInnerHTML en este panel.
//
// El análisis es una acción EXPLÍCITA (botón), no automática: recién al pulsarlo
// se proyectan los hallazgos (allow-list, sin rutas ni código) hacia el proveedor.
import { useState } from "react";

import { AIDisabledError, reviewProjectAI } from "../api/client";
import type { AIReviewResult, Severity } from "../api/types";
import { useMeta } from "../hooks";
import { useT } from "../i18n";

const SEV_CLASS: Record<Severity, string> = {
  critical: "danger",
  high: "warn",
  medium: "warn",
  low: "off",
  info: "off",
};

/** Renderiza texto del modelo como TEXTO PLANO (nunca HTML). pre-wrap conserva
 *  saltos de línea sin interpretar marcado. */
function PlainText({ children }: { children: string }) {
  return <p className="ai-text">{children}</p>;
}

function Transparencia({ r }: { r: AIReviewResult }) {
  const t = useT();
  return (
    <div className="ai-meta">
      <span className="badge off">{t("ai.provider", { v: r.provider_type })}</span>
      <span className="badge off">{t("ai.model", { v: r.model })}</span>
      {r.egress ? (
        <span className="badge danger" title={t("ai.egressTip")}>
          {t("ai.egress")}
        </span>
      ) : (
        <span className="badge ok" title={t("ai.noEgressTip")}>
          {t("ai.noEgress")}
        </span>
      )}
      <span className="muted" style={{ fontSize: 12 }}>
        {new Date(r.generated_at).toLocaleString()}
      </span>
    </div>
  );
}

export function AIReviewPanel({ projectKey }: { projectKey: string }) {
  const t = useT();
  const { data: meta } = useMeta();
  const aiEnabled = meta?.ai_enabled ?? false;

  const [result, setResult] = useState<AIReviewResult | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "disabled" | "error">("idle");
  const [message, setMessage] = useState("");

  async function run() {
    setState("loading");
    setResult(null);
    setMessage("");
    try {
      setResult(await reviewProjectAI(projectKey));
      setState("idle");
    } catch (e: unknown) {
      if (e instanceof AIDisabledError) {
        setState("disabled");
        setMessage(e.message);
      } else {
        setState("error");
        setMessage(e instanceof Error ? e.message : String(e));
      }
    }
  }

  return (
    <section className="ai-panel">
      <div className="ai-head">
        <h3>
          {t("ai.title")}
          <span className="badge ai-badge" style={{ marginLeft: 10 }}>
            {t("ai.badge")}
          </span>
        </h3>
        <button
          className="btn-back"
          onClick={run}
          disabled={state === "loading" || !aiEnabled}
          title={aiEnabled ? t("ai.tipEnabled") : t("ai.tipDisabled")}
        >
          {state === "loading" ? t("ai.analyzing") : t("ai.analyze")}
        </button>
      </div>

      {/* Aviso permanente de naturaleza de la feature (transparencia P0-4). */}
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        {t("ai.notice")}
      </p>

      {!aiEnabled && state === "idle" && (
        <p className="state">
          {t("ai.disabledState")}
        </p>
      )}
      {state === "disabled" && <p className="state">{message}</p>}
      {state === "error" && <p className="state">{t("ai.error", { msg: message })}</p>}
      {state === "loading" && <p className="state">{t("ai.loading")}</p>}

      {result && (
        <div className="ai-result">
          <Transparencia r={result} />
          {/* summary: TEXTO PLANO — React lo escapa, no se interpreta como HTML. */}
          <PlainText>{result.summary}</PlainText>
          {result.prioritized.length > 0 && (
            <ol className="ai-list">
              {result.prioritized.map((p, i) => (
                <li key={i}>
                  <div className="ai-item-head">
                    <span className={`badge ${SEV_CLASS[p.severity]}`}>{p.severity}</span>
                    {/* title/rationale/action: TEXTO PLANO, sin marcado. */}
                    <strong className="ai-text" style={{ margin: 0 }}>{p.title}</strong>
                  </div>
                  <PlainText>{p.rationale}</PlainText>
                  <p className="ai-text ai-action">→ {p.recommended_action}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
