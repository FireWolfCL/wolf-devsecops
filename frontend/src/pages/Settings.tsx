// Configuración (admin): fuentes y proyectos (la IA vive en pages/AIConfig.tsx).
// NUNCA se muestra ni se envía el valor de un token: solo el NOMBRE de la variable
// de entorno y si está presente.
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { settingsApi } from "../api/admin";
import type { ProjectCfgOut, ProjectSourcesConfig, SourceOut } from "../api/types";
import { errText, fmtDate } from "../components/util";
import { useAsync } from "../hooks";
import { useT, type TFn } from "../i18n";

// ─── Fuentes ────────────────────────────────────────────────────────────────
function SourceRow({ s, onSaved }: { s: SourceOut; onSaved: () => void }) {
  const t = useT();
  const [url, setUrl] = useState(s.url);
  const [tokenEnv, setTokenEnv] = useState(s.token_env);
  const [enabled, setEnabled] = useState(s.enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const dirty = url !== s.url || tokenEnv !== s.token_env || enabled !== s.enabled;

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await settingsApi.putSource(s.name, { url: url.trim(), token_env: tokenEnv.trim(), enabled });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(errText(err)); // 400: token literal o URL inválida
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={save}>
      <h3>
        <span className="mono">{s.name}</span>{" "}
        <span className={`badge ${s.token_present ? "ok" : "danger"}`}>
          {s.token_present ? t("settings.varPresent") : t("settings.varAbsent")}
        </span>
      </h3>
      <div className="field">
        <label htmlFor={`src-url-${s.name}`}>{t("integ.url")}</label>
        <input id={`src-url-${s.name}`} type="text" required maxLength={400} value={url} onChange={(e) => setUrl(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={`src-env-${s.name}`}>{t("integ.tokenEnv")}</label>
        <input id={`src-env-${s.name}`} type="text" className="mono" required maxLength={120} value={tokenEnv} onChange={(e) => setTokenEnv(e.target.value)} />
        <span className="hint">{t("settings.tokenEnvHint")}</span>
      </div>
      <label className="check">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {t("common.enabledF")}
      </label>
      {!s.token_present && <div className="alert warn">{t("settings.tokenMissing", { name: s.token_env })}</div>}
      {error && <div className="alert err" role="alert">{error}</div>}
      {saved && !dirty && <div className="alert ok" role="status">{t("common.saved")}</div>}
      <div className="form-actions">
        <button className="btn primary" type="submit" disabled={busy || !dirty}>{busy ? t("common.saving") : t("common.save")}</button>
        <span className="muted" style={{ fontSize: 12 }}>{t("settings.updated", { date: fmtDate(s.updated_at) })}</span>
      </div>
    </form>
  );
}

function SourcesSection() {
  const t = useT();
  const { data, loading, error, reload } = useAsync(settingsApi.listSources, []);
  return (
    <section className="section">
      <h2>{t("settings.sources")}</h2>
      {loading && !data && <p className="state">{t("settings.loadingSources")}</p>}
      {error && <div className="alert err" role="alert">{error}</div>}
      {data && data.length === 0 && <p className="state">{t("settings.noSources")}</p>}
      <div className="cards">
        {data?.map((s) => <SourceRow key={s.name} s={s} onSaved={reload} />)}
      </div>
    </section>
  );
}

// ─── Proyectos ──────────────────────────────────────────────────────────────
const SOURCES_EXAMPLE = `{
  "github": { "repo": "owner/repo", "ci_workflow": "ci.yml" },
  "gitlab": { "project": "group/repo", "ref": "main" },
  "defectdojo": { "product": "Product name" }
}`;

function parseSources(text: string, t: TFn): ProjectSourcesConfig {
  const trimmed = text.trim();
  if (trimmed === "") return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(t("settings.sourcesNotJson"));
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(t("settings.sourcesNotObject"));
  }
  for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
    if (v === null || typeof v !== "object" || Array.isArray(v)) {
      throw new Error(t("settings.sourceNotObject", { name: k }));
    }
  }
  return parsed as ProjectSourcesConfig;
}

