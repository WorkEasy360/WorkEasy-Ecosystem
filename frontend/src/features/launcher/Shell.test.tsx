import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { Shell } from "@/features/launcher/Shell";

const replace = vi.fn();
let pathname = "/o/acme/dashboard";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ orgSlug: "acme" }),
  usePathname: () => pathname,
}));

const ACCESS = {
  organization: { organization_id: "o1", slug: "acme", display_name: "Acme", status: "active", write_allowed: true },
  membership: { membership_id: "m1", is_owner: false, roles: ["member"] },
  permissions: ["platform.organization.view"],
  console: "member",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** A fetch whose session reply is held back until `releaseSession()`. `brand` is
 * what the organization's own brand lookup answers (none, by default). */
function heldSessionFetch(session: unknown, access: { status: number; body: unknown }, brand: unknown = null) {
  const seen: string[] = [];
  let releaseSession: () => void = () => undefined;
  const sessionGate = new Promise<void>((resolve) => {
    releaseSession = resolve;
  });
  global.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = (typeof input === "string" ? input : input.toString()).split("?")[0];
    seen.push(path);
    if (path === "/api/v1/auth/session") {
      await sessionGate;
      return json(200, session);
    }
    if (path === "/api/v1/orgs/acme/access") return json(access.status, access.body);
    if (path === "/api/v1/orgs/acme/brand") return json(200, { brand });
    throw new Error(`unexpected request ${path}`);
  }) as typeof fetch;
  return { seen, releaseSession: () => releaseSession() };
}

