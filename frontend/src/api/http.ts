// Capa HTTP única. Adjunta Bearer a toda llamada y centraliza 401/403:
//  - 401 -> limpia el token y avisa a la app (AuthContext) para ir a /login.
//  - 403 -> NO desloguea; ApiError con mensaje "sin permiso".
// El servidor es la autoridad; el front solo traduce sus respuestas.
import { tokenStorage } from "../auth/tokenStorage";
import { tNow } from "../i18n";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let unauthorizedHandler: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

interface ReqOpts {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  form?: Record<string, string>;
  /** 401 es un error de negocio (credencial/código/password inválido), no sesión expirada. */
  keepSessionOn401?: boolean;
  /** No adjuntar Bearer (login). */
  anonymous?: boolean;
}

function formatDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // 422 de FastAPI: [{loc, msg, ...}]
    const parts = detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown }).loc)
            ? ((d as { loc: unknown[] }).loc.slice(1).join(".") || "")
            : "";
          return `${loc ? loc + ": " : ""}${String((d as { msg: unknown }).msg)}`;
        }
        return null;
      })
      .filter((s): s is string => s !== null);
    if (parts.length > 0) return parts.join("; ");
  }
  return fallback;
}

export async function request<T>(path: string, opts: ReqOpts = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = opts.anonymous ? null : tokenStorage.get();
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(opts.form).toString();
  }

  let resp: Response;
  try {
    resp = await fetch(path, { method: opts.method ?? "GET", headers, body });
  } catch {
    throw new ApiError(0, tNow("auth.noConnection"));
  }

  if (resp.ok) {
    if (resp.status === 204) return undefined as T;
    return (await resp.json()) as T;
  }

  const payload = (await resp.json().catch(() => null)) as { detail?: unknown } | null;
  const fallback = `HTTP ${resp.status} en ${path}`;

  if (resp.status === 401 && !opts.keepSessionOn401) {
    tokenStorage.clear();
    unauthorizedHandler?.();
    throw new ApiError(401, formatDetail(payload?.detail, tNow("auth.sessionExpired")));
  }
  if (resp.status === 403) {
    throw new ApiError(403, tNow("auth.forbidden"));
  }
  throw new ApiError(resp.status, formatDetail(payload?.detail, fallback));
}
