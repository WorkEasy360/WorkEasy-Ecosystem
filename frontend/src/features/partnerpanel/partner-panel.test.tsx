import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerShell } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, SIGNED_IN, type Reply } from "@/features/controlplane/testSupport";
import { PartnerDashboard } from "./Dashboard";
import { Affiliates, SubPartners, Team } from "./Network";
import { Balance, PaymentLinks, ProductBilling, Products } from "./Catalog";
import { Integrations, Settings } from "./Account";

let pathname = "/partner/p1";
const nav = vi.hoisted(() => ({ router: { replace: () => {}, push: () => {}, refresh: () => {} } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => pathname,
}));

const ID = "11111111-1111-1111-1111-111111111111";
const API = `/api/v1/partner/${ID}`;

const OWNER_PERMISSIONS = [
  "dashboard.view",
  "profile.view",
  "profile.manage",
  "member.view",
  "member.manage",
  "product.view",
  "product.manage",
  "activity.view",
  "customer.view",
  "customer.provision",
  "customer.export",
  "pricing.view",
  "subscription.sell",
  "commission.view",
  "payout.view",
  "referral.view",
  "referral.create",
  "subpartner.view",
  "subpartner.manage",
  "branding.view",
  "branding.manage",
  "domain.view",
  "domain.manage",
].map((p) => `platform.partner_${p}`);

function portal(permissions: string[] = OWNER_PERMISSIONS, status = "active"): Reply {
  return ok({
    partners: [
      {
        partner_id: ID,
        code: "acme",
        display_name: "Acme Partners",
        status,
        role: "owner",
        role_label: "Partner Owner",
        is_admin: true,
        capabilities: ["customer.provision", "subscription.sell", "commission.view"],
        permissions,
      },
    ],
  });
}

const DASHBOARD = {
  partner: { id: ID, name: "Acme Partners", currency: "INR", status: "active" },
  range: { key: "30d", from: "2026-09-15T00:00:00+00:00", to: "2026-10-15T00:00:00+00:00" },
  customers: { total: 34, active: 30, suspended: 4, ended: 1, active_subscriptions: 21 },
  sales: {
    transactions: 12,
    gross: [{ currency: "INR", amount_minor: 1499000 }],
    commission_earned: [{ currency: "INR", amount_minor: 149900 }],
    source: "commission_ledger",
  },
  money: {
    balances: [],
    pending_commission: [{ currency: "INR", amount_minor: 50000 }],
    pending_payout: [{ currency: "INR", amount_minor: 99900 }],
  },
  charts: {
    gross: { INR: [{ date: "2026-10-01", amount_minor: 700000 }, { date: "2026-10-02", amount_minor: 799000 }] },
    commission: { INR: [{ date: "2026-10-01", amount_minor: 70000 }] },
    transactions: [{ date: "2026-10-01", count: 5 }],
  },
  sub_partners: { total: 2 },
  recent: {
    customers: [{ organization_id: "o1", name: "Zen Retail", link_status: "active", started_at: "2026-10-01T10:00:00Z" }],
    commissions: [{ id: "c1", kind: "accrual", amount_minor: 149900, currency: "INR", event_at: "2026-10-02T10:00:00Z" }],
    payouts: [],
  },
  not_implemented: {
    partner_cost: "The platform has no wholesale-cost model.",
    customer_limit: "No customer or user limit exists for a partner in the commercial model.",
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
  pathname = `/partner/${ID}`;
});

function shell(children: React.ReactNode) {
  return render(<PartnerShell partnerId={ID}>{children}</PartnerShell>);
}

