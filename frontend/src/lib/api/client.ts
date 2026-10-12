/**
 * The one fetch client (Task 56: no duplicate fetch clients - axios/ky add
 * nothing `fetch` doesn't already do at this app's scale).
 *
 * Every call is same-origin, through next.config.ts's rewrite to the Django
 * API - never a second origin, never wildcard CORS (Task 36). Every call is
 * `cache: "no-store"`: launcher/session/access state is security-sensitive
 * and must never be served stale for speed (Task 30) - only the catalog
 * metadata a decision is *built from* could ever be cached, and this app
 * does not attempt that optimisation without measuring a real need for it
 * first (Task 29: "do not optimize by guess").
 *
 * CSRF: Django's double-submit cookie. Read here, sent as a header on every
 * unsafe method - see docs/phase-4/07-security.md for why the cookie is
 * guaranteed to exist by the time this is ever called from a write.
 */

const CSRF_COOKIE = "w360_csrf";
/** Production's name (backend production.py, ADR-043): `__Host-` makes the
 * browser refuse a same-named cookie a sibling host plants for the parent
 * domain, which would otherwise hand an attacker a known token. */
const HOST_CSRF_COOKIE = "__Host-w360_csrf";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    const code =
      typeof body === "object" && body !== null && "code" in body
        ? String((body as { code: unknown }).code)
        : "";
    super(code || `Request failed with status ${status}`);
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

/** The double-submit cookie's value, for the rare caller that cannot go
 * through {@link apiFetch} (which already attaches it as `X-CSRFToken` on
 * every unsafe method). Django accepts the same token as a
 * `csrfmiddlewaretoken` form field on a plain POST. */
export function getCsrfToken(): string | undefined {
  return readCookie(HOST_CSRF_COOKIE) ?? readCookie(CSRF_COOKIE);
}

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name.replace(/[.$?*|{}()[\]\\/+^]/g, "\\$&")}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : undefined;
}

export interface ApiOptions extends Omit<RequestInit, "cache"> {
  /** Query parameters, appended and encoded. */
  query?: Record<string, string | number | undefined>;
}

function buildUrl(path: string, query?: ApiOptions["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

/**
 * Calls the platform API. Throws {@link ApiError} on a non-2xx response so
 * callers write one `try/catch` rather than checking `res.ok` everywhere.
 */
export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { query, ...init } = options;
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!SAFE_METHODS.has(method)) {
    const token = readCookie(HOST_CSRF_COOKIE) ?? readCookie(CSRF_COOKIE);
    if (token) headers.set("X-CSRFToken", token);
  }

  const response = await fetch(buildUrl(path, query), {
    ...init,
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json") ? await response.json() : undefined;

  if (!response.ok) {
    throw new ApiError(response.status, body);
  }
  return body as T;
}
