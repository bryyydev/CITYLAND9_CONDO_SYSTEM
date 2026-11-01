// Small fetch wrapper for the local Flask API.
// - same origin (/api), the session cookie is sent automatically; no tokens in localStorage
// - adds the CSRF token to every state-changing request
// - turns error responses into ApiError carrying the server's message

let csrfToken: string | null = null;

export class ApiError extends Error {
  readonly status: number;
  /** Per-field messages for forms (e.g. {"username": "That username is already taken."}). */
  readonly fields: Record<string, string>;
  /** Everything else the server sent with the error (e.g. needsReview + candidates). */
  readonly details: Record<string, unknown>;
  constructor(status: number, message: string, fields: Record<string, string> = {}, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
    this.details = details;
  }
}

/** The module has no API on the server yet (it still runs on the classic screen). */
export class NotConnectedError extends ApiError {
  constructor(feature: string) {
    super(501, `${feature} is not connected to the server yet.`);
  }
}

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

async function ensureCsrf(): Promise<string> {
  if (!csrfToken) {
    const res = await fetch("/api/auth/csrf", { credentials: "same-origin" });
    csrfToken = ((await res.json()) as { csrfToken: string }).csrfToken;
  }
  return csrfToken;
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** Download a file the server generates (e.g. an SOA PDF) and save it. Refuses anything that is not
 * the expected file type, so a sign-in or error page is never saved as a .pdf; the server's error
 * message is shown instead. The temporary object URL is released afterwards. */
export async function downloadFile(path: string, { expect, fallbackName }: { expect: string; fallbackName: string }): Promise<string> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { credentials: "same-origin", headers: { Accept: `${expect}, application/json` } });
  } catch {
    throw new ApiError(0, "Can't reach the CityLand 9 server. Check that the server PC is on and connected to the network.");
  }
  const type = res.headers.get("Content-Type") ?? "";
  if (!res.ok || !type.startsWith(expect)) {
    const data = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new ApiError(res.status, data?.error?.message || (res.ok ? "The server did not return a PDF." : `Download failed (${res.status}).`));
  }
  const blob = await res.blob();
  if (!blob.size) throw new ApiError(500, "The server returned an empty file.");
  const match = /filename="?([^";]+)"?/i.exec(res.headers.get("Content-Disposition") ?? "");
  const name = match?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
  return name;
}

export async function http<T>(path: string, { method = "GET", body }: { method?: Method; body?: unknown } = {}): Promise<T> {
  const upload = body instanceof FormData; // file uploads: the browser sets the multipart boundary
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined && !upload) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRFToken"] = await ensureCsrf();

  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : upload ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the CityLand 9 server. Check that the server PC is on and connected to the network.");
  }

  if (res.status === 204) return null as T;
  const data = (await res.json().catch(() => null)) as { error?: { message?: string; fields?: Record<string, string> } } | null;
  if (!res.ok) {
    if (res.status === 403 && data?.error?.message?.includes("CSRF")) csrfToken = null; // refetch next time
    throw new ApiError(res.status, data?.error?.message || `Request failed (${res.status}).`, data?.error?.fields ?? {}, (data?.error ?? {}) as Record<string, unknown>);
  }
  return data as T;
}
