import { describe, it, expect } from "vitest";
import { routePortalRequest } from "./proxy";
import {
  pageOwner,
  parseOrigin,
  portalForHost,
  portalHref,
  readPortalOrigins,
  safeNext,
  servesPage,
  signInUrl,
  stateCookieName,
  type PortalOrigins,
} from "@/lib/portals";

/**
 * Multi-portal host routing (ADR-043). The host decides which UI a page is
 * shown on and sends a signed-out visitor to the central sign-in - never what
 * anyone may do: these functions read no session contents and grant nothing.
 */

const ORIGINS: PortalOrigins = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://auth.workeasy360.com",
};
const STATE = "s".repeat(43);

function route(host: string, path: string, opts: { session?: boolean; state?: string | null } = {}) {
  const [pathname, search = ""] = path.split("?");
  return routePortalRequest({
    origins: ORIGINS,
    host,
    pathname,
    search: search ? `?${search}` : "",
    hasSession: opts.session ?? false,
    existingState: opts.state ?? null,
    newState: () => STATE,
  });
}

describe("the configuration", () => {
  it("is all four origins or single-host mode", () => {
    const env = {
      W360_ADMIN_ORIGIN: ORIGINS.admin,
      W360_PARTNER_ORIGIN: ORIGINS.partner,
      W360_APP_ORIGIN: ORIGINS.app,
      W360_AUTH_ORIGIN: `${ORIGINS.auth}/`,
    };
    expect(readPortalOrigins(env)).toEqual(ORIGINS);
    expect(readPortalOrigins({ ...env, W360_AUTH_ORIGIN: undefined })).toBeNull();
    expect(readPortalOrigins({ ...env, W360_ADMIN_ORIGIN: "https://*.workeasy360.com" })).toBeNull();
    expect(readPortalOrigins({ ...env, W360_APP_ORIGIN: "https://app.workeasy360.com/x" })).toBeNull();
    expect(readPortalOrigins({})).toBeNull();
    expect(parseOrigin("javascript:alert(1)")).toBeNull();
  });

  it("maps each host to exactly one portal; anything else is not a portal", () => {
    expect(portalForHost("admin.workeasy360.com", ORIGINS)).toBe("admin");
    expect(portalForHost("AUTH.workeasy360.com.", ORIGINS)).toBe("auth");
    expect(portalForHost("crm.workeasy360.com", ORIGINS)).toBeNull();
    expect(portalForHost("admin.workeasy360.com.evil.com", ORIGINS)).toBeNull();
    expect(portalForHost("", ORIGINS)).toBeNull();
  });

  it("names a production binding cookie the browser keeps host-only", () => {
    expect(stateCookieName(ORIGINS)).toBe("__Host-w360_portal_state");
    expect(stateCookieName({ ...ORIGINS, auth: "http://127.0.0.1:3999" })).toBe("w360_portal_state");
  });
});

describe("which portal serves a page", () => {
  it("gives each page one home", () => {
    expect(pageOwner("/admin/customers")).toBe("admin");
    expect(pageOwner("/partner/p1/customers")).toBe("partner");
    expect(pageOwner("/o/acme/apps")).toBe("app");
    expect(pageOwner("/login")).toBe("auth");
    expect(pageOwner("/reset-password")).toBe("auth");
    expect(pageOwner("/auth/continue")).toBeNull();
    expect(pageOwner("/administrator")).toBeNull();
  });

  it("keeps the three partner workspaces inside the partner portal (ADR-047)", () => {
    for (const path of ["/partner/reseller/dashboard", "/partner/white-label/brand-settings/url-manager", "/partner/affiliate/referral-links"]) {
      expect(pageOwner(path), path).toBe("partner");
      expect(servesPage("partner", path), path).toBe(true);
      expect(servesPage("admin", path), path).toBe(false);
      expect(servesPage("app", path), path).toBe(false);
    }
  });

  it("lets the central sign-in ask which organization a product sign-in is for", () => {
    expect(servesPage("auth", "/organizations/select")).toBe(true);
    expect(servesPage("app", "/organizations/select")).toBe(true);
    expect(servesPage("admin", "/organizations/select")).toBe(false);
  });
});