describe("the partner navigation", () => {
  it("offers every area of the panel to an owner, in the brief's groups", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [portal()] });
    shell(<h1>Page</h1>);
    await screen.findByRole("heading", { name: "Page" });
    const links = within(screen.getByRole("navigation")).getAllByRole("link").map((a) => a.textContent);
    for (const label of [
      "Dashboard",
      "Customers",
      "Sub-Partners",
      "Affiliates",
      "Products",
      "Plans & Add-ons",
      "Pricing",
      "Subscriptions",
      "Orders",
      "Commissions",
      "Payouts",
      "Currencies",
      "Partner Balance",
      "Payments",
      "Payment Links",
      "Product Billing",
      "Brand Settings",
      "Domains",
      "Product Manager",
      "Appearance",
      "Channels",
      "Templates",
      "Alerts",
      "Logs",
      "Integrations",
      "Authentication",
      "Shared Resources",
      "Users & Permissions",
      "Activity",
      "Settings",
    ]) {
      expect(links, label).toContain(label);
    }
  });

  it("shows a role only what its permissions allow", async () => {
    const finance = ["dashboard.view", "profile.view", "member.view", "product.view", "commission.view", "payout.view", "pricing.view"].map(
      (p) => `platform.partner_${p}`,
    );
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [portal(finance)] });
    shell(<h1>Page</h1>);
    await screen.findByRole("heading", { name: "Page" });
    const links = within(screen.getByRole("navigation")).getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual(expect.arrayContaining(["Commissions", "Payouts", "Partner Balance"]));
    for (const absent of ["Customers", "Sub-Partners", "Affiliates", "Brand Settings", "Domains", "Activity"]) {
      expect(links).not.toContain(absent);
    }
  });

  it("replaces the whole portal with the password change while a temporary password is in force", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: true, user_id: "u1", email: "d@example.com", password_change_required: true })],
      "GET /api/v1/partner/portal": [portal()],
    });
    shell(<h1>Secret page</h1>);
    expect(await screen.findByRole("heading", { name: "Choose your own password" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Secret page" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
    expect(calls.every((c) => !c.url.includes("/dashboard"))).toBe(true);
  });
});

