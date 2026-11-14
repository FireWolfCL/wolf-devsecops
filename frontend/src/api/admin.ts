// Clientes por recurso de la capa Admin. Rutas y cuerpos calcan
// backend/wolf_devsecops/schemas_admin.py y api/routes_*.py.
import { request } from "./http";
import type {
  AISettingsIn,
  AISettingsOut,
  AvailableSource,
  ApplyMigrationsOut,
  CurrentVersionOut,
  LDAPConfigIn,
  LDAPConfigOut,
  LicenseStatus,
  ScheduledReportConfig,
  LoginResponse,
  MFAEnableResponse,
  MFASetupResponse,
  Me,
  PasswordChange,
  ProjectCfgIn,
  ProbeResult,
  ProjectCfgOut,
  ResetPasswordResponse,
  SourceIn,
  SourceOut,
  SourceTokenStatus,
  TokenResponse,
  UpdateStatusOut,
  UserCreate,
  UserListResponse,
  UserOut,
  UserUpdate,
} from "./types";

const enc = encodeURIComponent;

export const authApi = {
  // 401 aquí = credenciales/código inválidos, NO sesión expirada.
  login: (username: string, password: string) =>
    request<LoginResponse>("/api/v1/auth/login", {
      method: "POST",
      form: { username, password },
      anonymous: true,
      keepSessionOn401: true,
    }),
  verifyMfa: (mfa_token: string, code: string) =>
    request<TokenResponse>("/api/v1/auth/mfa/verify", {
      method: "POST",
      json: { mfa_token, code },
      anonymous: true,
      keepSessionOn401: true,
    }),
  me: () => request<Me>("/api/v1/auth/me"),
};

export const profileApi = {
  changePassword: (body: PasswordChange) =>
    request<void>("/api/v1/users/me/change-password", { method: "POST", json: body }),
  mfaSetup: () => request<MFASetupResponse>("/api/v1/users/me/mfa/setup", { method: "POST" }),
  mfaEnable: (code: string) =>
    request<MFAEnableResponse>("/api/v1/users/me/mfa/enable", { method: "POST", json: { code } }),
  // 401 = password incorrecta, no sesión expirada.
  mfaDisable: (password: string) =>
    request<void>("/api/v1/users/me/mfa/disable", {
      method: "POST",
      json: { password },
      keepSessionOn401: true,
    }),
};

export const usersApi = {
  list: () => request<UserListResponse>("/api/v1/users"),
  create: (body: UserCreate) => request<UserOut>("/api/v1/users", { method: "POST", json: body }),
  update: (id: string, body: UserUpdate) =>
    request<UserOut>(`/api/v1/users/${enc(id)}`, { method: "PATCH", json: body }),
  resetPassword: (id: string) =>
    request<ResetPasswordResponse>(`/api/v1/users/${enc(id)}/reset-password`, { method: "POST" }),
  unlock: (id: string) => request<void>(`/api/v1/users/${enc(id)}/unlock`, { method: "POST" }),
};

export const settingsApi = {
  availableSources: () => request<AvailableSource[]>("/api/v1/settings/available-sources"),
  testSource: (name: string) =>
    request<ProbeResult>(`/api/v1/settings/sources/${enc(name)}/test`, { method: "POST" }),
  sources: () => request<SourceOut[]>("/api/v1/settings/sources"),
  listSources: () => request<SourceOut[]>("/api/v1/settings/sources"),
  putSource: (name: string, body: SourceIn) =>
    request<SourceOut>(`/api/v1/settings/sources/${enc(name)}`, { method: "PUT", json: body }),
  // Guarda el VALOR del token cifrado en reposo (write-only). La respuesta nunca trae el valor.
  setSourceToken: (name: string, token: string) =>
    request<SourceTokenStatus>(`/api/v1/settings/sources/${enc(name)}/token`, {
      method: "PUT",
      json: { token },
    }),
  // Borra el token cifrado: la fuente vuelve a resolver desde la variable de entorno.
  clearSourceToken: (name: string) =>
    request<SourceTokenStatus>(`/api/v1/settings/sources/${enc(name)}/token`, { method: "DELETE" }),
  listProjects: () => request<ProjectCfgOut[]>("/api/v1/settings/projects"),
  createProject: (body: ProjectCfgIn) =>
    request<ProjectCfgOut>("/api/v1/settings/projects", { method: "POST", json: body }),
  updateProject: (key: string, body: ProjectCfgIn) =>
    request<ProjectCfgOut>(`/api/v1/settings/projects/${enc(key)}`, { method: "PUT", json: body }),
  deleteProject: (key: string) =>
    request<void>(`/api/v1/settings/projects/${enc(key)}`, { method: "DELETE" }),
  getAI: () => request<AISettingsOut>("/api/v1/settings/ai"),
  putAI: (body: AISettingsIn) =>
    request<AISettingsOut>("/api/v1/settings/ai", { method: "PUT", json: body }),
  // Licencia (E4): la licencia NO es un secreto (JWS verificable), pero la respuesta
  // nunca trae el token, solo el estado + origen. PUT valida firma/producto (422).
  getLicense: () => request<LicenseStatus>("/api/v1/settings/license"),
  putLicense: (token: string) =>
    request<LicenseStatus>("/api/v1/settings/license", { method: "PUT", json: { token } }),
  clearLicense: () => request<LicenseStatus>("/api/v1/settings/license", { method: "DELETE" }),
  // SSO / LDAP (E2, feature Pro). Las escrituras exigen FEATURE_SSO (402 si no). El
  // bind password es write-only (su endpoint aparte); la respuesta nunca trae el valor.
  getLdap: () => request<LDAPConfigOut>("/api/v1/settings/ldap"),
  putLdap: (body: LDAPConfigIn) =>
    request<LDAPConfigOut>("/api/v1/settings/ldap", { method: "PUT", json: body }),
  setLdapBindPassword: (password: string) =>
    request<LDAPConfigOut>("/api/v1/settings/ldap/bind-password", { method: "PUT", json: { password } }),
  clearLdapBindPassword: () =>
    request<LDAPConfigOut>("/api/v1/settings/ldap/bind-password", { method: "DELETE" }),
  // Reporte de avance programado (feature Pro): escribir exige FEATURE_REPORTS_SCHEDULED.
  getScheduledReport: () => request<ScheduledReportConfig>("/api/v1/settings/scheduled-report"),
  putScheduledReport: (body: { enabled: boolean; every_days: number }) =>
    request<ScheduledReportConfig>("/api/v1/settings/scheduled-report", { method: "PUT", json: body }),
};

export const updatesApi = {
  version: () => request<CurrentVersionOut>("/api/v1/updates/version"),
  status: () => request<UpdateStatusOut>("/api/v1/updates/status"),
  migrate: (confirm_backup: boolean) =>
    request<ApplyMigrationsOut>("/api/v1/updates/migrate", { method: "POST", json: { confirm_backup } }),
};
