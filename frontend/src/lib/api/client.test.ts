import { describe, it, expect, vi, afterEach } from "vitest";
import { apiFetch, ApiError, getCsrfToken } from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("apiFetch", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    document.cookie = "w360_csrf=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
    vi.restoreAllMocks();
  });

  it("returns parsed JSON on a 2xx response", async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    const result = await apiFetch<{ ok: boolean }>("/api/v1/whatever");
    expect(result).toEqual({ ok: true });
  });

  it("throws ApiError carrying the status and code on a non-2xx response", async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse(401, { code: "invalid_credentials" }));
    await expect(apiFetch("/api/v1/auth/login")).rejects.toMatchObject(
      new ApiError(401, { code: "invalid_credentials" }),
    );
  });

  it("never throws for a 2xx response even with an unrecognised shape", async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse(200, []));
    await expect(apiFetch("/api/v1/whatever")).resolves.toEqual([]);
  });

  it("attaches the CSRF header on an unsafe method, never on GET", async () => {
    document.cookie = "w360_csrf=the-token; path=/;";
    // A fresh Response per call - a mocked-once Response object can only
    // have its body read once, exactly like a real network response.
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    global.fetch = fetchMock;

    await apiFetch("/api/v1/auth/login", { method: "POST", body: "{}" });
    const [, postInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((postInit.headers as Headers).get("X-CSRFToken")).toBe("the-token");

    await apiFetch("/api/v1/auth/session");
    const [, getInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect((getInit.headers as Headers).get("X-CSRFToken")).toBeNull();
  });

  it("always sends cache: no-store - security-sensitive state is never cached (Task 30)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
    global.fetch = fetchMock;
    await apiFetch("/api/v1/orgs/acme/apps");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.cache).toBe("no-store");
  });
});

describe("getCsrfToken", () => {
  afterEach(() => {
    document.cookie = "w360_csrf=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
  });

  it("reads the cookie Django's double-submit CSRF protection expects", () => {
    document.cookie = "w360_csrf=abc123; path=/;";
    expect(getCsrfToken()).toBe("abc123");
  });

  it("returns undefined when no cookie is set yet", () => {
    expect(getCsrfToken()).toBeUndefined();
  });
});
