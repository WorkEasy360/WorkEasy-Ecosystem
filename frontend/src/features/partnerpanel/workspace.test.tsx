import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PortalChooser, openingPath } from "@/features/controlplane/PartnerScreens";
import { expectNoViolations, mockApi, ok, SIGNED_IN, type Call, type Reply } from "@/features/controlplane/testSupport";
import { Workspace } from "./WorkspaceShell";

/**
 * A workspace (ADR-047) is opened by the server's answer, drawn from the
 * person's permissions, and refused for everything else. These tests mount the
 * real shell over a mocked API and check each of those: the sidebar, the refusal
 * for an account without the workspace, a page the workspace does not serve, a
 * page the person's permissions do not open, the aliases, the switcher, the
 * status banner and where the portal chooser sends someone.
 */

let pathname = "/partner/affiliate/dashboard";
const nav = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => pathname,
}));

const ID = "22222222-2222-2222-2222-222222222222";

const perm = (...keys: string[]) => keys.map((k) => `platform.partner_${k}`);

const AFFILIATE_PERMISSIONS = perm(
  "dashboard.view",
  "profile.view",
  "profile.manage",
  "member.view",
  "member.manage",
  "product.view",
  "referral.view",
  "referral.create",
  "commission.view",
  "payout.view",
);

const RESELLER_PERMISSIONS = perm(
  "dashboard.view",
  "profile.view",
  "member.view",
  "product.view",
  "customer.view",
  "pricing.view",
  "commission.view",
  "payout.view",
  "subpartner.view",
);

function partner(over: Record<string, unknown> = {}) {
  return {
    partner_id: ID,
    code: "acme",
    display_name: "Acme Partners",
    status: "active",
    role: "owner",
    role_label: "Partner Owner",
    is_admin: true,
    capabilities: [],
    workspaces: [{ key: "affiliate", label: "Affiliate" }],
    default_experience: "affiliate",
    landing: "affiliate",
    permissions: AFFILIATE_PERMISSIONS,
    ...over,
  };
}

const portal = (...partners: unknown[]): Reply => ok({ partners });

const OVERVIEW = {
  partner: { id: ID, name: "Acme Partners", currency: "INR", status: "active" },
  range: { key: "30d", from: "2026-09-22T00:00:00Z", to: "2026-10-22T00:00:00Z" },
  kpis: { clicks: 4, signups: null, conversions: 2, conversion_rate_bp: 5000 },
  primary_link: null,
  links: [],
  campaigns: [],
  campaign_presets: ["General"],
  series: [],
  money: null,
  recent: { conversions: [], payouts: null },
  not_implemented: { affiliate_signups: "No separate sign-up step is recorded." },
};

const DASHBOARD = {
  partner: { id: ID, name: "Acme Partners", currency: "INR", status: "active" },
  range: { key: "this_month", from: "2026-10-01T00:00:00Z", to: "2026-10-22T00:00:00Z" },
  previous: { from: "2026-09-09T00:00:00Z", to: "2026-10-01T00:00:00Z", new_customers: null, sales: null },
  products: { active: 2, sales: null },
  tickets: { open: 0, breached: 0 },
  customers: null,
  sales: null,
  money: null,
  charts: null,
  sub_partners: null,
  recent: { customers: null, commissions: null, payouts: null },
  not_implemented: {},
};

const UPGRADE = {
  current: "affiliate",
  eligible: ["reseller", "white_label"],
  can_request: true,
  open: null,
  recent: [],
  steps: { reseller: [], white_label: [] },
  products: [],
  checklist: [],
  fees: { reseller: { amount_minor: 2000000, currency: "INR" }, white_label: { amount_minor: 10000000, currency: "INR" } },
};

const NOTIFICATIONS = {
  view: "unread",
  items: [],
  counts: { all: 0, unread: 0, important: 0, important_unread: 0 },
  window: 100,
  truncated: false,
  not_implemented: {},
};

