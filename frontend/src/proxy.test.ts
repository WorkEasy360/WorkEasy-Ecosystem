import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { contentSecurityPolicy, isPublicPath } from "./proxy";

/**
 * The CSP is the one piece of this app that is *only* exercised by a real
 * browser, so nothing else in this suite would notice it going wrong. These
 * assertions pin the two properties that were actually broken in review,
 * both of which shipped a policy that looked strict and left the app
 * unusable in production.
 */

function directive(csp: string, name: string): string {
  const clause = csp
    .split(";")
    .map((part) => part.trim())
    .find((part) => part === name || part.startsWith(`${name} `));
  return clause ?? "";
}

describe("contentSecurityPolicy", () => {
  const prod = contentSecurityPolicy("TEST-NONCE", false);

  it("binds scripts to a per-request nonce and strict-dynamic", () => {
    expect(directive(prod, "script-src")).toBe(
      "script-src 'self' 'nonce-TEST-NONCE' 'strict-dynamic'",
    );
  });

  it("never allows unsafe-eval or unsafe-inline scripts in production", () => {
    expect(directive(prod, "script-src")).not.toContain("'unsafe-eval'");
    expect(directive(prod, "script-src")).not.toContain("'unsafe-inline'");
  });

  it("allows unsafe-eval for scripts only in development (React's error reconstruction)", () => {
    expect(directive(contentSecurityPolicy("N", true), "script-src")).toContain(
      "'unsafe-eval'",
    );
  });

  it("carries no nonce in style-src, which would block every inline style attribute", () => {
    // CSP3, "does a source list allow all inline behavior?": a nonce or hash
    // anywhere in the list makes inline style *attributes* unconditionally
    // disallowed, and a nonce cannot be attached to an attribute. This app
    // renders inline style attributes on almost every page, so a nonce here
    // renders the whole UI unstyled - see proxy.ts for the full reasoning.
    const style = directive(prod, "style-src");
    expect(style).not.toContain("nonce-");
    expect(style).toContain("'unsafe-inline'");
  });

  it("is identical for styles in development and production", () => {
    // Anything that differs between the two is a policy no developer ever
    // sees fail before it reaches a user.
    expect(directive(contentSecurityPolicy("N", true), "style-src")).toBe(
      directive(contentSecurityPolicy("N", false), "style-src"),
    );
  });

  it("keeps every other origin closed", () => {
    expect(directive(prod, "default-src")).toBe("default-src 'self'");
    expect(directive(prod, "connect-src")).toBe("connect-src 'self'");
    expect(directive(prod, "object-src")).toBe("object-src 'none'");
    expect(directive(prod, "frame-ancestors")).toBe("frame-ancestors 'none'");
    expect(directive(prod, "base-uri")).toBe("base-uri 'self'");
    expect(directive(prod, "form-action")).toBe("form-action 'self'");
    expect(prod).not.toContain("*");
  });
});

describe("nonce-based CSP requires dynamic rendering", () => {
  it("the root layout opts the whole route tree out of static prerendering", () => {
    // Next.js injects the nonce while server-rendering a request. A page
    // prerendered at build time has no request, so its script tags carry no
    // nonce - and 'strict-dynamic' means a script without the nonce does not
    // run at all. Before this was set, /login was prerendered and therefore
    // completely inert in production.
    const layout = readFileSync(resolve(__dirname, "app/layout.tsx"), "utf8");
    expect(layout).toMatch(/export const dynamic\s*=\s*["']force-dynamic["']/);
  });
});

describe("public paths (no session cookie needed)", () => {
  it("lets an invitee open the invitation page before they have an account", () => {
    expect(isPublicPath("/invitations/accept")).toBe(true);
    expect(isPublicPath("/login")).toBe(true);
  });

  it("lets a customer's owner open their activation link before they have a password", () => {
    expect(isPublicPath("/owner-invitations/accept")).toBe(true);
    expect(isPublicPath("/owner-invitations")).toBe(false);
    expect(isPublicPath("/owner-invitations/acceptx")).toBe(false);
  });

  it("keeps everything else behind the session check, including look-alike paths", () => {
    for (const path of ["/", "/o/acme", "/o/acme/settings/members", "/invitations", "/invitations/acceptx", "/billing/test-checkout", "/admin"]) {
      expect(isPublicPath(path), path).toBe(false);
    }
  });
});
