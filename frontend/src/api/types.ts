// Espejo 1:1 del contrato normalizado del backend (wolf_devsecops/models.py).
// Si el backend cambia el contrato, estos tipos deben cambiar con él.

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type CIStatus = "success" | "failure" | "running" | "unknown";
export type SourceStatus = "ok" | "stale" | "unavailable" | "not_configured";

export interface SeverityCount {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface CISummary {
  status: CIStatus;
  workflow: string;
  branch: string | null;
  run_number: number | null;
  html_url: string | null;
  finished_at: string | null;
}

export interface FindingsSummary {
  counts: SeverityCount;
  total_active: number;
  url: string | null;
}

export interface QualitySummary {
  quality_gate: "OK" | "WARN" | "ERROR" | "NONE";
  bugs: number;
  vulnerabilities: number;
  code_smells: number;
  coverage: number | null;
  url: string | null;
}

export interface ReleaseSummary {
  tag: string | null;
  name: string | null;
  published_at: string | null;
  html_url: string | null;
  sbom_url: string | null;
}

export interface SourceResult<T> {
  status: SourceStatus;
  data: T | null;
  error: string | null;
  fetched_at: string | null;
}

// ─── Postura / % de cumplimiento (docs/POSTURE-RUBRIC.md) ───────────────────
// El % NUNCA se muestra sin su cobertura y su banda.
export type PostureBand =
  | "solido"
  | "aceptable"
  | "en_riesgo"
  | "critico"
  | "evidencia_insuficiente"
  | "sin_datos";

export type CheckOutcome = "pass" | "partial" | "fail" | "na";

export interface CheckResult {
  id: string;
  result: CheckOutcome;
  weight: number;
  earned: number;
  reason_code: string; // ok | source_not_configured | source_unavailable | no_verdict | threshold_not_met | missing_data
}

export interface PostureScore {
  rubric_version: string;
  score: number | null;
  coverage: number;
  band: PostureBand;
  stale: boolean;
  degraded: boolean;
  capped_by: "coverage" | "critical" | null;
  checks: CheckResult[];
}

export interface GlobalPosture {
  /** Organización a la que pertenece este agregado (no se mezcla entre orgs). */
  org: string;
  global_score: number | null;
  coverage: number;
  band: string;
  distribution: Record<string, number>;
  at_risk: string[];
  critical: string[];
  project_count: number;
}

/** GET /api/v1/posture: un agregado por organización; `primary` es la titular. */
export interface PostureOverview {
  orgs: GlobalPosture[];
  primary: string;
}

export interface ProjectSummary {
  key: string;
  org: string;
  name: string;
  ci: SourceResult<CISummary>;
  findings: SourceResult<FindingsSummary>;
  quality: SourceResult<QualitySummary>;
  release: SourceResult<ReleaseSummary>;
  /** CVEs de imagen/filesystem (Harbor + Trivy fusionados). */
  containers: SourceResult<FindingsSummary>;
  posture: PostureScore | null;
  /** Deep-links de salida hacia cada herramienta (URL pública navegable). */
  links: Record<string, string>;
  generated_at: string;
}

export interface ProjectListItem {
  key: string;
  org: string;
  name: string;
  ci_status: CIStatus | null;
  findings: SeverityCount | null;
  container_findings: SeverityCount | null;
  quality_gate: string | null;
  worst_source_status: SourceStatus;
  posture_score: number | null;
  posture_band: string;
  posture_coverage: number;
}

// ─── Dashboard por herramienta (una fuente, todos los proyectos) ────────────
export interface ToolRow {
  key: string;
  name: string;
  status: SourceStatus;
  summary: Record<string, unknown> | null;
  error: string | null;
  link: string | null; // deep-link al proyecto en la herramienta
}

export interface ToolDashboard {
  tool: string;
  rows: ToolRow[];
  totals: Record<string, unknown>;
  configured_count: number;
  tool_url: string | null; // home pública de la herramienta ("Abrir en…")
  generated_at: string;
}

// ─── Reporte documental por proyecto (GET /projects/{key}/report) ───────────
export interface ReportSection {
  source: string; // containers | defectdojo | sonarqube | github
  tool_label: string;
  status: SourceStatus;
  headline: string;
  note: string | null;
  findings: TrivyFinding[];
  link: string | null;
}

export interface ProjectReport {
  key: string;
  name: string;
  org: string;
  generated_at: string;
  posture: PostureScore | null;
  posture_summary: string;
  sections: ReportSection[];
}

export interface SourceDetail {
  project_key: string;
  source: string;
  status: SourceStatus;
  fetched_at: string | null;
  payload: Record<string, unknown>;
  error: string | null;
}

// Un hallazgo accionable normalizado (payload.findings del detalle). Lo emiten
// por igual los conectores Trivy y DefectDojo, para un drill-down uniforme.
export interface TrivyFinding {
  kind: "vuln" | "misconfig";
  id: string | null; // CVE / regla
  pkg: string | null;
  installed: string | null;
  fixed: string | null; // versión que lo corrige (o resolución); null = sin parche
  fix?: string | null; // remediación explícita (p. ej. mitigation de DefectDojo)
  severity: string; // CRITICAL | HIGH | MEDIUM | LOW | UNKNOWN
  title: string;
  target: string | null;
}

// ─── Reviewer IA (F6, ADR-007) ──────────────────────────────────────────────
// Espejo de AIReviewResult / PrioritizedFinding (backend models.py). La salida
// del modelo es DATO, nunca marcado: el frontend la renderiza como texto plano.
export interface PrioritizedFinding {
  title: string;
  severity: Severity;
  rationale: string;
  recommended_action: string;
}

export interface AIReviewResult {
  provider_type: "local" | "hosted";
  model: string;
  egress: boolean; // true = el dato salió a un tercero (hosted)
  generated_at: string;
  summary: string;
  prioritized: PrioritizedFinding[];
}

// ─── Capa Admin (A2..A5) ────────────────────────────────────────────────────
// Espejo 1:1 de backend/wolf_devsecops/schemas_admin.py. Los timestamps llegan
// como string ISO-8601.
export type Role = "admin" | "operator" | "viewer";

export interface LoginResponse {
  access_token: string | null;
  token_type: string;
  mfa_required: boolean;
  mfa_token: string | null;
  must_change_password: boolean;
  must_enroll_mfa: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  must_change_password: boolean;
  must_enroll_mfa: boolean;
}

export interface Me {
  id: string;
  username: string;
  email: string | null;
  full_name: string | null;
  role: Role;
  is_active: boolean;
  mfa_enabled: boolean;
  must_change_password: boolean;
  must_enroll_mfa: boolean;
  last_login_at: string | null;
}

export interface PasswordChange {
  current_password: string;
  new_password: string; // min 8
}

export interface MFASetupResponse {
  secret: string;
  otpauth_uri: string;
  qr_code_svg: string; // SVG generado por el backend (confiable)
}

export interface MFAEnableResponse {
  recovery_codes: string[];
}

export interface UserOut {
  id: string;
  username: string;
  email: string | null;
  full_name: string | null;
  role: Role;
  is_active: boolean;
  mfa_enabled: boolean;
  must_change_password: boolean;
  locked_until: string | null;
  last_login_at: string | null;
  created_at: string | null;
}

export interface UserListResponse {
  items: UserOut[];
  total: number;
}

export interface UserCreate {
  username: string; // 3..100, [A-Za-z0-9._@-]
  password: string; // 8..128
  role: Role;
  email?: string | null;
  full_name?: string | null;
  must_change_password?: boolean;
}

export interface UserUpdate {
  role?: Role;
  is_active?: boolean;
  full_name?: string | null;
}

export interface ResetPasswordResponse {
  temporary_password: string;
}

export interface SourceOut {
  name: string;
  url: string;
  token_env: string;
  enabled: boolean;
  token_present: boolean;
  updated_at: string | null;
}

export interface SourceIn {
  url: string;
  token_env: string;
  enabled: boolean;
}

/** GET /settings/available-sources: catálogo (configurada o no). */
export interface AvailableSource {
  name: string;
  configured: boolean;
  enabled: boolean;
  token_present: boolean;
  supported_versions: string;
}

/** POST /settings/sources/{name}/test (models.ProbeResult). Nunca incluye el token. */
export interface ProbeResult {
  source: string;
  reachable: boolean;
  authenticated: boolean;
  version: string | null;
  compatible: boolean;
  detail: string | null;
}

export type ProjectSourcesConfig = Record<string, Record<string, unknown>>;

export interface ProjectCfgOut {
  key: string;
  name: string;
  enabled: boolean;
  sources: ProjectSourcesConfig;
  created_at: string | null;
}

export interface ProjectCfgIn {
  key?: string | null; // obligatorio en POST
  name: string;
  sources: ProjectSourcesConfig;
  enabled: boolean;
}

export interface AISettingsOut {
  provider: string;
  model: string;
  base_url: string | null;
  token_env: string | null;
  token_present: boolean;
  enabled: boolean;
  env_allows_ai: boolean;
  effective_enabled: boolean;
}

export interface AISettingsIn {
  provider: string;
  model: string;
  base_url: string | null;
  token_env: string | null;
  enabled: boolean;
}

export interface CurrentVersionOut {
  current_version: string;
}

export interface LatestReleaseOut {
  available: boolean;
  reason: string | null; // no_repo | network_error | http_NNN | invalid_response | no_matching_release
  tag: string | null;
  version: string | null;
  name: string | null;
  notes: string | null; // texto NO confiable: render inerte
  url: string | null;
  published_at: string | null;
}

export interface ServerVersionOut {
  current_version: string;
  latest: LatestReleaseOut;
  update_available: boolean | null; // null = no se pudo verificar
}

export interface MigrationStatusOut {
  current: string | null;
  head: string | null;
  pending: number; // -1 = pendiente pero no contable
  up_to_date: boolean;
}

export interface UpdateStatusOut {
  server: ServerVersionOut;
  release_source: { repo: string; configured: boolean };
  migrations: MigrationStatusOut;
  update_command: string; // display-only
  environment: string;
}

export interface ApplyMigrationsOut {
  migrations: MigrationStatusOut;
}

// ─── Inventario de dependencias (GET /api/v1/projects/{key}/dependencies) ───
export interface DependencyItem {
  name: string;
  version: string;
  ecosystem: string;
  target: string;
  vulnerable: boolean;
  cve_ids: string[];
  fixed_version: string | null;
}

export interface DependencyInventory {
  project_key: string;
  generated_at: string;
  source: "trivy";
  /** true = inventario parcial: solo paquetes con CVE (el CI no usó --list-all-pkgs). */
  degraded: boolean;
  packages: DependencyItem[];
}

// ─── Mapa de código (GET /api/v1/projects/{key}/codemap) ─────────────────────
export interface ModuleNode {
  /** Ruta relativa al repo, separada por "/". */
  path: string;
  type: "file" | "dir";
  size: number | null;
}

export interface ModuleTree {
  project_key: string;
  generated_at: string;
  nodes: ModuleNode[];
  truncated: boolean;
}

// ─── Tendencia histórica (GET /api/v1/projects/{key}/history?limit=N) ────────
export interface PostureSnapshot {
  /** ISO 8601. */
  captured_at: string;
  compliance_pct: number;
  band: string;
  crit: number;
  high: number;
  med: number;
  low: number;
  coverage_pct: number | null;
}

export interface PostureHistory {
  project_key: string;
  /** Orden ascendente por captured_at; puede venir vacío. */
  snapshots: PostureSnapshot[];
}

// ─── Feature B: propuesta de fix con código (ADR-018) ───────────────────────
// Espejo de AICodeFixResult. original/proposed/explanation son DATO no
// confiable: se renderizan SOLO como texto plano (jamás HTML).
export type AIProviderKind = "local" | "hosted";

export interface AICodeFixResult {
  provider_type: AIProviderKind;
  model: string;
  egress: boolean;
  original_snippet: string;
  proposed_snippet: string;
  explanation: string;
}

// ─── Feature D: branch securitizado + PR draft (ADR-017) ────────────────────
export interface RemediationPick {
  ref: string;
  title?: string;
  severity?: string;
  recommended_action?: string;
}

export interface RemediationBranchResult {
  branch: string;
  pr_url: string | null;
  applied: boolean;
  already_existed: boolean;
}
