// Cliente del dashboard. El navegador solo habla con /api (nunca ve tokens de
// fuentes). Todas las llamadas pasan por api/http.ts (Bearer + manejo 401/403).
import { tokenStorage } from "../auth/tokenStorage";
import { ApiError, request } from "./http";
import type {
  AICodeFixResult,
  AIProviderKind,
  AIReviewResult,
  DependencyInventory,
  EventList,
  EventReport,
  UnreadCount,
  ModuleTree,
  PostureHistory,
  PostureSparklines,
  PostureOverview,
  ProjectListItem,
  ProjectReport,
  ProjectSummary,
  RemediationBranchResult,
  RemediationPick,
  SourceDetail,
  ToolDashboard,
} from "./types";

export function fetchProjects(): Promise<ProjectListItem[]> {
  return request<ProjectListItem[]>("/api/v1/projects");
}

export function fetchProject(key: string): Promise<ProjectSummary> {
  return request<ProjectSummary>(`/api/v1/projects/${encodeURIComponent(key)}`);
}

export function fetchPosture(): Promise<PostureOverview> {
  return request<PostureOverview>("/api/v1/posture");
}

export type ToolName = "containers" | "sonarqube" | "defectdojo" | "github";

export function fetchTool(tool: ToolName): Promise<ToolDashboard> {
  return request<ToolDashboard>(`/api/v1/tools/${tool}`);
}

export function fetchReport(key: string): Promise<ProjectReport> {
  return request<ProjectReport>(`/api/v1/projects/${encodeURIComponent(key)}/report`);
}

export function fetchSourceDetail(key: string, source: string): Promise<SourceDetail> {
  return request<SourceDetail>(
    `/api/v1/projects/${encodeURIComponent(key)}/sources/${source}`,
  );
}

export function fetchDependencies(key: string): Promise<DependencyInventory> {
  return request<DependencyInventory>(`/api/v1/projects/${encodeURIComponent(key)}/dependencies`);
}

// 404 = el proyecto no tiene GitHub configurado (no es un error roto): la vista
// lo distingue por ApiError.status.
export function fetchCodemap(key: string): Promise<ModuleTree> {
  return request<ModuleTree>(`/api/v1/projects/${encodeURIComponent(key)}/codemap`);
}

// 404 = el proyecto no existe; `snapshots` puede venir vacío. `days` acota la
// ventana (7/30/90); la edición Community la recorta a 30d en el backend.
export function fetchHistory(key: string, days?: number): Promise<PostureHistory> {
  const qs = days != null ? `?days=${encodeURIComponent(String(days))}` : "";
  return request<PostureHistory>(`/api/v1/projects/${encodeURIComponent(key)}/history${qs}`);
}

// Series de % por proyecto para los sparklines del dashboard (una sola llamada).
export function fetchSpark(days?: number): Promise<PostureSparklines> {
  const qs = days != null ? `?days=${encodeURIComponent(String(days))}` : "";
  return request<PostureSparklines>(`/api/v1/projects/history/spark${qs}`);
}

export interface Meta {
  project_count: number;
  sources_configured: string[];
  ai_enabled: boolean;
  config_error?: string;
  /** Edición efectiva del despliegue + features premium activas. El backend es
   *  la autoridad del gate; esto es para mostrar/ocultar y poner "Upgrade". */
  edition?: string; // community | pro | enterprise
  features?: string[];
  limits?: { max_projects: number | null; max_orgs: number | null };
  license?: {
    present: boolean;
    valid: boolean;
    edition?: string;
    customer?: string;
    expires_at?: string | null;
    days_left?: number | null;
    error?: string | null; // invalid_signature | expired | wrong_product
  };
}

export function fetchMeta(): Promise<Meta> {
  return request<Meta>("/api/meta");
}

// Error específico del gate del reviewer IA: el backend responde 503 mientras
// la IA está deshabilitada. Se distingue de un error de red para mostrar el
// estado "deshabilitado" en vez de un error genérico.
export class AIDisabledError extends Error {}

export async function reviewProjectAI(key: string): Promise<AIReviewResult> {
  try {
    return await request<AIReviewResult>(`/api/v1/projects/${encodeURIComponent(key)}/ai/review`, {
      method: "POST",
    });
  } catch (e) {
    if (e instanceof ApiError && e.status === 503) {
      throw new AIDisabledError(e.message || "Reviewer IA deshabilitado.");
    }
    throw e;
  }
}

// Feature B. 403/503/404/422 llegan como ApiError (status); la vista los trata
// como estados esperados. Ni base_url ni token viajan: el backend los resuelve.
// Export del audit trail (feature Pro). Descarga autenticada vía blob (el endpoint
// responde con Content-Disposition). 402 si la edición no incluye audit.export.
export async function downloadAudit(format: "json" | "csv"): Promise<void> {
  const token = tokenStorage.get();
  const res = await fetch(`/api/v1/audit/export?format=${format}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `audit.${format}`;
  a.click();
  URL.revokeObjectURL(url);
}

export function proposeCodeFix(
  key: string,
  body: { finding_ref: string; provider: AIProviderKind; model: string; source?: "defectdojo" | "sonarqube" },
): Promise<AICodeFixResult> {
  return request<AICodeFixResult>(`/api/v1/projects/${encodeURIComponent(key)}/ai/code-fix`, {
    method: "POST",
    json: body,
  });
}

// Feature D. SOLO se llama tras la confirmación explícita del operador (modal).
export function createRemediationBranch(
  key: string,
  picked: RemediationPick[],
): Promise<RemediationBranchResult> {
  return request<RemediationBranchResult>(
    `/api/v1/projects/${encodeURIComponent(key)}/remediation/branch`,
    { method: "POST", json: { picked_findings: picked, confirm: true } },
  );
}

// ─── Notificaciones (campana + reporte) ─────────────────────────────────────
export function fetchEvents(limit = 50): Promise<EventList> {
  return request<EventList>(`/api/v1/events?limit=${limit}`);
}
export function fetchUnreadCount(): Promise<UnreadCount> {
  return request<UnreadCount>("/api/v1/events/unread_count");
}
export function markEventsRead(): Promise<void> {
  return request<void>("/api/v1/events/read", { method: "POST" });
}
export function fetchEventReport(days = 30): Promise<EventReport> {
  return request<EventReport>(`/api/v1/events/report?days=${days}`);
}
