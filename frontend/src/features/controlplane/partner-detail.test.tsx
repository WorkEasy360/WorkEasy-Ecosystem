import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerDetailScreen } from "./AdminPartners";
import { AllPartnerCustomers } from "./PartnerCustomers";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * Phase 10c: the reseller detail. What the backend cannot enforce for the
 * browser: each tab reads only what the server sent, changes go through the
 * existing commands with selections and integers, and nothing invents a
 * lifecycle for a ledger entry.
 */

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/admin/partners/p1",
}));

const ID = "p1";
const ALL = [
  "platform.staff_partner.view",
  "platform.staff_partner.manage",
  "platform.staff_commission.view",
  "platform.staff_commission.manage",
  "platform.staff_pricing.manage",
  "platform.staff_payout.manage",
  "platform.staff_payout.approve",
  "platform.staff_audit.view",
];

const DETAIL = {
  id: ID,
  code: "acme",
  display_name: "Acme Partners",
  legal_name: "Acme Partners Ltd",
  contact_name: "Priya Shah",
  contact_email: "priya@acme.example",
  payout_currency: "GBP",
  status: "active",
  version: 3,
  created_at: "2026-01-01T00:00:00Z",
  activated_at: "2026-01-02T00:00:00Z",
  status_reason: "",
  capabilities: [{ capability: "customer.provision", granted_at: "2026-01-02T00:00:00Z", expires_at: null, is_effective: true }],
  members: [
    { id: "m1", user_id: "u1", email: "boss@acme.example", role: "admin", added_at: "2026-01-02T00:00:00Z", password_set: false },
    { id: "m2", user_id: "u2", email: "ops@acme.example", role: "viewer", added_at: "2026-01-03T00:00:00Z", password_set: true },
  ],
  product_grants: [{ id: "g1", product_code: "crm", plan_code: null }],
  live_customers: 2,
};

const SUMMARY = {
  partner: { ...DETAIL },
  experience: [{ key: "reseller", label: "Reseller" }],
  capabilities: [
    { key: "customer.provision", label: "Create customer organizations", group: "customer_management", granted_at: "2026-01-02T00:00:00Z", expires_at: null },
  ],
  customers: { live: 2, active: 2, suspended: 0, ended: 1, owner_activation_pending: 1 },
  products: [{ grant_id: "g1", product_code: "crm", product_name: "CRM", plan_code: null }],
  active_subscriptions: 1,
  commission: [{ currency: "GBP", balance_minor: 5000, unpaid_minor: 2000, entries: 3 }],
  commission_rule: { code: "default", version: 1, rate_bp: 1000, product_code: "", scope: "programme_default", effective_from: "2026-01-01T00:00:00Z" },
  payouts: { open: 1, by_status: { draft: 1, ready: 0, approved: 0, paid: 0, cancelled: 0 }, latest: { id: "po1", status: "draft", currency: "GBP", period_end: "2026-02-01", payable_minor: 2000 } },
  white_label: { enabled: true, brand_configured: false, domain_status: "requested", domains: { active: 0, verified: 0, awaiting: 1 } },
  setup_progress: [
    { key: "profile", label: "Profile", state: "complete", detail: "" },
    { key: "products", label: "Products", state: "complete", detail: "" },
    { key: "pricing", label: "Pricing", state: "incomplete", detail: "Customers pay the list price." },
    { key: "commission", label: "Commission", state: "complete", detail: "programme default" },
    { key: "administrator", label: "Administrator", state: "pending", detail: "Waiting for them to choose a password." },
    { key: "branding", label: "Branding", state: "optional", detail: "" },
    { key: "domain", label: "Domain", state: "pending", detail: "requested" },
  ],
};

function customerRow(overrides: Record<string, unknown> = {}) {
  return {
    link_id: "l1",
    organization_id: "o1",
    name: "Widget Co",
    slug: "widget-co",
    organization_status: "active",
    partner_id: ID,
    partner_code: "acme",
    partner_name: "Acme Partners",
    link_status: "active",
    started_at: "2026-02-01T00:00:00Z",
    ended_at: null,
    end_reason: "",
    subscription_status: "active",
    products: ["crm"],
    seat_capacity: 5,
    owner: { activation: "pending", email: "owner@widget.example", invitation_id: "i1", expires_at: "2026-03-01T00:00:00Z", resolved_at: null },
    ...overrides,
  };
}

