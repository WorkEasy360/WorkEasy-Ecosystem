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

function mount(slug: string, segments: string[] | undefined, routes: Record<string, Reply[]>): Call[] {
  const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], ...routes });
  render(<Workspace slug={slug} segments={segments} />);
  return calls;
}

const sidebar = () => within(screen.getByRole("navigation", { name: /workspace$/ })).getAllByRole("link").map((a) => a.textContent);

beforeEach(() => {
  vi.restoreAllMocks();
  nav.replace.mockReset();
  nav.push.mockReset();
  pathname = "/partner/affiliate/dashboard";
});

describe("the affiliate workspace", () => {
  it("draws its sidebar from the permissions the server resolved, and no customer entry", async () => {
    mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(partner())],
      [`GET /api/v1/partner/${ID}/affiliate/overview`]: [ok(OVERVIEW)],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Welcome, Acme Partners" })).toBeInTheDocument();
    expect(sidebar()).toEqual([
      "Dashboard",
      "Referral Links",
      "Referral Codes",
      "Campaigns",
      "Marketing Materials",
      "Leads & Referrals",
      "Conversions",
      "Commissions",
      "Payouts",
      "Reports & Analytics",
      "Profile & Settings",
      "Support",
    ]);
    for (const hidden of ["Customers", "Subscriptions", "Pricing & Margins", "Sub-Resellers", "Brand Settings"]) {
      expect(sidebar()).not.toContain(hidden);
    }
    const links = within(screen.getByRole("navigation", { name: /workspace$/ })).getAllByRole("link");
    expect(links[0]).toHaveAttribute("href", "/partner/affiliate/dashboard");
    expect(links[0]).toHaveAttribute("aria-current", "page");
  });

  it("refuses a page the workspace does not serve - whatever the person's permissions", async () => {
    // Even a member holding the customer view is not given customer pages here.
    const calls = mount("affiliate", ["users"], {
      "GET /api/v1/partner/portal": [portal(partner({ permissions: [...AFFILIATE_PERMISSIONS, ...perm("customer.view")] }))],
    });
    expect(await screen.findByRole("heading", { name: "That page is not part of the Affiliate workspace." })).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual(["GET /api/v1/auth/session", "GET /api/v1/partner/portal"]);
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
    const guest = { partner_id: ID, reason: "Support ticket 42", expires_at: "2026-03-01T10:30:00Z" };
    const calls = mount("affiliate", ["dashboard"], {
      "GET /api/v1/partner/portal": [portal(partner({ role: "viewer", is_admin: false, guest }))],
      [`GET /api/v1/partner/${ID}/affiliate/overview`]: [ok(OVERVIEW)],
      "POST /api/v1/partner/guest-view/end": [ok({ ended: true })],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Welcome, Acme Partners" })).toBeInTheDocument();
    const banner = screen.getByText(/Viewing Partner Portal as Acme Partners\./);
    fireEvent.click(within(banner).getByRole("button", { name: "Exit Guest Session" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/admin/partners/${ID}`));
    expect(calls.some((c) => c.key === "POST /api/v1/partner/guest-view/end")).toBe(true);
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
      [`GET /api/v1/partner/${ID}/affiliate/overview`]: [ok(OVERVIEW)],
      "POST /api/v1/partner/guest-view/end": [{ status: 503, body: { code: "unavailable" } }],
    });
    const banner = await screen.findByText(/Viewing Partner Portal as Acme Partners\./);
    fireEvent.click(within(banner).getByRole("button", { name: "Exit Guest Session" }));
    expect(await within(banner).findByRole("alert")).toHaveTextContent("The guest session could not be ended.");
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
    expect(sidebar()).toContain("Commissions");
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
      [`GET /api/v1/partner/${ID}/affiliate/overview`]: [ok(OVERVIEW)],
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
      [`GET /api/v1/partner/${ID}/affiliate/overview`]: [ok(OVERVIEW)],
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
      [`GET /api/v1/partner/${ID}/affiliate/overview`]: [ok(OVERVIEW)],
    });
    await screen.findByRole("heading", { level: 1, name: "Welcome, Acme Partners" });
    await expectNoViolations(document.body);
  });

  it("a refusal is announced as an alert on its own page", async () => {
    mount("reseller", ["dashboard"], { "GET /api/v1/partner/portal": [portal(partner())] });
    await screen.findByRole("alert");
    expect(screen.getByRole("main")).toBeInTheDocument();
    await expectNoViolations(document.body);
  });
});