/** What the unified dashboard (2026-11-03) reads, every level's sections. */
const dashboardRoutes = (over: Record<string, Reply[]> = {}): Record<string, Reply[]> => ({
  [`GET /api/v1/partner/${ID}/dashboard`]: [ok(DASHBOARD)],
  [`GET /api/v1/partner/${ID}/affiliate/overview`]: [ok(OVERVIEW)],
  [`GET /api/v1/partner/${ID}/upgrade`]: [ok(UPGRADE)],
  [`GET /api/v1/partner/${ID}/notifications`]: [ok(NOTIFICATIONS)],
  [`GET /api/v1/partner/${ID}/products`]: [ok({ products: [], not_implemented: {} })],
  [`GET /api/v1/partner/${ID}/white-label/status`]: [ok({ brand: null, domain: null, storefront: null })],
  ...over,
});

const FOURTEEN = [
  "Dashboard",
  "Affiliate",
  "Reseller",
  "White Label",
  "Product Store",
  "Customers",
  "Sales & Orders",
  "Commission & Earnings",
  "Earnings & Payouts",
  "Communication",
  "Staff",
  "Helpdesk",
  "Reports & Analytics",
  "Settings",
];

function mount(slug: string, segments: string[] | undefined, routes: Record<string, Reply[]>): Call[] {
  const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], ...routes });
  render(<Workspace slug={slug} segments={segments} />);
  return calls;
}

const sidebar = () => within(screen.getByRole("navigation", { name: /workspace$/ })).getAllByRole("link").map((a) => a.textContent);
/** Every drawn entry, open (a link) or locked (a button), in order. */
const entryLabels = () =>
  [...screen.getByRole("navigation", { name: /workspace$/ }).querySelectorAll("li > a, li > button")].map((el) =>
    (el.textContent ?? "").replace(" (locked - upgrade to unlock)", ""),
  );
const lockedLabels = () =>
  within(screen.getByRole("navigation", { name: /workspace$/ }))
    .queryAllByRole("button")
    .map((b) => (b.textContent ?? "").replace(" (locked - upgrade to unlock)", ""));


beforeEach(() => {
  vi.restoreAllMocks();
  nav.replace.mockReset();
  nav.push.mockReset();
  pathname = "/partner/affiliate/dashboard";
});

