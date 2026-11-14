// 6.3.d — Vista global de excepciones (riesgos aceptados y otros estados). Evidencia
// ISO 27001 A.8.8. Tarjetas-resumen + cola de pendientes + registro filtrable; admin
// aprueba propuestas de riesgo aceptado, operator+ revoca las aplicadas. Todo es DATO
// del backend (texto plano). Estado vacío instructivo: nada de riesgos inventados.
import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { approveException, fetchExceptions, revokeException } from "../api/client";
import type { ExceptionItem } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { fmtDate } from "../components/util";
import { useAsync } from "../hooks";
import { useT } from "../i18n";

const STATUSES = ["applied", "proposed", "revoked", "expired", "expire_failed", "write_failed"] as const;
type Tile = "applied" | "proposed" | "patchable" | "expiring" | "attention";
const TILE_TONE: Record<Tile, string> = { applied: "ok", proposed: "info", patchable: "warn", expiring: "warn", attention: "danger" };

function daysLeft(e: ExceptionItem): number | null {
  return e.expires_at ? (new Date(e.expires_at).getTime() - Date.now()) / 86400000 : null;
}
function isExpiring(e: ExceptionItem): boolean {
  if (e.status !== "applied") return false;
  const d = daysLeft(e);
  return d !== null && d >= 0 && d < 14;
}
// Riesgo aceptado vigente cuyo parche apareció tras aceptarlo (ADR-021): hay que remediar.
function isPatchable(e: ExceptionItem): boolean {
  return e.status === "applied" && e.kind === "risk_accepted" && Boolean(e.patchable_since && e.fixed_version);
}
function isAttention(e: ExceptionItem): boolean {
  return e.status === "expire_failed" || e.status === "write_failed";
}
function expiryClass(e: ExceptionItem): string {
  if (!e.expires_at || e.status !== "applied") return "";
  const d = daysLeft(e);
  return d !== null && d < 0 ? "exp-over" : d !== null && d < 14 ? "exp-soon" : "";
}

export function AcceptedRisksView() {
  const t = useT();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const canWrite = user?.role === "admin" || user?.role === "operator";
  // `filter` es un estado válido ("applied"…) o un filtro derivado ("expiring"/"attention").
  const [params] = useSearchParams();
  const [filter, setFilter] = useState<string>(params.get("f") === "patchable" ? "patchable" : "applied");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Se trae TODO una vez (volumen de gobernanza es chico) y se deriva en el cliente:
  // tarjetas-resumen siempre sobre el total, tabla según el filtro elegido.
  const res = useAsync(() => fetchExceptions({}), []);
  const { data, loading, error, reload } = res;
  const all = useMemo(() => data?.items ?? [], [data]);

  const counts = useMemo(() => {
    let applied = 0, proposed = 0, patchable = 0, expiring = 0, attention = 0;
    for (const e of all) {
      if (e.status === "applied") { applied++; if (isExpiring(e)) expiring++; if (isPatchable(e)) patchable++; }
      else if (e.status === "proposed") proposed++;
      if (isAttention(e)) attention++;
    }
    return { applied, proposed, patchable, expiring, attention };
  }, [all]);

  const shown = useMemo(() => {
    if (filter === "") return all;
    if (filter === "patchable") return all.filter(isPatchable);
    if (filter === "expiring") return all.filter(isExpiring);
    if (filter === "attention") return all.filter(isAttention);
    return all.filter((e) => e.status === filter);
  }, [all, filter]);

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

  const tiles: { key: Tile; n: number }[] = [
    { key: "applied", n: counts.applied },
    { key: "proposed", n: counts.proposed },
    { key: "patchable", n: counts.patchable },
    { key: "expiring", n: counts.expiring },
    { key: "attention", n: counts.attention },
  ];
  const selectValue = (STATUSES as readonly string[]).includes(filter) ? filter : "";

  return (
    <div className="risks-view">
      <div className="list-bar">
        <h2 className="list-title">{t("risks.title")}</h2>
        <div className="list-tools">
          <select value={selectValue} onChange={(e) => setFilter(e.target.value)} aria-label={t("risks.filterStatus")}>
            <option value="">{t("risks.allStatuses")}</option>
            {STATUSES.map((s) => <option key={s} value={s}>{t(`risks.status.${s}`)}</option>)}
          </select>
        </div>
      </div>
      <p className="muted">{t("risks.subtitle")}</p>

      {/* Tarjetas-resumen: clic = filtra el registro. Siempre reflejan el total. */}
      <div className="risks-stats">
        {tiles.map((ti) => (
          <button key={ti.key} type="button"
            className={`risks-stat tone-${TILE_TONE[ti.key]}${filter === ti.key ? " on" : ""}`}
            aria-pressed={filter === ti.key} onClick={() => setFilter(ti.key)}>
            <span className="risks-stat-n">{ti.n}</span>
            <span className="risks-stat-l">{t(`risks.stat.${ti.key}`)}</span>
          </button>
        ))}
      </div>

      {err && <div className="alert err" role="alert">{err}</div>}
      {loading && !data && <p className="state" aria-busy="true">{t("common.loading")}</p>}
      {error && <div className="alert err" role="alert">{error} <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button></div>}

      {data && all.length === 0 && (
        <div className="risks-empty card">
          <h3>{t("risks.emptyTitle")}</h3>
          <p className="muted">{t("risks.emptyBody")}</p>
          <Link to="/" className="btn sm">{t("risks.goProjects")}</Link>
        </div>
      )}
      {data && all.length > 0 && shown.length === 0 && <p className="empty">{t("risks.empty")}</p>}

      {shown.length > 0 && (
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
              {shown.map((e) => (
                <tr key={e.id} className={isPatchable(e) ? "patchable" : undefined}>
                  <td>{e.project_key}</td>
                  <td className="mono">{e.source}</td>
                  <td>
                    {e.finding_title || e.finding_ref}
                    {isPatchable(e) && (
                      <div><span className="pill soft-warn patch-badge" title={e.cve_id ?? undefined}>
                        {t("risks.patchBadge", { fixed: e.fixed_version ?? "" })}
                      </span></div>
                    )}
                  </td>
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
                      <button type="button" className={`btn sm${isPatchable(e) ? " primary" : ""}`} disabled={busyId === e.id}
                              onClick={() => act(e.id, revokeException)}>{isPatchable(e) ? t("risks.revokeRemediate") : t("risks.revoke")}</button>
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