const PRICING = {
  currency: "GBP",
  results: [
    {
      product_code: "crm",
      product_name: "CRM",
      plan_version_id: "pv1",
      plan_code: "crm_professional",
      plan_name: "CRM Professional",
      interval: "monthly",
      currency: "GBP",
      list_price_minor: 4999,
      partner_price_minor: 4500,
      partner_price_source: "partner_book",
      discount_minor: 499,
      discount_bp: 998,
      commission_bp: 1000,
      commission_minor: 450,
      platform_net_minor: 4050,
    },
  ],
};

const COMMISSION = {
  partner_earns: true,
  rules: [{ id: "r1", code: "pc-abc", version: 2, product_code: "", rate_bp: 1500, effective_from: "2026-02-01T00:00:00Z", retired_at: null, state: "in_force", reason: "Agreed" }],
  live_versions: { "": 2 },
  in_force: { code: "pc-abc", version: 2, rate_bp: 1500, product_code: "", scope: "partner", effective_from: "2026-02-01T00:00:00Z" },
};

function routes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    [`GET /api/v1/admin/partners/${ID}`]: [ok(DETAIL)],
    [`GET /api/v1/admin/partners/${ID}/summary`]: [ok(SUMMARY)],
    [`GET /api/v1/admin/partners/${ID}/customers`]: [ok({ results: [customerRow(), customerRow({ link_id: "l2", organization_id: "o2", name: "Old Co", link_status: "ended", ended_at: "2026-02-15T00:00:00Z", subscription_status: "", products: [], organization_status: "", owner: { activation: "activated", email: "x@old.example", invitation_id: "i2", expires_at: "2026-01-01T00:00:00Z", resolved_at: "2026-01-02T00:00:00Z" } })], next_cursor: null, has_more: false })],
    [`GET /api/v1/admin/partners/${ID}/pricing`]: [ok(PRICING)],
    [`GET /api/v1/admin/partners/${ID}/commission`]: [ok(COMMISSION)],
    "GET /api/v1/admin/partners/products": [ok({ results: [{ code: "crm", name: "CRM", status: "available", plans: [] }, { code: "books", name: "Books", status: "available", plans: [] }] })],
    "GET /api/v1/admin/partners/capabilities": [ok({ results: [{ key: "customer.provision", label: "Create customer organizations", group: "customer_management" }], groups: [], presets: [] })],
    ...extra,
  };
}

const writes = (calls: Call[]) => calls.filter((c) => c.key.startsWith("POST ") || c.key.startsWith("PUT ") || c.key.startsWith("PATCH ") || c.key.startsWith("DELETE "));

function mount(permissions = ALL) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <PartnerDetailScreen partnerId={ID} />
    </AdminContextForTest>,
  );
}

