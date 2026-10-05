import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { offCanonicalRedirect, transportHeaders } from "@/proxy";
import { brandHostParam } from "./LoginScreen";
import { BrandedLanding } from "./BrandedLanding";
import { signInHandoffUrl } from "./handoff";

/**
 * Phase 12, D-035 decision B (ADR-039): authentication stays on the canonical
 * host. A partner's custom domain shows its brand and hands the visitor over;
 * it never renders a credential field or holds a session.
 */

describe("offCanonicalRedirect", () => {
  const canonical = "app.workeasy360.com";

  it("serves everything on the canonical host", () => {
    for (const path of ["/", "/login", "/dashboard", "/admin", "/o/acme/dashboard"]) {
      expect(offCanonicalRedirect("app.workeasy360.com", path, "", canonical)).toBeNull();
      expect(offCanonicalRedirect("APP.workeasy360.com:443", path, "", canonical)).toBeNull();
    }
  });

  it("serves only the branded landing on a partner host", () => {
    expect(offCanonicalRedirect("app.wl-brand.com", "/login", "", canonical)).toBeNull();
    expect(offCanonicalRedirect("app.wl-brand.com", "/", "", canonical)).toBe("/login");
  });

  it("sends every other page on a partner host to the canonical host", () => {
    expect(offCanonicalRedirect("app.wl-brand.com", "/dashboard", "?x=1", canonical)).toBe(
      "https://app.workeasy360.com/dashboard?x=1",
    );
    expect(offCanonicalRedirect("app.wl-brand.com", "/signup", "", canonical)).toBe(
      "https://app.workeasy360.com/signup",
    );
    expect(offCanonicalRedirect("app.wl-brand.com", "/reset-password", "", canonical)).toBe(
      "https://app.workeasy360.com/reset-password",
    );
  });

  it("the redirect target is always the configured canonical host, whatever the request says", () => {
    const target = offCanonicalRedirect("evil.example.net", "//evil.example.net/x", "", canonical);
    expect(target?.startsWith("https://app.workeasy360.com/")).toBe(true);
  });

  it("treats every host as canonical when none is configured (development)", () => {
    expect(offCanonicalRedirect("localhost:3000", "/dashboard", "", "")).toBeNull();
  });
});

describe("transportHeaders", () => {
  it("adds HSTS only outside development, and preload only on the canonical host", () => {
    expect(transportHeaders(true, true)["Strict-Transport-Security"]).toBeUndefined();
    expect(transportHeaders(false, true)["Strict-Transport-Security"]).toContain("preload");
    expect(transportHeaders(false, false)["Strict-Transport-Security"]).toBe("max-age=31536000");
    expect(transportHeaders(false, true)["Cross-Origin-Opener-Policy"]).toBe("same-origin");
    expect(transportHeaders(false, true)["Permissions-Policy"]).toContain("camera=()");
  });
});

describe("brandHostParam", () => {
  it("accepts a plain host name only", () => {
    expect(brandHostParam("App.WL-Brand.com")).toBe("app.wl-brand.com");
    for (const bad of [null, "", "javascript:alert(1)", "https://x.com", "x.com/path", "x..com", "a b.com", "-x.com", "x.com:443"]) {
      expect(brandHostParam(bad)).toBeUndefined();
    }
  });
});

describe("signInHandoffUrl", () => {
  it("targets the canonical sign-in page and names only this host", () => {
    expect(signInHandoffUrl("https://app.workeasy360.com/", "App.WL-Brand.com:443")).toBe(
      "https://app.workeasy360.com/login?brand=app.wl-brand.com",
    );
  });
});

describe("BrandedLanding", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            brand: {
              brand_name: "WL Brand",
              accent_color: "#0f766e",
              support_email: "help@wl-brand.com",
              support_url: "",
              logo_url: null,
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
  });

  it("shows the brand and a hand-off link, and never a password field", async () => {
    const { container } = render(<BrandedLanding handoffUrl="https://app.workeasy360.com/login?brand=app.wl-brand.com" />);
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WL Brand"));
    const link = await screen.findByRole("link", { name: "Continue to sign in" });
    expect(link.getAttribute("href")).toMatch(/^https:\/\/app\.workeasy360\.com\/login\?brand=/);
    expect(container.querySelector("input")).toBeNull();
    expect(container.querySelector("form")).toBeNull();
  });
});
