import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { LoginScreen } from "./LoginScreen";
import ContinueSignInPage from "@/app/auth/continue/page";
import { PortalsProvider } from "@/lib/portalContext";
import type { PortalOrigins } from "@/lib/portals";

/**
 * Each portal's own sign-in page (ADR-059), and the hand-off landing kept for
 * a sign-in already in flight (ADR-043). The host only says which portal to
 * open: the server answers what this account may do there.
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

describe("each portal's own sign-in page (ADR-059)", () => {
  const LOGIN_OK: Reply = { status: 200, body: { user_id: "u1", email: "a@example.com", name: "A" } };

  async function signIn() {
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "a@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  }

  it("the Admin portal signs in on its own host and opens the console page asked for", async () => {
    query = `next=${encodeURIComponent("/admin/partners")}`;
    const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], "POST /api/v1/auth/login": [LOGIN_OK] });
    renderPortal(<LoginScreen portal="admin" />);
    expect(await screen.findByRole("heading", { name: "Sign in as Administrator" })).toBeInTheDocument();
    await signIn();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin/partners"));
    // No central sign-in, no hand-off: the session is this host's.
    expect(calls.some((c) => c.key.includes("handoff"))).toBe(false);
    expect(assign).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: "Create an account" })).not.toBeInTheDocument();
  });

  it("the Partner portal signs in on its own host and opens the partner portal", async () => {
    const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], "POST /api/v1/auth/login": [LOGIN_OK] });
    renderPortal(<LoginScreen portal="partner" />);
    expect(await screen.findByRole("heading", { name: "Sign in as Partner" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Create an account" })).not.toBeInTheDocument();
    await signIn();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/partner"));
    expect(calls.some((c) => c.key.includes("handoff"))).toBe(false);
  });

  it("the customer portal signs in on its own host and offers sign-up", async () => {
    query = `next=${encodeURIComponent("/o/acme")}`;
    const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], "POST /api/v1/auth/login": [LOGIN_OK] });
    const { container } = renderPortal(<LoginScreen portal="app" />);
    expect(await screen.findByRole("heading", { name: "Sign in as User" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create an account" })).toHaveAttribute("href", "/signup");
    await expectNoViolations(container);
    await signIn();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme"));
    expect(calls.some((c) => c.key.includes("handoff"))).toBe(false);
  });

  it("the host names the mode, whatever the address says", async () => {
    query = "mode=admin";
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    renderPortal(<LoginScreen portal="partner" />);
    expect(await screen.findByRole("heading", { name: "Sign in as Partner" })).toBeInTheDocument();
  });

  it("already signed in on this portal goes straight on", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN_SESSION] });
    renderPortal(<LoginScreen portal="admin" />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin"));
    expect(assign).not.toHaveBeenCalled();
  });

  it("a page another portal names is never a return target", async () => {
    query = `next=${encodeURIComponent("/admin")}`;
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], "POST /api/v1/auth/login": [LOGIN_OK] });
    renderPortal(<LoginScreen portal="partner" />);
    await signIn();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/partner"));
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
