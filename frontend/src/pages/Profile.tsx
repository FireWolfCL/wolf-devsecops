// Perfil: datos de cuenta, cambio de contraseña y ciclo de vida de MFA.
import { useState } from "react";
import type { FormEvent } from "react";

import { profileApi } from "../api/admin";
import type { MFASetupResponse } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { CopyButton, errText, fmtDate } from "../components/util";
import { useT } from "../i18n";

function ChangePassword() {
  const t = useT();
  const { refreshUser } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (next.length < 8) return setError(t("profile.pwd.tooShort"));
    if (next !== confirm) return setError(t("profile.pwd.mismatch"));
    setBusy(true);
    try {
      await profileApi.changePassword({ current_password: current, new_password: next });
      setCurrent(""); setNext(""); setConfirm("");
      setDone(true);
      await refreshUser();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card section">
      <h3>{t("profile.pwd.title")}</h3>
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="cp-cur">{t("profile.pwd.current")}</label>
          <input id="cp-cur" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cp-new">{t("profile.pwd.new")}</label>
          <input id="cp-new" type="password" autoComplete="new-password" required minLength={8} value={next} onChange={(e) => setNext(e.target.value)} />
          <span className="hint">{t("profile.pwd.min")}</span>
        </div>
        <div className="field">
          <label htmlFor="cp-conf">{t("profile.pwd.confirm")}</label>
          <input id="cp-conf" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        {error && <div className="alert err" role="alert">{error}</div>}
        {done && <div className="alert ok" role="status">{t("profile.pwd.updated")}</div>}
        <button className="btn primary" type="submit" disabled={busy}>{busy ? t("common.saving") : t("profile.pwd.title")}</button>
      </form>
    </div>
  );
}

function MfaSection() {
  const t = useT();
  const { user, refreshUser } = useAuth();
  const [setup, setSetup] = useState<MFASetupResponse | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [disablePwd, setDisablePwd] = useState("");
  const [showDisable, setShowDisable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user) return null;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const startSetup = () => run(async () => setSetup(await profileApi.mfaSetup()));
  const enable = (e: FormEvent) => {
    e.preventDefault();
    return run(async () => {
      const r = await profileApi.mfaEnable(code.trim());
      setRecovery(r.recovery_codes);
      setSetup(null);
      setCode("");
      await refreshUser();
    });
  };
  const disable = (e: FormEvent) => {
    e.preventDefault();
    return run(async () => {
      await profileApi.mfaDisable(disablePwd);
      setDisablePwd("");
      setShowDisable(false);
      await refreshUser();
    });
  };

  return (
    <div className="card section">
      <h3>
        {t("profile.mfa.title")}{" "}
        <span className={`badge ${user.mfa_enabled ? "ok" : "warn"}`}>{user.mfa_enabled ? t("profile.mfa.active") : t("profile.mfa.inactive")}</span>
      </h3>

      {user.must_enroll_mfa && !user.mfa_enabled && (
        <div className="alert warn" role="alert">{t("profile.mfa.required")}</div>
      )}
      {error && <div className="alert err" role="alert">{error}</div>}

      {recovery && (
        <div className="alert warn" role="alert">
          <strong>{t("profile.mfa.recoveryWarn")}</strong>
          <ul className="codes">
            {recovery.map((c) => <li key={c}>{c}</li>)}
          </ul>
          <div className="form-actions">
            <CopyButton text={recovery.join("\n")} label={t("profile.mfa.copyCodes")} />
            <button type="button" className="btn sm" onClick={() => setRecovery(null)}>{t("profile.mfa.saved")}</button>
          </div>
        </div>
      )}

      {!user.mfa_enabled && !setup && (
        <button type="button" className="btn primary" onClick={startSetup} disabled={busy}>{t("profile.mfa.setup")}</button>
      )}

      {!user.mfa_enabled && setup && (
        <div>
          <p>{t("profile.mfa.step1")}</p>
          {/* qr_code_svg es un SVG generado por NUESTRO backend (confiable). Es la única
              inyección de HTML permitida; secreto, URI y recovery codes van como TEXTO. */}
          <div className="qr" role="img" aria-label={t("profile.mfa.qrAlt")}
            dangerouslySetInnerHTML={{ __html: setup.qr_code_svg }} />
          <p className="muted" style={{ marginBottom: 4 }}>{t("profile.mfa.secretManual")}</p>
          <div className="secret">{setup.secret}</div>
          <form onSubmit={enable} style={{ marginTop: 16 }}>
            <div className="field">
              <label htmlFor="mfa-code">{t("profile.mfa.step2")}</label>
              <input id="mfa-code" type="text" inputMode="numeric" autoComplete="one-time-code" required
                value={code} onChange={(e) => setCode(e.target.value)} />
            </div>
            <div className="form-actions">
              <button className="btn primary" type="submit" disabled={busy}>{t("profile.mfa.enable")}</button>
              <button className="btn" type="button" onClick={() => { setSetup(null); setCode(""); setError(null); }}>{t("common.cancel")}</button>
            </div>
          </form>
        </div>
      )}

      {user.mfa_enabled && !showDisable && (
        <button type="button" className="btn danger" onClick={() => setShowDisable(true)}>{t("profile.mfa.disable")}</button>
      )}
      {user.mfa_enabled && showDisable && (
        <form onSubmit={disable}>
          <div className="field">
            <label htmlFor="mfa-pwd">{t("profile.mfa.disableConfirm")}</label>
            <input id="mfa-pwd" type="password" autoComplete="current-password" required value={disablePwd} onChange={(e) => setDisablePwd(e.target.value)} />
          </div>
          <div className="form-actions">
            <button className="btn danger" type="submit" disabled={busy}>{t("profile.mfa.disable")}</button>
            <button className="btn" type="button" onClick={() => { setShowDisable(false); setDisablePwd(""); setError(null); }}>{t("common.cancel")}</button>
          </div>
        </form>
      )}
    </div>
  );
}

export function Profile() {
  const t = useT();
  const { user } = useAuth();
  if (!user) return null;
  return (
    <>
      <h2 className="page-title">{t("profile.title")}</h2>
      {user.must_change_password && (
        <div className="alert warn" role="alert">{t("profile.mustChange")}</div>
      )}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="row"><span className="muted">{t("profile.username")}</span><span className="mono">{user.username}</span></div>
        <div className="row"><span className="muted">{t("profile.name")}</span><span>{user.full_name ?? "—"}</span></div>
        <div className="row"><span className="muted">{t("profile.email")}</span><span>{user.email ?? "—"}</span></div>
        <div className="row"><span className="muted">{t("profile.role")}</span><span className="mono">{user.role}</span></div>
        <div className="row"><span className="muted">{t("profile.lastLogin")}</span><span>{fmtDate(user.last_login_at)}</span></div>
      </div>
      <ChangePassword />
      <MfaSection />
    </>
  );
}
