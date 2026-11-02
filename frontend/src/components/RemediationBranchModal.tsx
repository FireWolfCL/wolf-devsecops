// Confirmación explícita de "Crear branch securitizado" (Feature D · ADR-017).
// La petición con confirm:true SOLO sale del click en "Confirmar" de este modal;
// nunca se auto-ejecuta. Muestra el branch que se creará, los findings elegidos
// y deja claro que se crea un PR DRAFT y que NO se mergea nada. Todo es texto
// plano (React escapa); pr_url solo se enlaza si es https.
import { useEffect, useRef, useState } from "react";

import { createRemediationBranch } from "../api/client";
import { ApiError } from "../api/http";
import type { RemediationBranchResult, RemediationPick } from "../api/types";
import { useT } from "../i18n";

export const MAX_PICKS = 50;

/** Renderiza **negritas** del string i18n sin HTML (segmentos impares = strong). */
function Emph({ text }: { text: string }) {
  return (
    <>
      {text.split("**").map((seg, i) => (i % 2 === 1 ? <strong key={i}>{seg}</strong> : <span key={i}>{seg}</span>))}
    </>
  );
}

type Phase =
  | { k: "confirm" }
  | { k: "busy" }
  | { k: "done"; r: RemediationBranchResult }
  | { k: "fail"; status: number; msg: string };

export function RemediationBranchModal({
  projectKey, picks, onClose, onWriteDisabled,
}: {
  projectKey: string;
  picks: RemediationPick[];
  onClose: () => void;
  /** El backend respondió 503: el padre deshabilita el botón. */
  onWriteDisabled: () => void;
}) {
  const t = useT();
  const [phase, setPhase] = useState<Phase>({ k: "confirm" });
  const cancelRef = useRef<HTMLButtonElement>(null);
  const prevFocus = useRef<Element | null>(null);

  // Foco inicial en "Cancelar" (la opción segura); se devuelve al cerrar.
  useEffect(() => {
    prevFocus.current = document.activeElement;
    cancelRef.current?.focus();
    return () => { (prevFocus.current as HTMLElement | null)?.focus?.(); };
  }, []);

  const busy = phase.k === "busy";
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  // Vista previa del nombre: el sufijo hash lo asigna el servidor.
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const branchPreview = `wolf-sec/fix-${date}-…`;

  async function confirm() {
    setPhase({ k: "busy" });
    try {
      setPhase({ k: "done", r: await createRemediationBranch(projectKey, picks) });
    } catch (e: unknown) {
      const status = e instanceof ApiError ? e.status : 0;
      const msg = e instanceof Error ? e.message : String(e);
      if (status === 503) onWriteDisabled();
      setPhase({ k: "fail", status, msg });
    }
  }

  const failText = (status: number, msg: string): string => {
    switch (status) {
      case 502: return t("rbr.err502", { msg });
      case 503: return t("rbr.err503", { msg });
      case 403: return t("rbr.err403");
      case 400: return t("rbr.err400", { msg });
      case 404: return t("rbr.err404", { msg });
      case 422: return t("rbr.err422", { msg });
      default: return t("rbr.errGeneric", { msg });
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="rbr-title">
        <h3 id="rbr-title">{t("rbr.title")}</h3>

        {phase.k !== "done" && (
          <>
            <div className="alert warn" role="note">
              <Emph text={t("rbr.warn")} />
            </div>
            <dl className="modal-kv">
              <dt>{t("rbr.project")}</dt>
              <dd className="mono">{projectKey}</dd>
              <dt>{t("rbr.branchLabel")}</dt>
              <dd className="mono">{branchPreview}</dd>
            </dl>
            <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>{t("rbr.branchNote")}</p>
            <strong style={{ fontSize: 13 }}>{t("rbr.findings", { n: picks.length })}</strong>
            <ul className="modal-picks">
              {picks.map((p, i) => (
                <li key={`${p.ref}-${i}`}>
                  <span className="mono">{p.ref}</span>
                  {p.severity ? <span className="muted"> · {p.severity}</span> : null}
                  {p.title ? <span> — {p.title}</span> : null}
                </li>
              ))}
            </ul>
          </>
        )}

        {phase.k === "fail" && (
          <div className="alert err" role="alert">{failText(phase.status, phase.msg)}</div>
        )}

        {phase.k === "done" && (
          <div className="rbr-result" role="status">
            <div className="alert ok">
              <strong>{phase.r.already_existed ? t("rbr.existed") : t("rbr.applied")}</strong>
            </div>
            <dl className="modal-kv">
              <dt>{t("rbr.branch")}</dt>
              <dd className="mono">{phase.r.branch}</dd>
              <dt>{t("rbr.pr")}</dt>
              <dd>
                {phase.r.pr_url && /^https:\/\//i.test(phase.r.pr_url) ? (
                  <a href={phase.r.pr_url} target="_blank" rel="noopener noreferrer">{t("rbr.prLink")} ↗</a>
                ) : (
                  <span className="muted">{t("rbr.noPr")}</span>
                )}
              </dd>
            </dl>
            <p className="muted" style={{ fontSize: 13 }}><Emph text={t("rbr.warn")} /></p>
          </div>
        )}

        <div className="modal-actions">
          <button type="button" ref={cancelRef} className="btn" disabled={busy} onClick={onClose}>
            {phase.k === "done" || phase.k === "fail" ? t("rbr.close") : t("rbr.cancel")}
          </button>
          {(phase.k === "confirm" || phase.k === "busy") && (
            <button type="button" className="btn primary" disabled={busy} onClick={confirm}>
              {busy ? t("rbr.creating") : t("rbr.confirm")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
