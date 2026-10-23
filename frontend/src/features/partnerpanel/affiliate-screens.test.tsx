import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerContext } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, type Call, type Reply } from "@/features/controlplane/testSupport";
import type { PortalPartner } from "@/lib/api/controlPlane";
import { AffiliateDashboard, Campaigns, Leads, MarketingMaterials, ReferralCodes, ReferralLinks } from "./AffiliateScreens";
import { Reports } from "./Reports";
import { WhiteLabelDashboard } from "./WhiteLabelDashboard";

/**
 * The affiliate workspace's screens, the shared reports and the white-label
 * status strip (ADR-047). Each is mounted inside the partner context the shell
 * provides, over a mocked API, so the tests are about what a person sees and what
 * the screen asks the server to do - not about styling.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/partner/affiliate/dashboard",
}));

const ID = "33333333-3333-3333-3333-333333333333";
const API = `/api/v1/partner/${ID}`;
const BASE = "/partner/affiliate";

const perm = (...keys: string[]) => keys.map((k) => `platform.partner_${k}`);
const AFFILIATE = perm("dashboard.view", "profile.view", "member.view", "product.view", "referral.view", "referral.create", "commission.view", "payout.view");

function partner(permissions: string[] = AFFILIATE, status = "active"): PortalPartner {
  return {
    partner_id: ID,
    code: "af",
    display_name: "Aff Co",
    status,
    role: "owner",
    is_admin: true,
    capabilities: [],
    permissions,
  };
}

function inWorkspace(ui: React.ReactNode, permissions?: string[], base = BASE, status = "active") {
  return render(<PartnerContext.Provider value={{ partner: partner(permissions, status), base }}>{ui}</PartnerContext.Provider>);
}

const LINK = {
  id: "c1",
  code: "K7Q2M9AB",
  label: "LinkedIn",
  status: "active",
  created_at: "2026-10-01T10:00:00Z",
  campaign: "LinkedIn",
  url: "https://auth.example.com/signup?ref=K7Q2M9AB",
  clicks: 8,
  conversions: 2,
};

const OVERVIEW = {
  partner: { id: ID, name: "Aff Co", currency: "INR", status: "active" },
  range: { key: "30d", from: "2026-09-22T00:00:00Z", to: "2026-10-22T00:00:00Z" },
  kpis: { clicks: 8, signups: null, conversions: 2, conversion_rate_bp: 2500 },
  primary_link: LINK,
  links: [
    LINK,
    { ...LINK, id: "c2", code: "ZZZZ2222", label: "Email", campaign: "Email", url: null, status: "revoked", clicks: 0, conversions: 0 },
  ],
  campaigns: [
    { campaign: "Email", links: 1, clicks: 0, conversions: 0, conversion_rate_bp: null },
    { campaign: "LinkedIn", links: 1, clicks: 8, conversions: 2, conversion_rate_bp: 2500 },
  ],
  campaign_presets: ["General", "LinkedIn", "YouTube", "Email", "Website"],
  series: [],
  money: {
    earned: [{ currency: "INR", amount_minor: 120000 }],
    pending: [{ currency: "INR", amount_minor: 30000 }],
    available: [],
    paid: [{ currency: "INR", amount_minor: 90000 }],
    balances: [],
  },
  recent: {
    conversions: [{ id: "r1", code: "K7Q2M9AB", code_label: "LinkedIn", captured_at: "2026-10-02T10:00:00Z", converted_at: "2026-10-03T10:00:00Z", commission_minor: 5000, currency: "INR" }],
    payouts: [],
  },
  not_implemented: {
    affiliate_signups: "No separate sign-up step is recorded, so Signups would only repeat Conversions.",
    marketing_materials: "There is no shared marketing library yet.",
    promo_discount_codes: "A referral code gives the visitor no discount.",
  },
};

const overview = (over: Record<string, unknown> = {}): Reply => ok({ ...OVERVIEW, ...over });
const route = (r: Reply | Reply[]) => ({ [`GET ${API}/affiliate/overview`]: Array.isArray(r) ? r : [r] });

let writeText: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.restoreAllMocks();
  writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
});

describe("the affiliate dashboard", () => {
  it("leads with the referral link and a Copy Link button that copies exactly that link", async () => {
    mockApi(route(overview()));
    const { container } = inWorkspace(<AffiliateDashboard />);
    const box = (await screen.findByRole("region", { name: "Your Referral Link" })) as HTMLElement;
    const input = within(box).getByLabelText("Your referral link") as HTMLInputElement;
    expect(input.value).toBe(LINK.url);
    expect(input.readOnly).toBe(true);
    fireEvent.click(within(box).getByRole("button", { name: "Copy Link" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(LINK.url));
    expect(await within(box).findByText("Copied.")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("says when the clipboard refuses, instead of pretending", async () => {
    writeText.mockRejectedValue(new Error("denied"));
    mockApi(route(overview()));
    inWorkspace(<AffiliateDashboard />);
    fireEvent.click(await screen.findByRole("button", { name: "Copy Link" }));
    expect(await screen.findByText(/Could not copy - select the text and copy it/)).toBeInTheDocument();
  });

  it("shows the figures the server computed, with Signups stated as not recorded", async () => {
    mockApi(route(overview()));
    inWorkspace(<AffiliateDashboard />);
    const kpis = await screen.findByRole("list", { name: "Key figures" });
    const text = (label: string) => within(kpis).getByText(label).closest("li")!.textContent;
    expect(text("Clicks")).toMatch(/8/);
    expect(text("Conversions")).toMatch(/2/);
    expect(text("Conversion rate")).toMatch(/25\.00%/);
    expect(text("Signups")).toMatch(/No separate sign-up step is recorded/);
    expect(text("Commission earned")).toMatch(/1,200\.00/);
    expect(text("Pending commission")).toMatch(/300\.00/);
    expect(text("Paid commission")).toMatch(/900\.00/);
    // The browser does no arithmetic: the rate shown is the server's basis points.
    expect(text("Conversion rate")).not.toMatch(/Infinity|NaN/);
  });

  it("shows a dash, not 0%, when there were no clicks", async () => {
    mockApi(route(overview({ kpis: { clicks: 0, signups: null, conversions: 0, conversion_rate_bp: null }, primary_link: null, links: [], campaigns: [] })));
    inWorkspace(<AffiliateDashboard />);
    const kpis = await screen.findByRole("list", { name: "Key figures" });
    const rate = within(kpis).getByText("Conversion rate").closest("li")!;
    expect(rate.textContent).toMatch(/—/);
    expect(rate.textContent).toMatch(/No clicks in this period/);
    expect(await screen.findByText(/no active referral link yet/)).toBeInTheDocument();
  });

  it("does not show money to someone without the commission view", async () => {
    mockApi(route(overview({ money: null })));
    inWorkspace(<AffiliateDashboard />, perm("dashboard.view", "referral.view"));
    const kpis = await screen.findByRole("list", { name: "Key figures" });
    expect(within(kpis).getByText("Commission earned").closest("li")!.textContent).toMatch(/Needs commission access/);
  });

  it("asks the server for the range chosen and for nothing it was not shown", async () => {
    const calls = mockApi(route([overview(), overview()]));
    inWorkspace(<AffiliateDashboard />);
    await screen.findByRole("list", { name: "Key figures" });
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${API}/affiliate/overview`)).toHaveLength(2));
    expect(calls[1].url).toContain("range=7d");
  });
});

describe("the custom date range", () => {
  it("shows the two dates at once but asks the server for nothing until both are applied", async () => {
    const calls = mockApi(route([overview(), overview()]));
    inWorkspace(<AffiliateDashboard />);
    await screen.findByRole("list", { name: "Key figures" });
    const overviewCalls = () => calls.filter((c) => c.key === `GET ${API}/affiliate/overview`);
    expect(overviewCalls()).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    expect(screen.getByLabelText("From")).toBeInTheDocument();
    // Nothing is asked yet: a custom range without dates would be refused (400).
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(overviewCalls()).toHaveLength(1);
    expect(screen.queryByText(/Couldn.t load/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-09-30" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(overviewCalls()).toHaveLength(2));
    expect(overviewCalls()[1].url).toContain("range=custom");
    expect(overviewCalls()[1].url).toContain("from=2026-09-01");
    expect(overviewCalls()[1].url).toContain("to=2026-09-30");
  });
});

describe("referral links", () => {
  it("creates a tracking link under a preset campaign, sending only the label", async () => {
    const calls = mockApi({
      ...route([overview(), overview()]),
      [`POST ${API}/referrals`]: [{ status: 201, body: { id: "c9", code: "NEW12345", label: "YouTube" } }],
    });
    inWorkspace(<ReferralLinks />);
    await screen.findByRole("heading", { name: "Create a tracking link" });
    fireEvent.change(screen.getByLabelText("Campaign"), { target: { value: "YouTube" } });
    fireEvent.click(screen.getByRole("button", { name: "Create tracking link" }));
    expect(await screen.findByText("Tracking link created for YouTube.")).toBeInTheDocument();
    const post = calls.find((c) => c.key === `POST ${API}/referrals`) as Call;
    expect(post.body).toEqual({ label: "YouTube" });
  });

  it("accepts another campaign name, bounded", async () => {
    const calls = mockApi({
      ...route([overview(), overview()]),
      [`POST ${API}/referrals`]: [{ status: 201, body: { id: "c9", code: "NEW12345", label: "Podcast" } }],
    });
    inWorkspace(<ReferralLinks />);
    await screen.findByRole("heading", { name: "Create a tracking link" });
    fireEvent.change(screen.getByLabelText("Campaign"), { target: { value: "__other" } });
    const name = screen.getByLabelText("Campaign name");
    expect(name).toHaveAttribute("maxlength", "64");
    expect(screen.getByRole("button", { name: "Create tracking link" })).toBeDisabled();
    fireEvent.change(name, { target: { value: "Podcast" } });
    fireEvent.click(screen.getByRole("button", { name: "Create tracking link" }));
    await screen.findByText("Tracking link created for Podcast.");
    expect((calls.find((c) => c.key === `POST ${API}/referrals`) as Call).body).toEqual({ label: "Podcast" });
  });

  it("lists each link with its address, clicks and conversions; a switched-off link has no address to copy", async () => {
    mockApi(route(overview()));
    inWorkspace(<ReferralLinks />);
    const table = await screen.findByRole("table", { name: "Your tracking links" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0].textContent).toContain(LINK.url);
    expect(within(rows[0]).getByRole("button", { name: "Copy LinkedIn link" })).toBeInTheDocument();
    expect(rows[1].textContent).toContain("Switched off");
    expect(within(rows[1]).queryByRole("button", { name: /Copy/ })).not.toBeInTheDocument();
  });

  it("switches a link off through the server and says earlier referrals still count", async () => {
    const calls = mockApi({
      ...route([overview(), overview()]),
      [`POST ${API}/referral-codes/c1/disable`]: [ok({ id: "c1", status: "revoked" })],
    });
    inWorkspace(<ReferralLinks />);
    fireEvent.click(await screen.findByRole("button", { name: /Switch off/ }));
    expect(await screen.findByText(/Earlier referrals still count/)).toBeInTheDocument();
    expect(calls.some((c) => c.key === `POST ${API}/referral-codes/c1/disable`)).toBe(true);
  });

  it("offers no creating or switching off to someone who may only view", async () => {
    mockApi(route(overview()));
    inWorkspace(<ReferralLinks />, perm("dashboard.view", "referral.view"));
    await screen.findByRole("table", { name: "Your tracking links" });
    expect(screen.queryByRole("heading", { name: "Create a tracking link" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Switch off/ })).not.toBeInTheDocument();
  });

  it("offers no creating when the partner is not active", async () => {
    mockApi(route(overview()));
    inWorkspace(<ReferralLinks />, AFFILIATE, BASE, "suspended");
    await screen.findByRole("table", { name: "Your tracking links" });
    expect(screen.queryByRole("heading", { name: "Create a tracking link" })).not.toBeInTheDocument();
  });
});

describe("codes, campaigns, materials and leads", () => {
  it("referral codes list the code itself and say plainly that there are no discount codes", async () => {
    mockApi(route(overview()));
    inWorkspace(<ReferralCodes />);
    const table = await screen.findByRole("table", { name: "Your referral codes" });
    expect(within(table).getByText("K7Q2M9AB")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Copy code K7Q2M9AB" })).toBeInTheDocument();
    expect(screen.getByText("A referral code gives the visitor no discount.")).toBeInTheDocument();
  });

  it("campaigns show the server's rate per campaign and call a campaign a label", async () => {
    mockApi(route(overview()));
    inWorkspace(<Campaigns />);
    const table = await screen.findByRole("table", { name: "Campaign results" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0].textContent).toMatch(/Email/);
    expect(rows[0].textContent).toMatch(/—/);
    expect(rows[1].textContent).toMatch(/LinkedIn/);
    expect(rows[1].textContent).toMatch(/25\.00%/);
    expect(screen.getByText(/never changes who earns commission/)).toBeInTheDocument();
  });

  it("marketing materials say they are not available yet - with the server's reason", async () => {
    mockApi(route(overview()));
    inWorkspace(<MarketingMaterials />);
    expect(await screen.findByRole("heading", { name: "Not available yet" })).toBeInTheDocument();
    expect(screen.getByText("There is no shared marketing library yet.")).toBeInTheDocument();
  });

  it("leads and referrals show dates, a campaign and a status - never a person", async () => {
    mockApi({
      [`GET ${API}/referrals`]: [
        ok({
          counts: { captured: 3, converted: 1, expired: 0 },
          total: 4,
          codes: [],
          referrals: {
            results: [
              { id: "x1", code: "K7Q2M9AB", label: "LinkedIn", status: "converted", captured_at: "2026-10-01T10:00:00Z", converted_at: "2026-10-02T10:00:00Z" },
              { id: "x2", code: "K7Q2M9AB", label: "", status: "captured", captured_at: "2026-10-03T10:00:00Z", converted_at: null },
            ],
            next_cursor: null,
            has_more: false,
          },
        }),
      ],
    });
    const { container } = inWorkspace(<Leads />);
    const table = await screen.findByRole("table", { name: "Your referrals, newest first" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Followed your link",
      "Campaign",
      "Status",
      "Became a customer",
    ]);
    expect(within(table).getByText("General")).toBeInTheDocument(); // an unlabelled link is the general campaign
    expect(container.textContent).not.toMatch(/@/);
    expect(container.textContent).toMatch(/no name, email or address is kept/);
    await expectNoViolations(container);
  });
});

describe("reports", () => {
  const BASE_REPORT = { partner: OVERVIEW.partner, range: OVERVIEW.range, customers: null, products: null, sales: null, referrals: null, not_implemented: { affiliate_signups: "No separate sign-up step." } };

  it("draws only the sections the server sent", async () => {
    mockApi({
      [`GET ${API}/reports`]: [
        ok({
          ...BASE_REPORT,
          referrals: { clicks: 8, conversions: 2, conversion_rate_bp: 2500, by_campaign: [{ campaign: "LinkedIn", clicks: 8, conversions: 2, conversion_rate_bp: 2500 }], series: [] },
          sales: { transactions: 3, gross: [{ currency: "INR", amount_minor: 500000 }], commission_earned: [{ currency: "INR", amount_minor: 50000 }], charts: null, source: "commission_ledger" },
        }),
      ],
    });
    const { container } = inWorkspace(<Reports />);
    expect(await screen.findByRole("heading", { name: "Referrals" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sales and commission" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Customers" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Product performance" })).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Referrals by campaign" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("shows customers and product performance to a seller, and a plain message to a role with nothing to report", async () => {
    mockApi({
      [`GET ${API}/reports`]: [
        ok({
          ...BASE_REPORT,
          customers: { total: 5, active: 4, suspended: 1, ended: 0, active_subscriptions: 3 },
          products: [{ product_code: "crm", product_name: "Easy CRM", customers_holding: 4, active_subscriptions: 3 }],
        }),
        ok(BASE_REPORT),
      ],
    });
    const first = inWorkspace(<Reports />, undefined, "/partner/reseller");
    expect(await screen.findByRole("table", { name: "Customers and subscriptions by product" })).toBeInTheDocument();
    first.unmount();
    inWorkspace(<Reports />, undefined, "/partner/reseller");
    expect(await screen.findByText("There is nothing to report for your role.")).toBeInTheDocument();
  });

  it("refuses an invalid range by showing the server's refusal", async () => {
    mockApi({ [`GET ${API}/reports`]: [{ status: 400, body: { code: "partner_validation_error", detail: "range must be 7d, 30d, 90d or custom." } }] });
    inWorkspace(<Reports />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/range must be/);
  });
});

describe("the white-label dashboard strip", () => {
  const DASH = {
    partner: OVERVIEW.partner,
    range: OVERVIEW.range,
    customers: null,
    sales: null,
    money: null,
    charts: null,
    sub_partners: null,
    recent: { customers: null, commissions: null, payouts: null },
    not_implemented: { partner_cost: "No wholesale cost.", customer_limit: "No customer limit." },
  };
  const PRODUCTS_ACTIVITY = { currency: "INR", products: [], available: [], can_manage: false, not_implemented: {} };
  const common = (status: Reply) => ({
    [`GET ${API}/dashboard`]: [ok(DASH)],
    [`GET ${API}/white-label/status`]: [status],
    [`GET ${API}/products`]: [ok(PRODUCTS_ACTIVITY)],
    [`GET ${API}/notifications`]: [ok({ view: "unread", items: [], counts: { all: 0, unread: 0, important: 0, important_unread: 0 }, window: 100, truncated: false, not_implemented: {} })],
  });

  it("shows where the brand and the domain stand, with links to them", async () => {
    mockApi(common(ok({ brand: { state: "in_progress", name: "Acme", logo: false }, domain: { state: "verification_pending", hostname: "app.acme.example" } })));
    inWorkspace(<WhiteLabelDashboard />, perm("dashboard.view", "branding.view", "domain.view"), "/partner/white-label");
    const strip = await screen.findByRole("list", { name: "White-label status" });
    expect(within(strip).getByText("Brand setup").closest("li")!.textContent).toMatch(/In progress/);
    expect(within(strip).getByText("Brand setup").closest("li")!.textContent).toMatch(/Add a logo to finish/);
    expect(within(strip).getByText("Custom domain").closest("li")!.textContent).toMatch(/Verification pending.*app\.acme\.example/);
    expect(screen.getByRole("link", { name: "Open brand settings" })).toHaveAttribute("href", "/partner/white-label/brand-settings");
    expect(screen.getByRole("link", { name: "Open custom domain" })).toHaveAttribute("href", "/partner/white-label/brand-settings/url-manager");
  });

  it("is just the dashboard when the person may see neither", async () => {
    mockApi(common(ok({ brand: null, domain: null })));
    inWorkspace(<WhiteLabelDashboard />, perm("dashboard.view"), "/partner/white-label");
    await screen.findByRole("heading", { level: 1, name: /Welcome, Aff Co/ });
    expect(screen.queryByRole("heading", { name: "White-label status" })).not.toBeInTheDocument();
  });
});
