// 6.3.d — Vista global de excepciones (riesgos aceptados y otros estados). Evidencia
// ISO 27001 A.8.8. Filtra por estado/categoría; admin aprueba propuestas de riesgo
// aceptado, operator+ revoca las aplicadas. Todo es DATO del backend (texto plano).
import { useCallback, useState } from "react";

import { approveException, fetchExceptions, revokeException } from "../api/client";
import type { ExceptionItem } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { fmtDate } from "../components/util";
import { useAsync } from "../hooks";
import { useT } from "../i18n";

const STATUSES = ["applied", "proposed", "revoked", "expired", "expire_failed", "write_failed"] as const;

function expiryClass(e: ExceptionItem): string {
  if (!e.expires_at || e.status !== "applied") return "";
  const days = (new Date(e.expires_at).getTime() - Date.now()) / 86400000;
  return days < 0 ? "exp-over" : days < 14 ? "exp-soon" : "";
}

export function AcceptedRisksView() {
  const t = useT();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const canWrite = user?.role === "admin" || user?.role === "operator";
  const [status, setStatus] = useState<string>("applied");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const res = useAsync(() => fetchExceptions(status ? { status } : {}), [status]);
  const { data, loading, error, reload } = res;

  const act = useCallback(async (id: string, fn: (id: string) => Promise<ExceptionItem>) => {
    setErr(null);
    setBusyId(id);
    try {
      await fn(id);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }, [reload]);

  const items = data?.items ?? [];

  return (
    <div className="risks-view">
      <div className="list-bar">
        <h2 className="list-title">{t("risks.title")}</h2>
        <div className="list-tools">
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("risks.filterStatus")}>
            <option value="">{t("risks.allStatuses")}</option>
            {STATUSES.map((s) => <option key={s} value={s}>{t(`risks.status.${s}`)}</option>)}
          </select>
        </div>
      </div>
      <p className="muted">{t("risks.subtitle")}</p>

      {err && <div className="alert err" role="alert">{err}</div>}
      {loading && !data && <p className="state" aria-busy="true">{t("common.loading")}</p>}
      {error && <div className="alert err" role="alert">{error} <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button></div>}
      {data && items.length === 0 && <p className="empty">{t("risks.empty")}</p>}

      {items.length > 0 && (
        <div className="hist-scroll">
          <table className="hist-table risks-table">
            <thead>
              <tr>
                <th>{t("risks.project")}</th>
                <th>{t("risks.source")}</th>
                <th>{t("risks.finding")}</th>
                <th>{t("risks.kind")}</th>
                <th>{t("risks.status")}</th>
                <th>{t("risks.by")}</th>
                <th>{t("risks.expires")}</th>
                <th>{t("risks.justification")}</th>
                <th aria-label={t("risks.actions")} />
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id}>
                  <td>{e.project_key}</td>
                  <td className="mono">{e.source}</td>
                  <td>{e.finding_title || e.finding_ref}</td>
                  <td>{t(`exc.kind.${e.kind}`)}</td>
                  <td><span className={`pill ${e.status === "applied" ? "pass" : (e.status === "expire_failed" || e.status === "write_failed") ? "fail" : e.status === "expired" ? "soft-warn" : "neutral"}`}>{t(`risks.status.${e.status}`)}</span></td>
                  <td className="mono">{e.approved_by || e.created_by || "—"}</td>
                  <td className={`mono ${expiryClass(e)}`}>{e.expires_at ? fmtDate(e.expires_at) : "—"}</td>
                  <td className="risks-just">{e.justification}</td>
                  <td className="risks-actions">
                    {isAdmin && e.status === "proposed" && (
                      <button type="button" className="btn sm primary" disabled={busyId === e.id}
                              onClick={() => act(e.id, approveException)}>{t("risks.approve")}</button>
                    )}
                    {canWrite && (e.status === "applied" || e.status === "expire_failed" || e.status === "write_failed") && (
                      <button type="button" className="btn sm" disabled={busyId === e.id}
                              onClick={() => act(e.id, revokeException)}>{t("risks.revoke")}</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
