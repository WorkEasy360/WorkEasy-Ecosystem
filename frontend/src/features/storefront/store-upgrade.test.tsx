import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { ConsoleAccess } from "@/lib/api/console";
import { PartnerShell } from "@/features/controlplane/ControlShell";
import { AdminContextForTest, expectNoViolations, mockApi, ok, SIGNED_IN, type Reply } from "@/features/controlplane/testSupport";
import { OrgAccessContext } from "@/features/console/OrgAccess";
import { StoreProductScreen } from "@/features/console/StoreProductScreen";
import { Plans } from "@/features/partnerpanel/Catalog";
import { CommercialRules } from "@/features/controlplane/productmanager/CommercialRules";
import { StoreDashboard } from "@/features/controlplane/productmanager/StoreDashboard";
import { percentToBp } from "@/features/controlplane/amounts";
import { PlanComparison } from "./PlanComparison";

/**
 * ADR-056 on screen: the customer's product page and plan comparison, a
 * partner setting its own price inside Super Admin's range, and Super Admin's
 * Commercial rules and Store overview. Every figure on these pages is the
 * server's - the tests feed numbers and check they are drawn, never computed.
 */

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/o/acme/store/harbor_crm",
  useSearchParams: () => new URLSearchParams(),
}));

beforeEach(() => {
  vi.restoreAllMocks();
  nav.push.mockReset();
  window.localStorage.clear();
});

const PLANS = [
  {
    plan_version_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    plan_code: "basic",
    name: "Basic",
    caption: "",
    description: "",
    billing_type: "paid" as const,
    kind: "standard" as const,
    addon_for: null,
    product_code: "harbor_crm",
    product_name: "Harbor CRM",
    product_slug: "harbor-crm",
    included_seats: 2,
    max_seats: null,
    trial_days: 0,
    recommended: false,
    trial_available: true,
    prices: [
      { interval: "monthly", currency: "INR", model: "flat", amount_minor: 49900, list_amount_minor: 49900, discount_bp: 0, setup_fee_minor: null, from_tiers: false },
      { interval: "annual", currency: "INR", model: "flat", amount_minor: 499000, list_amount_minor: 499000, discount_bp: 0, setup_fee_minor: null, from_tiers: false },
    ],
  },
  {
    plan_version_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    plan_code: "advanced",
    name: "Advanced",
    caption: "",
    description: "",
    billing_type: "paid" as const,
    kind: "standard" as const,
    addon_for: null,
    product_code: "harbor_crm",
    product_name: "Harbor CRM",
    product_slug: "harbor-crm",
    included_seats: 10,
    max_seats: 50,
    trial_days: 0,
    recommended: true,
    trial_available: false,
    prices: [
      { interval: "monthly", currency: "INR", model: "flat", amount_minor: 89910, list_amount_minor: 99900, discount_bp: 1000, setup_fee_minor: 250000, from_tiers: false },
    ],
  },
];

