// Licencia (admin). Carga por la web la licencia firmada por la WLA (E4).
// La licencia NO es un secreto: es un JWS que el servidor verifica con la pública
// embebida. Aun así NO se muestra ni se re-descarga el token: esta pantalla solo
// pega/aplica uno nuevo y muestra el ESTADO que reporta el servidor (la autoridad:
// admin + MFA, validación de firma/producto, precedencia BD>entorno).
import { useRef, useState } from "react";
import type { ChangeEvent } from "react";

import { ApiError } from "../api/http";
import { settingsApi } from "../api/admin";
import type { LicenseStatus } from "../api/types";
import { errText, fmtDate } from "../components/util";
import { useAsync } from "../hooks";
import { useT, type TFn } from "../i18n";

/** Traduce el error del servidor conservando el detalle (422 distingue firma/producto). */
function applyErrorText(err: unknown, t: TFn): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return t("license.err403");
    return t("license.errServer", { msg: err.message }); // 422: firma inválida o producto
  }
  return errText(err);
}

/** Línea de estado + clase de badge a partir del estado del servidor. */
function describe(s: LicenseStatus, t: TFn): { cls: string; text: string } {
  if (!s.present) return { cls: "off", text: t("license.absent") };
  if (s.valid) {
    const soon = s.days_left != null && s.days_left <= 14;
    return {
      cls: soon ? "warn" : "ok",
      text: t("license.active", {
        customer: s.customer || "—",
        edition: s.edition || "—",
        days: s.days_left ?? "—",
      }),
    };
  }
  if (s.error === "expired") return { cls: "err", text: t("license.expired") };
  if (s.error === "wrong_product") return { cls: "err", text: t("license.wrongProduct") };
  return { cls: "err", text: t("license.invalid") };
}

function LicensePanel({ initial }: { initial: LicenseStatus }) {
  const t = useT();
  const [status, setStatus] = useState<LicenseStatus>(initial);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const badge = describe(status, t);
  const sourceLabel =
    status.source === "db"
      ? t("license.sourceDb")
      : status.source === "env"
        ? t("license.sourceEnv")
        : t("license.sourceNone");

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setToken(String(reader.result || "").trim());
      setSaved(null);
      setError(null);
    };
    reader.onerror = () => setError(t("license.fileError"));
    reader.readAsText(file);
    if (fileRef.current) fileRef.current.value = ""; // permite re-subir el mismo archivo
  };

  const apply = async () => {
    const tok = token.trim();
    if (!tok) return setError(t("license.emptyToken"));
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const out = await settingsApi.putLicense(tok);
      setStatus(out);
      setToken("");
      setSaved(t("license.applied"));
    } catch (err) {
      setError(applyErrorText(err, t)); // 422 firma/producto · 403 sin admin/MFA
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (!window.confirm(t("license.confirmClear"))) return;
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const out = await settingsApi.clearLicense();
      setStatus(out);
      setSaved(t("license.cleared"));
    } catch (err) {
      setError(applyErrorText(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* ── Estado actual, tal como lo reporta el servidor ── */}
      <section className="card" aria-labelledby="lic-status" style={{ marginBottom: 16 }}>
        <h3 id="lic-status">
          {t("license.statusTitle")} <span className={`badge ${badge.cls}`}>{badge.text}</span>
        </h3>
        <div className="row">
          <span className="muted">{t("license.source")}</span>
          <span className="mono">{sourceLabel}</span>
        </div>
        {status.present && (
          <>
            <div className="row">
              <span className="muted">{t("license.edition")}</span>
              <span className="mono">{status.edition || "—"}</span>
            </div>
            <div className="row">
              <span className="muted">{t("license.customer")}</span>
              <span className="mono">{status.customer || "—"}</span>
            </div>
            <div className="row">
              <span className="muted">{t("license.expires")}</span>
              <span className="mono">{fmtDate(status.expires_at)}</span>
            </div>
          </>
        )}
        {status.present && (status.source === "db" ? (
          <div className="form-actions">
            <button type="button" className="btn sm danger" onClick={clear} disabled={busy}>
              {t("license.clear")}
            </button>
          </div>
        ) : (
          <p className="hint">{t("license.envNote")}</p>
        ))}
      </section>

      {/* ── Cargar / reemplazar ── */}
      <section className="card">
        <h3>{t("license.applyTitle")}</h3>
        <p className="muted">{t("license.applyIntro")}</p>
        <div className="field">
          <label htmlFor="lic-token">{t("license.tokenLabel")}</label>
          <textarea
            id="lic-token"
            className="mono"
            spellCheck={false}
            autoComplete="off"
            rows={5}
            placeholder={t("license.tokenPlaceholder")}
            value={token}
            onChange={(e) => { setToken(e.target.value); setSaved(null); }}
          />
          <span className="hint">{t("license.tokenHint")}</span>
        </div>
        <div className="field">
          <label htmlFor="lic-file">{t("license.fileLabel")}</label>
          <input id="lic-file" ref={fileRef} type="file" accept=".lic,.jwt,.txt,text/plain" onChange={onFile} />
        </div>
        {error && <div className="alert err" role="alert">{error}</div>}
        {saved && <div className="alert ok" role="status">{saved}</div>}
        <div className="form-actions">
          <button className="btn primary" type="button" onClick={apply} disabled={busy || !token.trim()}>
            {busy ? t("common.saving") : t("license.applyBtn")}
          </button>
        </div>
      </section>
    </>
  );
}

export function License() {
  const t = useT();
  const { data, loading, error, reload } = useAsync(settingsApi.getLicense, []);
  return (
    <>
      <h2 className="page-title">{t("license.title")}</h2>
      <p className="muted">{t("license.intro")}</p>
      {loading && !data && <p className="state">{t("license.loading")}</p>}
      {error && (
        <div className="alert err" role="alert">
          {error} <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button>
        </div>
      )}
      {data && <LicensePanel initial={data} />}
    </>
  );
}
