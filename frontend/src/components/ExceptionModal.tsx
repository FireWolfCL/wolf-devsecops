// Modal para registrar una excepción de hallazgo (ADR-021, 6.3.b): falso positivo,
// aceptar riesgo, fuera de alcance o mitigado. Escribe el estado en la fuente vía el
// backend (RBAC + Pro los decide el servidor). El motivo es obligatorio.
import { useState } from "react";

import { createException } from "../api/client";
import type { ExceptionKind } from "../api/types";
import { useT } from "../i18n";

const KINDS: ExceptionKind[] = ["false_positive", "risk_accepted", "out_of_scope", "mitigated"];

export function ExceptionModal({
  projectKey, source, finding, onClose, onDone,
}: {
  projectKey: string;
  source: "defectdojo" | "sonarqube";
  finding: { ref: string; title?: string | null; category?: string | null; cve?: string | null; fixed?: string | null };
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const t = useT();
  const [kind, setKind] = useState<ExceptionKind>("false_positive");
  const [justification, setJustification] = useState("");
  const [expiresDays, setExpiresDays] = useState<number>(90);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Aceptar riesgo SIEMPRE se propone (lo aplica un admin con MFA al aprobar, ADR-021).
  const willPropose = kind === "risk_accepted";

  const submit = async () => {
    if (justification.trim().length < 3) {
      setError(t("exc.needJustification"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createException(projectKey, {
        source,
        finding_ref: finding.ref,
        kind,
        justification: justification.trim(),
        finding_title: finding.title ?? null,
        category: finding.category ?? null,
        expires_days: kind === "risk_accepted" ? expiresDays : null,
        // Seguimiento de parche (solo riesgo aceptado): sin CVE el servidor intenta derivarlo
        // del título; sin CVE no se rastrea. fixed null = "aceptado sin parche".
        // `|| null`: un CVE vacío ("") debe ir como null (el `??` lo dejaba "" y el
        // patrón CVE del backend lo rechazaba con 422). Sin CVE el servidor lo deriva del título.
        cve_id: kind === "risk_accepted" ? (finding.cve || null) : null,
        fixed_version: kind === "risk_accepted" ? (finding.fixed || null) : null,
      });
      onDone(willPropose ? t("exc.proposed") : t("exc.applied"));
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={t("exc.title")} onClick={onClose}>
      <div className="modal exc-modal" onClick={(e) => e.stopPropagation()}>
        <h3>{t("exc.title")}</h3>
        <p className="muted exc-target">{finding.title || finding.ref}</p>

        <label className="field">
          <span>{t("exc.kind")}</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as ExceptionKind)}>
            {KINDS.map((k) => <option key={k} value={k}>{t(`exc.kind.${k}`)}</option>)}
          </select>
        </label>

        {kind === "risk_accepted" && (
          <label className="field">
            <span>{t("exc.expires")}</span>
            <input type="number" min={1} max={365} value={expiresDays}
                   onChange={(e) => setExpiresDays(Math.max(1, Math.min(365, Number(e.target.value) || 90)))} />
          </label>
        )}

        <label className="field">
          <span>{t("exc.justification")} *</span>
          <textarea rows={3} value={justification} onChange={(e) => setJustification(e.target.value)}
                    placeholder={t("exc.justificationHint")} />
        </label>

        {willPropose && <p className="alert info sm">{t("exc.willPropose")}</p>}
        {error && <p className="alert err sm" role="alert">{error}</p>}

        <div className="modal-actions">
          <button type="button" className="btn sm" onClick={onClose} disabled={busy}>{t("common.cancel")}</button>
          <button type="button" className="btn sm primary" onClick={submit} disabled={busy}>
            {busy ? t("exc.sending") : willPropose ? t("exc.propose") : t("exc.apply")}
          </button>
        </div>
      </div>
    </div>
  );
}
