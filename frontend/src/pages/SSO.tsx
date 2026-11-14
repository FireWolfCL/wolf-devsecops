// SSO / LDAP (admin, E2 — feature Pro). Configura el directorio contra el que se
// autentican los usuarios. El backend ya está revisado; esta pantalla solo lee/escribe
// la config vía /settings/ldap. El bind password es WRITE-ONLY (endpoint aparte): nunca
// se muestra ni se re-descarga; solo se fija uno nuevo o se quita. Escribir exige Pro
// (402 si la edición no lo habilita).
import { useState } from "react";

import { ApiError } from "../api/http";
import { settingsApi } from "../api/admin";
import type { LDAPConfigOut, LDAPConfigIn } from "../api/types";
import { errText } from "../components/util";
import { useAsync } from "../hooks";
import { useT, type TFn } from "../i18n";

function saveErrorText(err: unknown, t: TFn): string {
  if (err instanceof ApiError) {
    if (err.status === 402) return t("sso.err402");
    if (err.status === 403) return t("sso.err403");
    return t("sso.errServer", { msg: err.message });
  }
  return errText(err);
}

function SSOPanel({ initial }: { initial: LDAPConfigOut }) {
  const t = useT();
  const [s, setS] = useState<LDAPConfigOut>(initial);
  const [hostsText, setHostsText] = useState(initial.hosts.join("\n"));
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const gated = !s.sso_available;
  const set = <K extends keyof LDAPConfigOut>(k: K, v: LDAPConfigOut[K]) => {
    setS((p) => ({ ...p, [k]: v }));
    setSaved(null);
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    setSaved(null);
    const hosts = hostsText.split("\n").map((h) => h.trim()).filter(Boolean);
    const body: LDAPConfigIn = {
      enabled: s.enabled, hosts, base_dn: s.base_dn, use_ssl: s.use_ssl,
      start_tls: s.start_tls, tls_verify: s.tls_verify, tls_ca_pem: s.tls_ca_pem || null,
      user_dn_template: s.user_dn_template || null, user_filter: s.user_filter,
      bind_dn: s.bind_dn || null, bind_password_env: s.bind_password_env || null,
      timeout_seconds: s.timeout_seconds,
    };
    try {
      const out = await settingsApi.putLdap(body);
      setS(out);
      setHostsText(out.hosts.join("\n"));
      setSaved(t("sso.saved"));
    } catch (err) {
      setError(saveErrorText(err, t));
    } finally {
      setBusy(false);
    }
  };

  const setBindPw = async () => {
    if (!pw.trim()) return;
    setBusy(true); setError(null); setSaved(null);
    try {
      const out = await settingsApi.setLdapBindPassword(pw);
      setS(out); setPw(""); setSaved(t("sso.bindPwSaved"));
    } catch (err) {
      setError(saveErrorText(err, t));
    } finally { setBusy(false); }
  };

  const clearBindPw = async () => {
    if (!window.confirm(t("sso.bindPwConfirm"))) return;
    setBusy(true); setError(null); setSaved(null);
    try {
      const out = await settingsApi.clearLdapBindPassword();
      setS(out); setSaved(t("sso.bindPwCleared"));
    } catch (err) {
      setError(saveErrorText(err, t));
    } finally { setBusy(false); }
  };

  return (
    <>
      {gated && <div className="alert warn" role="status">{t("sso.proGate")}</div>}

      <section className="card" style={{ marginBottom: 16 }}>
        <h3>{t("sso.connection")}</h3>
        <label className="check">
          <input type="checkbox" checked={s.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          <span>{t("sso.enabled")}</span>
        </label>
        <p className="hint">{t("sso.enabledHint")}</p>

        <div className="field">
          <label htmlFor="ldap-hosts">{t("sso.hosts")}</label>
          <textarea id="ldap-hosts" className="mono" rows={3} spellCheck={false} autoComplete="off"
            placeholder="ldaps://dc1.example.com&#10;ldaps://dc2.example.com"
            value={hostsText} onChange={(e) => { setHostsText(e.target.value); setSaved(null); }} />
          <span className="hint">{t("sso.hostsHint")}</span>
        </div>
        <div className="field">
          <label htmlFor="ldap-base">{t("sso.baseDn")}</label>
          <input id="ldap-base" className="mono" value={s.base_dn}
            placeholder="dc=example,dc=com" onChange={(e) => set("base_dn", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ldap-timeout">{t("sso.timeout")}</label>
          <input id="ldap-timeout" type="number" min={1} max={60} value={s.timeout_seconds}
            onChange={(e) => set("timeout_seconds", Math.max(1, Math.min(60, Number(e.target.value) || 8)))} />
        </div>
      </section>

      <section className="card" style={{ marginBottom: 16 }}>
        <h3>{t("sso.tls")}</h3>
        <label className="check">
          <input type="checkbox" checked={s.use_ssl} onChange={(e) => set("use_ssl", e.target.checked)} />
          <span>{t("sso.useSsl")}</span>
        </label>
        <label className="check">
          <input type="checkbox" checked={s.start_tls} onChange={(e) => set("start_tls", e.target.checked)} />
          <span>{t("sso.startTls")}</span>
        </label>
        <div className="field">
          <label htmlFor="ldap-verify">{t("sso.tlsVerify")}</label>
          <select id="ldap-verify" value={s.tls_verify}
            onChange={(e) => set("tls_verify", e.target.value as "required" | "relaxed")}>
            <option value="required">{t("sso.verifyRequired")}</option>
            <option value="relaxed">{t("sso.verifyRelaxed")}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="ldap-ca">{t("sso.caPem")}</label>
          <textarea id="ldap-ca" className="mono" rows={4} spellCheck={false} autoComplete="off"
            placeholder="-----BEGIN CERTIFICATE-----" value={s.tls_ca_pem || ""}
            onChange={(e) => set("tls_ca_pem", e.target.value)} />
          <span className="hint">{t("sso.caPemHint")}</span>
        </div>
      </section>

      <section className="card" style={{ marginBottom: 16 }}>
        <h3>{t("sso.mapping")}</h3>
        <div className="field">
          <label htmlFor="ldap-tmpl">{t("sso.userDnTemplate")}</label>
          <input id="ldap-tmpl" className="mono" value={s.user_dn_template || ""}
            placeholder="uid={username},ou=people,dc=example,dc=com"
            onChange={(e) => set("user_dn_template", e.target.value)} />
          <span className="hint">{t("sso.userDnTemplateHint")}</span>
        </div>
        <div className="field">
          <label htmlFor="ldap-filter">{t("sso.userFilter")}</label>
          <input id="ldap-filter" className="mono" value={s.user_filter}
            placeholder="(uid={username})" onChange={(e) => set("user_filter", e.target.value)} />
          <span className="hint">{t("sso.userFilterHint")}</span>
        </div>
      </section>

      <section className="card">
        <h3>{t("sso.bind")}</h3>
        <p className="muted">{t("sso.bindIntro")}</p>
        <div className="field">
          <label htmlFor="ldap-binddn">{t("sso.bindDn")}</label>
          <input id="ldap-binddn" className="mono" value={s.bind_dn || ""}
            placeholder="cn=svc-wolf,ou=svc,dc=example,dc=com" onChange={(e) => set("bind_dn", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ldap-bindpw">
            {t("sso.bindPassword")}{" "}
            <span className={`badge ${s.bind_password_present ? "ok" : "off"}`}>
              {s.bind_password_present ? t("sso.bindPwPresent") : t("sso.bindPwAbsent")}
            </span>
          </label>
          <input id="ldap-bindpw" type="password" autoComplete="new-password" value={pw}
            placeholder={t("sso.bindPwPlaceholder")} onChange={(e) => { setPw(e.target.value); setSaved(null); }} />
          <div className="form-actions">
            <button type="button" className="btn sm" onClick={setBindPw} disabled={busy || !pw.trim()}>
              {t("sso.bindPwSet")}
            </button>
            {s.bind_password_present && (
              <button type="button" className="btn sm danger" onClick={clearBindPw} disabled={busy}>
                {t("sso.bindPwClear")}
              </button>
            )}
          </div>
        </div>
        <div className="field">
          <label htmlFor="ldap-pwenv">{t("sso.bindPasswordEnv")}</label>
          <input id="ldap-pwenv" className="mono" value={s.bind_password_env || ""}
            placeholder="WOLF_LDAP_BIND_PASSWORD" onChange={(e) => set("bind_password_env", e.target.value)} />
          <span className="hint">{t("sso.bindPasswordEnvHint")}</span>
        </div>
      </section>

      {error && <div className="alert err" role="alert" style={{ marginTop: 16 }}>{error}</div>}
      {saved && <div className="alert ok" role="status" style={{ marginTop: 16 }}>{saved}</div>}
      <div className="form-actions" style={{ marginTop: 16 }}>
        <button type="button" className="btn primary" onClick={save} disabled={busy || gated}>
          {busy ? t("common.saving") : t("sso.save")}
        </button>
      </div>
    </>
  );
}

export function SSO() {
  const t = useT();
  const { data, loading, error, reload } = useAsync(settingsApi.getLdap, []);
  return (
    <>
      <h2 className="page-title">{t("sso.title")}</h2>
      <p className="muted">{t("sso.intro")}</p>
      {loading && !data && <p className="state">{t("sso.loading")}</p>}
      {error && (
        <div className="alert err" role="alert">
          {error} <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button>
        </div>
      )}
      {data && <SSOPanel initial={data} />}
    </>
  );
}