describe("the organization shell (2026-10 audit: no session -> access waterfall)", () => {
  beforeEach(() => {
    replace.mockReset();
    pathname = "/o/acme/dashboard";
  });

  it("asks for access while the session is still loading, then renders", async () => {
    const { seen, releaseSession } = heldSessionFetch(
      { authenticated: true, user_id: "u1", email: "ada@example.com", name: "Ada Lovelace" },
      { status: 200, body: ACCESS },
    );
    render(
      <Shell>
        <h1>Dashboard</h1>
      </Shell>,
    );

    await waitFor(() => expect(seen).toContain("/api/v1/orgs/acme/access"));
    // Still waiting on the session: nothing below the shell has rendered.
    expect(screen.getByText("Loading Workeasy360…")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Dashboard" })).toBeNull();

    releaseSession();
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("sends a signed-out visitor to sign-in, never showing the access answer", async () => {
    const { releaseSession } = heldSessionFetch(
      { authenticated: false },
      { status: 404, body: { code: "not_found", detail: "Not found." } },
    );
    render(
      <Shell>
        <h1>Dashboard</h1>
      </Shell>,
    );
    releaseSession();

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText(/don.t have access to this organization/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Dashboard" })).toBeNull();
  });
});

describe("a white-label customer's console", () => {
  const SESSION = { authenticated: true, user_id: "u1", email: "ada@example.com", name: "Ada Lovelace" };
  const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;

  const ownBrand = (portal: Record<string, unknown> = {}, over: Record<string, unknown> = {}) => ({
    brand_name: "ABC Business Cloud",
    accent_color: "#336699",
    support_email: "help@abc.example",
    support_url: "https://abc.example/support",
    logo_url: null,
    portal: {
      short_name: "ABC",
      support_name: "Sam from ABC",
      support_phone: "+44 20 7946 0000",
      website_url: "https://abc.example",
      welcome_message: "Welcome to ABC",
      footer_text: "ABC Ltd, 1 High Street",
      login_show_welcome: true,
      login_show_support: true,
      ...portal,
    },
    ...over,
  });

  async function openShell(brand: unknown) {
    const { releaseSession } = heldSessionFetch(SESSION, { status: 200, body: ACCESS }, brand);
    const view = render(
      <Shell>
        <h1>Dashboard</h1>
      </Shell>,
    );
    releaseSession();
    await screen.findByRole("heading", { name: "Dashboard" });
    return view;
  }

  beforeEach(() => {
    replace.mockReset();
    pathname = "/o/acme/dashboard";
  });

  it("names the header by the short name when the partner set one", async () => {
    await openShell(ownBrand());
    const header = screen.getByRole("banner");
    await waitFor(() => expect(within(header).getByText("ABC")).toBeInTheDocument());
    expect(within(header).queryByText("ABC Business Cloud")).not.toBeInTheDocument();
  });

  it("names the header by the brand name when there is no short name", async () => {
    await openShell(ownBrand({ short_name: "" }));
    await waitFor(() => expect(within(screen.getByRole("banner")).getByText("ABC Business Cloud")).toBeInTheDocument());
  });

  it("shows the footer text, the support details and the website, as text and safe links", async () => {
    await openShell(ownBrand());
    expect(await screen.findByText("ABC Ltd, 1 High Street")).toBeInTheDocument();
    expect(screen.getByText("Sam from ABC")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "help@abc.example" })).toHaveAttribute("href", "mailto:help@abc.example");
    expect(screen.getByRole("link", { name: "+44 20 7946 0000" })).toHaveAttribute("href", "tel:+442079460000");
    const support = screen.getByRole("link", { name: "Support page" });
    expect(support).toHaveAttribute("href", "https://abc.example/support");
    expect(support).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Visit our website" })).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("greets the customer on the dashboard, even when the sign-in page does not show the message", async () => {
    await openShell(ownBrand({ login_show_welcome: false }));
    expect(await screen.findByText("Welcome to ABC")).toBeInTheDocument();
  });

  it("keeps the welcome message to the dashboard, and the footer on every page", async () => {
    pathname = "/o/acme/apps";
    await openShell(ownBrand());
    expect(await screen.findByText("ABC Ltd, 1 High Street")).toBeInTheDocument();
    expect(screen.queryByText("Welcome to ABC")).not.toBeInTheDocument();
  });

  it("draws nothing extra for an organization with no white-label partner", async () => {
    await openShell(null);
    expect(within(screen.getByRole("banner")).getByText("Workeasy360")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Visit our website" })).not.toBeInTheDocument();
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
  });

  it("copes with a brand from before the portal existed", async () => {
    await openShell({ brand_name: "Old Brand", accent_color: "", support_email: "", support_url: "", logo_url: null });
    await waitFor(() => expect(within(screen.getByRole("banner")).getByText("Old Brand")).toBeInTheDocument());
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
  });

  it("draws hostile words as text, never as markup or an unsafe link", async () => {
    const { container } = await openShell(
      ownBrand({ footer_text: EVIL, welcome_message: EVIL, support_name: EVIL, website_url: "javascript:alert(1)" }, { support_url: "javascript:alert(1)" }),
    );
    await waitFor(() => expect(container.textContent).toContain("<img"));
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelector("a[href^='javascript:']")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});

describe("the seven-menu sidebar and top bar (2026-11-11)", () => {
  const SESSION = { authenticated: true, user_id: "u1", email: "ada@example.com", name: "Ada Lovelace" };
  const withPermissions = (...permissions: string[]) => ({ ...ACCESS, permissions: ["platform.organization.view", ...permissions] });

  async function openAt(path: string, access: unknown) {
    pathname = path;
    const { releaseSession } = heldSessionFetch(SESSION, { status: 200, body: access });
    render(
      <Shell>
        <h1>Page</h1>
      </Shell>,
    );
    releaseSession();
    await screen.findByRole("heading", { name: "Page" });
  }

  const sidebar = () => screen.getByRole("navigation", { name: "User Admin" });

  beforeEach(() => replace.mockReset());

  it("opens an old address under its menu, with the entry's tabs above the page", async () => {
    await openAt("/o/acme/settings/invitations", withPermissions("platform.member.view", "platform.member.invite"));
    const nav = sidebar();
    // The menu holding the page is open, and its entry is the current page.
    expect(within(nav).getByRole("button", { name: "Organization & Team" })).toHaveAttribute("aria-expanded", "true");
    expect(within(nav).getByRole("link", { name: "Members" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Organization" })).not.toHaveAttribute("aria-current");
    const tabs = screen.getByRole("navigation", { name: "Sections" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Members", "Invitations"]);
    expect(within(tabs).getByRole("link", { name: "Invitations" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Members" })).toHaveAttribute("href", "/o/acme/settings/members");
  });

  it("shows a plain member the seven menus, and no tab strip for a single page", async () => {
    await openAt("/o/acme/settings/organization", ACCESS);
    expect(screen.queryByRole("navigation", { name: "Sections" })).toBeNull();
    const nav = sidebar();
    expect(within(nav).getByRole("link", { name: "Organization & Team" })).toHaveAttribute("aria-current", "page");
    // No billing permission: the menus are still there, leading to pages that say who can help.
    expect(within(nav).getByRole("link", { name: "Billing & Subscriptions" })).toHaveAttribute("href", "/o/acme/settings/billing");
    expect(within(nav).getByRole("link", { name: "Wallets & Credits" })).toHaveAttribute("href", "/o/acme/wallets");
    expect(within(nav).getByRole("button", { name: "Settings" })).toHaveAttribute("aria-expanded", "false");
    expect(within(nav).queryByRole("link", { name: "Activity History" })).toBeNull();
    // The Store card at the foot of the menu.
    expect(within(nav).getByRole("link", { name: /Visit WorkEasy360 Store/ })).toHaveAttribute("href", "/o/acme/store");
  });

  it("marks only Account & Security current on its page, not Organization Security", async () => {
    await openAt("/o/acme/settings/security", withPermissions("platform.security.view"));
    const nav = sidebar();
    expect(within(nav).getByRole("link", { name: "Account & Security" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Organization Security" })).not.toHaveAttribute("aria-current");
    const tabs = screen.getByRole("navigation", { name: "Sections" });
    expect(within(tabs).getByRole("link", { name: "Password & Sessions" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Sign-in Preferences" })).not.toHaveAttribute("aria-current");
  });

  it("puts the organization, search, notifications, help and the account in the top bar", async () => {
    await openAt("/o/acme/dashboard", ACCESS);
    const header = screen.getByRole("banner");
    // One organization (the list could not be read here): named, with nothing to choose.
    expect(within(header).getByText("Acme")).toBeInTheDocument();
    expect(within(header).getByRole("button", { name: /^Search products, subscriptions, invoices, tickets…\s*\(Ctrl\+K\)$/ })).toBeInTheDocument();
    expect(within(header).getByRole("button", { name: /^Notifications/ })).toBeInTheDocument();
    expect(within(header).getByRole("link", { name: "Help" })).toHaveAttribute("href", "/o/acme/help");
    expect(within(header).getByRole("button", { name: /^Account menu for Ada Lovelace Member · Acme/ })).toBeInTheDocument();
  });
});

describe("the account menu (unified login, ADR-066)", () => {
  const SESSION = { authenticated: true, user_id: "u1", email: "rahul@abccompany.com", name: "Rahul Sharma" };
  const replaceLocation = vi.fn();
  let calls: string[] = [];

  function serve(switchReply: { status: number; body: unknown }) {
    calls = [];
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = (typeof input === "string" ? input : input.toString()).split("?")[0];
      calls.push(`${(init?.method ?? "GET").toUpperCase()} ${path}`);
      if (path === "/api/v1/auth/session") return json(200, SESSION);
      if (path === "/api/v1/orgs/acme/access") return json(200, ACCESS);
      if (path === "/api/v1/orgs/acme/brand") return json(200, { brand: null });
      if (path === "/api/v1/auth/switch-account") return json(switchReply.status, switchReply.body);
      if (path === "/api/v1/auth/logout") return json(200, { ok: true });
      return json(404, {});
    }) as typeof fetch;
  }

  beforeEach(() => {
    pathname = "/o/acme/dashboard";
    replace.mockReset();
    replaceLocation.mockReset();
    vi.stubGlobal("location", { ...window.location, replace: replaceLocation, assign: vi.fn() });
  });

  async function openMenu() {
    render(
      <Shell>
        <h1>Page</h1>
      </Shell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.click(screen.getByRole("button", { name: /^Account menu for Rahul Sharma/ }));
    return fireEvent;
  }

  it("offers profile, switch organization, switch account, security and logout", async () => {
    serve({ status: 200, body: { redirect_url: "/login?switched=1" } });
    await openMenu();
    const items = screen.getAllByRole("menuitem").map((m) => m.textContent?.trim());
    expect(items).toEqual(["My Profile", "Switch Organization", "Switch Account", "Account & Security", "Logout"]);
    expect(screen.getByRole("menuitem", { name: "Switch Organization" })).toHaveAttribute("href", "/organizations/select");
    // The header names the current organization.
    expect(screen.getByRole("button", { name: /^Account menu for Rahul Sharma Member · Acme/ })).toBeInTheDocument();
  });

  it("switches account through the server, forgets this person's browser state and loads the next page afresh", async () => {
    serve({ status: 200, body: { redirect_url: "https://auth.example.com/switch-account#handle=h" } });
    localStorage.setItem("w360.admin.recent", "[1]");
    localStorage.setItem("w360.columns.members", "[\"name\"]");
    sessionStorage.setItem("w360.store-org", "acme");
    const fireEvent = await openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Switch Account" }));
    await waitFor(() => expect(replaceLocation).toHaveBeenCalledWith("https://auth.example.com/switch-account#handle=h"));
    expect(calls).toContain("POST /api/v1/auth/switch-account");
    expect(localStorage.getItem("w360.admin.recent")).toBeNull();
    expect(sessionStorage.getItem("w360.store-org")).toBeNull();
    // A layout choice names nobody, so it stays.
    expect(localStorage.getItem("w360.columns.members")).toBe("[\"name\"]");
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps everything and says so when the switch did not complete", async () => {
    serve({ status: 500, body: { code: "error" } });
    sessionStorage.setItem("w360.store-org", "acme");
    const fireEvent = await openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Switch Account" }));
    expect(await screen.findByText(/You are still signed in/)).toBeInTheDocument();
    expect(replaceLocation).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("w360.store-org")).toBe("acme");
  });

  it("logs out with a full page load to the sign-in page", async () => {
    serve({ status: 200, body: { redirect_url: "/login?switched=1" } });
    const fireEvent = await openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Logout" }));
    await waitFor(() => expect(replaceLocation).toHaveBeenCalledWith("/login"));
    expect(calls).toContain("POST /api/v1/auth/logout");
  });
});