describe("the return-URL allowlist", () => {
  it("keeps a page the portal serves", () => {
    expect(safeNext("admin", "/admin/customers?q=acme")).toBe("/admin/customers?q=acme");
    expect(safeNext("app", "/o/acme/explore")).toBe("/o/acme/explore");
    expect(safeNext("partner", "/partner/p1")).toBe("/partner/p1");
  });

  it("keeps a workspace page for the partner portal and for no other", () => {
    expect(safeNext("partner", "/partner/white-label/dashboard")).toBe("/partner/white-label/dashboard");
    expect(safeNext("partner", "/partner/affiliate/referral-links?x=1")).toBe("/partner/affiliate/referral-links?x=1");
    expect(safeNext("admin", "/partner/affiliate/dashboard")).toBe("/admin");
    expect(safeNext("app", "/partner/reseller/dashboard")).toBe("/dashboard");
  });

  it("refuses everything else, so `next` is never an open redirect", () => {
    for (const hostile of [
      "https://evil.example/admin",
      "//evil.example/admin",
      "javascript:alert(1)",
      "data:text/html,x",
      "/admin/../o/acme",
      "/admin/%2e%2e/o",
      "/admin\\evil",
      "/administrator",
      "/o/acme",
      `/admin/${String.fromCharCode(0)}`,
      `/admin/${"x".repeat(600)}`,
      "http://localhost/admin",
    ]) {
      expect(safeNext("admin", hostile), hostile).toBe("/admin");
    }
  });

  it("builds the sign-in address on the configured sign-in origin only", () => {
    const url = new URL(signInUrl(ORIGINS, "admin", "//evil.example", STATE));
    expect(url.origin).toBe(ORIGINS.auth);
    expect(url.pathname).toBe("/login");
    expect(url.searchParams.get("mode")).toBe("admin");
    expect(url.searchParams.get("next")).toBeNull();
    expect(url.searchParams.get("state")).toBe(STATE);
    expect(portalHref(ORIGINS, "partner", "/partner")).toBe("https://partner.workeasy360.com/partner");
    expect(portalHref(null, "partner", "/partner")).toBe("/partner");
  });
});

