// Fetch wrapper: bearer token from localStorage, JSON in/out, typed errors.
// Reads go straight to the network (the service worker caches them);
// writes go through the outbox (see outbox.ts) so logging never blocks on the network.

const TOKEN_KEY = "health.token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token.trim());
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the token lives for this page only */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

export class AuthError extends ApiError {}
export class OfflineError extends Error {}

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export async function request<T>(method: Method, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const h: Record<string, string> = { Accept: "application/json", ...headers };
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  if (body !== undefined) h["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new OfflineError((e as Error).message || "network unavailable");
  }
  if (res.status === 401) throw new AuthError(401, "unauthorized");
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail ?? data ?? res.statusText;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export const get = <T>(path: string) => request<T>("GET", path);

export function describeError(e: unknown): string {
  if (e instanceof AuthError) return "Not signed in — enter the API token in Settings.";
  if (e instanceof OfflineError) return "Offline — queued, will send when back online.";
  if (e instanceof ApiError) {
    const d = e.detail as { reason?: string; rails?: string[] } | string | { msg?: string }[] | null;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => x.msg ?? JSON.stringify(x)).join("; ");
    if (d && typeof d === "object" && "reason" in d) return `${d.reason}`;
    return `HTTP ${e.status}`;
  }
  return (e as Error)?.message ?? String(e);
}

export async function downloadExport(): Promise<void> {
  const token = getToken();
  const res = await fetch("/api/v1/export", { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `health-export-${new Date().toISOString().slice(0, 10)}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