async function openTab(name: string) {
  fireEvent.click(await screen.findByRole("tab", { name }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
});

describe("the tabs", () => {
  it("shows the ten sections in order, and starts on Overview", async () => {
    mockApi(routes());
    mount();
    await screen.findByRole("heading", { name: /Acme Partners/ });
    const tabs = within(screen.getByRole("tablist", { name: "Partner sections" })).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "Overview",
      "Customers",
      "Products & Pricing",
      "Subscriptions",
      "Commissions",
      "Payouts",
      "Branding",
      "Domains",
      "Users & Permissions",
      "Activity",
    ]);
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: "Setup progress" })).toBeInTheDocument();
  });

  it("hides the tabs whose data the staff member may not read", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view"]);
    await screen.findByRole("heading", { name: /Acme Partners/ });
    const labels = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(labels).not.toContain("Commissions");
    expect(labels).not.toContain("Payouts");
    expect(labels).not.toContain("Activity");
    expect(labels).toContain("Customers");
  });

  it("opens on the section named in the address and moves between tabs with the arrow keys", async () => {
    query = "tab=commissions";
    mockApi(
      routes({
        "GET /api/v1/admin/commission/accruals": [ok({ results: [], next_cursor: null, has_more: false })],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [ok(SUMMARY)],
      }),
    );
    mount();
    const tab = await screen.findByRole("tab", { name: "Commissions" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tab, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Payouts" })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps the lifecycle actions above the tabs", async () => {
    mockApi(routes());
    mount();
    expect(await screen.findByRole("button", { name: "Suspend" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Terminate" })).toBeInTheDocument();
  });
});

describe("Overview", () => {
  it("shows setup progress as text, the commission rule, and what awaits an owner", async () => {
    mockApi(routes());
    const { container } = mount();
    await screen.findByRole("heading", { name: "Setup progress" });
    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    expect(screen.getAllByText("Waiting")).toHaveLength(2);
    expect(screen.getAllByText("Optional")).toHaveLength(1);
    expect(screen.getByText("Earns 10% (programme default).")).toBeInTheDocument();
    expect(screen.getByText("1 awaiting their owner.")).toBeInTheDocument();
    expect(container.textContent).toMatch(/50\.00.*earned/);
    await expectNoViolations(container);
  });

  it("jumps to the section a card is about", async () => {
    mockApi(routes());
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Open customers" }));
    expect(screen.getByRole("tab", { name: "Customers" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Customers", () => {
  it("says an unclaimed account is pending, never onboarded, and shows an ended one as history", async () => {
    mockApi(routes());
    const { container } = mount();
    await openTab("Customers");
    const table = await screen.findByRole("table", { name: "This partner's customers" });
    const live = within(table).getByRole("link", { name: "Widget Co" }).closest("tr")!;
    expect(within(live).getByText("Owner activation pending")).toBeInTheDocument();
    expect(within(live).getByText("owner@widget.example")).toBeInTheDocument();
    const old = within(table).getByRole("link", { name: "Old Co" }).closest("tr")!;
    expect(within(old).getByText("Owner activated")).toBeInTheDocument();
    expect(within(old).getByText(/^ended /)).toBeInTheDocument();
    // An ended relationship carries none of the customer's current state.
    expect(within(old).queryByText("crm")).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/token/i);
    await expectNoViolations(container);
  });

  it("filters on the server", async () => {
    const calls = mockApi(routes());
    mount();
    await openTab("Customers");
    await screen.findByRole("table", { name: "This partner's customers" });
    fireEvent.change(screen.getByLabelText("Owner activation"), { target: { value: "pending" } });
    await waitFor(() =>
      expect(calls.some((c) => c.key === `GET /api/v1/admin/partners/${ID}/customers` && c.url.includes("activation=pending"))).toBe(true),
    );
  });
});

describe("Products & Pricing", () => {
  it("shows the server's figures and saves a price as integer minor units", async () => {
    const calls = mockApi(routes({ [`POST /api/v1/admin/partners/${ID}/prices`]: [{ status: 201, body: { id: "pr", interval: "monthly", amount_minor: 4200 } }] }));
    const { container } = mount();
    await openTab("Products & Pricing");
    const table = await screen.findByRole("table", { name: "Partner prices" });
    expect(within(table).getByText("£49.99")).toBeInTheDocument();
    expect(within(table).getByText("£45.00")).toBeInTheDocument();
    expect(within(table).getByText("£4.99 (9.98%)")).toBeInTheDocument();
    expect(within(table).getByText("£4.50 at 10%")).toBeInTheDocument();
    expect(within(table).getByText("£40.50")).toBeInTheDocument();
    await expectNoViolations(container);

    fireEvent.change(screen.getByLabelText(/New partner price for CRM Professional/), { target: { value: "42.00" } });
    fireEvent.click(within(table).getByRole("button", { name: "Save" }));
    await screen.findByText("Partner price saved.");
    expect(calls.find((c) => c.key.endsWith("/prices"))!.body).toEqual({ plan_version_id: "pv1", interval: "monthly", amount_minor: 4200 });
  });

  it("offers no price editing without the pricing permission", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view", "platform.staff_partner.manage", "platform.staff_commission.view"]);
    await openTab("Products & Pricing");
    const table = await screen.findByRole("table", { name: "Partner prices" });
    expect(within(table).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("refuses an amount that is not money and sends nothing", async () => {
    const calls = mockApi(routes());
    mount();
    await openTab("Products & Pricing");
    await screen.findByRole("table", { name: "Partner prices" });
    fireEvent.change(screen.getByLabelText(/New partner price for CRM Professional/), { target: { value: "forty" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Enter an amount in GBP/);
    expect(writes(calls)).toEqual([]);
  });

  it("assigns a commission with the version the server reported and basis points", async () => {
    const calls = mockApi(routes({ [`POST /api/v1/admin/partners/${ID}/commission`]: [{ status: 201, body: { id: "r2", code: "pc-abc", version: 3, rate_bp: 1750, effective_from: "2026-03-01T00:00:00Z", created: true } }] }));
    mount();
    await openTab("Products & Pricing");
    expect(await screen.findByText(/In force now: 15% \(partner\)/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("New rate (%)"), { target: { value: "17.5" } });
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Agreed a better rate" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign rate" }));
    await screen.findByText("Commission rate assigned.");
    expect(calls.find((c) => c.key.endsWith("/commission") && c.key.startsWith("POST"))!.body).toEqual({
      rate_bp: 1750,
      reason: "Agreed a better rate",
      expected_version: 2,
    });
  });

  it("surfaces a stale-version refusal instead of overwriting", async () => {
    mockApi(routes({ [`POST /api/v1/admin/partners/${ID}/commission`]: [{ status: 409, body: { code: "concurrent_modification", detail: "The partner's commission rule was at version 3, not 2. Re-read it and retry." } }] }));
    mount();
    await openTab("Products & Pricing");
    await screen.findByText(/In force now/);
    fireEvent.change(screen.getByLabelText("New rate (%)"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Agreed a better rate" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign rate" }));
    expect(await screen.findByText(/was at version 3, not 2/)).toBeInTheDocument();
  });

  it("does not offer rate assignment without the commission permission", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view", "platform.staff_partner.manage", "platform.staff_commission.view"]);
    await openTab("Products & Pricing");
    expect(await screen.findByText(/needs the platform\.staff_commission\.manage permission/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Assign rate" })).not.toBeInTheDocument();
  });

  it("grants a product from the server's list and can revoke a grant", async () => {
    const calls = mockApi(
      routes({
        [`POST /api/v1/admin/partners/${ID}/product-grants`]: [{ status: 201, body: { id: "g2" } }],
        [`POST /api/v1/admin/partners/${ID}/product-grants/g1/revoke`]: [ok({ id: "g1", status: "revoked" })],
      }),
    );
    mount();
    await openTab("Products & Pricing");
    const select = await screen.findByLabelText("Allow a product");
    // The options come from the server's catalog, which loads after the select
    // first renders: choosing before they are there selects nothing.
    await screen.findByRole("option", { name: "Books" });
    fireEvent.change(select, { target: { value: "books" } });
    fireEvent.click(screen.getByRole("button", { name: "Grant product" }));
    await screen.findByText("Product granted.");
    expect(calls.find((c) => c.key.endsWith("/product-grants"))!.body).toEqual({ product_code: "books" });
    fireEvent.click(screen.getAllByRole("button", { name: "Revoke" })[0]);
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/g1/revoke"))).toBe(true));
  });
});

describe("Subscriptions", () => {
  it("shows subscriptions and links to where they are changed - it writes nothing", async () => {
    const calls = mockApi(routes());
    mount();
    await openTab("Subscriptions");
    const table = await screen.findByRole("table", { name: "Customer subscriptions" });
    expect(within(table).getAllByRole("link", { name: "Manage on the customer page" })[0]).toHaveAttribute("href", "/admin/customers/o1");
    expect(writes(calls)).toEqual([]);
    expect(calls.some((c) => c.url.includes("status=live"))).toBe(true);
  });
});

describe("Commissions", () => {
  const ledger = {
    results: [
      { id: "a1", kind: "accrual", amount_minor: 500, currency: "GBP", event_at: "2026-02-01T00:00:00Z", clears_at: "2026-03-01T00:00:00Z", reason_code: "", organization_id: "o1", organization_name: "Widget Co", source_ref: "subscriptions.item:8f3a1c0d-1111", rule_code: "default", rule_version: 1, rate_bp: 1000, payout: null },
      { id: "a2", kind: "reversal", amount_minor: -200, currency: "GBP", event_at: "2026-02-10T00:00:00Z", clears_at: "2026-02-10T00:00:00Z", reason_code: "cancellation", organization_id: "o1", organization_name: "Widget Co", source_ref: "ledger.entry:a1", rule_code: "default", rule_version: 1, rate_bp: 1000, payout: { id: "po1", status: "draft" } },
    ],
    next_cursor: null,
    has_more: false,
  };

  it("shows kinds, customer, sale, rule and payout linkage - and no invented lifecycle", async () => {
    mockApi(routes({ "GET /api/v1/admin/commission/accruals": [ok(ledger)] }));
    const { container } = mount();
    await openTab("Commissions");
    const table = await screen.findByRole("table", { name: "Commission ledger entries" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("accrual")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Widget Co")).toBeInTheDocument();
    expect(within(rows[0]).getByText("default v1 · 10%")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Not in a payout")).toBeInTheDocument();
    expect(within(rows[1]).getByText("reversal")).toBeInTheDocument();
    expect(within(rows[1]).getByText("draft")).toBeInTheDocument(); // the *payout's* state
    // The kind column carries only the three real kinds.
    const kinds = rows.map((r) => within(r).getAllByRole("cell")[1].textContent ?? "");
    for (const cell of kinds) expect(cell).toMatch(/^(accrual|reversal|adjustment)/);
    expect(table.textContent).not.toMatch(/\b(approved|paid)\b/i);
    await expectNoViolations(container);
  });

  it("asks the server for this partner's rows only", async () => {
    const calls = mockApi(routes({ "GET /api/v1/admin/commission/accruals": [ok(ledger)] }));
    mount();
    await openTab("Commissions");
    await screen.findByRole("table", { name: "Commission ledger entries" });
    expect(calls.find((c) => c.key === "GET /api/v1/admin/commission/accruals")!.url).toContain(`partner_id=${ID}`);
  });
});

describe("Payouts", () => {
  const payouts = (status: string) => ({
    results: [{ id: "po1", status, currency: "GBP", period_end: "2026-02-01", gross_minor: 2000, adjustments_minor: 0, payable_minor: 2000, line_count: 2, version: 1 }],
    next_cursor: null,
    has_more: false,
  });

  it("reuses the payout lifecycle controls and filters to this partner", async () => {
    const calls = mockApi(routes({ "GET /api/v1/admin/payouts": [ok(payouts("draft"))] }));
    mount();
    await openTab("Payouts");
    const table = await screen.findByRole("table", { name: "This partner's payouts" });
    expect(within(table).getByRole("button", { name: "Mark ready" })).toBeInTheDocument();
    expect(calls.find((c) => c.key === "GET /api/v1/admin/payouts")!.url).toContain(`partner_id=${ID}`);
    expect(screen.getByText(/moves no money/)).toBeInTheDocument();
  });

  it("offers approval only to someone who holds the approve permission", async () => {
    mockApi(routes({ "GET /api/v1/admin/payouts": [ok(payouts("ready"))] }));
    const { unmount } = mount();
    await openTab("Payouts");
    await screen.findByRole("table", { name: "This partner's payouts" });
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    unmount();
    mockApi(routes({ "GET /api/v1/admin/payouts": [ok(payouts("ready"))] }));
    mount(ALL.filter((p) => p !== "platform.staff_payout.approve"));
    await openTab("Payouts");
    await screen.findByRole("table", { name: "This partner's payouts" });
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});

describe("Users & Permissions", () => {
  it("keeps the capability and member sections, and shows who has not chosen a password", async () => {
    mockApi(routes());
    mount();
    await openTab("Users & Permissions");
    expect(await screen.findByRole("table", { name: "Granted capabilities" })).toBeInTheDocument();
    const members = await screen.findByRole("table", { name: "Members" });
    const boss = within(members).getByText("boss@acme.example").closest("tr")!;
    expect(within(boss).getByText("Invitation pending")).toBeInTheDocument();
    expect(within(boss).getByRole("button", { name: "Resend setup link" })).toBeInTheDocument();
    const ops = within(members).getByText("ops@acme.example").closest("tr")!;
    expect(within(ops).getByText("Ready")).toBeInTheDocument();
    expect(within(ops).queryByRole("button", { name: "Resend setup link" })).not.toBeInTheDocument();
  });

  it("adds a person through the invitation path and can resend a setup link", async () => {
    const calls = mockApi(
      routes({
        [`POST /api/v1/admin/partners/${ID}/administrators`]: [{ status: 201, body: { id: "m3", role: "admin", invited: true } }],
        [`POST /api/v1/admin/partners/${ID}/members/m1/resend-setup`]: [ok({ sent: true })],
      }),
    );
    mount();
    await openTab("Users & Permissions");
    fireEvent.change(await screen.findByLabelText("Add a person by email"), { target: { value: "new@acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Add user" }));
    await screen.findByText(/emailed a link to choose a password/);
    expect(calls.find((c) => c.key.endsWith("/administrators"))!.body).toEqual({ email: "new@acme.example", role: "admin" });
    fireEvent.click(screen.getByRole("button", { name: "Resend setup link" }));
    await screen.findByText(/The old one no longer works/);
  });
});

describe("Activity", () => {
  it("lists the audit entries the server returned", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}/activity`]: [
          ok({
            results: [{ id: "e1", occurred_at: "2026-02-01T00:00:00Z", action: "partner.capability_granted", actor_kind: "staff", resource_type: "partners.capability_grant", resource_id: "x", outcome: "success", reason: "Set up" }],
            next_cursor: null,
            has_more: false,
          }),
        ],
      }),
    );
    const { container } = mount();
    await openTab("Activity");
    const table = await screen.findByRole("table", { name: "Partner activity" });
    expect(within(table).getByText("partner.capability_granted")).toBeInTheDocument();
    await expectNoViolations(container);
  });
});

describe("Branding and Domains", () => {
  it("shows the stored brand and links to the white-label setup", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}/brand`]: [ok({ brand: { brand_name: "Acme Brand", logo_file_id: null, accent_color: "#1a56db", theme: {}, support_email: "help@acme.example", support_url: "", version: 0, updated_at: "2026-02-01T00:00:00Z" } })],
      }),
    );
    mount();
    await openTab("Branding");
    expect(await screen.findByText("Acme Brand")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set up white-label" })).toHaveAttribute("href", `/admin/partners/white-label/${ID}`);
  });

  it("shows domain DNS proof and disables through the existing command", async () => {
    const calls = mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}/domains`]: [
          ok({ results: [{ id: "d1", hostname: "app.acme.example", status: "verification_pending", verification_record: { type: "TXT", name: "_w360-verify.app.acme.example", value: "w360-verify=abc" }, failure_code: "record_not_found", version: 1 }] }),
        ],
        "POST /api/v1/admin/domains/d1/disable": [ok({ id: "d1", status: "disabled" })],
      }),
    );
    mount();
    await openTab("Domains");
    expect(await screen.findByText(/_w360-verify\.app\.acme\.example = w360-verify=abc/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await screen.findByText("Domain disabled.");
    expect(calls.some((c) => c.key === "POST /api/v1/admin/domains/d1/disable")).toBe(true);
  });
});

describe("hostile text is text", () => {
  const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;
  it("in the header, the overview and the customer table", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok({ ...DETAIL, display_name: EVIL, contact_name: EVIL })],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [ok({ ...SUMMARY, products: [{ grant_id: "g", product_code: "x", product_name: EVIL, plan_code: null }], setup_progress: [{ key: "profile", label: "Profile", state: "complete", detail: EVIL }] })],
      }),
    );
    const { container } = mount();
    await screen.findByRole("heading", { name: "Setup progress" });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).toContain("<img");
    await openTab("Customers");
  });
});

describe("Partners → Customers", () => {
  it("lists every partner's customers with the partner named", async () => {
    mockApi({
      "GET /api/v1/admin/partners/customers": [ok({ results: [customerRow()], next_cursor: null, has_more: false })],
    });
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <AllPartnerCustomers />
      </AdminContextForTest>,
    );
    const table = await screen.findByRole("table", { name: "Partner customers" });
    expect(within(table).getByRole("link", { name: "Acme Partners" })).toHaveAttribute("href", `/admin/partners/${ID}`);
    expect(within(table).getByText("Owner activation pending")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("starts from the filters in the address", async () => {
    query = "subscription=active";
    const calls = mockApi({ "GET /api/v1/admin/partners/customers": [ok({ results: [], next_cursor: null, has_more: false })] });
    render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <AllPartnerCustomers />
      </AdminContextForTest>,
    );
    await screen.findByText("No customers match.");
    expect(calls[0].url).toContain("subscription=active");
  });
});
