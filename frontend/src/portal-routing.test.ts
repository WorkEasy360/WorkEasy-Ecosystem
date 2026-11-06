import { describe, it, expect } from "vitest";
import { routePortalRequest } from "./proxy";
import {
  pageOwner,
  parseOrigin,
  portalForHost,
  portalHref,
  portalSignInPath,
  readPortalOrigins,
  safeNext,
  servesPage,
  signInUrl,
  stateCookieName,
  type PortalOrigins,
} from "@/lib/portals";

/**
 * Multi-portal host routing (ADR-043, ADR-059). The host decides which UI a
 * page is shown on and sends a signed-out visitor to that portal's own
 * sign-in page - never what anyone may do: these functions read no session
 * contents and grant nothing.
 */

const ORIGINS: PortalOrigins = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://auth.workeasy360.com",
};
const STATE = "s".repeat(43);

function route(host: string, path: string, opts: { session?: boolean } = {}) {
  const [pathname, search = ""] = path.split("?");
  return routePortalRequest({
    origins: ORIGINS,
    host,
    pathname,
    search: search ? `?${search}` : "",
    hasSession: opts.session ?? false,
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
    expect(pageOwner("/login")).toBe("app");
    expect(pageOwner("/reset-password")).toBe("app");
    expect(pageOwner("/signup")).toBe("app");
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

  it("gives the Admin, Partner and customer portals each their own sign-in pages (ADR-059)", () => {
    for (const portal of ["admin", "partner", "app"] as const) {
      for (const path of ["/login", "/forgot-password", "/reset-password"]) {
        expect(servesPage(portal, path), `${portal}${path}`).toBe(true);
      }
    }
    // Only a customer signs up; the central host keeps /login for products.
    expect(servesPage("admin", "/signup")).toBe(false);
    expect(servesPage("partner", "/signup")).toBe(false);
    expect(servesPage("app", "/signup")).toBe(true);
    expect(servesPage("auth", "/login")).toBe(true);
    expect(servesPage("auth", "/signup")).toBe(false);
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
  it("sends a signed-out visit to this portal's own sign-in page, remembering the page", () => {
    expect(route("admin.workeasy360.com", "/admin/customers?q=acme")).toEqual({
      kind: "redirect",
      location: `${ORIGINS.admin}/login?next=${encodeURIComponent("/admin/customers?q=acme")}`,
    });
    expect(route("app.workeasy360.com", "/o/acme")).toEqual({
      kind: "redirect",
      location: `${ORIGINS.app}/login?next=${encodeURIComponent("/o/acme")}`,
    });
    expect(route("partner.workeasy360.com", "/partner")).toEqual({ kind: "redirect", location: `${ORIGINS.partner}/login` });
    expect(portalSignInPath("admin", "//evil.example")).toBe("/login");
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
    expect(route("partner.workeasy360.com", "/partner/white-label/dashboard")).toEqual({
      kind: "redirect",
      location: `${ORIGINS.partner}/login?next=${encodeURIComponent("/partner/white-label/dashboard")}`,
    });
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
    expect(route("partner.workeasy360.com", "/reset-password")).toEqual({ kind: "serve" });
    expect(route("partner.workeasy360.com", "/signup")).toEqual({
      kind: "redirect",
      location: "https://app.workeasy360.com/signup",
    });
    // An emailed link from before ADR-059 still opens (the fragment carries over).
    expect(route("auth.workeasy360.com", "/verify-email")).toEqual({
      kind: "redirect",
      location: "https://app.workeasy360.com/verify-email",
    });
  });

  it("opens each portal at its own root", () => {
    expect(route("admin.workeasy360.com", "/")).toEqual({ kind: "redirect", location: `${ORIGINS.admin}/admin` });
    expect(route("partner.workeasy360.com", "/")).toEqual({ kind: "redirect", location: `${ORIGINS.partner}/partner` });
    expect(route("auth.workeasy360.com", "/")).toEqual({ kind: "redirect", location: `${ORIGINS.app}/login` });
    expect(route("app.workeasy360.com", "/")).toEqual({ kind: "serve" });
  });

  it("serves sign-in on each portal's own host - no central sign-in (ADR-059)", () => {
    expect(route("admin.workeasy360.com", "/login")).toEqual({ kind: "serve" });
    expect(route("partner.workeasy360.com", "/login?next=/partner/p1")).toEqual({ kind: "serve" });
    expect(route("app.workeasy360.com", "/login?mode=user")).toEqual({ kind: "serve" });
  });

  it("sends a sign-in address naming another portal to that portal's own sign-in page", () => {
    expect(route("app.workeasy360.com", "/login?mode=partner")).toEqual({ kind: "redirect", location: `${ORIGINS.partner}/login` });
    expect(route("app.workeasy360.com", "/login?mode=admin&next=%2Fadmin%2Fusers")).toEqual({
      kind: "redirect",
      location: `${ORIGINS.admin}/login?next=%2Fadmin%2Fusers`,
    });
    // An old central sign-in address lands on the portal it named.
    expect(route("auth.workeasy360.com", `/login?mode=admin&next=%2Fadmin&state=${STATE}`)).toEqual({
      kind: "redirect",
      location: `${ORIGINS.admin}/login?next=%2Fadmin`,
    });
    expect(route("auth.workeasy360.com", "/login")).toEqual({ kind: "redirect", location: `${ORIGINS.app}/login` });
  });

  it("keeps a product sign-in on the central sign-in host", () => {
    expect(route("auth.workeasy360.com", "/login?resume=tok")).toEqual({ kind: "serve" });
    expect(route("app.workeasy360.com", "/login?resume=tok&reauth=1")).toEqual({
      kind: "redirect",
      location: `${ORIGINS.auth}/login?resume=tok&reauth=1`,
    });
  });

  it("serves the sign-in pages and the hand-off landing without a session", () => {
    expect(route("app.workeasy360.com", "/signup")).toEqual({ kind: "serve" });
    expect(route("admin.workeasy360.com", "/forgot-password")).toEqual({ kind: "serve" });
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
