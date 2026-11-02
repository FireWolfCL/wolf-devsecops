// Gestión de usuarios (admin). El servidor aplica los guards (último admin,
// autobloqueo -> 400); aquí solo se refleja su mensaje y se evita lo obvio.
import { useState } from "react";
import type { FormEvent } from "react";

import { usersApi } from "../api/admin";
import type { Role, UserOut } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { CopyButton, errText, fmtDate } from "../components/util";
import { useT } from "../i18n";
import { useAsync } from "../hooks";

const ROLES: Role[] = ["admin", "operator", "viewer"];

function isLocked(u: UserOut): boolean {
  return u.locked_until !== null && new Date(u.locked_until).getTime() > Date.now();
}

function CreateUser({ onCreated }: { onCreated: () => void }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [mustChange, setMustChange] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await usersApi.create({
        username: username.trim(),
        password,
        role,
        email: email.trim() || null,
        full_name: fullName.trim() || null,
        must_change_password: mustChange,
      });
      setUsername(""); setPassword(""); setEmail(""); setFullName(""); setRole("viewer"); setMustChange(true);
      setOpen(false);
      onCreated();
    } catch (err) {
      setError(errText(err)); // 409 = usuario duplicado, 422 = validación
    } finally {
      setBusy(false);
    }
  };

  if (!open) return <button type="button" className="btn primary" onClick={() => setOpen(true)}>{t("users.new")}</button>;
  return (
    <form className="card" onSubmit={submit} style={{ marginBottom: 16 }}>
      <h3>{t("users.new")}</h3>
      <div className="field">
        <label htmlFor="nu-user">{t("users.username")}</label>
        <input id="nu-user" type="text" required minLength={3} maxLength={100} pattern="[A-Za-z0-9._@\-]+" value={username} onChange={(e) => setUsername(e.target.value)} />
        <span className="hint">{t("users.usernameHint")}</span>
      </div>
      <div className="field">
        <label htmlFor="nu-pass">{t("users.initialPassword")}</label>
        <input id="nu-pass" type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="nu-role">{t("users.role")}</label>
        <select id="nu-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => <option key={r} value={r}>{t(`role.${r}`)}</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor="nu-name">{t("users.fullName")}</label>
        <input id="nu-name" type="text" maxLength={100} value={fullName} onChange={(e) => setFullName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="nu-mail">{t("users.email")}</label>
        <input id="nu-mail" type="email" maxLength={255} value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <label className="check">
        <input type="checkbox" checked={mustChange} onChange={(e) => setMustChange(e.target.checked)} />
        {t("users.mustChange")}
      </label>
      {error && <div className="alert err" role="alert">{error}</div>}
      <div className="form-actions">
        <button className="btn primary" type="submit" disabled={busy}>{busy ? t("users.creating") : t("users.create")}</button>
        <button className="btn" type="button" onClick={() => { setOpen(false); setError(null); }}>{t("common.cancel")}</button>
      </div>
    </form>
  );
}

function UserRow({
  u, isSelf, onChanged, onTempPassword, onError,
}: {
  u: UserOut;
  isSelf: boolean;
  onChanged: () => void;
  onTempPassword: (username: string, pwd: string) => void;
  onError: (msg: string | null) => void;
}) {
  const t = useT();
  const [role, setRole] = useState<Role>(u.role);
  const [busy, setBusy] = useState(false);
  const dirty = role !== u.role;

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    onError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      // 400 = guard último-admin / autobloqueo: se muestra el motivo del servidor.
      onError(`${u.username}: ${errText(err)}`);
      setRole(u.role);
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr>
      <td>
        <span className="mono">{u.username}</span>
        {isSelf && <span className="badge off" style={{ marginLeft: 8 }}>{t("users.you")}</span>}
        {u.full_name && <div className="muted" style={{ fontSize: 12 }}>{u.full_name}</div>}
      </td>
      <td>
        <select aria-label={t("users.roleOf", { user: u.username })} value={role} disabled={busy} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => <option key={r} value={r}>{t(`role.${r}`)}</option>)}
        </select>
      </td>
      <td>
        <span className={`badge ${u.is_active ? "ok" : "off"}`}>{u.is_active ? t("users.active") : t("users.inactive")}</span>{" "}
        {isLocked(u) && <span className="badge danger" title={t("users.lockedUntil", { date: fmtDate(u.locked_until) })}>{t("users.locked")}</span>}
      </td>
      <td><span className={`badge ${u.mfa_enabled ? "ok" : "warn"}`}>{u.mfa_enabled ? t("users.mfa") : t("users.noMfa")}</span></td>
      <td className="muted">{fmtDate(u.last_login_at)}</td>
      <td>
        <div className="cell-actions">
          {dirty && (
            <button className="btn sm primary" disabled={busy}
              onClick={() => act(async () => { await usersApi.update(u.id, { role }); })}>
              {t("users.saveRole")}
            </button>
          )}
          <button className="btn sm" disabled={busy || isSelf}
            title={isSelf ? t("users.selfStateTitle") : undefined}
            onClick={() => {
              const msg = t(u.is_active ? "users.confirmDeactivate" : "users.confirmReactivate", { user: u.username });
              if (window.confirm(msg))
                void act(async () => { await usersApi.update(u.id, { is_active: !u.is_active }); });
            }}>
            {u.is_active ? t("users.deactivate") : t("users.reactivate")}
          </button>
          <button className="btn sm" disabled={busy}
            onClick={() => {
              if (window.confirm(t("users.confirmReset", { user: u.username })))
                void act(async () => {
                  const r = await usersApi.resetPassword(u.id);
                  onTempPassword(u.username, r.temporary_password);
                });
            }}>
            {t("users.resetPassword")}
          </button>
          {isLocked(u) && (
            <button className="btn sm" disabled={busy} onClick={() => act(async () => { await usersApi.unlock(u.id); })}>
              {t("users.unlock")}
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

export function Users() {
  const t = useT();
  const { user: me } = useAuth();
  const { data, loading, error, reload } = useAsync(usersApi.list, []);
  const [actionError, setActionError] = useState<string | null>(null);
  const [temp, setTemp] = useState<{ username: string; pwd: string } | null>(null);

  return (
    <>
      <div className="toolbar">
        <h2 className="page-title">{t("users.title")} {data && <span className="muted">({data.total})</span>}</h2>
        <CreateUser onCreated={reload} />
      </div>
      <p className="muted">{t("users.guardNote")}</p>

      {temp && (
        <div className="alert warn" role="alert">
          <strong>{temp.username}</strong>: {t("users.tempPwd")}
          <div className="secret" style={{ margin: "8px 0" }}>{temp.pwd}</div>
          <div className="form-actions">
            <CopyButton text={temp.pwd} />
            <button className="btn sm" onClick={() => setTemp(null)}>{t("common.close")}</button>
          </div>
        </div>
      )}
      {actionError && <div className="alert err" role="alert">{actionError}</div>}

      {loading && <p className="state">{t("users.loading")}</p>}
      {error && <div className="alert err" role="alert">{error}</div>}
      {data && (
        <div className="table-wrap">
          <table className="table-static">
            <thead>
              <tr><th>{t("users.colUser")}</th><th>{t("users.colRole")}</th><th>{t("common.colState")}</th><th>{t("users.colMfa")}</th><th>{t("users.colLast")}</th><th>{t("common.colActions")}</th></tr>
            </thead>
            <tbody>
              {data.items.map((u) => (
                <UserRow key={u.id} u={u} isSelf={u.id === me?.id} onChanged={reload}
                  onTempPassword={(username, pwd) => setTemp({ username, pwd })} onError={setActionError} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