describe("the dashboard", () => {
  it("shows the server's figures, and says why a figure does not exist instead of showing zero", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/dashboard`]: [ok(DASHBOARD)],
    });
    const { container } = shell(<PartnerDashboard />);
    expect(await screen.findByRole("heading", { name: "Welcome, Acme Partners" })).toBeInTheDocument();
    const figures = await screen.findByRole("list", { name: "Key figures" });
    expect(within(figures).getByText("34")).toBeInTheDocument();
    expect(within(figures).getByText("21")).toBeInTheDocument();
    expect(within(figures).getByText(/14,990\.00/)).toBeInTheDocument(); // gross, formatted from minor units
    expect(within(figures).getByText(/1,499\.00/)).toBeInTheDocument(); // commission
    // No cost and no limit model exists: the card says so, never "0".
    expect(within(figures).getByText(/no wholesale-cost model/i)).toBeInTheDocument();
    expect(within(figures).getByText(/No customer or user limit/i)).toBeInTheDocument();
    // Reading sends nothing but the range.
    expect(calls.filter((c) => c.key.startsWith("GET /api/v1/partner/") && c.url.includes("dashboard")).length).toBe(1);
    expect(calls.every((c) => c.body === undefined)).toBe(true);
    await expectNoViolations(container);
  });

  it("hides what a role may not see as a note, not as a zero", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/dashboard`]: [ok({ ...DASHBOARD, customers: null, sales: null, money: null, charts: null, sub_partners: null })],
    });
    shell(<PartnerDashboard />);
    const figures = await screen.findByRole("list", { name: "Key figures" });
    expect(within(figures).getAllByText("Needs commission access.").length).toBeGreaterThan(0);
    expect(within(figures).getAllByText("Not shown for your role.").length).toBe(2);
    expect(screen.queryByRole("heading", { name: "Sales overview" })).not.toBeInTheDocument();
  });

  it("asks the server for another range, and for a custom one by date", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/dashboard`]: [ok(DASHBOARD)],
    });
    shell(<PartnerDashboard />);
    await screen.findByRole("list", { name: "Key figures" });
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("range=7d"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-09-30" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("range=custom") && c.url.includes("from=2026-09-01") && c.url.includes("to=2026-09-30"))).toBe(true),
    );
  });

  it("draws the chart from the server's per-day amounts and offers the same data as a table", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/dashboard`]: [ok(DASHBOARD)],
    });
    shell(<PartnerDashboard />);
    const chart = await screen.findByRole("img", { name: /Gross sales by day: 2 days/ });
    expect(chart.querySelectorAll("rect").length).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Show as table" }));
    const table = await screen.findByRole("table", { name: "Gross sales by day" });
    expect(within(table).getByText("2026-10-02")).toBeInTheDocument();
    expect(within(table).getByText(/7,990\.00/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Transactions" }));
    fireEvent.click(screen.getByRole("button", { name: "Show chart" }));
    expect(await screen.findByRole("img", { name: /Transactions by day/ })).toBeInTheDocument();
  });

  it("offers only the quick links the person may use", async () => {
    const limited = ["dashboard.view", "profile.view", "member.view", "product.view", "commission.view"].map((p) => `platform.partner_${p}`);
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(limited)],
      [`GET ${API}/dashboard`]: [ok(DASHBOARD)],
    });
    shell(<PartnerDashboard />);
    const quick = await screen.findByRole("heading", { name: "Quick links" });
    const links = within(quick.closest("section")!).getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual(["Manage products", "View commission"]);
  });

  it("invites a first sale when there is nothing yet", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/dashboard`]: [
        ok({ ...DASHBOARD, customers: { total: 0, active: 0, suspended: 0, ended: 0, active_subscriptions: 0 }, sales: { transactions: 0, gross: [], commission_earned: [], source: "commission_ledger" }, charts: { gross: {}, commission: {}, transactions: [] }, recent: { customers: [], commissions: [], payouts: [] } }),
      ],
    });
    shell(<PartnerDashboard />);
    expect(await screen.findByText("No sales in this period")).toBeInTheDocument();
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
  });
});

describe("sub-partners", () => {
  const ROW = {
    id: "c1",
    link_id: "l1",
    name: "Northern Agency",
    code: "acme-northern-1a2b3c",
    status: "draft",
    link_status: "active",
    currency: "INR",
    country: "IN",
    administrator: { name: "Nora North", email: "nora@northern.example.com", invited: false, last_login_at: null },
    customers: 3,
    products: ["easy_crm"],
    capabilities: ["customer.provision"],
    experiences: ["reseller"],
    created_at: "2026-10-01T10:00:00Z",
  };
  const OPTIONS = {
    capabilities: [
      { key: "customer.provision", label: "Create customers", description: "Create customer organizations" },
      { key: "subscription.sell", label: "Sell products", description: "Sell approved products" },
    ],
    products: [{ code: "easy_crm", name: "Easy CRM" }],
    depth: { max: 1, can_have_children: true },
    not_implemented: { subpartner_pricing: "A sub-partner sells at list price.", subpartner_commission: "A parent sees no sub-partner commission." },
  };

  it("lists sub-partners with their facts, activates a draft and creates a new one", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/sub-partners`]: [ok({ results: [ROW], next_cursor: null, has_more: false })],
      [`GET ${API}/sub-partners/options`]: [ok(OPTIONS)],
      [`POST ${API}/sub-partners/c1/activate`]: [ok({ id: "c1", status: "active" })],
      [`POST ${API}/sub-partners`]: [{ status: 201, body: { id: "c2", code: "x", status: "draft" } }],
    });
    const { container } = shell(<SubPartners />);
    const table = await screen.findByRole("table", { name: "Your sub-partners" });
    expect(within(table).getByText("Northern Agency")).toBeInTheDocument();
    expect(within(table).getByText("nora@northern.example.com")).toBeInTheDocument();
    fireEvent.click(within(table).getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/sub-partners/c1/activate`)).toBe(true));

    fireEvent.change(await screen.findByLabelText("Partner name"), { target: { value: "Southern Agency" } });
    fireEvent.change(screen.getByLabelText("Administrator's email"), { target: { value: "sam@southern.example.com" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Create customers" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Easy CRM" }));
    fireEvent.click(screen.getByRole("button", { name: "Create sub-partner" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/sub-partners`)).toBe(true));
    const create = calls.find((c) => c.key === `POST ${API}/sub-partners`)!;
    expect(create.body).toEqual({
      display_name: "Southern Agency",
      administrator_email: "sam@southern.example.com",
      administrator_name: "",
      capabilities: ["customer.provision"],
      products: ["easy_crm"],
    });
    // The browser names selections only: no code, no status, no partner id.
    expect(Object.keys(create.body as object)).not.toEqual(expect.arrayContaining(["code", "status", "parent_id", "partner_id"]));
    await expectNoViolations(container);
  });

  it("says plainly what a sub-partner does not get", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/sub-partners`]: [ok({ results: [], next_cursor: null, has_more: false })],
      [`GET ${API}/sub-partners/options`]: [ok(OPTIONS)],
    });
    shell(<SubPartners />);
    expect(await screen.findByText("You have no sub-partners yet.")).toBeInTheDocument();
    expect(await screen.findByText("A sub-partner sells at list price.")).toBeInTheDocument();
    expect(screen.getByText("A parent sees no sub-partner commission.")).toBeInTheDocument();
  });

  it("shows no way to create one to a partner that may not", async () => {
    const viewOnly = ["dashboard.view", "subpartner.view"].map((p) => `platform.partner_${p}`);
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(viewOnly)],
      [`GET ${API}/sub-partners`]: [ok({ results: [ROW], next_cursor: null, has_more: false })],
    });
    shell(<SubPartners />);
    await screen.findByRole("table", { name: "Your sub-partners" });
    expect(screen.queryByRole("button", { name: "Create sub-partner" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });
});

describe("affiliates", () => {
  it("shows links as full addresses, the programme rules and never any visitor detail", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/affiliates`]: [
        ok({
          programme: {
            enabled: true,
            attribution_window_days: 90,
            visible_to_you: ["campaign label"],
            never_shown: ["name", "email"],
            commission_rules: [],
          },
          summary: { captured: 4, converted: 1 },
          codes: [{ id: "k1", code: "ABCD2345", label: "Spring", status: "active", url: "https://auth.workeasy360.com/signup?ref=ABCD2345" }],
          affiliates: [],
          not_implemented: {},
        }),
      ],
    });
    shell(<Affiliates />);
    expect(await screen.findByText("https://auth.workeasy360.com/signup?ref=ABCD2345")).toBeInTheDocument();
    expect(screen.getByText("converted")).toBeInTheDocument();
  });
});

