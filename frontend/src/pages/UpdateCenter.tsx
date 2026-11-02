// Centro de Update v1 (ADR-016, admin). Reporta versión/release/migraciones y
// aplica migraciones con salvaguardas. NO actualiza el código: el comando de
// redeploy lo corre el operador en el host (display-only).
import { useState } from "react";

import { updatesApi } from "../api/admin";
import type { UpdateStatusOut } from "../api/types";
import { CopyButton, errText, fmtDate } from "../components/util";
import { useAsync } from "../hooks";
import { useT, type TFn } from "../i18n";

const KNOWN_REASONS = ["no_repo", "network_error", "invalid_response", "no_matching_release"];

function reasonText(reason: string | null, t: TFn): string {
  if (!reason) return t("updates.reasonUnknown");
  if (KNOWN_REASONS.includes(reason)) return `${t(`updates.reason.${reason}`)} (${reason})`;
  return reason; // p. ej. http_403
}

/** Solo enlaces https: la URL viene de una fuente externa no confiable. */
function safeHttpsUrl(u: string | null): string | null {
  if (!u) return null;
  try {
    return new URL(u).protocol === "https:" ? u : null;
  } catch {
    return null;
  }
}

function Verdict({ s }: { s: UpdateStatusOut }) {
  const t = useT();
  const ua = s.server.update_available;
  const mig = s.migrations;
  if (ua === false && mig.up_to_date) {
    return <div className="alert ok" role="status">{t("updates.upToDateMsg")}</div>;
  }
  return (
    <>
      {ua === true && <div className="alert warn" role="status">{t("updates.newAvailable")}</div>}
      {ua === null && (
        <div className="alert warn" role="status">
          {t("updates.cannotVerify", { reason: reasonText(s.server.latest.reason, t) })}
        </div>
      )}
      {!mig.up_to_date && (
        <div className="alert warn" role="status">
          {mig.pending >= 0 ? t("updates.pendingMig", { n: mig.pending }) : t("updates.pendingMigUnknown")}
        </div>
      )}
    </>
  );
}

function MigrateBox({ s, onApplied }: { s: UpdateStatusOut; onApplied: () => void }) {
  const t = useT();
  const isProd = s.environment === "production";
  const [backup, setBackup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const apply = async () => {
    const msg = isProd ? t("updates.confirmProd") : t("updates.confirmDev");
    if (!window.confirm(msg)) return;
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await updatesApi.migrate(backup);
      setDone(true);
      onApplied();
    } catch (err) {
      // 400 = irreversible / falta respaldo; 500 = "Migration failed: ..." (se muestra tal cual).
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginTop: 12 }}>
      <label className="check">
        <input type="checkbox" checked={backup} onChange={(e) => setBackup(e.target.checked)} />
        {t("updates.backupLabel")}{isProd ? t("updates.backupRequired") : ""}
      </label>
      {error && <div className="alert err" role="alert" style={{ whiteSpace: "pre-wrap" }}>{error}</div>}
      {done && <div className="alert ok" role="status">{t("updates.applied")}</div>}
      <button className="btn primary" disabled={busy || (isProd && !backup)} onClick={apply}>
        {busy ? t("updates.applying") : t("updates.apply")}
      </button>
    </div>
  );
}

export function UpdateCenter() {
  const t = useT();
  const { data, loading, error, reload } = useAsync(updatesApi.status, []);

  return (
    <>
      <div className="toolbar">
        <h2 className="page-title">{t("updates.title")}</h2>
        <button className="btn" onClick={reload} disabled={loading}>{loading ? t("updates.checking") : t("updates.recheck")}</button>
      </div>
      {loading && !data && <p className="state">{t("updates.querying")}</p>}
      {error && <div className="alert err" role="alert">{error}</div>}
      {data && (
        <>
          <Verdict s={data} />
          <div className="cards">
            <div className="card">
              <h3>{t("updates.version")}</h3>
              <div className="row"><span className="muted">{t("updates.installed")}</span><span className="mono">v{data.server.current_version}</span></div>
              <div className="row">
                <span className="muted">{t("updates.latest")}</span>
                <span className="mono">
                  {data.server.latest.available ? (data.server.latest.version ?? data.server.latest.tag ?? "—") : t("updates.unavailable")}
                </span>
              </div>
              <div className="row"><span className="muted">{t("updates.env")}</span><span className="mono">{data.environment}</span></div>
              <div className="row">
                <span className="muted">{t("updates.releaseSource")}</span>
                <span className="mono">{data.release_source.configured ? data.release_source.repo : t("updates.notConfigured")}</span>
              </div>
              {!data.server.latest.available && (
                <p className="muted">{t("updates.sourceUnavailable", { reason: reasonText(data.server.latest.reason, t) })}</p>
              )}
            </div>

            <div className="card">
              <h3>{t("updates.migrations")} <span className={`badge ${data.migrations.up_to_date ? "ok" : "warn"}`}>{data.migrations.up_to_date ? t("updates.badgeOk") : t("updates.badgePending")}</span></h3>
              <div className="row"><span className="muted">{t("updates.current")}</span><span className="mono">{data.migrations.current ?? "—"}</span></div>
              <div className="row"><span className="muted">{t("updates.head")}</span><span className="mono">{data.migrations.head ?? "—"}</span></div>
              <div className="row"><span className="muted">{t("updates.pending")}</span><span className="mono">{data.migrations.pending < 0 ? t("updates.undeterminable") : data.migrations.pending}</span></div>
              {!data.migrations.up_to_date && <MigrateBox s={data} onApplied={reload} />}
            </div>
          </div>

          {data.server.latest.available && (
            <section className="section">
              <h2>
                {data.server.latest.name ?? data.server.latest.tag ?? t("updates.latest")}{" "}
                <span className="muted" style={{ fontSize: 13 }}>{fmtDate(data.server.latest.published_at)}</span>
              </h2>
              {safeHttpsUrl(data.server.latest.url) && (
                <p><a href={safeHttpsUrl(data.server.latest.url)!} target="_blank" rel="noopener noreferrer">{t("updates.viewRelease")}</a></p>
              )}
              <h3 style={{ fontSize: 14 }}>{t("updates.changelog")}</h3>
              {/* notes es texto externo NO confiable: se renderiza como texto inerte, jamás HTML. */}
              <pre className="notes">{data.server.latest.notes?.trim() ? data.server.latest.notes : t("updates.noNotes")}</pre>
            </section>
          )}

          <section className="section">
            <h2>{t("updates.howTitle")}</h2>
            <p className="muted">{t("updates.howText")}</p>
            <div className="cmd">{data.update_command}</div>
            <div style={{ marginTop: 8 }}><CopyButton text={data.update_command} label={t("updates.copyCommand")} /></div>
          </section>
        </>
      )}
    </>
  );
}
