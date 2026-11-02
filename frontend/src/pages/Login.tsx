// Login de dos pasos: credenciales -> (si mfa_required) código TOTP o de recuperación.
// Banderas must_change_password / must_enroll_mfa: se resuelven en /profile.
import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { errText } from "../components/util";
import { useT } from "../i18n";

export function Login() {
  const { user, loading, awaitingMfa, login, completeMfaLogin, cancelMfa } = useAuth();
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (loading) return <p className="state">{t("auth.verifyingSession")}</p>;
  if (user) {
    // Sesión ya activa (o recién establecida): decidir destino según banderas.
    const dest = user.must_change_password || user.must_enroll_mfa ? "/profile" : from;
    return <Navigate to={dest} replace />;
  }

  const submitCreds = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await login(username.trim(), password);
      if (out.kind === "ok") navigate(from, { replace: true });
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await completeMfaLogin(code.trim());
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-box">
      <h1 style={{ marginBottom: 4 }}>Wolf DevSecOps</h1>
      <p className="muted" style={{ marginTop: 0 }}>{t("login.subtitle")}</p>
      <div className="card">
        {!awaitingMfa ? (
          <form onSubmit={submitCreds}>
            <div className="field">
              <label htmlFor="username">{t("login.username")}</label>
              <input id="username" type="text" autoComplete="username" autoFocus required
                value={username} onChange={(e) => setUsername(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="password">{t("login.password")}</label>
              <input id="password" type="password" autoComplete="current-password" required
                value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            {error && <div className="alert err" role="alert">{error}</div>}
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? t("login.submitting") : t("login.submit")}
            </button>
          </form>
        ) : (
          <form onSubmit={submitCode}>
            <p>{t("login.mfaIntro")}</p>
            <div className="field">
              <label htmlFor="code">{t("login.code")}</label>
              <input id="code" type="text" inputMode="text" autoComplete="one-time-code" autoFocus required
                value={code} onChange={(e) => setCode(e.target.value)} />
            </div>
            {error && <div className="alert err" role="alert">{error}</div>}
            <div className="form-actions">
              <button className="btn primary" type="submit" disabled={busy}>
                {busy ? t("login.verifying") : t("login.verify")}
              </button>
              <button className="btn" type="button" onClick={() => { cancelMfa(); setCode(""); setError(null); }}>
                {t("login.back")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