const ROWS = [
  { key: "seats", label: "Users included", kind: "seats", unit: "", values: { "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": { included: 2, max: null }, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": { included: 10, max: 50 } } },
  { key: "crm.pipelines", label: "Pipelines", kind: "quota", unit: "pipelines", values: { "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": 3, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": -1 } },
  { key: "crm.forecast", label: "Forecasting", kind: "boolean", unit: "", values: { "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": false, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": true } },
];

function productPage(overrides: Record<string, unknown> = {}) {
  return {
    currency: "INR",
    billing_account: true,
    product: {
      code: "harbor_crm",
      slug: "harbor-crm",
      name: "Harbor CRM",
      tagline: "Sell more",
      description: "A CRM for **growing** teams.",
      availability: "available",
      category: "Sales",
      icon: "",
      features: [],
      plans: PLANS,
      starting_price: { amount_minor: 49900, currency: "INR", interval: "monthly" },
      trial: { available: true, duration_days: 14, seat_limit: null, plan_codes: ["basic"] },
      holding: null,
      trial_state: "available",
      trial_ends_at: null,
      trial_days_left: null,
      trial_outcome: null,
      title: "Harbor CRM",
      summary: "The CRM for growing teams",
      highlights: ["Pipelines", "Email sync"],
      benefits: ["Close faster"],
      use_cases: ["Small sales teams"],
      tags: ["crm"],
      subcategory: "Pipeline",
      badges: ["featured"],
      documentation_url: "",
      support_url: "",
      status: "available",
      logo: null,
      banner: null,
      screenshots: [{ url: "/api/v1/public/products/harbor-crm/media/s1", caption: "Dashboard", width: 1280, height: 720 }],
      price: { amount_minor: 49900, currency: "INR", interval: "monthly", plan_name: "Basic" },
      yearly_deal: null,
      held_plan_code: null,
      renews_at: null,
      ...overrides,
    },
    plans: PLANS,
    comparison: ROWS,
    related: [{ code: "harbor_desk", slug: "harbor-desk", name: "Harbor Desk", title: "Harbor Desk", summary: "Help desk", icon: "", category: "Sales", price: null }],
  };
}

const OWNER = ["platform.organization.view", "commercial.subscription.manage"];

function mountProduct(permissions = OWNER) {
  const access = { permissions } as unknown as ConsoleAccess;
  return render(
    <OrgAccessContext.Provider value={{ slug: "acme", access, reload: vi.fn() }}>
      <main>
        <StoreProductScreen productCode="harbor_crm" />
      </main>
    </OrgAccessContext.Provider>,
  );
}

describe("typed percentages", () => {
  it("become exact basis points, never through a float", () => {
    expect(percentToBp("30")).toBe(3000);
    expect(percentToBp("12.5")).toBe(1250);
    expect(percentToBp("0.07")).toBe(7);
    expect(percentToBp("100")).toBe(10000);
    for (const bad of ["", "-1", "100.01", "1.234", "abc", "1e2"]) expect(percentToBp(bad)).toBeNull();
  });
});

describe("plan comparison", () => {
  it("draws the server's prices, discount, set-up fee, seats and features, and highlights the recommended plan", () => {
    render(<PlanComparison plans={PLANS} rows={ROWS} />);
    const table = screen.getByRole("table");
    const head = within(table).getAllByRole("columnheader");
    expect(head.map((h) => h.textContent)).toEqual(["Basic", "RecommendedAdvanced"]);
    expect(within(table).getByText("₹499.00")).toBeInTheDocument();
    expect(within(table).getByText("₹899.10")).toBeInTheDocument();
    expect(within(table).getByLabelText("instead of ₹999.00")).toBeInTheDocument();
    expect(within(table).getByText("10% off")).toBeInTheDocument();
    expect(within(table).getByText(/₹2,500.00 one-time set-up/)).toBeInTheDocument();
    expect(within(table).getByText("10 (up to 50)")).toBeInTheDocument();
    expect(within(table).getByText("Unlimited")).toBeInTheDocument();
    expect(within(table).getByText("Included")).toBeInTheDocument();
  });

  it("switches the billing cycle without computing a price", () => {
    render(<PlanComparison plans={PLANS} rows={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Yearly" }));
    expect(screen.getByText("₹4,990.00")).toBeInTheDocument();
    expect(screen.getByText("Contact sales")).toBeInTheDocument(); // no yearly price for Advanced
  });
});

describe("the customer's product page", () => {
  it("shows the listing, comparison and related products, and passes axe", async () => {
    mockApi({ "GET /api/v1/orgs/acme/store/harbor_crm": [ok(productPage())] });
    const { container } = mountProduct();
    expect(await screen.findByRole("heading", { level: 1, name: "Harbor CRM" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Key features" })).toBeInTheDocument();
    expect(screen.getByText("Close faster")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Harbor Desk" })).toHaveAttribute("href", "/o/acme/store/harbor_desk");
    await expectNoViolations(container);
  });

  it("offers trial, buy and cart per plan from the server's facts; checkout gets identifiers only", async () => {
    mockApi({ "GET /api/v1/orgs/acme/store/harbor_crm": [ok(productPage())] });
    mountProduct();
    await screen.findByRole("heading", { level: 1, name: "Harbor CRM" });
    expect(screen.getByRole("button", { name: "Start 14-day free trial" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Buy now: Advanced" }));
    expect(nav.push).toHaveBeenCalledWith("/o/acme/checkout?plan=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb&interval=monthly");
    fireEvent.click(screen.getByRole("button", { name: "Add to cart: Basic" }));
    expect(await screen.findByRole("button", { name: "In cart: Basic" })).toBeDisabled();
  });

  it("a held product offers Open and marks the current plan; a non-owner sees no buying", async () => {
    mockApi({
      "GET /api/v1/orgs/acme/store/harbor_crm": [ok(productPage({ trial_state: "owned", holding: "subscription", status: "owned", held_plan_code: "basic" }))],
    });
    mountProduct(["platform.organization.view"]);
    await screen.findByRole("heading", { level: 1, name: "Harbor CRM" });
    expect(screen.getByText("Your plan")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Buy now/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add to cart/ })).not.toBeInTheDocument();
  });
});

// --- partner: own price inside the range ------------------------------------------------------

const ID = "11111111-1111-1111-1111-111111111111";
const API = `/api/v1/partner/${ID}`;
const PARTNER_PERMS = ["dashboard.view", "product.view", "product.manage", "pricing.view", "commission.view"].map((p) => `platform.partner_${p}`);
const PORTAL: Reply = ok({
  partners: [{ partner_id: ID, code: "acme", display_name: "Acme", status: "active", role: "owner", role_label: "Owner", is_admin: true, capabilities: [], permissions: PARTNER_PERMS }],
});

function partnerPlans(canSet: boolean) {
  return {
    currency: "INR",
    experience: "white_label",
    can_create_plan: false,
    not_implemented: {},
    plans: [
      {
        plan_version_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        plan_code: "crm_basic",
        plan_name: "CRM Basic",
        product_code: "crm",
        product_name: "Easy CRM",
        included_seats: 3,
        trial_days: 0,
        currency: "INR",
        enabled_for_sale: true,
        earning: { model: "margin", discount_bp: 2000 },
        prices: [
          {
            interval: "monthly",
            partner_price_minor: null,
            list_price_minor: 59900,
            customer_price_minor: 59900,
            partner_cost_minor: 47920,
            margin_minor: 11980,
            margin_bp: 2000,
            difference_minor: null,
            source: "list",
            expected_earning_minor: 11980,
            price_range: { min_minor: 50000, max_minor: 90000, partner_may_set: true, currency: "INR" },
            can_set_price: canSet,
          },
        ],
      },
    ],
  };
}

describe("partner pricing", () => {
  it("shows the allowed range and sets the partner's own price; the server's refusal is shown", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [PORTAL],
      [`GET ${API}/plans`]: [ok(partnerPlans(true)), ok(partnerPlans(true))],
      [`POST ${API}/own-price`]: [ok({ price_id: "x", amount_minor: 69900 })],
    });
    render(
      <PartnerShell partnerId={ID}>
        <Plans />
      </PartnerShell>,
    );
    const table = await screen.findByRole("table", { name: "Plans you can sell, with your cost and margin" });
    expect(within(table).getByText(/₹500.00 to ₹900.00/)).toBeInTheDocument();
    fireEvent.click(within(table).getByRole("button", { name: "Set your price: CRM Basic" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/Customer price/);
    fireEvent.change(input, { target: { value: "100" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save price" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("between ₹500.00 and ₹900.00");
    expect(calls.some((c) => c.key === `POST ${API}/own-price`)).toBe(false);
    fireEvent.change(input, { target: { value: "699" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save price" }));
    await waitFor(() => expect(calls.find((c) => c.key === `POST ${API}/own-price`)?.body).toEqual({ plan_version_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", interval: "monthly", amount_minor: 69900 }));
    expect(await screen.findByRole("status")).toHaveTextContent("₹699.00");
  });

  it("offers no price control when Super Admin has not allowed it", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [PORTAL], [`GET ${API}/plans`]: [ok(partnerPlans(false))] });
    render(
      <PartnerShell partnerId={ID}>
        <Plans />
      </PartnerShell>,
    );
    await screen.findByRole("table", { name: "Plans you can sell, with your cost and margin" });
    expect(screen.queryByRole("button", { name: /Set your price/ })).not.toBeInTheDocument();
  });
});

// --- Super Admin --------------------------------------------------------------------------------

const ADMIN = "/api/v1/admin/store";
const STAFF = ["platform.staff_catalog.view", "platform.staff_catalog.manage", "platform.staff_pricing.manage", "platform.staff_commission.view", "platform.staff_commission.manage"];

function quote(channel: string, extra: Record<string, unknown> = {}) {
  return {
    channel, currency: "INR", interval: "monthly", list_minor: 99900, discount_bp: 0, base_minor: 99900, customer_minor: 99900,
    price_source: "default_book", setup_fee_minor: null, tax_behaviour: "exclusive", tax_minor: null, partner_cost_minor: null,
    margin_minor: null, margin_bp: null, commission_minor: null, earning: "none", rule: null, partner_share_minor: 0,
    platform_share_minor: 99900, payable_minor: 99900, price_range: null, notes: [], ...extra,
  };
}

const RULES = {
  currency: "INR",
  currencies: ["INR"],
  can_manage: { catalog: true, pricing: true, commission: true },
  shows_money: true,
  products: [
    {
      id: "p1", code: "easy_crm", name: "Easy CRM", icon: "", category: "Sales", status: "active", listing: "listed",
      channels: { customer_store: true, affiliate: true, reseller: true, white_label: false },
      trial: null,
      affiliate_commission: { code: "cr-easy-crm", version: 1, scope: "product", rate_bp: 3000, fixed_amount_minor: null, fixed_currency: "", recurring: false, recurring_months: null, effective_from: "2026-10-01T00:00:00Z" },
      margins: { reseller: { code: "md-reseller", version: 1, scope: "all", discount_bp: 2000, recurring: true, effective_from: "2026-10-01T00:00:00Z" }, white_label: null },
      plans: [
        {
          plan_id: "pl1", plan_version_id: "v1", code: "crm_advanced", name: "CRM Advanced", status: "available", kind: "standard", billing_type: "paid",
          recommended: true, published: true, trial_days: 0, prices: [],
          ranges: [{ id: "r1", experience: "white_label", plan_id: "pl1", interval: "monthly", currency: "INR", min_minor: 80000, max_minor: 150000, partner_may_set: true, updated_at: null }],
          channels: {
            direct: { monthly: quote("direct") },
            affiliate: { monthly: quote("affiliate", { earning: "commission", commission_minor: 29970, partner_share_minor: 29970, platform_share_minor: 69930 }) },
            reseller: { monthly: quote("reseller", { earning: "margin", partner_cost_minor: 79920, margin_minor: 19980, margin_bp: 2000, partner_share_minor: 19980, platform_share_minor: 79920 }) },
            white_label: { monthly: quote("white_label") },
          },
        },
      ],
    },
  ],
};

const admin = (ui: React.ReactNode) => render(<AdminContextForTest permissions={STAFF}>{ui}</AdminContextForTest>);

describe("Super Admin commercial rules", () => {
  it("shows every Store level product-wise from the server's engine, and passes axe", async () => {
    mockApi({ [`GET ${ADMIN}/commercial-rules`]: [ok(RULES)] });
    const { container } = admin(<CommercialRules />);
    expect(await screen.findByRole("heading", { name: "Easy CRM" })).toBeInTheDocument();
    expect(screen.getByText("30%")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Plans of Easy CRM by Store level" });
    expect(within(table).getByText("commission ₹299.70")).toBeInTheDocument();
    expect(within(table).getByText(/cost ₹799.20 · margin ₹199.80/)).toBeInTheDocument();
    expect(within(table).getByText(/₹800.00–₹1,500.00/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "White-label" })).not.toBeChecked();
    await expectNoViolations(container);
  });

  it("saves a fixed, recurring, limited commission through the server", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/commercial-rules`]: [ok(RULES), ok(RULES)],
      [`POST ${ADMIN}/commission`]: [ok({ code: "cr-easy-crm", version: 2 })],
    });
    admin(<CommercialRules />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit commission" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: /Fixed amount/ }));
    fireEvent.change(within(dialog).getByLabelText(/Fixed commission/), { target: { value: "150" } });
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /recurring/ }));
    fireEvent.change(within(dialog).getByLabelText(/How many months/i), { target: { value: "12" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save commission" }));
    await waitFor(() =>
      expect(calls.find((c) => c.key === `POST ${ADMIN}/commission`)?.body).toMatchObject({
        product_code: "easy_crm",
        rate_bp: 0,
        fixed_amount_minor: 15000,
        fixed_currency: "INR",
        recurring: true,
        recurring_months: 12,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText(/^Commission for Easy CRM saved\./)).toBeInTheDocument();
  });

  it("sets a white-label price range", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/commercial-rules`]: [ok(RULES), ok(RULES)],
      [`POST ${ADMIN}/price-ranges`]: [ok({ id: "r2" })],
    });
    admin(<CommercialRules />);
    await screen.findByRole("heading", { name: "Easy CRM" });
    const table = screen.getByRole("table", { name: "Plans of Easy CRM by Store level" });
    fireEvent.click(within(table).getAllByRole("button", { name: "Add range" })[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/Lowest price/), { target: { value: "500" } });
    // Refused before any request: the message shows in the popup, which stays open.
    fireEvent.change(within(dialog).getByLabelText(/Highest price/), { target: { value: "400" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save range" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("The highest price must be at least the lowest.");
    expect(calls.some((c) => c.key === `POST ${ADMIN}/price-ranges`)).toBe(false);
    fireEvent.change(within(dialog).getByLabelText(/Highest price/), { target: { value: "900" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save range" }));
    await waitFor(() =>
      expect(calls.find((c) => c.key === `POST ${ADMIN}/price-ranges`)?.body).toMatchObject({
        experience: "reseller",
        plan_id: "pl1",
        min_minor: 50000,
        max_minor: 90000,
        partner_may_set: true,
      }),
    );
  });
});

describe("Super Admin store overview", () => {
  it("draws the server's counts by channel", async () => {
    mockApi({
      [`GET ${ADMIN}/dashboard`]: [
        ok({
          period: { days: 30, start: "", end: "" },
          catalog: { products_published: 4, products_draft: 1, products_coming_soon: 0, plans_active: 9 },
          sales: {
            orders: 12,
            revenue: [{ currency: "INR", amount_minor: 1200000, payments: 12 }],
            by_channel: {
              direct: { orders: 8, amounts: [{ currency: "INR", amount_minor: 800000 }] },
              reseller: { orders: 3, amounts: [{ currency: "INR", amount_minor: 300000 }] },
              white_label: { orders: 1, amounts: [{ currency: "INR", amount_minor: 100000 }] },
            },
            top_products: [{ code: "easy_crm", name: "Easy CRM", orders: 7 }],
          },
          affiliate_sales: [{ currency: "INR", sales: 2, basis_minor: 200000, commission_minor: 20000 }],
          subscriptions: { active: 20, trialing: 5, past_due: 0, cancelled: 1 },
          partner_requests: { promote: 2, sell: 1 },
        }),
      ],
    });
    admin(<StoreDashboard />);
    expect(await screen.findByText("Reseller sales (3)")).toBeInTheDocument();
    expect(screen.getByText("₹3,000.00")).toBeInTheDocument();
    expect(screen.getByText("Affiliate sales (2)")).toBeInTheDocument();
    expect(screen.getByText("Easy CRM - 7 orders")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "3" })).toHaveAttribute("href", "/admin/partners/product-requests");
  });
});
