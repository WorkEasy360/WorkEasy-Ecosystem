import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { LoginScreen } from "./LoginScreen";
import ContinueSignInPage from "@/app/auth/continue/page";
import { PortalsProvider } from "@/lib/portalContext";
import type { PortalOrigins } from "@/lib/portals";

/**
 * The central sign-in and the hand-off to a portal (ADR-043). The mode only
 * says where to go: the server answers whether this account may enter, and
 * the page shows that answer - go, verify a factor, or no access.
 */

let query = "";
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/login",
}));

const ORIGINS: PortalOrigins = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://auth.workeasy360.com",
};
const STATE = "s".repeat(43);

type Reply = { status: number; body?: unknown };
interface Call {
  key: string;
  body: unknown;
}
function mockApi(routes: Record<string, Reply[]>): Call[] {
  const calls: Call[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    calls.push({ key, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    const queue = routes[key];
    if (!queue) throw new Error(`unexpected request ${key}`);
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

const SIGNED_OUT: Reply = { status: 200, body: { authenticated: false } };
const SIGNED_IN_SESSION: Reply = {
  status: 200,
  body: { authenticated: true, user_id: "u1", email: "a@example.com", name: "A", consoles: { staff: false, partner: false } },
};

let assign: ReturnType<typeof vi.fn>;
let replace: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
  assign = vi.fn();
  replace = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign, replace, hash: "", pathname: "/login" });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPortal(ui: React.ReactNode) {
  return render(<PortalsProvider origins={ORIGINS}>{ui}</PortalsProvider>);
}

async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
  expect(violations.map((v: Result) => v.id)).toEqual([]);
}

describe("the central sign-in", () => {
  it("signs in once, then hands the sign-in to the chosen portal", async () => {
    query = `mode=user&next=${encodeURIComponent("/o/acme")}&state=${STATE}`;
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/auth/login": [{ status: 200, body: { user_id: "u1", email: "a@example.com", name: "A" } }],
      "POST /api/v1/auth/handoff": [{ status: 200, body: { redirect_url: `${ORIGINS.app}/auth/continue#code=c0de` } }],
    });
    renderPortal(<LoginScreen />);
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "a@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${ORIGINS.app}/auth/continue#code=c0de`));
    const handoff = calls.find((c) => c.key === "POST /api/v1/auth/handoff");
    // The mode and the page asked for, and the portal's binding value - the
    // server decides everything else.
    expect(handoff?.body).toEqual({ mode: "user", next: "/o/acme", state: STATE });
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("goes straight on when already signed in here", async () => {
    query = `mode=partner&state=${STATE}`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN_SESSION],
      "POST /api/v1/auth/handoff": [{ status: 200, body: { redirect_url: `${ORIGINS.partner}/auth/continue#code=c0de` } }],
    });
    renderPortal(<LoginScreen />);
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${ORIGINS.partner}/auth/continue#code=c0de`));
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("without the portal's binding value, asks that portal to start the sign-in", async () => {
    query = "mode=admin";
    const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_IN_SESSION] });
    renderPortal(<LoginScreen />);
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${ORIGINS.admin}/login?next=${encodeURIComponent("/admin")}`));
    expect(calls.some((c) => c.key === "POST /api/v1/auth/handoff")).toBe(false);
  });

  it("an account the server refuses sees no access - and nothing of the portal", async () => {
    query = `mode=admin&state=${STATE}`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN_SESSION],
      "POST /api/v1/auth/handoff": [{ status: 403, body: { code: "portal_access_denied" } }],
      "POST /api/v1/auth/logout": [{ status: 200, body: { ok: true } }],
    });
    const { container } = renderPortal(<LoginScreen />);
    expect(await screen.findByRole("heading", { name: "You don't have access to the Admin portal." })).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue as User" }));
    expect(assign).toHaveBeenCalledWith(`${ORIGINS.app}/login`);
    await expectNoViolations(container);
  });

  it("an administrator verifies the factor here, then goes on", async () => {
    query = `mode=admin&state=${STATE}`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN_SESSION],
      "POST /api/v1/auth/handoff": [
        { status: 403, body: { code: "mfa_required" } },
        { status: 200, body: { redirect_url: `${ORIGINS.admin}/auth/continue#code=c0de` } },
      ],
      "POST /api/v1/auth/mfa/step-up": [{ status: 200, body: { aal: 2 } }],
    });
    renderPortal(<LoginScreen />);
    fireEvent.change(await screen.findByLabelText(/code from your authenticator/), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: /Verify/ }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${ORIGINS.admin}/auth/continue#code=c0de`));
  });
});

describe("the hand-off landing on a portal", () => {
  it("spends the code from the fragment and opens the page the server names", async () => {
    vi.stubGlobal("location", { ...window.location, assign, replace, hash: "#code=c0de", pathname: "/auth/continue" });
    const replaceState = vi.spyOn(window.history, "replaceState");
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/auth/handoff/redeem": [{ status: 200, body: { next: "/admin/customers" } }],
    });
    render(<ContinueSignInPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/admin/customers"));
    expect(replaceState).toHaveBeenCalledWith(null, "", "/auth/continue");
    expect(calls.find((c) => c.key === "POST /api/v1/auth/handoff/redeem")?.body).toEqual({ code: "c0de" });
  });

  it("a spent or missing code says so and offers to sign in again", async () => {
    vi.stubGlobal("location", { ...window.location, assign, replace, hash: "", pathname: "/auth/continue" });
    mockApi({});
    render(<ContinueSignInPage />);
    expect(await screen.findByRole("heading", { name: "Sign-in couldn't be completed" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/login");
  });

  it("never follows anything but a same-site path", async () => {
    vi.stubGlobal("location", { ...window.location, assign, replace, hash: "#code=c0de", pathname: "/auth/continue" });
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/auth/handoff/redeem": [{ status: 200, body: { next: "//evil.example/x" } }],
    });
    render(<ContinueSignInPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });
});