describe("routing a page request", () => {
  it("sends a signed-out visit to the central sign-in, remembering the page", () => {
    const routed = route("admin.workeasy360.com", "/admin/customers?q=acme");
    expect(routed?.kind).toBe("redirect");
    if (routed?.kind !== "redirect") return;
    const url = new URL(routed.location);
    expect(url.origin).toBe(ORIGINS.auth);
    expect(Object.fromEntries(url.searchParams)).toEqual({ mode: "admin", next: "/admin/customers?q=acme", state: STATE });
    expect(routed.state).toBe(STATE);
  });

  it("reuses a valid binding value while a sign-in is under way", () => {
    const existing = "e".repeat(43);
    const routed = route("app.workeasy360.com", "/o/acme", { state: existing });
    expect(routed?.kind === "redirect" && routed.state).toBe(existing);
    const replaced = route("app.workeasy360.com", "/o/acme", { state: "too-short" });
    expect(replaced?.kind === "redirect" && replaced.state).toBe(STATE);
  });

  it("serves a signed-in visit, whoever it is - the server decides access", () => {
    expect(route("admin.workeasy360.com", "/admin", { session: true })).toEqual({ kind: "serve" });
    expect(route("partner.workeasy360.com", "/partner", { session: true })).toEqual({ kind: "serve" });
  });

  it("serves a workspace on the partner host, moves it there from another, and asks a signed-out visit to sign in", () => {
    expect(route("partner.workeasy360.com", "/partner/affiliate/dashboard", { session: true })).toEqual({ kind: "serve" });
    expect(route("app.workeasy360.com", "/partner/reseller/customers", { session: true })).toEqual({
      kind: "redirect",
      location: "https://partner.workeasy360.com/partner/reseller/customers",
    });
    const signedOut = route("partner.workeasy360.com", "/partner/white-label/dashboard");
    expect(signedOut?.kind).toBe("redirect");
    if (signedOut?.kind !== "redirect") return;
    const url = new URL(signedOut.location);
    expect(url.origin).toBe(ORIGINS.auth);
    expect(Object.fromEntries(url.searchParams)).toEqual({ mode: "partner", next: "/partner/white-label/dashboard", state: STATE });
  });

  it("moves a page to the portal that serves it", () => {
    expect(route("app.workeasy360.com", "/admin/users", { session: true })).toEqual({
      kind: "redirect",
      location: "https://admin.workeasy360.com/admin/users",
    });
    expect(route("admin.workeasy360.com", "/o/acme/apps?x=1", { session: true })).toEqual({
      kind: "redirect",
      location: "https://app.workeasy360.com/o/acme/apps?x=1",
    });
    expect(route("auth.workeasy360.com", "/dashboard", { session: true })).toEqual({
      kind: "redirect",
      location: "https://app.workeasy360.com/dashboard",
    });
    expect(route("partner.workeasy360.com", "/reset-password")).toEqual({
      kind: "redirect",
      location: "https://auth.workeasy360.com/reset-password",
    });
  });

  it("opens each portal at its own root", () => {
    expect(route("admin.workeasy360.com", "/")).toEqual({ kind: "redirect", location: `${ORIGINS.admin}/admin` });
    expect(route("partner.workeasy360.com", "/")).toEqual({ kind: "redirect", location: `${ORIGINS.partner}/partner` });
    expect(route("auth.workeasy360.com", "/")).toEqual({ kind: "redirect", location: `${ORIGINS.auth}/login` });
    expect(route("app.workeasy360.com", "/")).toEqual({ kind: "serve" });
  });

  it("turns sign-in on any portal into the central sign-in", () => {
    const routed = route("partner.workeasy360.com", "/login?next=/partner/p1");
    expect(routed?.kind === "redirect" && new URL(routed.location).searchParams.get("mode")).toBe("partner");
    expect(routed?.kind === "redirect" && new URL(routed.location).searchParams.get("next")).toBe("/partner/p1");
    expect(route("app.workeasy360.com", "/login?resume=tok")).toEqual({
      kind: "redirect",
      location: `${ORIGINS.auth}/login?resume=tok`,
    });
  });

  it("serves the sign-in pages and the hand-off landing without a session", () => {
    expect(route("auth.workeasy360.com", "/login?mode=admin")).toEqual({ kind: "serve" });
    expect(route("auth.workeasy360.com", "/signup")).toEqual({ kind: "serve" });
    expect(route("admin.workeasy360.com", "/auth/continue")).toEqual({ kind: "serve" });
    expect(route("app.workeasy360.com", "/products")).toEqual({ kind: "serve" });
    expect(route("auth.workeasy360.com", "/organizations/select?resume=r")).toEqual({
      kind: "redirect",
      location: `${ORIGINS.auth}/login?resume=r`,
    });
  });

  it("leaves a partner's own domain to the white-label rules", () => {
    expect(route("app.partner-brand.com", "/login")).toBeNull();
  });

  it("only ever redirects to a configured origin", () => {
    for (const path of ["//evil.example/x", "/\\evil.example", "/admin/..%2f..%2fevil", "/login?next=https://evil.example"]) {
      for (const host of ["admin.workeasy360.com", "partner.workeasy360.com", "app.workeasy360.com", "auth.workeasy360.com"]) {
        const routed = route(host, path);
        if (routed?.kind === "redirect") {
          const origin = new URL(routed.location, "https://placeholder.invalid").origin;
          expect(Object.values(ORIGINS), `${host}${path}`).toContain(origin);
        }
      }
    }
  });
});
