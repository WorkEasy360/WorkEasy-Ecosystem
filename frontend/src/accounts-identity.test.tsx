import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { routePortalRequest } from "./proxy";
import { pageOwner, readPortalOrigins, servesPage, type PortalOrigins } from "@/lib/portals";
import { accountsPage, continueFromAccounts, handsOffToApp } from "@/features/auth/accountsSignIn";
import { AccountHome } from "@/features/account/AccountHome";
import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";

/**
 * ADR-071: WorkEasy360 Accounts (accounts.workeasy360.com, the `auth` host) is
 * where a person's identity lives. With `W360_ACCOUNTS_IDENTITY=on` it hosts the
 * customer portal's sign-in, sign-up, verification and password reset, and the
 * customer portal signs in through it (the ADR-043 hand-off). Off: ADR-059
 * exactly. The Admin and Partner portals keep their own sign-in either way.
 */

const BASE = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://accounts.workeasy360.com",
  store: "",
};
const ON: PortalOrigins = { ...BASE, accountsIdentity: true };
const OFF: PortalOrigins = { ...BASE };
const STATE = "b".repeat(43);

function route(origins: PortalOrigins, host: string, path: string, session = false) {
  const [pathname, search = ""] = path.split("?");
  return routePortalRequest({
    origins,
    host,
    pathname,
    search: search ? `?${search}` : "",
    hasSession: session,
    newState: () => STATE,
  });
}

describe("configuration", () => {
  it("reads W360_ACCOUNTS_IDENTITY, off unless set", () => {
    const env = {
      W360_ADMIN_ORIGIN: BASE.admin,
      W360_PARTNER_ORIGIN: BASE.partner,
      W360_APP_ORIGIN: BASE.app,
      W360_AUTH_ORIGIN: BASE.auth,
    };
    expect(readPortalOrigins(env)?.accountsIdentity).toBeUndefined();
    expect(readPortalOrigins({ ...env, W360_ACCOUNTS_IDENTITY: "on" })?.accountsIdentity).toBe(true);
    expect(readPortalOrigins({ ...env, W360_ACCOUNTS_IDENTITY: "true" })?.accountsIdentity).toBe(true);
    expect(readPortalOrigins({ ...env, W360_ACCOUNTS_IDENTITY: "off" })?.accountsIdentity).toBeUndefined();
  });

  it("owns the identity pages only while on; the account page is always Accounts'", () => {
    for (const path of ["/signup", "/verify-email", "/forgot-password", "/reset-password", "/confirm-email", "/r/abc"]) {
      expect(pageOwner(path, ON), path).toBe("auth");
      expect(pageOwner(path, OFF), path).toBe("app");
    }
    expect(pageOwner("/account", OFF)).toBe("auth");
    expect(pageOwner("/account/sign-in-preferences", ON)).toBe("auth");
    // The Admin and Partner portals keep their own password reset.
    expect(servesPage("admin", "/reset-password", ON)).toBe(true);
    expect(servesPage("partner", "/forgot-password", ON)).toBe(true);
    expect(servesPage("app", "/reset-password", ON)).toBe(false);
  });
});

describe("off: ADR-059 exactly", () => {
  it("the customer portal signs in on its own host, and Accounts sends a plain sign-in there", () => {
    expect(route(OFF, "app.workeasy360.com", "/login")).toEqual({ kind: "serve" });
    expect(route(OFF, "app.workeasy360.com", "/o/acme/members")).toEqual({
      kind: "redirect",
      location: "https://app.workeasy360.com/login?next=%2Fo%2Facme%2Fmembers",
    });
    expect(route(OFF, "accounts.workeasy360.com", "/login")).toEqual({
      kind: "redirect",
      location: "https://app.workeasy360.com/login",
    });
    expect(route(OFF, "accounts.workeasy360.com", "/")).toEqual({ kind: "redirect", location: "https://app.workeasy360.com/login" });
    expect(route(OFF, "app.workeasy360.com", "/signup")).toEqual({ kind: "serve" });
  });
});

