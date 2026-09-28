// Small fetch wrapper for the local Flask API (no axios needed).
// - sends the session cookie (same origin)
// - adds the CSRF token to every state-changing request
// - turns error responses into ApiError with the server's message

let csrfToken = null;

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function ensureCsrf() {
  if (!csrfToken) {
    const res = await fetch("/api/auth/csrf", { credentials: "same-origin" });
    csrfToken = (await res.json()).csrfToken;
  }
  return csrfToken;
}

export function setCsrfToken(token) {
  csrfToken = token || null;
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRFToken"] = await ensureCsrf();

  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the CityLand 9 server. Check that the server PC is on and connected to the network.");
  }

  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 403 && data?.error?.message?.includes("CSRF")) csrfToken = null; // refetch next time
    throw new ApiError(res.status, data?.error?.message || `Request failed (${res.status}).`);
  }
  return data;
}
