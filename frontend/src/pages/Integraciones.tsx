// Integraciones (admin): catálogo → Conectar / Probar conexión. Todo texto del
// servidor (detail, version, errores) se renderiza como TEXTO de React; jamás HTML.
// El token nunca viaja: solo el NOMBRE de la variable de entorno.
import { useState } from "react";
import type { FormEvent } from "react";

import { settingsApi } from "../api/admin";
import type { AvailableSource, ProbeResult } from "../api/types";
import { errText } from "../components/util";
import { useT } from "../i18n";
import { useAsync } from "../hooks";

const CONTAINER_SOURCES = ["harbor", "trivy"];
const kindKey = (name: string) => (CONTAINER_SOURCES.includes(name) ? "integ.kindContainers" : "integ.kindCode");

function Light({ on, label }: { on: boolean; label: string }) {
  const t = useT();
  return (
    <div className="light">
      <span className={`dot ${on ? "on" : ""}`} aria-hidden="true" />
      <span>{label}: <strong>{on ? t("common.yes") : t("common.no")}</strong></span>
    </div>
  );
}

function ProbePanel({ r }: { r: ProbeResult }) {
  const t = useT();
  return (
    <div className="probe" role="status">
      <Light on={r.reachable} label={t("integ.reachable")} />
      <Light on={r.authenticated} label={t("integ.authenticated")} />
      <Light on={r.compatible} label={t("integ.compatible")} />
      <div className="muted" style={{ fontSize: 12 }}>
        {t("integ.detectedVersion")} <span className="mono">{r.version ?? "—"}</span>
      </div>
      {r.detail && <div className="alert info mono">{r.detail}</div>}
    </div>
  );
}

function ConnectForm({ name, onDone, onCancel }: { name: string; onDone: () => void; onCancel: () => void }) {
  const t = useT();
  const [url, setUrl] = useState("");
  const [tokenEnv, setTokenEnv] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await settingsApi.putSource(name, { url: url.trim(), token_env: tokenEnv.trim(), enabled });
      onDone();
    } catch (err) {
      setError(errText(err)); // 400: token literal / URL inválida / host bloqueado — tal cual
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <div className="field">
        <label htmlFor={`int-url-${name}`}>{t("integ.url")}</label>
        <input id={`int-url-${name}`} type="text" required maxLength={400} value={url} onChange={(e) => setUrl(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={`int-env-${name}`}>{t("integ.tokenEnv")}</label>
        <input id={`int-env-${name}`} type="text" className="mono" required maxLength={120} value={tokenEnv} onChange={(e) => setTokenEnv(e.target.value)} />
        <span className="hint">{t("integ.tokenHint")}</span>
      </div>
      <label className="check">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {t("common.enabledF")}
      </label>
      {error && <div className="alert err" role="alert">{error}</div>}
      <div className="form-actions">
        <button className="btn primary" type="submit" disabled={busy}>{busy ? t("common.saving") : t("integ.saveConnect")}</button>
        <button className="btn" type="button" onClick={onCancel} disabled={busy}>{t("common.cancel")}</button>
      </div>
    </form>
  );
}

// Pegar el VALOR del token: se cifra en reposo (Fernet) con precedencia sobre la env var.
// El valor nunca se muestra ni vuelve del servidor; solo se confirma su presencia.
function TokenForm({ s, onChanged }: { s: AvailableSource; onChanged: () => void }) {
  const t = useT();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await settingsApi.setSourceToken(s.name, token);
      setToken(""); // no retenemos el valor en memoria del navegador
      onChanged();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    setError(null);
    try {
      await settingsApi.clearSourceToken(s.name);
      onChanged();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} style={{ marginTop: 12 }}>
      <div className="field">
        <label htmlFor={`int-tok-${s.name}`}>{t("integ.tokenValue")}</label>
        <input
          id={`int-tok-${s.name}`}
          type="password"
          className="mono"
          autoComplete="off"
          maxLength={500}
          placeholder={s.token_source === "db" ? t("integ.tokenReplace") : t("integ.tokenPaste")}
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <span className="hint">{t("integ.tokenValueHint")}</span>
      </div>
      {error && <div className="alert err" role="alert">{error}</div>}
      <div className="form-actions">
        <button className="btn primary" type="submit" disabled={busy || !token.trim()}>
          {busy ? t("common.saving") : t("integ.tokenSave")}
        </button>
        {s.token_source === "db" && (
          <button className="btn" type="button" onClick={clear} disabled={busy}>
            {t("integ.tokenClear")}
          </button>
        )}
      </div>
    </form>
  );
}

