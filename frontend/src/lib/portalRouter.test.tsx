import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { usePortalRouter } from "./portalRouter";
import { PortalsProvider } from "./portalContext";
import type { PortalOrigins } from "./portals";

/** Another portal's page is another origin (ADR-043): a full navigation
 * there, never a client-side fetch the CSP would refuse. */

const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), back: vi.fn(), forward: vi.fn(), prefetch: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => nav.router }));

const ORIGINS: PortalOrigins = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://auth.workeasy360.com",
};

let assign: ReturnType<typeof vi.fn>;
let replace: ReturnType<typeof vi.fn>;

function on(host: string, origins: PortalOrigins | null = ORIGINS) {
  vi.stubGlobal("location", { ...window.location, host, assign, replace });
  return renderHook(() => usePortalRouter(), {
    wrapper: ({ children }) => <PortalsProvider origins={origins}>{children}</PortalsProvider>,
  }).result.current;
}

beforeEach(() => {
  assign = vi.fn();
  replace = vi.fn();
  nav.router.replace = vi.fn();
  nav.router.push = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

describe("usePortalRouter", () => {
  it("navigates within a portal with the ordinary router", () => {
    on("app.workeasy360.com").push("/o/acme/apps");
    expect(nav.router.push).toHaveBeenCalledWith("/o/acme/apps");
    expect(assign).not.toHaveBeenCalled();
  });

  it("goes to another portal's page with a full navigation to its origin", () => {
    on("admin.workeasy360.com").push("/organizations/select");
    expect(assign).toHaveBeenCalledWith("https://app.workeasy360.com/organizations/select");
    on("auth.workeasy360.com").replace("/o/acme?x=1");
    expect(replace).toHaveBeenCalledWith("https://app.workeasy360.com/o/acme?x=1");
    expect(nav.router.push).not.toHaveBeenCalled();
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("starts a sign-in on this host, so its binding cookie is set first", () => {
    on("partner.workeasy360.com").replace("/login");
    expect(assign).toHaveBeenCalledWith("/login");
    on("admin.workeasy360.com").replace("/login?mode=admin&next=%2Fadmin%2Fusers");
    expect(assign).toHaveBeenLastCalledWith("/login?mode=admin&next=%2Fadmin%2Fusers");
  });

  it("is the plain router in single-host mode", () => {
    on("localhost:3000", null).replace("/admin");
    expect(nav.router.replace).toHaveBeenCalledWith("/admin");
    expect(assign).not.toHaveBeenCalled();
  });
});
