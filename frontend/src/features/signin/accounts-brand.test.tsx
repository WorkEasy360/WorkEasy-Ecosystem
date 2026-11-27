import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { LoginScreen } from "@/features/auth/LoginScreen";
import { SignupScreen } from "@/features/auth/SelfService";
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

  it("a product sign-in on the accounts host is the Accounts Login card (2026-11-27 reference image)", async () => {
    query = "resume=abc";
    const { container } = render(
      <PortalsProvider origins={ORIGINS}>
        <LoginScreen portal="auth" accountsHost="accounts.workeasy360.com" />
      </PortalsProvider>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Account Login" })).toBeInTheDocument();
    expect(screen.getByText("Sign in once to access your authorized WorkEasy360 products.")).toBeInTheDocument();
    // Log In | Sign Up: the current one is the page itself (no link that could drop ?resume=).
    const modes = screen.getByRole("navigation", { name: "Log in or sign up" });
    expect(within(modes).getByText("Log In")).toHaveAttribute("aria-current", "page");
    expect(within(modes).queryByRole("link", { name: "Log In" })).not.toBeInTheDocument();
    expect(within(modes).getByRole("link", { name: "Sign Up" })).toHaveAttribute("href", "/signup");
    expect(screen.getByLabelText("Email Address")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("checkbox", { name: "Remember me" })).not.toBeChecked();
    expect(screen.getByRole("link", { name: "Forgot Password?" })).toHaveAttribute("href", "/forgot-password");
    expect(screen.getByRole("button", { name: "Log In to WorkEasy360" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create WorkEasy360 Account" })).toHaveAttribute("href", "/signup");
    // The card's foot and the left panel's four menus - labels, never links.
    for (const label of ["One Account", "Secure Access", "All Products", "accounts.workeasy360.com"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    for (const menu of ["My Profile", "Login & Security", "Devices & Sessions", "Settings"]) {
      expect(screen.getByText(menu)).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: menu })).not.toBeInTheDocument();
    }
    await expectNoViolations(container);
  });

  it("sends Remember me with the sign-in, then continues the product sign-in through the server", async () => {
    query = "resume=abc";
    const bodies: unknown[] = [];
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      if (url.startsWith("/api/v1/auth/session")) return json({ authenticated: false });
      if (url.startsWith("/api/v1/auth/login")) {
        bodies.push(JSON.parse(String(init?.body)));
        return json({ authenticated: true, user_id: "u1", email: "ravi@example.com" });
      }
      return json({}, 404);
    }) as typeof fetch;
    vi.stubGlobal("location", { ...window.location, href: "http://localhost/login?resume=abc", assign: vi.fn(), replace: vi.fn() });
    render(
      <PortalsProvider origins={ORIGINS}>
        <LoginScreen portal="auth" />
      </PortalsProvider>,
    );
    fireEvent.change(await screen.findByLabelText("Email Address"), { target: { value: "ravi@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long passphrase" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Remember me" }));
    fireEvent.click(screen.getByRole("button", { name: "Log In to WorkEasy360" }));
    await waitFor(() => expect(window.location.href).toBe("/api/v1/integrations/oauth/resume?resume=abc"));
    expect(bodies).toEqual([{ email: "ravi@example.com", password: "a long passphrase", remember: true }]); // pragma: allowlist secret
  });

  it("a product asking for the password again says so, without the Log In / Sign Up switch", async () => {
    query = "resume=abc&reauth=1";
    render(
      <PortalsProvider origins={ORIGINS}>
        <LoginScreen portal="auth" />
      </PortalsProvider>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Confirm it's you" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Log in or sign up" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm and continue" })).toBeInTheDocument();
  });

  it("sign-up on Accounts is the Sign Up half of the same card, and says an organization is optional", async () => {
    const { container } = render(<SignupScreen accounts accountsHost="accounts.workeasy360.com" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Create WorkEasy360 Account" })).toBeInTheDocument();
    const modes = screen.getByRole("navigation", { name: "Log in or sign up" });
    expect(within(modes).getByText("Sign Up")).toHaveAttribute("aria-current", "page");
    expect(within(modes).getByRole("link", { name: "Log In" })).toHaveAttribute("href", "/login");
    expect(screen.getByText(/Invited by a team\? Open the link in your invitation instead\./)).toBeInTheDocument();
    expect(screen.getByLabelText(/Company name \(optional\)/)).not.toBeRequired();
    expect(screen.queryByText("Create Organization Account")).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("a partner's branded sign-in on the accounts host keeps the plain page, without the WorkEasy360 story", async () => {
    query = "resume=abc&brand=crm.partner.example";
    render(
      <PortalsProvider origins={ORIGINS}>
        <LoginScreen portal="auth" />
      </PortalsProvider>,
    );
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Account Login" })).not.toBeInTheDocument();
    expect(screen.queryByText("One Account")).not.toBeInTheDocument();
  });

  it("the same product sign-in without portals (one host) keeps the platform's name", async () => {
    query = "resume=abc";
    render(<LoginScreen />);
    expect(await screen.findByRole("heading", { level: 1, name: "WorkEasy360" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "WorkEasy360 Accounts" })).not.toBeInTheDocument();
  });
});