function SourceCard({ s, onChanged }: { s: AvailableSource; onChanged: () => void }) {
  const t = useT();
  const [connecting, setConnecting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const test = async () => {
    setTesting(true);
    setError(null);
    setProbe(null);
    try {
      setProbe(await settingsApi.testSource(s.name));
    } catch (err) {
      setError(errText(err));
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="card int-card">
      <h3>
        <span className="mono">{s.name}</span>
        <span className="muted" style={{ fontSize: 12 }}>{t(kindKey(s.name))}</span>
      </h3>
      <div className="int-chips">
        {s.configured ? <span className="badge ok">{t("integ.configured")}</span> : <span className="badge off">{t("integ.unconfigured")}</span>}
        {s.configured && <span className={`badge ${s.enabled ? "ok" : "off"}`}>{s.enabled ? t("integ.active") : t("integ.inactive")}</span>}
        {s.configured && (
          <span className={`badge ${s.token_present ? "ok" : "danger"}`}>{s.token_present ? t("integ.tokenPresent") : t("integ.tokenAbsent")}</span>
        )}
        {s.configured && s.token_present && (
          <span className="badge">{s.token_source === "db" ? t("integ.tokenFromDb") : t("integ.tokenFromEnv")}</span>
        )}
        {s.configured && s.token_expiring_soon && (
          <span className="badge danger">{t("integ.tokenExpiringSoon")}</span>
        )}
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {t("integ.supportedVersions")} <span className="mono">{s.supported_versions}</span>
      </div>
      {s.configured && s.token_expires_at && (
        <div className="muted" style={{ fontSize: 12 }}>
          {t("integ.tokenExpires")} <span className="mono">{new Date(s.token_expires_at).toLocaleDateString()}</span>
        </div>
      )}

      {s.configured && (
        <div style={{ marginTop: 12 }}>
          <button className="btn" type="button" onClick={test} disabled={testing}>
            {testing ? <><span className="spin" aria-hidden="true" />{t("integ.testing")}</> : t("integ.test")}
          </button>
          {error && <div className="alert err" role="alert">{error}</div>}
          {probe && <ProbePanel r={probe} />}
          <TokenForm s={s} onChanged={onChanged} />
        </div>
      )}

      {!s.configured && !connecting && (
        <div style={{ marginTop: 12 }}>
          <button className="btn primary" type="button" onClick={() => setConnecting(true)}>{t("integ.connect")}</button>
        </div>
      )}
      {!s.configured && connecting && (
        <ConnectForm name={s.name} onCancel={() => setConnecting(false)} onDone={() => { setConnecting(false); onChanged(); }} />
      )}
    </div>
  );
}

export function Integraciones() {
  const t = useT();
  const { data, loading, error, reload } = useAsync(settingsApi.availableSources, []);
  const configured = data?.filter((s) => s.configured) ?? [];
  const withToken = configured.filter((s) => s.token_present).length;
  const containers = data?.filter((s) => CONTAINER_SOURCES.includes(s.name)) ?? [];
  const containersOn = containers.filter((s) => s.configured && s.enabled).length;

  return (
    <section className="section">
      <h2>{t("integ.title")}</h2>
      {loading && !data && <p className="state">{t("integ.loadingCatalog")}</p>}
      {error && <div className="alert err" role="alert">{error}</div>}
      {data && (
        <>
          <div className="int-summary">
            <div className="int-stat"><div className="n">{configured.length}/{data.length}</div><div className="l">{t("integ.connected")}</div></div>
            <div className="int-stat"><div className="n">{withToken}/{configured.length}</div><div className="l">{t("integ.withToken")}</div></div>
            <div className="int-stat"><div className="n">{containersOn}/{containers.length}</div><div className="l">{t("integ.containerCoverage")}</div></div>
          </div>
          {data.length === 0 && <p className="state">{t("integ.emptyCatalog")}</p>}
          <div className="int-grid">
            {data.map((s) => <SourceCard key={s.name} s={s} onChanged={reload} />)}
          </div>
        </>
      )}
    </section>
  );
}