describe("the team", () => {
  const MEMBERS = {
    members: [
      { id: "m1", name: "Owner One", email: "owner@acme.example.com", role: "owner", role_label: "Partner Owner", status: "active", setup_pending: false, mfa_enrolled: true, invited_at: null, last_login_at: null, joined_at: "2026-09-01T00:00:00Z", is_you: true },
      { id: "m2", name: "Sam Sales", email: "sam@acme.example.com", role: "sales", role_label: "Sales", status: "active", setup_pending: true, mfa_enrolled: false, invited_at: "2026-10-01T00:00:00Z", last_login_at: null, joined_at: "2026-10-01T00:00:00Z", is_you: false },
    ],
    roles: [
      { key: "owner", label: "Partner Owner", description: "Everything." },
      { key: "sales", label: "Sales", description: "Sells." },
      { key: "viewer", label: "Viewer", description: "Reads." },
    ],
    can_manage: true,
  };

  it("lets an administrator invite, change a role and remove, but never act on themselves", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/members`]: [ok(MEMBERS)],
      [`POST ${API}/members`]: [{ status: 201, body: { id: "m3", status: "invited" } }],
      [`PATCH ${API}/members/m2`]: [ok({ id: "m2", role: "viewer" })],
    });
    const { container } = shell(<Team />);
    const table = await screen.findByRole("table", { name: "Your team" });
    // The person themselves: no role control, no remove.
    const self = within(table).getByText("owner@acme.example.com").closest("tr")!;
    expect(within(self).queryByRole("combobox")).not.toBeInTheDocument();
    expect(within(self).queryByRole("button", { name: /Remove/ })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Role of sam@acme.example.com"), { target: { value: "viewer" } });
    await waitFor(() => expect(calls.some((c) => c.key === `PATCH ${API}/members/m2`)).toBe(true));
    expect(calls.find((c) => c.key === `PATCH ${API}/members/m2`)!.body).toEqual({ role: "viewer" });

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@acme.example.com" } });
    fireEvent.change(screen.getByLabelText("Role for the new user"), { target: { value: "sales" } });
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/members`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${API}/members`)!.body).toEqual({ email: "new@acme.example.com", full_name: "", role: "sales" });
    await expectNoViolations(container);
  });

  it("shows a read-only team to someone who cannot manage it", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/members`]: [ok({ ...MEMBERS, can_manage: false })],
    });
    shell(<Team />);
    await screen.findByRole("table", { name: "Your team" });
    expect(screen.queryByRole("button", { name: "Send invitation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});

describe("products and billing", () => {
  const PRODUCT = {
    code: "easy_crm",
    name: "Easy CRM",
    description: "Customer relationships.",
    tagline: "Sell more",
    icon: "",
    category: "Sales",
    status: "available",
    grant_scope: "all_plans",
    enabled_for_sale: true,
    show_in_explore: true,
    show_in_my_products: true,
    display_order: 1,
    trial: { offered_by_platform: true, days: 7, allowed_for_partner: true },
    portal_visible_by_platform: true,
    plans: [{ plan_code: "easy_crm_standard", plan_name: "Standard", intervals: [{ interval: "monthly", partner_price_minor: 149900, list_price_minor: 149900 }] }],
    customers: 5,
    active_subscriptions: 4,
  };
  const LIST = { currency: "INR", products: [PRODUCT], available: [{ code: "easybook", name: "EasyBook", tagline: "Books" }], can_manage: true, not_implemented: {} };

  it("draws a card per granted product and changes only the partner's own switches", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/products`]: [ok(LIST)],
      [`PUT ${API}/products/easy_crm`]: [ok({})],
    });
    shell(<Products />);
    const cards = await screen.findByRole("list", { name: "Your products" });
    expect(within(cards).getByText("Easy CRM")).toBeInTheDocument();
    expect(within(cards).getByText(/1,499\.00 monthly/)).toBeInTheDocument();
    expect(screen.getByText("EasyBook")).toBeInTheDocument(); // available, not granted: listed, not switchable
    fireEvent.click(within(cards).getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Offer for new sales" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${API}/products/easy_crm`)).toBe(true));
    expect(calls.find((c) => c.key === `PUT ${API}/products/easy_crm`)!.body).toEqual({
      enabled_for_sale: false,
      show_in_explore: true,
      show_in_my_products: true,
    });
  });

  it("disables a product for new sales from product billing", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/products`]: [ok(LIST)],
      [`PUT ${API}/products/easy_crm`]: [ok({})],
    });
    shell(<ProductBilling />);
    fireEvent.click(await screen.findByRole("button", { name: "Disable for new sales" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${API}/products/easy_crm`)).toBe(true));
    expect(calls.find((c) => c.key === `PUT ${API}/products/easy_crm`)!.body).toEqual({ enabled_for_sale: false });
  });

  it("shows a balance that is the ledger and offers no way to recharge", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/balance`]: [
        ok({
          balances: [{ currency: "INR", balance_minor: 200000, pending_minor: 50000, available_minor: 100000, in_payouts_minor: 50000, paid_minor: 0, entries: 4 }],
          entries: [],
          not_implemented: { recharge: "There is no prepaid partner credit." },
        }),
      ],
    });
    const { container } = shell(<Balance />);
    expect(await screen.findByText(/2,000\.00/)).toBeInTheDocument();
    expect(screen.getByText("There is no prepaid partner credit.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /recharge|top up|add funds/i })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });
});

describe("what the platform does not have", () => {
  const FEATURES = {
    permissions: [],
    roles: [],
    hierarchy: { max_depth: 1 },
    not_implemented: {
      payment_links: "Payment links exist only for a customer's own order.",
      recharge: "There is no prepaid partner credit.",
      integrations: "Connectors belong to a customer organization.",
    },
  };

  it("explains payment links instead of offering them", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/features`]: [ok(FEATURES)],
    });
    shell(<PaymentLinks />);
    expect(await screen.findByText("Payment links exist only for a customer's own order.")).toBeInTheDocument();
    const page = within(screen.getByRole("main"));
    expect(page.queryByRole("button")).not.toBeInTheDocument();
    expect(page.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("explains integrations instead of offering an installer", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/features`]: [ok(FEATURES)],
    });
    shell(<Integrations />);
    expect(await screen.findByText("Connectors belong to a customer organization.")).toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("settings", () => {
  const PROFILE = {
    partner_id: ID,
    version: 7,
    business: { display_name: "Acme Partners", legal_name: "Acme Ltd", business_email: "", website: "", phone: "", alternative_email: "", registration_number: "", tax_number: "", country: "IN", timezone: "Asia/Kolkata", address: {} },
    contacts: { primary: { name: "Pat", email: "pat@acme.example.com" }, billing: { same_as_primary: true } },
    support: { email: "", phone: "", url: "" },
    payout: { currency: "INR" },
    updated_at: "2026-10-01T00:00:00Z",
  };

  it("saves with the version it read and never offers the payout currency for editing", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal()],
      [`GET ${API}/profile`]: [ok(PROFILE)],
      [`PUT ${API}/profile`]: [ok({ ...PROFILE, version: 8 })],
    });
    const { container } = shell(<Settings />);
    fireEvent.change(await screen.findByLabelText("Business name"), { target: { value: "Acme Partners Pvt" } });
    expect(screen.getByText("INR")).toBeInTheDocument();
    expect(screen.queryByLabelText(/currency/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${API}/profile`)).toBe(true));
    const body = calls.find((c) => c.key === `PUT ${API}/profile`)!.body as Record<string, unknown>;
    expect(body.expected_version).toBe(7);
    expect((body.business as Record<string, string>).display_name).toBe("Acme Partners Pvt");
    // Money, payout and notes are not in what the browser can send from here.
    expect(Object.keys(body).sort()).toEqual(["business", "contacts", "expected_version", "support"]);
    await expectNoViolations(container);
  });
});
