import { describe, it, expect } from "vitest";
import { pageOwner, safeNext, servesPage, type PortalOrigins } from "@/lib/portals";
import { isPublicPath, routePortalRequest } from "@/proxy";

const ORIGINS: PortalOrigins = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://auth.workeasy360.com",
};

const route = (host: string, pathname: string, search = "", hasSession = true) =>
  routePortalRequest({ origins: ORIGINS, host, pathname, search, hasSession, newState: () => "s".repeat(43) });

describe("universal sign-in pages and their hosts (ADR-044)", () => {
  it("serves the access chooser and the sign-out confirmation on the central sign-in host only", () => {
    expect(pageOwner("/product-access")).toBe("auth");
    expect(pageOwner("/sign-out")).toBe("auth");
    expect(servesPage("app", "/product-access")).toBe(false);
    expect(route("app.workeasy360.com", "/product-access", "?resume=t")).toEqual({
      kind: "redirect",
      location: "https://auth.workeasy360.com/product-access?resume=t",
    });
    expect(route("auth.workeasy360.com", "/product-access", "?resume=t")).toEqual({ kind: "serve" });
  });

  it("sends a signed-out visitor of the chooser to sign in with the same request", () => {
    expect(route("auth.workeasy360.com", "/product-access", "?resume=t", false)).toEqual({
      kind: "redirect",
      location: "https://auth.workeasy360.com/login?resume=t",
    });
  });

  it("lets a signed-out visitor reach the sign-out confirmation, which only offers a way back", () => {
    expect(isPublicPath("/sign-out")).toBe(true);
    expect(route("auth.workeasy360.com", "/sign-out", "?request=x", false)).toEqual({ kind: "serve" });
  });

  it("keeps the standalone preferences page on the customer portal, as a valid return path", () => {
    expect(pageOwner("/settings/sign-in")).toBe("app");
    expect(safeNext("app", "/settings/sign-in")).toBe("/settings/sign-in");
    expect(route("auth.workeasy360.com", "/settings/sign-in")).toEqual({
      kind: "redirect",
      location: "https://app.workeasy360.com/settings/sign-in",
    });
  });
});