describe("the affiliate workspace", () => {
  it("draws the fourteen entries: open by permission, locked above its level (2026-11-03)", async () => {
    mount("affiliate", ["dashboard"], { "GET /api/v1/partner/portal": [portal(partner())], ...dashboardRoutes() });
    expect(await screen.findByRole("heading", { level: 1, name: "Partner Dashboard" })).toBeInTheDocument();
    expect(entryLabels()).toEqual(FOURTEEN);
    expect(lockedLabels()).toEqual(["Reseller", "White Label", "Customers", "Sales & Orders", "Communication", "Staff"]);
    expect(sidebar()).toEqual([
      "Dashboard",
      "Affiliate",
      "Product Store",
      "Commission & Earnings",
      "Earnings & Payouts",
      "Helpdesk",
      "Reports & Analytics",
      "Settings",
    ]);
    const links = within(screen.getByRole("navigation", { name: /workspace$/ })).getAllByRole("link");
    expect(links[0]).toHaveAttribute("href", "/partner/affiliate/dashboard");
    expect(links[0]).toHaveAttribute("aria-current", "page");
    // The top bar: the level and the persistent Upgrade Plan action.
    expect(screen.getByText("Partner Level:").parentElement).toHaveTextContent("Affiliate Partner");
    expect(screen.getByRole("button", { name: /Upgrade Plan/ })).toBeInTheDocument();
  });

  it("a locked entry opens the upgrade dialog, never a page", async () => {
    const calls = mount("affiliate", ["dashboard"], { "GET /api/v1/partner/portal": [portal(partner())], ...dashboardRoutes() });
    await screen.findByRole("heading", { level: 1, name: "Partner Dashboard" });
    fireEvent.click(within(screen.getByRole("navigation", { name: /workspace$/ })).getByRole("button", { name: /^Customers/ }));
    const dialog = await screen.findByRole("dialog", { name: "Unlock Customers" });
    expect(dialog).toHaveTextContent("Customers is part of the Reseller level");
    expect(await within(dialog).findByRole("link", { name: "Upgrade to Reseller" })).toHaveAttribute("href", "/partner/affiliate/upgrade/reseller");
    expect(dialog).toHaveTextContent("one-time");
    expect(calls.some((c) => c.url.includes("/customers"))).toBe(false);
    fireEvent.click(within(dialog).getAllByRole("button", { name: "Close" })[0]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("a consolidated entry draws its pages as one row of tabs, with the page's counts (2026-11-02)", async () => {
    pathname = "/partner/affiliate/store-console/requests";
    const STORE = {
      kind: "promote",
      products: [],
      requests: [],
      counts: { approved: 1, explore: 3, pending: 2 },
      can_request: true,
      cannot_request_reason: null,
      channels: {},
      prefill: {},
    };
    mount("affiliate", ["store-console", "requests"], {
      "GET /api/v1/partner/portal": [portal(partner())],
      [`GET /api/v1/partner/${ID}/affiliate/store`]: [ok(STORE), ok(STORE)],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Store Console" })).toBeInTheDocument();
    // One row of tabs - the Store Console's own is folded into it.
    const rows = screen.getAllByRole("navigation", { name: "Sections" });
    expect(rows).toHaveLength(1);
    await waitFor(() =>
      expect(within(rows[0]).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("aria-current")])).toEqual([
        ["Explore Products", "/partner/affiliate/store", null],
        ["Approved / Promoting (1)", "/partner/affiliate/store-console", null],
        ["Request Access (3)", "/partner/affiliate/store-console/explore", null],
        ["My Requests (2 pending)", "/partner/affiliate/store-console/requests", "page"],
      ]),
    );
    // The sidebar's Product Store is the current entry.
    const current = within(screen.getByRole("navigation", { name: /workspace$/ })).getByRole("link", { name: "Product Store" });
    expect(current).toHaveAttribute("aria-current", "page");
  });

  it("an old address opens its page under the new entry, and a tab follows its permission (2026-11-02)", async () => {
    pathname = "/partner/affiliate/affiliates/payouts";
    mount("affiliate", ["affiliates", "payouts"], {
      "GET /api/v1/partner/portal": [portal(partner({ permissions: perm("payout.view") }))],
      [`GET /api/v1/partner/${ID}/payouts`]: [ok({ results: [], next_cursor: null, has_more: false, balances: [], not_implemented: {} })],
    });
    expect(await screen.findByRole("link", { name: "Earnings & Payouts" })).toHaveAttribute("aria-current", "page");
    // Its tabs: the wallet and the payout history (current here).
    const tabs = screen.getByRole("navigation", { name: "Sections" });
    expect(within(tabs).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("aria-current")])).toEqual([
      ["Earnings", null],
      ["Payout History", "page"],
    ]);
    expect(sidebar()).toEqual(["Earnings & Payouts"]);
  });

  it("a locked page shows what unlocks it - whatever the person's permissions - and loads nothing", async () => {
    // Even a member holding the customer view is not given customer pages at this level.
    const calls = mount("affiliate", ["users"], {
      "GET /api/v1/partner/portal": [portal(partner({ permissions: [...AFFILIATE_PERMISSIONS, ...perm("customer.view")] }))],
    });
    expect(await screen.findByRole("heading", { level: 1, name: /Customers is part of the Reseller level/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "See upgrade options" })).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual(["GET /api/v1/auth/session", "GET /api/v1/partner/portal"]);
  });

  it("refuses a page the workspace does not serve", async () => {
    const calls = mount("affiliate", ["no-such-page"], { "GET /api/v1/partner/portal": [portal(partner())] });
    expect(await screen.findByRole("heading", { name: "That page is not part of the Affiliate workspace." })).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual(["GET /api/v1/auth/session", "GET /api/v1/partner/portal"]);
  });

  it("an upgraded partner's old affiliate address opens the same page at its own level", async () => {
    pathname = "/partner/affiliate/referral-links";
    mount("affiliate", ["referral-links"], {
      "GET /api/v1/partner/portal": [
        portal(
          partner({
            workspaces: [
              { key: "reseller", label: "Reseller" },
              { key: "affiliate", label: "Affiliate" },
            ],
            level: "reseller",
            permissions: [...RESELLER_PERMISSIONS, ...perm("referral.view", "referral.create")],
          }),
        ),
      ],
    });
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/partner/reseller/referral-links"));
  });

  it("refuses a page the person's permissions do not open, before it asks for any data", async () => {
    const calls = mount("affiliate", ["referral-links"], {
      "GET /api/v1/partner/portal": [portal(partner({ permissions: perm("dashboard.view", "profile.view", "product.view") }))],
    });
    expect(await screen.findByRole("heading", { name: "You don't have access to this page." })).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/affiliate/overview"))).toBe(false);
  });
});

describe("a staff guest view (Super Admin → Guest login)", () => {
  it("opens in the partner's own workspace, says whose portal it is and leads back to the partner's admin page", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const guest = {
      partner_id: ID,
      reason: "Support ticket 42",
      expires_at: "2026-03-01T10:30:00Z",
      viewer: { name: "Sam Staff", email: "sam@workeasy360.com" },
    };
    const calls = mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(partner({ role: "viewer", is_admin: false, guest }))],
      ...dashboardRoutes(),
      "POST /api/v1/partner/guest-view/end": [ok({ ended: true, return_url: "/admin/partners/affiliate" })],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Partner Dashboard" })).toBeInTheDocument();
    const banner = screen.getByRole("status", { name: "Guest Mode" });
    expect(banner).toHaveTextContent("Guest Mode – Acme Partners (Affiliate). Read only");
    // Which partner, of which type, and who is looking.
    expect(banner).toHaveTextContent("viewing as Sam Staff");
    fireEvent.click(within(banner).getByRole("button", { name: "Exit Guest Mode" }));
    // Back to the Admin page Login as Guest was chosen on.
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/admin/partners/affiliate"));
    expect(calls.some((c) => c.key === "POST /api/v1/partner/guest-view/end")).toBe(true);
    vi.unstubAllGlobals();
  });

  it("never follows a way back that is not one of the Admin's partner pages", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const guest = { partner_id: ID, reason: "Support ticket 42", expires_at: "2026-03-01T10:30:00Z" };
    mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(partner({ role: "viewer", is_admin: false, guest }))],
      ...dashboardRoutes(),
      "POST /api/v1/partner/guest-view/end": [ok({ ended: true, return_url: "https://evil.example.com/admin/partners/all" })],
    });
    const banner = await screen.findByRole("status", { name: "Guest Mode" });
    fireEvent.click(within(banner).getByRole("button", { name: "Exit Guest Mode" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/admin/partners/${ID}`));
    vi.unstubAllGlobals();
  });
});

describe("exiting a guest view that cannot be ended", () => {
  it("stays on the page and says so instead of pretending it was left", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const guest = { partner_id: ID, reason: "Support ticket 42", expires_at: "2026-03-01T10:30:00Z" };
    mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(partner({ role: "viewer", is_admin: false, guest }))],
      ...dashboardRoutes(),
      "POST /api/v1/partner/guest-view/end": [{ status: 503, body: { code: "unavailable" } }],
    });
    const banner = await screen.findByRole("status", { name: "Guest Mode" });
    fireEvent.click(within(banner).getByRole("button", { name: "Exit Guest Mode" }));
    expect(await within(banner).findByRole("alert")).toHaveTextContent("Guest Mode could not be ended.");
    expect(assign).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("a workspace the account does not hold", () => {
  it("an affiliate asking for the reseller workspace is told so, and nothing is loaded", async () => {
    pathname = "/partner/reseller/users";
    const calls = mount("reseller", ["users"], { "GET /api/v1/partner/portal": [portal(partner())] });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("You don't have access to the Reseller workspace.");
    expect(within(alert).getByRole("link", { name: "Open your Affiliate workspace" })).toHaveAttribute("href", "/partner/affiliate/dashboard");
    // No sidebar, no customers, no second request of any kind.
    expect(screen.queryByRole("navigation", { name: /workspace$/ })).not.toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual(["GET /api/v1/auth/session", "GET /api/v1/partner/portal"]);
  });

  it("an account that belongs to no partner is sent to the chooser", async () => {
    mount("white-label", ["dashboard"], { "GET /api/v1/partner/portal": [portal()] });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("You don't have access to the White-label workspace.");
    expect(within(alert).getByRole("link", { name: "Go to your partner accounts" })).toHaveAttribute("href", "/partner");
  });

  it("an unknown workspace address renders nothing at all", () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN] });
    const { container } = render(<Workspace slug="distributor" segments={["dashboard"]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

const WHITE_LABEL_PERMISSIONS = perm(
  "dashboard.view",
  "profile.view",
  "member.view",
  "product.view",
  "customer.view",
  "pricing.view",
  "commission.view",
  "payout.view",
  "subpartner.view",
  "referral.view",
  "branding.view",
  "domain.view",
);

describe("the white-label workspace (2026-11-02)", () => {
  const whiteLabel = (over: Record<string, unknown> = {}) =>
    partner({
      workspaces: [{ key: "white_label", label: "White-label" }],
      default_experience: "white_label",
      landing: "white_label",
      permissions: WHITE_LABEL_PERMISSIONS,
      ...over,
    });
  const current = () =>
    within(screen.getByRole("navigation", { name: /workspace$/ }))
      .getAllByRole("link")
      .filter((a) => a.getAttribute("aria-current") === "page")
      .map((a) => a.textContent);

  it("Custom Domain is a tab of White Label, the only current entry on its page", async () => {
    pathname = "/partner/white-label/brand-settings/url-manager";
    mount("white-label", ["brand-settings", "url-manager"], { "GET /api/v1/partner/portal": [portal(whiteLabel())] });
    await waitFor(() => expect(current()).toEqual(["White Label"]));
    const tabs = screen.getByRole("navigation", { name: "Sections" });
    expect(within(tabs).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("aria-current")])).toEqual([
      ["Brand Settings", null],
      ["Website Branding", null],
      ["Custom Domain", "page"],
      ["Translations", null],
    ]);
  });

  it("nothing is locked for a white-label partner, and it is offered no upgrade", async () => {
    pathname = "/partner/white-label/users";
    mount("white-label", ["users"], { "GET /api/v1/partner/portal": [portal(whiteLabel())] });
    await waitFor(() => expect(current()).toEqual(["Customers"]));
    expect(lockedLabels()).toEqual([]);
    expect(entryLabels()).toEqual(FOURTEEN);
    expect(screen.getByText("Partner Level:").parentElement).toHaveTextContent("White Label Partner");
    expect(screen.queryByRole("button", { name: /Upgrade Plan/ })).toBeNull();
    const rows = screen.getAllByRole("navigation", { name: "Sections" });
    expect(rows).toHaveLength(1);
    expect(within(rows[0]).getAllByRole("link").map((a) => a.textContent)).toEqual(["Customers", "Subscriptions"]);
  });

  it("Website Branding is current on the setup page", async () => {
    pathname = "/partner/white-label/brand-settings/white-label-setup";
    mount("white-label", ["brand-settings", "white-label-setup"], { "GET /api/v1/partner/portal": [portal(whiteLabel())] });
    await waitFor(() => expect(current()).toEqual(["White Label"]));
    const tabs = screen.getByRole("navigation", { name: "Sections" });
    expect(within(tabs).getByRole("link", { name: "Website Branding" })).toHaveAttribute("aria-current", "page");
  });

  it("the referral programme pages are tabs of the Affiliate entry", async () => {
    pathname = "/partner/white-label/affiliates/settings";
    mount("white-label", ["affiliates", "settings"], { "GET /api/v1/partner/portal": [portal(whiteLabel())] });
    await waitFor(() => expect(current()).toEqual(["Affiliate"]));
    const tabs = screen.getByRole("navigation", { name: "Sections" });
    expect(within(tabs).getByRole("link", { name: "Programme Settings" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Promote Products" })).toHaveAttribute("href", "/partner/white-label/promote/store");
  });
});

describe("the reseller workspace", () => {
  const reseller = (over: Record<string, unknown> = {}) =>
    partner({ workspaces: [{ key: "reseller", label: "Reseller" }], default_experience: "reseller", landing: "reseller", permissions: RESELLER_PERMISSIONS, ...over });

  it("opens a friendly address as the page it names", async () => {
    pathname = "/partner/reseller/customers";
    mount("reseller", ["customers"], { "GET /api/v1/partner/portal": [portal(reseller())] });
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/partner/reseller/users"));
  });

  it("opens the workspace's root on its dashboard", async () => {
    mount("reseller", undefined, { "GET /api/v1/partner/portal": [portal(reseller())] });
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/partner/reseller/dashboard"));
  });

  it("does not offer the customer pages to a role without the customer view", async () => {
    mount("reseller", ["users"], {
      "GET /api/v1/partner/portal": [portal(reseller({ permissions: perm("dashboard.view", "profile.view", "pricing.view", "commission.view", "payout.view", "product.view") }))],
    });
    expect(await screen.findByRole("heading", { name: "You don't have access to this page." })).toBeInTheDocument();
    expect(sidebar()).not.toContain("Customers");
    expect(sidebar()).toContain("Commission & Earnings");
  });

  it("an upgraded affiliate works in one panel: the reseller one carries the affiliate programme (2026-11-03)", async () => {
    pathname = "/partner/reseller/promote/store-console/requests";
    const upgraded = reseller({
      workspaces: [
        { key: "reseller", label: "Reseller" },
        { key: "affiliate", label: "Affiliate" },
      ],
      permissions: [...RESELLER_PERMISSIONS, ...perm("referral.view", "referral.create")],
    });
    const STORE = { kind: "promote", products: [], requests: [], counts: { approved: 2, explore: 1, pending: 0 }, can_request: true, cannot_request_reason: null, channels: {}, prefill: {} };
    mount("reseller", ["promote", "store-console", "requests"], {
      "GET /api/v1/partner/portal": [portal(upgraded)],
      [`GET /api/v1/partner/${ID}/affiliate/store`]: [ok(STORE), ok(STORE)],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Store Console" })).toBeInTheDocument();
    // No second panel to switch to.
    expect(screen.queryByRole("navigation", { name: "Workspace" })).toBeNull();
    for (const label of ["Affiliate", "Reseller", "Product Store"]) expect(sidebar()).toContain(label);
    const currentEntry = within(screen.getByRole("navigation", { name: /workspace$/ })).getByRole("link", { name: "Affiliate" });
    expect(currentEntry).toHaveAttribute("aria-current", "page");
    // The promoting Store is the Affiliate entry's Promote Products tab; the reseller's Store sells.
    const tabs = screen.getByRole("navigation", { name: "Sections" });
    expect(within(tabs).getByRole("link", { name: "Promote Products" })).toHaveAttribute("aria-current", "page");
    const pages = await screen.findByRole("navigation", { name: "Pages" });
    await waitFor(() =>
      expect(within(pages).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(
        expect.arrayContaining([
          "/partner/reseller/promote/store-console",
          "/partner/reseller/promote/store-console/explore",
          "/partner/reseller/promote/store-console/requests",
        ]),
      ),
    );
  });

  it("a reseller that never promoted sees no affiliate programme", async () => {
    mount("reseller", ["dashboard"], { "GET /api/v1/partner/portal": [portal(reseller())], ...dashboardRoutes() });
    await waitFor(() => expect(sidebar()).toContain("Product Store"));
    // The Affiliate entry needs the referral view: not drawn, and not locked either.
    expect(sidebar()).not.toContain("Affiliate");
    expect(lockedLabels()).toEqual(["White Label"]);
  });

  it("the Reseller entry draws its tabs, and a pricing page keeps its own row under them", async () => {
    pathname = "/partner/reseller/pricing/rules";
    mount("reseller", ["pricing", "rules"], { "GET /api/v1/partner/portal": [portal(reseller())] });
    const tabs = await screen.findByRole("navigation", { name: "Sections" });
    expect(within(tabs).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("aria-current")])).toEqual([
      ["My Products", null],
      ["Wholesale Catalogue", null],
      ["My Requests", null],
      ["Product Settings", null],
      ["Pricing & Margins", "page"],
      ["Margin Calculator", null],
      ["Downline Partners", null],
    ]);
    const pages = await screen.findByRole("navigation", { name: "Pages" });
    expect(within(pages).getAllByRole("link").map((a) => a.textContent)).toEqual(["Currency", "Package & Pricing", "Pricing Rule"]);
    expect(within(screen.getByRole("navigation", { name: /workspace$/ })).getByRole("link", { name: "Reseller" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("Commission & Earnings keeps the balance and disputes beside the commissions", async () => {
    pathname = "/partner/reseller/payments/balance";
    mount("reseller", ["payments", "balance"], { "GET /api/v1/partner/portal": [portal(reseller())] });
    const tabs = await screen.findByRole("navigation", { name: "Sections" });
    expect(within(tabs).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("aria-current")])).toEqual([
      ["Commissions", "/partner/reseller/payments/commissions", null],
      ["Balance", "/partner/reseller/payments/balance", "page"],
      ["Disputes", "/partner/reseller/disputes", null],
    ]);
  });

  it("a reseller sees White Label locked, and the dialog offers White-label", async () => {
    mount("reseller", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(reseller())],
      ...dashboardRoutes({ [`GET /api/v1/partner/${ID}/upgrade`]: [ok({ ...UPGRADE, current: "reseller", eligible: ["white_label"] })] }),
    });
    await screen.findByRole("heading", { level: 1, name: "Partner Dashboard" });
    expect(lockedLabels()).toEqual(["White Label"]);
    fireEvent.click(within(screen.getByRole("navigation", { name: /workspace$/ })).getByRole("button", { name: /^White Label/ }));
    const dialog = await screen.findByRole("dialog", { name: "Unlock White Label" });
    expect(await within(dialog).findByRole("link", { name: "Upgrade to White-label" })).toHaveAttribute("href", "/partner/reseller/upgrade/white-label");
    expect(within(dialog).queryByRole("link", { name: "Upgrade to Reseller" })).toBeNull();
  });

  it("sends an ended partner to the money it can still read", async () => {
    mount("reseller", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(reseller({ status: "terminated", permissions: perm("commission.view", "payout.view") }))],
    });
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/partner/reseller/payments/commissions"));
    expect(await screen.findByText(/This partner account has ended/)).toBeInTheDocument();
  });

  it("says plainly when the partner is suspended", async () => {
    mount("reseller", ["no-such-page"], { "GET /api/v1/partner/portal": [portal(reseller({ status: "suspended" }))] });
    expect(await screen.findByText(/This partner account is suspended/)).toBeInTheDocument();
  });
});

describe("holding more than one workspace", () => {
  const both = () =>
    partner({
      workspaces: [
        { key: "reseller", label: "Reseller" },
        { key: "affiliate", label: "Affiliate" },
      ],
      permissions: [...RESELLER_PERMISSIONS, ...perm("referral.view", "referral.create")],
    });

  it("offers a switcher, marking the current one, and an account-menu entry for the other", async () => {
    mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(both())],
      ...dashboardRoutes(),
    });
    const switcher = await screen.findByRole("navigation", { name: "Workspace" });
    const links = within(switcher).getAllByRole("link");
    expect(links.map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("aria-current")])).toEqual([
      // ADR-053: in the order a partner grows - Affiliate, Reseller, White-label.
      ["Affiliate", "/partner/affiliate/dashboard", "page"],
      ["Reseller", "/partner/reseller/dashboard", null],
    ]);
  });

  it("offers none to an account with one", async () => {
    mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(partner())],
      ...dashboardRoutes(),
    });
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("navigation", { name: "Workspace" })).not.toBeInTheDocument();
  });

  it("a workspace the account holds opens even when the preference is another", async () => {
    mount("reseller", ["reports"], {
      "GET /api/v1/partner/portal": [portal({ ...both(), default_experience: "affiliate", landing: "affiliate" })],
      [`GET /api/v1/partner/${ID}/reports`]: [ok({ partner: OVERVIEW.partner, range: OVERVIEW.range, customers: null, products: null, sales: null, referrals: null, not_implemented: {} })],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Reports & Analytics" })).toBeInTheDocument();
  });
});

describe("belonging to two accounts that hold the same workspace", () => {
  it("asks which one rather than opening the first, and loads nothing of either", async () => {
    const calls = mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [
        portal(
          partner({ partner_id: "a1", display_name: "Alpha" }),
          partner({ partner_id: "b2", display_name: "Beta" }),
        ),
      ],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Which Affiliate account?" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Alpha" })).toHaveAttribute("href", "/partner/a1");
    expect(screen.getByRole("link", { name: "Beta" })).toHaveAttribute("href", "/partner/b2");
    // Nothing of a partner was requested: no dashboard, no overview.
    expect(calls.map((c) => c.url).filter((u) => u.includes("/partner/a1") || u.includes("/partner/b2"))).toEqual([]);
    expect(screen.queryByRole("navigation", { name: /workspace$/ })).not.toBeInTheDocument();
  });
});

describe("where the portal chooser sends someone", () => {
  it("opens the workspace the server resolved, else the generic panel", () => {
    expect(openingPath({ partner_id: ID, landing: "white_label" })).toBe("/partner/white-label/dashboard");
    expect(openingPath({ partner_id: ID, landing: "reseller" })).toBe("/partner/reseller/dashboard");
    expect(openingPath({ partner_id: ID, landing: null })).toBe(`/partner/${ID}`);
    expect(openingPath({ partner_id: ID })).toBe(`/partner/${ID}`);
    // A landing that is not a workspace is not followed.
    expect(openingPath({ partner_id: ID, landing: "../admin" })).toBe(`/partner/${ID}`);
  });

  it("does not guess between two accounts that hold the same workspace", () => {
    const a = { partner_id: "a1", landing: "reseller", workspaces: [{ key: "reseller" }] };
    const b = { partner_id: "b2", landing: "reseller", workspaces: [{ key: "reseller" }] };
    const c = { partner_id: "c3", landing: "affiliate", workspaces: [{ key: "affiliate" }] };
    // A workspace address names no partner: with two resellers each opens its own panel.
    expect(openingPath(a, [a, b])).toBe("/partner/a1");
    expect(openingPath(b, [a, b])).toBe("/partner/b2");
    // One of each kind is unambiguous.
    expect(openingPath(a, [a, c])).toBe("/partner/reseller/dashboard");
    expect(openingPath(c, [a, c])).toBe("/partner/affiliate/dashboard");
  });

  it("lists two accounts of one kind, each linking to its own panel", async () => {
    mockApi({
      "GET /api/v1/partner/portal": [
        portal(
          partner({ partner_id: "a1", display_name: "Alpha", landing: "affiliate" }),
          partner({ partner_id: "b2", display_name: "Beta", landing: "affiliate" }),
        ),
      ],
    });
    render(<PortalChooser />);
    expect(await screen.findByRole("link", { name: "Alpha" })).toHaveAttribute("href", "/partner/a1");
    expect(screen.getByRole("link", { name: "Beta" })).toHaveAttribute("href", "/partner/b2");
  });

  it("goes straight to the workspace for a single account", async () => {
    mockApi({ "GET /api/v1/partner/portal": [portal(partner({ landing: "affiliate" }))] });
    render(<PortalChooser />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/partner/affiliate/dashboard"));
  });

  it("lists several accounts, each linking to where it opens", async () => {
    mockApi({
      "GET /api/v1/partner/portal": [
        portal(
          partner({ partner_id: "a1", display_name: "Alpha", landing: "affiliate" }),
          partner({ partner_id: "b2", display_name: "Beta", landing: null, workspaces: [] }),
        ),
      ],
    });
    render(<PortalChooser />);
    expect(await screen.findByRole("link", { name: "Alpha" })).toHaveAttribute("href", "/partner/affiliate/dashboard");
    expect(screen.getByRole("link", { name: "Beta" })).toHaveAttribute("href", "/partner/b2");
    expect(nav.replace).not.toHaveBeenCalled();
  });
});

describe("accessibility", () => {
  it("the affiliate dashboard shell has no detectable violations", async () => {
    mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(partner())],
      ...dashboardRoutes(),
    });
    await screen.findByRole("heading", { level: 1, name: "Partner Dashboard" });
    await screen.findByRole("heading", { name: "Partner levels" });
    await expectNoViolations(document.body);
  });

  it("the upgrade dialog has no detectable violations", async () => {
    mount("affiliate", ["dashboard"], { "GET /api/v1/partner/portal": [portal(partner())], ...dashboardRoutes() });
    await screen.findByRole("heading", { level: 1, name: "Partner Dashboard" });
    fireEvent.click(screen.getByRole("button", { name: /Upgrade Plan/ }));
    const dialog = await screen.findByRole("dialog", { name: "Upgrade your partnership" });
    await within(dialog).findByRole("link", { name: "Upgrade to Reseller" });
    await expectNoViolations(document.body);
  });

  it("a refusal is announced as an alert on its own page", async () => {
    mount("reseller", ["dashboard"], { "GET /api/v1/partner/portal": [portal(partner())] });
    await screen.findByRole("alert");
    expect(screen.getByRole("main")).toBeInTheDocument();
    await expectNoViolations(document.body);
  });
});
