import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { LoginScreen } from "./LoginScreen";
import { adminSignInUrl, destinationAfterSignIn, parseMode, safeAdminNext } from "./signInMode";
import DashboardEntryPage from "@/app/dashboard/page";

/**
 * "Administrator" or "User" at sign-in. The mode picks a destination after the
 * one shared sign-in; it is never sent to the server, never stored in the
 * browser, and never decides access (the Admin Console asks the server - see
 * the backend's tests/partner_center/test_admin_sign_in.py).
 */

let query = "";
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/login",
}));

const RULESET = { runOnly: { type: "tag" as const, values: ["wcag2a", "wcag2aa"] } };
async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, RULESET);
  const detail = violations.map((v: Result) => `${v.id}: ${v.help}\n    ${v.nodes[0]?.html ?? ""}`).join("\n  ");
  expect(violations, detail).toEqual([]);
}

type Reply = { status: number; body?: unknown };
interface Call {
  key: string;
  url: string;
  body: unknown;
}
function mockApi(routes: Record<string, Reply[]>): Call[] {
  const calls: Call[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    calls.push({ key, url, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
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
const SIGNED_IN = { status: 200, body: { user_id: "u1", email: "a@example.com", name: "A" } };

let stores: { local: ReturnType<typeof vi.spyOn>; session: ReturnType<typeof vi.spyOn> };

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
  nav.router.replace = vi.fn();
  stores = {
    local: vi.spyOn(Storage.prototype, "setItem"),
    session: vi.spyOn(window.sessionStorage, "setItem"),
  };
});

afterEach(() => {
  // Neither mode, nor anything else from sign-in, is kept in browser storage.
  expect(stores.local).not.toHaveBeenCalled();
  expect(stores.session).not.toHaveBeenCalled();
});

async function signIn(email = "a@example.com", password = "correct horse battery staple") {
  fireEvent.change(await screen.findByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

// --- the helpers ---------------------------------------------------------------------------

describe("the mode and the return path", () => {
  it("knows exactly three modes", () => {
    expect(parseMode("admin")).toBe("admin");
    expect(parseMode("partner")).toBe("partner");
    expect(parseMode("user")).toBe("user");
    for (const other of [null, "", "Admin", "superuser", "staff", "administrator"]) expect(parseMode(other)).toBeNull();
  });

  it("accepts only a console page as the place to return to", () => {
    expect(safeAdminNext("/admin")).toBe("/admin");
    expect(safeAdminNext("/admin/partners/resellers?status=active")).toBe("/admin/partners/resellers?status=active");
    for (const hostile of [
      "https://evil.example/admin",
      "//evil.example/admin",
      "/administrator",
      "/admin/../o/acme",
      "/admin/%2e%2e/o/acme",
      "/admin\\evil",
      "/o/acme",
      "javascript:alert(1)",
      `/admin/${"x".repeat(600)}`,
      `/admin/${String.fromCharCode(10)}x`,
    ]) {
      const result = safeAdminNext(hostile);
      expect(result === null || /^\/admin(\/|$|\?)/.test(result), hostile).toBe(true);
      expect(result ?? "", hostile).not.toMatch(/evil|\/o\/acme/);
    }
  });

  it("sends each mode to its own place", () => {
    expect(destinationAfterSignIn("admin", null)).toBe("/admin");
    expect(destinationAfterSignIn("admin", "/admin/partners")).toBe("/admin/partners");
    expect(destinationAfterSignIn("admin", "https://evil.example")).toBe("/admin");
    expect(destinationAfterSignIn("user", "/admin/partners")).toBe("/dashboard?mode=user");
    expect(destinationAfterSignIn(null, null)).toBe("/dashboard");
    expect(destinationAfterSignIn("partner", "/admin")).toBe("/partner");
    expect(adminSignInUrl("/admin")).toBe("/login?mode=admin");
    expect(adminSignInUrl("/admin/partners/all")).toBe(`/login?mode=admin&next=${encodeURIComponent("/admin/partners/all")}`);
  });
});

// --- the page --------------------------------------------------------------------------------

describe("the sign-in page", () => {
  it("opens the separate Partner Panel from the Partner choice when one is configured", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    render(<LoginScreen partnerPanelUrl="http://localhost:5180/login" />);
    const section = await screen.findByRole("region", { name: "How would you like to sign in?" });
    const choices = within(section).getAllByRole("link").slice(0, 3);
    expect(choices.map((a) => [a.getAttribute("aria-label"), a.getAttribute("href")])).toEqual([
      ["Administrator", "/login?mode=admin"],
      ["Partner", "http://localhost:5180/login"],
      ["User", "/login?mode=user"],
    ]);
    expect(choices[1]).toHaveAccessibleDescription("Manage your customers, sales and earnings");
  });

  it("first asks how the person wants to sign in, with three keyboard-reachable choices", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    const { container } = render(<LoginScreen />);
    expect(await screen.findByRole("heading", { level: 1, name: "Welcome to Workeasy360" })).toBeInTheDocument();
    const section = screen.getByRole("region", { name: "How would you like to sign in?" });
    const choices = within(section).getAllByRole("link").slice(0, 3);
    expect(choices.map((a) => [a.getAttribute("aria-label"), a.getAttribute("href")])).toEqual([
      ["Administrator", "/login?mode=admin"],
      ["Partner", "/login?mode=partner"],
      ["User", "/login?mode=user"],
    ]);
    expect(choices[0]).toHaveAccessibleDescription("Manage the Workeasy360 platform");
    expect(choices[1]).toHaveAccessibleDescription("Manage your customers, sales and earnings");
    expect(choices[2]).toHaveAccessibleDescription("Access your organization and products");
    // Real links: in the tab order, activated by Enter, no custom key handling.
    for (const link of choices) {
      expect(link.tagName).toBe("A");
      expect(link).not.toHaveAttribute("tabindex", "-1");
    }
    // No form yet: nothing to type into until a way in is chosen.
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("Administrator: the same form and request, then the Admin Console decides", async () => {
    query = "mode=admin";
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/auth/login": [SIGNED_IN],
    });
    const { container } = render(<LoginScreen />);
    expect(await screen.findByRole("heading", { name: "Sign in as Administrator" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Change/ })).not.toBeInTheDocument();
    await expectNoViolations(container);
    await signIn();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin"));
    const login = calls.find((c) => c.key === "POST /api/v1/auth/login")!;
    // Only an email and a password reach the server - never the mode.
    expect(login.body).toEqual({ email: "a@example.com", password: "correct horse battery staple" });
    expect(login.url).not.toMatch(/mode|role/);
  });

  it("Administrator returns to the console page that sent them, and only to a console page", async () => {
    query = `mode=admin&next=${encodeURIComponent("/admin/partners/resellers")}`;
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], "POST /api/v1/auth/login": [SIGNED_IN] });
    const first = render(<LoginScreen />);
    await signIn();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin/partners/resellers"));
    first.unmount();

    nav.router.replace = vi.fn();
    query = `mode=admin&next=${encodeURIComponent("https://evil.example/admin")}`;
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], "POST /api/v1/auth/login": [SIGNED_IN] });
    render(<LoginScreen />);
    await signIn();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin"));
  });

  it("User: the ordinary workspace, without being sent to the console", async () => {
    query = "mode=user";
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], "POST /api/v1/auth/login": [SIGNED_IN] });
    render(<LoginScreen />);
    expect(await screen.findByRole("heading", { name: "Sign in as User" })).toBeInTheDocument();
    await signIn();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard?mode=user"));
  });

  it("a failed sign-in reads the same in both modes and says nothing about administrators", async () => {
    const texts: string[] = [];
    for (const mode of ["admin", "user"]) {
      query = `mode=${mode}`;
      mockApi({
        "GET /api/v1/auth/session": [SIGNED_OUT],
        "POST /api/v1/auth/login": [{ status: 401, body: { code: "invalid_credentials" } }],
      });
      const view = render(<LoginScreen />);
      await signIn("someone@example.com", "wrong");
      texts.push((await screen.findByRole("alert")).textContent ?? "");
      view.unmount();
    }
    expect(texts[0]).toBe(texts[1]);
    expect(texts[0]).not.toMatch(/admin|staff/i);
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("a product sign-in or a partner's branded sign-in skips the choice", async () => {
    for (const q of ["resume=abc123", "brand=app.partner-example.com"]) {
      query = q;
      mockApi({
        "GET /api/v1/auth/session": [SIGNED_OUT],
        "GET /api/v1/public/branding": [{ status: 404, body: { code: "not_found" } }],
      });
      const view = render(<LoginScreen />);
      expect(await screen.findByLabelText("Password")).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Administrator" })).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it("someone already signed in goes where the chosen mode leads", async () => {
    query = "mode=admin";
    mockApi({ "GET /api/v1/auth/session": [{ status: 200, body: { authenticated: true, user_id: "u1", email: "a@example.com" } }] });
    render(<LoginScreen />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin"));
  });
});

// --- after signing in as a User -----------------------------------------------------------------

describe("the dashboard entry", () => {
  const staffSession: Reply = {
    status: 200,
    body: { authenticated: true, user_id: "u1", email: "s@example.com", consoles: { staff: true, partner: false } },
  };
  const oneOrg: Reply = { status: 200, body: { organizations: [{ slug: "acme" }], has_more: false } };

  it("sends staff to the console when no mode was chosen (unchanged)", async () => {
    mockApi({ "GET /api/v1/auth/session": [staffSession], "GET /api/v1/organizations": [oneOrg] });
    render(<DashboardEntryPage />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin"));
  });

  it("gives an administrator who chose User their organization, not the console", async () => {
    query = "mode=user";
    mockApi({ "GET /api/v1/auth/session": [staffSession], "GET /api/v1/organizations": [oneOrg] });
    render(<DashboardEntryPage />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme"));
    expect(nav.router.replace).not.toHaveBeenCalledWith("/admin");
  });

  it("sends a signed-out visitor to the User sign-in", async () => {
    query = "mode=user";
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    render(<DashboardEntryPage />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/login?mode=user"));
  });
});