describe("on: identity lives on WorkEasy360 Accounts", () => {
  it("Accounts serves its own sign-in, sign-up, verification and reset", () => {
    for (const path of ["/login", "/login?next=%2Faccount", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/confirm-email"]) {
      expect(route(ON, "accounts.workeasy360.com", path), path).toEqual({ kind: "serve" });
    }
    expect(route(ON, "accounts.workeasy360.com", "/")).toEqual({ kind: "redirect", location: "https://accounts.workeasy360.com/account" });
  });

  it("a signed-out visit to the account page signs in on Accounts and comes back", () => {
    expect(route(ON, "accounts.workeasy360.com", "/account")).toEqual({
      kind: "redirect",
      location: "https://accounts.workeasy360.com/login?next=%2Faccount",
    });
    expect(route(ON, "accounts.workeasy360.com", "/account", true)).toEqual({ kind: "serve" });
  });

  it("the customer portal signs in through Accounts, bound to its own host by a state cookie", () => {
    const signedOut = route(ON, "app.workeasy360.com", "/o/acme/members");
    expect(signedOut).toEqual({
      kind: "redirect",
      location: `https://accounts.workeasy360.com/login?mode=user&next=%2Fo%2Facme%2Fmembers&state=${STATE}`,
      bind: STATE,
    });
    // Its old sign-in address starts the same flow, keeping a safe `next` only.
    expect(route(ON, "app.workeasy360.com", "/login?next=%2Fbilling")).toEqual({
      kind: "redirect",
      location: `https://accounts.workeasy360.com/login?mode=user&next=%2Fbilling&state=${STATE}`,
      bind: STATE,
    });
    expect(route(ON, "app.workeasy360.com", "/login?next=https%3A%2F%2Fevil.example")).toEqual({
      kind: "redirect",
      location: `https://accounts.workeasy360.com/login?mode=user&state=${STATE}`,
      bind: STATE,
    });
    // ...and the hand-off lands on its own continue page, which needs no session.
    expect(route(ON, "app.workeasy360.com", "/auth/continue")).toEqual({ kind: "serve" });
  });

  it("the customer portal's old identity pages move to the same address on Accounts", () => {
    expect(route(ON, "app.workeasy360.com", "/signup?plan=x")).toEqual({
      kind: "redirect",
      location: "https://accounts.workeasy360.com/signup?plan=x",
    });
    expect(route(ON, "app.workeasy360.com", "/reset-password")).toEqual({
      kind: "redirect",
      location: "https://accounts.workeasy360.com/reset-password",
    });
    expect(route(ON, "app.workeasy360.com", "/verify-email")).toEqual({
      kind: "redirect",
      location: "https://accounts.workeasy360.com/verify-email",
    });
  });

  it("the Admin and Partner portals keep their own sign-in", () => {
    expect(route(ON, "admin.workeasy360.com", "/login")).toEqual({ kind: "serve" });
    expect(route(ON, "partner.workeasy360.com", "/reset-password")).toEqual({ kind: "serve" });
    expect(route(ON, "admin.workeasy360.com", "/admin/products")).toEqual({
      kind: "redirect",
      location: "https://admin.workeasy360.com/login?next=%2Fadmin%2Fproducts",
    });
    expect(route(ON, "accounts.workeasy360.com", "/login?mode=admin")).toEqual({
      kind: "redirect",
      location: "https://admin.workeasy360.com/login",
    });
  });

  it("a product sign-in is unchanged", () => {
    expect(route(ON, "accounts.workeasy360.com", "/login?resume=abc")).toEqual({ kind: "serve" });
    expect(route(ON, "app.workeasy360.com", "/login?resume=abc")).toEqual({
      kind: "redirect",
      location: "https://accounts.workeasy360.com/login?resume=abc",
    });
  });
});

describe("after signing in on Accounts", () => {
  afterEach(() => vi.restoreAllMocks());

  it("goes only to Accounts' own account pages", () => {
    expect(accountsPage("/account")).toBe("/account");
    expect(accountsPage("/account/sign-in-preferences")).toBe("/account/sign-in-preferences");
    for (const bad of [null, "", "/dashboard", "//evil.example", "https://evil.example/account", "/account/../admin", "/account?x=1"]) {
      expect(accountsPage(bad), String(bad)).toBe("/account");
    }
  });

  it("hands the sign-in to the customer portal only with its binding value", () => {
    expect(handsOffToApp(new URLSearchParams({ mode: "user", state: STATE }))).toBe(true);
    expect(handsOffToApp(new URLSearchParams({ state: STATE }))).toBe(true);
    expect(handsOffToApp(new URLSearchParams({ mode: "admin", state: STATE }))).toBe(false);
    expect(handsOffToApp(new URLSearchParams({ mode: "user", state: "short" }))).toBe(false);
    expect(handsOffToApp(new URLSearchParams({ mode: "user" }))).toBe(false);
  });

  it("issues the hand-off and follows it; a refused one falls back to the account page", async () => {
    const calls = mockApi({
      "POST /api/v1/auth/handoff": [
        ok({ redirect_url: "https://app.workeasy360.com/auth/continue#code=abc" }),
        { status: 400, body: { code: "handoff_invalid" } },
      ],
    });
    const go = vi.fn();
    const params = new URLSearchParams({ mode: "user", state: STATE, next: "/o/acme" });
    await continueFromAccounts(params, go);
    expect(go).toHaveBeenLastCalledWith("https://app.workeasy360.com/auth/continue#code=abc");
    expect(calls[0].body).toEqual({ mode: "user", next: "/o/acme", state: STATE });
    await continueFromAccounts(params, go);
    expect(go).toHaveBeenLastCalledWith("/account");
    await continueFromAccounts(new URLSearchParams({ next: "/account/sign-in-preferences" }), go);
    expect(go).toHaveBeenLastCalledWith("/account/sign-in-preferences");
  });
});

describe("the account page", () => {
  it("shows the person, their organizations (opening in User Admin) and their security; passes axe", async () => {
    mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: true, user_id: "u1", email: "ravi@example.com", name: "Ravi Kumar" })],
      "GET /api/v1/organizations": [
        ok({
          organizations: [{ organization_id: "o1", slug: "acme", display_name: "Acme Traders", status: "active", membership_id: "m1", is_owner: true }],
          next_cursor: null,
          has_more: false,
        }),
      ],
      "GET /api/v1/auth/sessions": [ok({ sessions: [] })],
      "GET /api/v1/auth/activity": [ok({ events: [] })],
      "GET /api/v1/auth/two-step": [ok({ available: true, enabled: false, method: null, enabled_at: null, recovery_codes_remaining: 0 })],
    });
    const { container } = render(<AccountHome appOrigin="https://app.workeasy360.com" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Your WorkEasy360 account" })).toBeInTheDocument();
    expect(await screen.findByText("Ravi Kumar")).toBeInTheDocument();
    const org = await screen.findByRole("link", { name: "Acme Traders" });
    expect(org).toHaveAttribute("href", "https://app.workeasy360.com/o/acme");
    expect(within(org.closest("li") as HTMLElement).getByText("Owner")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Your account" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Account & security" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Sign out everywhere" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    // Accounts V3: the shared security panels carry two-step verification here too.
    expect(await screen.findByRole("button", { name: "Set up two-step verification" })).toBeInTheDocument();
    await expectNoViolations(container);
  });
});
