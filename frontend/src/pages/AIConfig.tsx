// Configuración de IA (admin). ADR-002: el secreto NUNCA pasa por el navegador.
// Aquí solo se elige proveedor/modelo/base_url y el NOMBRE de la variable de entorno
// que contiene la API key; el valor vive en deploy/.env y se resuelve en el servidor
// (`token_present` es un booleano). Prohibido añadir un input para el valor del secreto.
// El servidor es la autoridad (admin + MFA, techo WOLF_AI_ENABLED, validación de URL/egreso);
// esta pantalla solo refleja sus respuestas.
import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "../api/http";
import { settingsApi } from "../api/admin";
import type { AISettingsOut } from "../api/types";
import { errText } from "../components/util";
import { useAsync } from "../hooks";
import { useT, type TFn } from "../i18n";

type PresetId = "local" | "claude" | "openai" | "custom";

interface Preset {
  id: PresetId;
  nameKey: string;
  descKey: string;
  provider: "local" | "hosted";
  baseUrl: string;
  tokenEnv: string;
  /** Modelo sugerido; editable. Vacío = no pisar el modelo actual. */
  model: string;
}

const PRESETS: Preset[] = [
  { id: "local", nameKey: "aicfg.preset.local", descKey: "aicfg.preset.localDesc", provider: "local", baseUrl: "http://localhost:11434/v1", tokenEnv: "", model: "" },
  { id: "claude", nameKey: "aicfg.preset.claude", descKey: "aicfg.preset.claudeDesc", provider: "hosted", baseUrl: "https://api.anthropic.com/v1/", tokenEnv: "ANTHROPIC_API_KEY", model: "claude-sonnet-4-5" },
  { id: "openai", nameKey: "aicfg.preset.openai", descKey: "aicfg.preset.openaiDesc", provider: "hosted", baseUrl: "https://api.openai.com/v1", tokenEnv: "OPENAI_API_KEY", model: "gpt-4o" },
];

const CUSTOM = { id: "custom" as const, nameKey: "aicfg.preset.custom", descKey: "aicfg.preset.customDesc" };

const norm = (u: string) => u.trim().replace(/\/+$/, "").toLowerCase();

/** El preset se DERIVA de los valores: si el admin edita a mano, pasa a «Personalizado». */
function detectPreset(provider: string, baseUrl: string): PresetId {
  const hit = PRESETS.find((p) => p.provider === provider.trim() && norm(p.baseUrl) === norm(baseUrl));
  return hit ? hit.id : "custom";
}

/** Traduce el error del servidor a un mensaje accionable, conservando el detalle. */
function saveErrorText(err: unknown, t: TFn): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return t("aicfg.err403");
    if (err.status === 409) return `${t("aicfg.err409")} ${t("aicfg.errServer", { msg: err.message })}`;
    return t("aicfg.errServer", { msg: err.message }); // 400/422: validación (URL, egreso, token_env…)
  }
  return errText(err);
}

