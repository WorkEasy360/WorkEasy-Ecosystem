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

const EARN = [{ currency: "INR", sales_minor: 200000, commission_minor: 20000, sales: 2 }];

const LINK = {
  id: "c1",
  code: "K7Q2M9AB",
  label: "LinkedIn",
  status: "active",
  created_at: "2026-10-01T10:00:00Z",
  campaign: "LinkedIn",
  campaign_id: null,
  campaign_name: null,
  product_code: "easy_crm",
  product_name: "Easy CRM",
  plan_code: "",
  url: "https://app.example.com/r/K7Q2M9AB",
  clicks: 8,
  visitors: 6,
  registrations: 3,
  conversions: 2,
  earnings: EARN,
};

const PRODUCT = {
  code: "easy_crm",
  name: "Easy CRM",
  slug: "easy-crm",
  tagline: "Sell more",
  summary: "The approved Easy CRM summary.",
  description: "Full description.",
  category: "crm",
  icon: "",
  plans: [{ plan_code: "crm_basic", plan_name: "Basic" }],
  media: [{ id: "m1", kind: "logo", width: 64, height: 64, content_type: "image/png", caption: "Easy CRM logo", url: "/api/v1/public/products/easy-crm/media/m1" }],
};

const OVERVIEW = {
  partner: { id: ID, name: "Aff Co", currency: "INR", status: "active" },
  range: { key: "30d", from: "2026-09-22T00:00:00Z", to: "2026-10-22T00:00:00Z" },
  kpis: { clicks: 8, unique_visitors: 6, signups: 3, conversions: 2, conversion_rate_bp: 2500 },
  primary_link: LINK,
  links: [
    LINK,
    {
      ...LINK,
      id: "c2",
      code: "ZZZZ2222",
      label: "Email",
      campaign: "Email",
      product_code: "",
      product_name: null,
      url: null,
      status: "revoked",
      clicks: 0,
      visitors: 0,
      registrations: 0,
      conversions: 0,
      earnings: [],
    },
  ],
  campaigns: [
    { campaign: "Email", campaign_id: null, links: 1, clicks: 0, visitors: 0, registrations: 0, conversions: 0, conversion_rate_bp: null, earnings: [] },
    { campaign: "LinkedIn", campaign_id: null, links: 1, clicks: 8, visitors: 6, registrations: 3, conversions: 2, conversion_rate_bp: 2500, earnings: EARN },
  ],
  campaign_list: [{ id: "cmp1", name: "CRM October", status: "active", links: 0, created_at: "2026-10-05T10:00:00Z" }],
  campaign_presets: ["General", "LinkedIn", "YouTube", "Email", "Website"],
  products: [PRODUCT],
  top_products: [{ product_code: "easy_crm", product_name: "Easy CRM", sales: 2, earnings: EARN }],
  series: [],
  money: {
    sales: [{ currency: "INR", amount_minor: 200000, sales: 2 }],
    earned: [{ currency: "INR", amount_minor: 120000 }],
    pending: [{ currency: "INR", amount_minor: 30000 }],
    available: [],
    paid: [{ currency: "INR", amount_minor: 90000 }],
    balances: [],
  },
  recent: {
    conversions: [
      {
        id: "r1",
        code: "K7Q2M9AB",
        code_label: "LinkedIn",
        captured_at: "2026-10-02T10:00:00Z",
        registered_at: "2026-10-02T11:00:00Z",
        converted_at: "2026-10-03T10:00:00Z",
        products: [{ product_code: "easy_crm", plan_code: "crm_basic" }],
        sales_minor: 100000,
        status: "purchased",
        commission_minor: 5000,
        currency: "INR",
      },
    ],
    payouts: [],
  },
  not_implemented: {
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

  it("shows the figures the server computed: clicks, visitors, registrations, sales and commission", async () => {
    mockApi(route(overview()));
    inWorkspace(<AffiliateDashboard />);
    const kpis = await screen.findByRole("list", { name: "Key figures" });
    const text = (label: string) => within(kpis).getByText(label).closest("li")!.textContent;
    expect(text("Referral clicks")).toMatch(/8/);
    expect(text("Unique visitors")).toMatch(/6/);
    expect(text("Registrations")).toMatch(/3/);
    expect(text("Conversions")).toMatch(/2/);
    expect(text("Conversion rate")).toMatch(/25\.00%/);
    expect(text("Sales attributed")).toMatch(/2,000\.00/);
    expect(text("Pending commission")).toMatch(/300\.00/);
    expect(text("Approved commission")).toMatch(/—/);
    expect(text("Paid commission")).toMatch(/900\.00/);
    // The browser does no arithmetic: the rate shown is the server's basis points.
    expect(text("Conversion rate")).not.toMatch(/Infinity|NaN/);
    expect(await screen.findByRole("table", { name: "The products your referrals bought most" })).toHaveTextContent(/Easy CRM/);
    expect(screen.getByRole("table", { name: "Your latest conversions" })).toHaveTextContent(/easy_crm \(crm_basic\)/);
  });

  it("shows a dash, not 0%, when there were no clicks", async () => {
    mockApi(route(overview({ kpis: { clicks: 0, unique_visitors: 0, signups: 0, conversions: 0, conversion_rate_bp: null }, primary_link: null, links: [], campaigns: [] })));
    inWorkspace(<AffiliateDashboard />);
    const kpis = await screen.findByRole("list", { name: "Key figures" });
    const rate = within(kpis).getByText("Conversion rate").closest("li")!;
    expect(rate.textContent).toMatch(/—/);
    expect(rate.textContent).toMatch(/No clicks in this period/);
    expect(await screen.findByText(/no active referral link yet/)).toBeInTheDocument();
  });

  it("does not show money to someone without the commission view", async () => {
    mockApi(route(overview({ money: null, top_products: null })));
    inWorkspace(<AffiliateDashboard />, perm("dashboard.view", "referral.view"));
    const kpis = await screen.findByRole("list", { name: "Key figures" });
    expect(within(kpis).getByText("Pending commission").closest("li")!.textContent).toMatch(/Needs commission access/);
    expect(within(kpis).getByText("Sales attributed").closest("li")!.textContent).toMatch(/Needs commission access/);
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
  it("creates a link for a product, a campaign and a code of the partner's choosing - identifiers only", async () => {
    const calls = mockApi({
      ...route([overview(), overview()]),
      [`POST ${API}/referrals`]: [{ status: 201, body: { id: "c9", code: "AMAN360", label: "CRM October", url: "https://app.example.com/r/AMAN360" } }],
    });
    inWorkspace(<ReferralLinks />);
    await screen.findByRole("heading", { name: "Create a referral link" });
    fireEvent.change(screen.getByLabelText("Product"), { target: { value: "easy_crm" } });
    fireEvent.change(screen.getByLabelText("Plan (optional)"), { target: { value: "crm_basic" } });
    fireEvent.change(screen.getByLabelText("Campaign (optional)"), { target: { value: "cmp1" } });
    fireEvent.change(screen.getByLabelText("Your own code (optional)"), { target: { value: "aman360" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate link" }));
    expect(await screen.findByText("Referral link created.")).toBeInTheDocument();
    const post = calls.find((c) => c.key === `POST ${API}/referrals`) as Call;
    expect(post.body).toEqual({ product_code: "easy_crm", plan_code: "crm_basic", campaign_id: "cmp1", code: "AMAN360" });
  });

  it("creates a general link when nothing is chosen", async () => {
    const calls = mockApi({
      ...route([overview(), overview()]),
      [`POST ${API}/referrals`]: [{ status: 201, body: { id: "c9", code: "NEW12345", label: "", url: "https://app.example.com/r/NEW12345" } }],
    });
    inWorkspace(<ReferralLinks />);
    await screen.findByRole("heading", { name: "Create a referral link" });
    expect(screen.getByLabelText("Plan (optional)")).toBeDisabled();
    expect(screen.getByLabelText("Your own code (optional)")).toHaveAttribute("maxlength", "16");
    fireEvent.click(screen.getByRole("button", { name: "Generate link" }));
    await screen.findByText("Referral link created.");
    expect((calls.find((c) => c.key === `POST ${API}/referrals`) as Call).body).toEqual({});
  });

  it("lists each link with its address, product and results; a switched-off link has no address to copy", async () => {
    mockApi(route(overview()));
    inWorkspace(<ReferralLinks />);
    const table = await screen.findByRole("table", { name: "Your referral links" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0].textContent).toContain(LINK.url);
    expect(rows[0].textContent).toContain("Easy CRM");
    expect(rows[0].textContent).toMatch(/2,000\.00/);
    expect(within(rows[0]).getByRole("button", { name: "Copy link K7Q2M9AB" })).toBeInTheDocument();
    expect(rows[1].textContent).toContain("switched off");
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
    await screen.findByRole("table", { name: "Your referral links" });
    expect(screen.queryByRole("heading", { name: "Create a referral link" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Switch off/ })).not.toBeInTheDocument();
  });

  it("offers no creating when the partner is not active", async () => {
    mockApi(route(overview()));
    inWorkspace(<ReferralLinks />, AFFILIATE, BASE, "suspended");
    await screen.findByRole("table", { name: "Your referral links" });
    expect(screen.queryByRole("heading", { name: "Create a referral link" })).not.toBeInTheDocument();
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

  it("referral codes: a partner creates its own code, sending only the code", async () => {
    const calls = mockApi({
      ...route([overview(), overview()]),
      [`POST ${API}/referrals`]: [{ status: 201, body: { id: "c9", code: "AMAN360", label: "", url: "https://app.example.com/r/AMAN360" } }],
    });
    inWorkspace(<ReferralCodes />);
    fireEvent.change(await screen.findByLabelText("Code"), { target: { value: "aman360" } });
    fireEvent.click(screen.getByRole("button", { name: "Create code" }));
    expect(await screen.findByText("Referral code created.")).toBeInTheDocument();
    expect((calls.find((c) => c.key === `POST ${API}/referrals`) as Call).body).toEqual({ code: "AMAN360" });
  });

  it("campaigns: create, archive, and the server's rate and earnings per campaign", async () => {
    const calls = mockApi({
      ...route([overview(), overview(), overview()]),
      [`POST ${API}/campaigns`]: [{ status: 201, body: { id: "cmp2", name: "Diwali", status: "active" } }],
      [`POST ${API}/campaigns/cmp1/status`]: [ok({ id: "cmp1", status: "revoked" })],
    });
    inWorkspace(<Campaigns />);
    const table = await screen.findByRole("table", { name: "Campaign results" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0].textContent).toMatch(/CRM October/);
    expect(rows[1].textContent).toMatch(/Email/);
    expect(rows[1].textContent).toMatch(/—/);
    expect(rows[2].textContent).toMatch(/LinkedIn/);
    expect(rows[2].textContent).toMatch(/25\.00%/);
    expect(rows[2].textContent).toMatch(/2,000\.00/);
    fireEvent.change(screen.getByLabelText("Campaign name"), { target: { value: "Diwali" } });
    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    await screen.findByText(/Campaign "Diwali" created/);
    expect((calls.find((c) => c.key === `POST ${API}/campaigns`) as Call).body).toEqual({ name: "Diwali" });
    fireEvent.click(await screen.findByRole("button", { name: /Archive/ }));
    await screen.findByText(/Campaign archived/);
    expect((calls.find((c) => c.key === `POST ${API}/campaigns/cmp1/status`) as Call).body).toEqual({ active: false });
  });

  it("marketing materials show the approved listing and make a link for the product", async () => {
    const calls = mockApi({
      ...route(overview()),
      [`POST ${API}/referrals`]: [{ status: 201, body: { id: "c9", code: "CRM00001", label: "", url: "https://app.example.com/r/CRM00001" } }],
    });
    const { container } = inWorkspace(<MarketingMaterials />);
    expect(await screen.findByRole("heading", { name: "Easy CRM" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Easy CRM logo" })).toHaveAttribute("src", PRODUCT.media[0].url);
    expect(screen.getByRole("link", { name: /Download logo/ })).toHaveAttribute("href", `${PRODUCT.media[0].url}?download=1`);
    expect(screen.getByText("The approved Easy CRM summary.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create a link for Easy CRM" }));
    expect(await screen.findByText("https://app.example.com/r/CRM00001")).toBeInTheDocument();
    expect((calls.find((c) => c.key === `POST ${API}/referrals`) as Call).body).toEqual({ product_code: "easy_crm" });
    await expectNoViolations(container);
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
      "Signed up",
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