function ProjectForm({
  initial, orgs, onDone, onCancel,
}: {
  initial: ProjectCfgOut | null; // null = crear
  orgs: string[]; // organizaciones ya existentes (para el datalist de agrupación)
  onDone: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  const editing = initial !== null;
  const [key, setKey] = useState(initial?.key ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [org, setOrg] = useState(initial?.org ?? "");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [sourcesText, setSourcesText] = useState(initial ? JSON.stringify(initial.sources, null, 2) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    let sources: ProjectSourcesConfig;
    try {
      sources = parseSources(sourcesText, t);
    } catch (err) {
      return setError(errText(err));
    }
    const orgVal = org.trim() || null; // vacío = default/actual en el backend
    setBusy(true);
    try {
      if (initial) {
        await settingsApi.updateProject(initial.key, { key: initial.key, name: name.trim(), org: orgVal, sources, enabled });
      } else {
        await settingsApi.createProject({ key: key.trim(), name: name.trim(), org: orgVal, sources, enabled });
      }
      onDone();
    } catch (err) {
      setError(errText(err)); // 409 clave duplicada / 404 / 422 / 402 multi-org Pro
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit} style={{ marginBottom: 16 }}>
      <h3>{initial ? t("settings.editProject", { key: initial.key }) : t("settings.newProject")}</h3>
      <div className="field">
        <label htmlFor="pj-key">{t("settings.key")}</label>
        <input id="pj-key" type="text" required disabled={editing} pattern="[A-Za-z0-9][A-Za-z0-9._\-]{0,99}" value={key} onChange={(e) => setKey(e.target.value)} />
        <span className="hint">{t("settings.keyHint")}</span>
      </div>
      <div className="field">
        <label htmlFor="pj-name">{t("common.name")}</label>
        <input id="pj-name" type="text" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="pj-org">{t("settings.org")}</label>
        <input id="pj-org" type="text" list="pj-org-list" maxLength={100} placeholder="Wolf" value={org} onChange={(e) => setOrg(e.target.value)} />
        <datalist id="pj-org-list">
          {orgs.map((o) => <option key={o} value={o} />)}
        </datalist>
        <span className="hint">{t("settings.orgHint")}</span>
      </div>
      <div className="field">
        <label htmlFor="pj-src">{t("settings.sourcesJson")}</label>
        <textarea id="pj-src" spellCheck={false} placeholder={SOURCES_EXAMPLE} value={sourcesText} onChange={(e) => setSourcesText(e.target.value)} />
        <span className="hint">{t("settings.sourcesHint")}</span>
      </div>
      <label className="check">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {t("common.enabledM")}
      </label>
      {error && <div className="alert err" role="alert">{error}</div>}
      <div className="form-actions">
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Guardando…" : "Guardar"}</button>
        <button className="btn" type="button" onClick={onCancel}>{t("common.cancel")}</button>
      </div>
    </form>
  );
}

function ProjectsSection() {
  const t = useT();
  const { data, loading, error, reload } = useAsync(settingsApi.listProjects, []);
  const [form, setForm] = useState<"none" | "new" | string>("none");
  const [actionError, setActionError] = useState<string | null>(null);

  const remove = async (p: ProjectCfgOut) => {
    if (!window.confirm(t("settings.confirmDelete", { key: p.key }))) return;
    setActionError(null);
    try {
      await settingsApi.deleteProject(p.key);
      reload();
    } catch (err) {
      setActionError(errText(err));
    }
  };

  const editing = data?.find((p) => p.key === form) ?? null;
  const done = () => { setForm("none"); reload(); };
  const orgs = [...new Set((data ?? []).map((p) => p.org).filter(Boolean))].sort();

  return (
    <section className="section">
      <div className="toolbar">
        <h2 style={{ margin: 0 }}>{t("settings.projects")}</h2>
        {form === "none" && <button className="btn primary" onClick={() => setForm("new")}>{t("settings.newProject")}</button>}
      </div>
      {form === "new" && <ProjectForm initial={null} orgs={orgs} onDone={done} onCancel={() => setForm("none")} />}
      {editing && <ProjectForm key={editing.key} initial={editing} orgs={orgs} onDone={done} onCancel={() => setForm("none")} />}
      {actionError && <div className="alert err" role="alert">{actionError}</div>}
      {loading && !data && <p className="state">{t("settings.loadingProjects")}</p>}
      {error && <div className="alert err" role="alert">{error}</div>}
      {data && data.length === 0 && <p className="state">{t("settings.noProjects")}</p>}
      {data && data.length > 0 && (
        <div className="table-wrap">
          <table className="table-static">
            <thead><tr><th>{t("settings.colKey")}</th><th>{t("common.name")}</th><th>{t("settings.colOrg")}</th><th>{t("settings.sources")}</th><th>{t("common.colState")}</th><th>{t("common.colActions")}</th></tr></thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.key}>
                  <td className="mono">{p.key}</td>
                  <td>{p.name}</td>
                  <td>{p.org}</td>
                  <td className="mono">{Object.keys(p.sources).join(", ") || "—"}</td>
                  <td><span className={`badge ${p.enabled ? "ok" : "off"}`}>{p.enabled ? t("settings.projEnabled") : t("settings.projDisabled")}</span></td>
                  <td>
                    <div className="cell-actions">
                      <button className="btn sm" onClick={() => setForm(p.key)}>{t("common.edit")}</button>
                      <button className="btn sm danger" onClick={() => remove(p)}>{t("common.delete")}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ─── Reporte de avance programado (feature Pro) ──────────────────────────────
function ScheduledReportSection() {
  const t = useT();
  const { data, loading, reload } = useAsync(settingsApi.getScheduledReport, []);
  const [enabled, setEnabled] = useState(false);
  const [everyDays, setEveryDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (data) { setEnabled(data.enabled); setEveryDays(data.every_days); }
  }, [data]);

  const save = async () => {
    setBusy(true); setError(null); setSaved(false);
    try {
      await settingsApi.putScheduledReport({ enabled, every_days: everyDays });
      setSaved(true); reload();
    } catch (e) {
      setError(errText(e)); // 402 si la edición no habilita la feature
    } finally { setBusy(false); }
  };

  return (
    <section className="section">
      <h3>{t("sreport.title")}</h3>
      <p className="muted">{t("sreport.intro")}</p>
      <div className="card">
        {!data && loading && <p className="state">{t("common.loading")}</p>}
        {data && !data.available && <div className="alert warn" role="status">{t("sreport.proGate")}</div>}
        {data && (
          <>
            <label className="check">
              <input type="checkbox" checked={enabled}
                onChange={(e) => { setEnabled(e.target.checked); setSaved(false); }} />
              <span>{t("sreport.enabled")}</span>
            </label>
            <div className="field">
              <label htmlFor="sr-days">{t("sreport.everyDays")}</label>
              <input id="sr-days" type="number" min={1} max={90} value={everyDays}
                onChange={(e) => { setEveryDays(Math.max(1, Math.min(90, Number(e.target.value) || 7))); setSaved(false); }} />
            </div>
            <div className="row">
              <span className="muted">{t("sreport.lastSent")}</span>
              <span className="mono">{data.last_sent_at ? fmtDate(data.last_sent_at) : "—"}</span>
            </div>
            {error && <div className="alert err" role="alert">{error}</div>}
            {saved && <div className="alert ok" role="status">{t("sreport.saved")}</div>}
            <div className="form-actions">
              <button type="button" className="btn primary" onClick={save} disabled={busy || !data.available}>
                {busy ? t("common.saving") : t("sreport.save")}
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

export function Settings() {
  const t = useT();
  return (
    <>
      <h2 className="page-title">{t("settings.title")}</h2>
      <p className="muted">{t("settings.intro")}</p>
      <SourcesSection />
      <ProjectsSection />
      <ScheduledReportSection />
      <section className="section">
        <p className="muted">
          {t("settings.aiMoved")} <Link to="/admin/ai">{t("settings.aiGo")}</Link>
        </p>
      </section>
    </>
  );
}
