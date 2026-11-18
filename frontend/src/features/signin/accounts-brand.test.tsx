import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { LoginScreen } from "@/features/auth/LoginScreen";
import { PortalsProvider } from "@/lib/portalContext";
import { portalForHost, readPortalOrigins, type PortalOrigins } from "@/lib/portals";
import { ACCOUNTS_NAME, AccountsBrand, accountsTitle } from "./AccountsBrand";

/**
 * ADR-067: the central sign-in host is WorkEasy360 Accounts
 * (accounts.workeasy360.com). The pages it serves carry that name; every other
 * host's sign-in keeps its own. The frontend never knows the previous name
 * (auth.workeasy360.com): the edge sends that host to the API, which forwards
 * every browser here.
 */

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/login",
}));

const ORIGINS: PortalOrigins = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://accounts.workeasy360.com",
  store: "",
};

function mockSignedOut() {
  global.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.startsWith("/api/v1/auth/session")) {
      return new Response(JSON.stringify({ authenticated: false }), { status: 200, headers: { "content-type": "application/json" } });
    }
    return new Response(JSON.stringify({ brand: null }), { status: 404, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
}

async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
  expect(violations.map((v: Result) => v.id)).toEqual([]);
}

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
  mockSignedOut();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WorkEasy360 Accounts", () => {
  it("is the name of the central sign-in host, and of its page titles", () => {
    expect(ACCOUNTS_NAME).toBe("WorkEasy360 Accounts");
    expect(accountsTitle()).toBe("WorkEasy360 Accounts");
    expect(accountsTitle("Sign out")).toBe("Sign out - WorkEasy360 Accounts");
  });

  it("the accounts host is the central sign-in portal, read from W360_AUTH_ORIGIN", () => {
    const origins = readPortalOrigins({
      W360_ADMIN_ORIGIN: ORIGINS.admin,
      W360_PARTNER_ORIGIN: ORIGINS.partner,
      W360_APP_ORIGIN: ORIGINS.app,
      W360_AUTH_ORIGIN: ORIGINS.auth,
    });
    expect(origins).not.toBeNull();
    expect(portalForHost("accounts.workeasy360.com", origins!)).toBe("auth");
    // The previous name is no portal of the frontend's: it never reaches it.
    expect(portalForHost("auth.workeasy360.com", origins!)).toBeNull();
  });

  it("its mark reads as WorkEasy360 Accounts, for sight and for assistive technology", () => {
    const { container } = render(<AccountsBrand />);
    expect(screen.getByText("Accounts")).toBeInTheDocument();
    const mark = container.querySelector("[data-accounts-brand]");
    expect(mark).not.toBeNull();
    // The logo's images are decorative; its name is text (visually hidden).
    expect(mark!.querySelector(".w360-visually-hidden")?.textContent).toBe("Workeasy360");
    expect(mark!.textContent).toBe("Workeasy360Accounts");
    expect([...mark!.querySelectorAll("img")].every((img) => img.getAttribute("alt") === "")).toBe(true);
  });

  it("a product sign-in on the accounts host is headed WorkEasy360 Accounts", async () => {
    query = "resume=abc";
    const { container } = render(
      <PortalsProvider origins={ORIGINS}>
        <LoginScreen portal="auth" />
      </PortalsProvider>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "WorkEasy360 Accounts" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Sign in" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("the same product sign-in without portals (one host) keeps the platform's name", async () => {
    query = "resume=abc";
    render(<LoginScreen />);
    expect(await screen.findByRole("heading", { level: 1, name: "WorkEasy360" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "WorkEasy360 Accounts" })).not.toBeInTheDocument();
  });
});
