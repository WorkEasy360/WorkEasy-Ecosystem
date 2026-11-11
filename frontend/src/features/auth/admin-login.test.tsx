import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { LoginScreen } from "./LoginScreen";
import { ForgotPasswordScreen, ResetPasswordScreen } from "./SelfService";

/**
 * The Admin sign-in (2026-11-09): the owner's reference design on the existing
 * sign-in endpoint. The server decides everything; these check what the page
 * sends, what it says when refused, and that it offers no way to register.
 */

let query = "";
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/login",
}));

type Reply = { status: number; body?: unknown };
function mockApi(routes: Record<string, Reply>) {
  const calls: { key: string; body: unknown }[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    calls.push({ key, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    const reply = routes[key];
    if (!reply) throw new Error(`unexpected request ${key}`);
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

const SIGNED_OUT: Reply = { status: 200, body: { authenticated: false } };
const OK: Reply = { status: 200, body: { user_id: "u1", email: "a@example.com", name: "A" } };

const fill = (email: string, password: string) => {
  fireEvent.change(screen.getByLabelText("Email Address"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
};
const submit = () => fireEvent.click(screen.getByRole("button", { name: /Sign In to Admin/ }));

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.replace.mockReset();
  query = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the Admin sign-in page", () => {
  it("has the reference design's parts and offers no registration", async () => {
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT });
    render(<LoginScreen portal="admin" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Admin Login" })).toBeTruthy();
    expect(screen.getAllByText("Admin Portal").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Platform Control Center/)).toBeTruthy();
    for (const label of [
      "Platform & User Management",
      "Product & Plan Management",
      "Orders, Billing & Payments",
      "Credits & Usage Control",
      "All Products",
      "Partners",
      "Customers",
      "Subscriptions",
      "Reports",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByRole("link", { name: "Forgot Password?" }).getAttribute("href")).toBe("/forgot-password?mode=admin");
    expect(screen.queryByRole("link", { name: /create an account/i })).toBeNull();
    expect(screen.queryByText(/Super Admin/)).toBeNull();
  });

  it("is free of accessibility violations", async () => {
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT });
    const { container } = render(<LoginScreen portal="admin" />);
    await screen.findByRole("heading", { level: 1 });
    const { violations } = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    expect(violations.map((v: Result) => v.id)).toEqual([]);
  });

  it("does not ask the server about an empty or malformed form", async () => {
    const calls = mockApi({ "GET /api/v1/auth/session": SIGNED_OUT });
    render(<LoginScreen portal="admin" />);
    await screen.findByRole("heading", { level: 1 });
    submit();
    expect((await screen.findByRole("alert")).textContent).toMatch(/email address/i);
    fill("not-an-email", "secret");
    submit();
    expect(screen.getByRole("alert").textContent).toMatch(/valid email/i);
    fill("a@example.com", "");
    submit();
    expect(screen.getByRole("alert").textContent).toMatch(/password/i);
    expect(calls.filter((c) => c.key.startsWith("POST"))).toEqual([]);
  });

  it("sends Remember me unticked by default and goes to the console", async () => {
    const calls = mockApi({ "GET /api/v1/auth/session": SIGNED_OUT, "POST /api/v1/auth/login": OK });
    render(<LoginScreen portal="admin" />);
    await screen.findByRole("heading", { level: 1 });
    fill("a@example.com", "correct horse");
    submit();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin"));
    expect(calls.find((c) => c.key === "POST /api/v1/auth/login")?.body).toEqual({
      email: "a@example.com",
      password: "correct horse",  // pragma: allowlist secret
      remember: false,
    });
  });

  it("sends Remember me when ticked and returns to the page asked for", async () => {
    query = "next=/admin/orders";
    const calls = mockApi({ "GET /api/v1/auth/session": SIGNED_OUT, "POST /api/v1/auth/login": OK });
    render(<LoginScreen portal="admin" />);
    await screen.findByRole("heading", { level: 1 });
    fill("a@example.com", "correct horse");
    fireEvent.click(screen.getByLabelText("Remember Me"));
    submit();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin/orders"));
    expect((calls.find((c) => c.key === "POST /api/v1/auth/login")?.body as { remember: boolean }).remember).toBe(true);
  });

  it("ignores a next that leaves the console", async () => {
    query = "next=//evil.example/admin";
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT, "POST /api/v1/auth/login": OK });
    render(<LoginScreen portal="admin" />);
    await screen.findByRole("heading", { level: 1 });
    fill("a@example.com", "correct horse");
    submit();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin"));
  });

  it.each([
    [401, "invalid_credentials", /isn't right/],
    [401, "account_locked", /temporarily locked/],
    [401, "account_disabled", /disabled/],
    [429, "rate_limited", /Too many sign-in attempts/],
    [500, "boom", /Something went wrong/],
  ])("says what a %s %s means and lets the person try again", async (status, code, words) => {
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT, "POST /api/v1/auth/login": { status, body: { code } } });
    render(<LoginScreen portal="admin" />);
    await screen.findByRole("heading", { level: 1 });
    fill("a@example.com", "wrong");
    submit();
    expect((await screen.findByRole("alert")).textContent).toMatch(words);
    expect((screen.getByRole("button", { name: /Sign In to Admin/ }) as HTMLButtonElement).disabled).toBe(false);
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("shows and hides the password", async () => {
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT });
    render(<LoginScreen portal="admin" />);
    await screen.findByRole("heading", { level: 1 });
    const field = screen.getByLabelText("Password") as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(field.type).toBe("text");
    fireEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(field.type).toBe("password");
  });

  it("keeps the standard page for a product sign-in on the Admin host", async () => {
    query = "resume=abc";
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT });
    render(<LoginScreen portal="admin" />);
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByText("Admin Login")).toBeNull();
  });

  it("gives the customer portal its own User Admin Login, never the Admin's", async () => {
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT });
    render(<LoginScreen portal="app" />);
    expect(await screen.findByRole("heading", { name: "User Admin Login" })).toBeTruthy();
    expect(screen.queryByText(/Platform Control Center/)).toBeNull();
    expect(screen.getByText("For customers and authorized team members")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Create Organization Account" }).getAttribute("href")).toBe("/signup");
    expect(screen.getByRole("button", { name: "Login to User Admin Panel" })).toBeTruthy();
  });
});

describe("the password pages on the Admin portal", () => {
  it("wear the Admin frame", async () => {
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT, "POST /api/v1/public/password/forgot": { status: 200, body: { ok: true } } });
    render(<ForgotPasswordScreen admin />);
    expect(await screen.findByRole("heading", { level: 1, name: "Reset your password" })).toBeTruthy();
    expect(screen.getByText(/Platform Control Center/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Email me a reset link" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/If an account uses/);
  });

  it("say a reset link without a token is not valid", async () => {
    mockApi({ "GET /api/v1/auth/session": SIGNED_OUT });
    render(<ResetPasswordScreen admin />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/not valid/);
    expect(screen.getByText(/Platform Control Center/)).toBeTruthy();
  });
});