function AIConfigForm({ initial }: { initial: AISettingsOut }) {
  const t = useT();
  const [server, setServer] = useState<AISettingsOut>(initial);
  const [provider, setProvider] = useState(initial.provider);
  const [model, setModel] = useState(initial.model);
  const [baseUrl, setBaseUrl] = useState(initial.base_url ?? "");
  const [tokenEnv, setTokenEnv] = useState(initial.token_env ?? "");
  const [enabled, setEnabled] = useState(initial.enabled);
  const [busy, setBusy] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const preset = detectPreset(provider, baseUrl);
  const remoteDraft = provider.trim() === "hosted";
  const remoteServer = server.provider === "hosted";
  const savedTokenEnv = server.token_env ?? "";
  const dirty =
    provider.trim() !== server.provider ||
    model.trim() !== server.model ||
    baseUrl.trim() !== (server.base_url ?? "") ||
    tokenEnv.trim() !== savedTokenEnv ||
    (server.env_allows_ai ? enabled : false) !== server.enabled;

  const applyPreset = (p: Preset) => {
    setProvider(p.provider);
    setBaseUrl(p.baseUrl);
    setTokenEnv(p.tokenEnv);
    if (p.model) setModel(p.model);
    setSaved(null);
  };

  const fillFrom = (out: AISettingsOut) => {
    setServer(out);
    setProvider(out.provider);
    setModel(out.model);
    setBaseUrl(out.base_url ?? "");
    setTokenEnv(out.token_env ?? "");
    setEnabled(out.enabled);
  };

  const recheck = async () => {
    setRechecking(true);
    setError(null);
    try {
      setServer(await settingsApi.getAI()); // solo refresca estado; conserva el borrador
    } catch (err) {
      setError(saveErrorText(err, t));
    } finally {
      setRechecking(false);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSaved(null);
    const wantEnabled = server.env_allows_ai ? enabled : false; // el techo del entorno manda
    if (!provider.trim()) return setError(t("aicfg.errProvider"));
    if (wantEnabled && !model.trim()) return setError(t("aicfg.errModel"));
    // Acción crítica: activar un proveedor remoto hace salir datos a un tercero.
    const becomesRemote = wantEnabled && remoteDraft && (!server.enabled || !remoteServer || norm(baseUrl) !== norm(server.base_url ?? ""));
    if (becomesRemote && !window.confirm(t("aicfg.confirmHosted", { url: baseUrl.trim() || t("aicfg.defaultUrl") }))) return;
    setBusy(true);
    try {
      const out = await settingsApi.putAI({
        provider: provider.trim(),
        model: model.trim(),
        base_url: baseUrl.trim() || null,
        token_env: tokenEnv.trim() || null, // SOLO el nombre de la variable
        enabled: wantEnabled,
      });
      fillFrom(out);
      setSaved(t("aicfg.saved", { state: out.effective_enabled ? t("aicfg.active") : t("aicfg.inactive") }));
    } catch (err) {
      setError(saveErrorText(err, t)); // 403 no-admin / 409 techo del entorno / 400 validación
    } finally {
      setBusy(false);
    }
  };

  const tokenBadge = !savedTokenEnv
    ? { cls: "off", text: t("aicfg.tokenNone") }
    : server.token_present
      ? { cls: "ok", text: t("aicfg.tokenPresent") }
      : { cls: "warn", text: t("aicfg.tokenAbsent") };

  return (
    <>
      {/* ── Estado real, tal como lo reporta el servidor ── */}
      <section className="card" aria-labelledby="aicfg-status" style={{ marginBottom: 16 }}>
        <h3 id="aicfg-status">
          {t("aicfg.statusTitle")}{" "}
          <span className={`badge ${server.effective_enabled ? "ok" : "off"}`}>
            {server.effective_enabled ? t("aicfg.active") : t("aicfg.inactive")}
          </span>{" "}
          <span className={`badge ${remoteServer ? "danger" : "ok"}`}>
            {remoteServer ? t("aicfg.egressYes") : t("aicfg.egressNo")}
          </span>
        </h3>
        <div className="row">
          <span className="muted">{t("aicfg.envCeiling")}</span>
          <span className={`badge ${server.env_allows_ai ? "ok" : "danger"}`}>
            {server.env_allows_ai ? t("aicfg.envAllows") : t("aicfg.envForbids")}
          </span>
        </div>
        <div className="row">
          <span className="muted">{t("aicfg.storedEnabled")}</span>
          <span className="mono">{server.enabled ? t("common.yes") : t("common.no")}</span>
        </div>
        <div className="row">
          <span className="muted">{t("aicfg.effective")}</span>
          <span className="mono">{server.effective_enabled ? t("common.yes") : t("common.no")}</span>
        </div>
        <div className="row">
          <span className="muted">{t("aicfg.tokenEnv")}</span>
          <span>
            {savedTokenEnv && <span className="mono">{savedTokenEnv}</span>} <span className={`badge ${tokenBadge.cls}`}>{tokenBadge.text}</span>
          </span>
        </div>
        <div className="form-actions">
          <button type="button" className="btn sm" onClick={recheck} disabled={rechecking || busy}>
            {rechecking ? t("updates.checking") : t("updates.recheck")}
          </button>
        </div>
        {!server.env_allows_ai && <div className="alert warn" role="alert">{t("aicfg.envForbidsMsg")}</div>}
      </section>

      <form className="card" onSubmit={save}>
        {/* ── Presets ── */}
        <fieldset className="preset-set">
          <legend>{t("aicfg.presetTitle")}</legend>
          <div className="preset-grid" role="radiogroup" aria-label={t("aicfg.presetTitle")}>
            {[...PRESETS, CUSTOM].map((p) => {
              const selected = preset === p.id;
              const full = PRESETS.find((x) => x.id === p.id);
              return (
                <button
                  key={p.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  className={`preset ${selected ? "active" : ""}`}
                  onClick={() => full && applyPreset(full)}
                  disabled={busy}
                >
                  <strong>{t(p.nameKey)}</strong>
                  <span className="muted">{t(p.descKey)}</span>
                </button>
              );
            })}
          </div>
          <span className="hint">{t("aicfg.presetHint")}</span>
        </fieldset>

        {/* ── Campos editables (también a mano) ── */}
        <div className="field">
          <label htmlFor="aicfg-provider">{t("aicfg.provider")}</label>
          <select id="aicfg-provider" value={provider} onChange={(e) => { setProvider(e.target.value); setSaved(null); }}>
            <option value="local">{t("aicfg.providerLocal")}</option>
            <option value="hosted">{t("aicfg.providerHosted")}</option>
            {provider !== "local" && provider !== "hosted" && <option value={provider}>{provider}</option>}
          </select>
          <span className="hint">{t("aicfg.providerHint")}</span>
        </div>
        <div className="field">
          <label htmlFor="aicfg-model">{t("aicfg.model")}</label>
          <input id="aicfg-model" type="text" maxLength={120} value={model} onChange={(e) => { setModel(e.target.value); setSaved(null); }} />
          <span className="hint">{t("aicfg.modelHint")}</span>
        </div>
        <div className="field">
          <label htmlFor="aicfg-url">{t("aicfg.baseUrl")}</label>
          <input id="aicfg-url" type="text" className="mono" maxLength={400} value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); setSaved(null); }} />
          <span className="hint">{t("aicfg.baseUrlHint")}</span>
        </div>
        <div className="field">
          <label htmlFor="aicfg-env">{t("aicfg.tokenEnv")}</label>
          {/* ADR-002: solo el NOMBRE de la variable. NO existe (ni debe existir) un input para el valor. */}
          <input id="aicfg-env" type="text" className="mono" maxLength={120} autoComplete="off" spellCheck={false} value={tokenEnv} onChange={(e) => { setTokenEnv(e.target.value); setSaved(null); }} />
          <span className="hint">{t("aicfg.tokenEnvHint")}</span>
          {tokenEnv.trim() !== savedTokenEnv && savedTokenEnv && (
            <span className="hint">{t("aicfg.tokenStatusStale", { name: savedTokenEnv })}</span>
          )}
        </div>
        <div className="alert info" role="note">{t("aicfg.secretNotice")}</div>
        {remoteDraft && !tokenEnv.trim() && <div className="alert warn" role="status">{t("aicfg.hostedNoToken")}</div>}

        {/* Advertencia de egreso del borrador, antes de guardar. */}
        <div className={`alert ${remoteDraft ? "warn" : "ok"}`} role="status">
          {remoteDraft ? t("aicfg.egressYes") : t("aicfg.egressNo")}
        </div>

        <label className="check">
          <input type="checkbox" checked={server.env_allows_ai && enabled} disabled={!server.env_allows_ai || busy} onChange={(e) => { setEnabled(e.target.checked); setSaved(null); }} />
          {t("aicfg.enable")}
        </label>

        {error && <div className="alert err" role="alert">{error}</div>}
        {saved && !dirty && <div className="alert ok" role="status">{saved}</div>}
        {dirty && !error && <p className="muted" style={{ fontSize: 12 }}>{t("aicfg.unsaved")}</p>}

        <div className="form-actions">
          <button className="btn primary" type="submit" disabled={busy || !dirty}>
            {busy ? t("common.saving") : t("common.save")}
          </button>
        </div>
      </form>
    </>
  );
}

export function AIConfig() {
  const t = useT();
  const { data, loading, error, reload } = useAsync(settingsApi.getAI, []);
  return (
    <>
      <h2 className="page-title">{t("aicfg.title")}</h2>
      <p className="muted">{t("aicfg.intro")}</p>
      {loading && !data && <p className="state">{t("aicfg.loading")}</p>}
      {error && (
        <div className="alert err" role="alert">
          {error} <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button>
        </div>
      )}
      {data && <AIConfigForm initial={data} />}
    </>
  );
}
