import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerDetailScreen } from "./AdminPartners";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * Phase 10c, simplified by ADR-048: the partner detail. What the backend cannot
 * enforce for the browser: the header says how complete the profile is and
 * leads to Complete Your Profile, Activate waits for the server's completion
 * answer, the tabs shown are the ones the server's labels make relevant, each
 * tab reads only what the server sent, changes go through the existing commands
 * with selections and integers, and nothing invents a lifecycle for a ledger
 * entry.
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
const VIEW_ONLY = ["platform.staff_partner.view"];

const RESELLER = { key: "reseller", label: "Reseller" };
const WHITE_LABEL = { key: "white_label", label: "White-label" };
const AFFILIATE = { key: "affiliate", label: "Affiliate" };

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
    { id: "m1", user_id: "u1", email: "boss@acme.example", role: "admin", added_at: "2026-01-02T00:00:00Z", password_set: false, full_name: "", last_login_at: null, invitation_sent_at: "2026-01-02T00:00:00Z", invitation_deferred: false },
    { id: "m2", user_id: "u2", email: "ops@acme.example", role: "viewer", added_at: "2026-01-03T00:00:00Z", password_set: true, full_name: "Olu Ops", last_login_at: "2026-02-01T09:00:00Z", invitation_sent_at: "2026-01-03T00:00:00Z", invitation_deferred: false },
  ],
  product_grants: [{ id: "g1", product_code: "crm", plan_code: null, trial_allowed: true, portal_visible: true }],
  live_customers: 2,
  experience: [RESELLER, WHITE_LABEL],
};

/** An active reselling white-label partner whose profile still lacks three of
 * its seven required sections. Pricing is optional and never counted. */
const COMPLETION = {
  percent: 57,
  complete: false,
  required: 7,
  done: 4,
  sections: [
    { key: "business", label: "Business details", state: "complete", required: true, detail: "" },
    { key: "capabilities", label: "Capabilities", state: "complete", required: true, detail: "" },
    { key: "products", label: "Products", state: "complete", required: true, detail: "" },
    { key: "pricing", label: "Pricing", state: "optional", required: false, detail: "Customers pay the list price." },
    { key: "commission", label: "Commission", state: "complete", required: true, detail: "programme default" },
    { key: "administrator", label: "Administrator", state: "pending", required: true, detail: "Waiting for them to choose a password." },
    { key: "brand", label: "Brand", state: "incomplete", required: true, detail: "No brand has been set up yet." },
    { key: "domain", label: "Domain", state: "pending", required: true, detail: "requested" },
  ],
  missing: [
    { key: "administrator", label: "Administrator", detail: "Waiting for them to choose a password." },
    { key: "brand", label: "Brand", detail: "No brand has been set up yet." },
    { key: "domain", label: "Domain", detail: "requested" },
  ],
  setup_status: "active",
};

/** A draft whose profile is complete: ready for staff to activate. */
const COMPLETE = {
  percent: 100,
  complete: true,
  required: 7,
  done: 7,
  sections: COMPLETION.sections.map((s) => (s.required ? { ...s, state: "complete", detail: "" } : s)),
  missing: [],
  setup_status: "ready",
};

const SUMMARY = {
  partner: { ...DETAIL },
  experience: [RESELLER, WHITE_LABEL],
  capabilities: [
    { key: "customer.provision", label: "Create customer organizations", group: "customer_management", granted_at: "2026-01-02T00:00:00Z", expires_at: null },
  ],
  customers: { live: 2, active: 2, suspended: 0, ended: 1, owner_activation_pending: 1 },
  products: [{ grant_id: "g1", product_code: "crm", product_name: "CRM", plan_code: null, trial_allowed: true, portal_visible: true }],
  active_subscriptions: 1,
  commission: [{ currency: "GBP", balance_minor: 5000, unpaid_minor: 2000, entries: 3 }],
  commission_rule: { code: "default", version: 1, rate_bp: 1000, product_code: "", scope: "programme_default", effective_from: "2026-01-01T00:00:00Z" },
  payouts: { open: 1, by_status: { draft: 1, ready: 0, approved: 0, paid: 0, cancelled: 0 }, latest: { id: "po1", status: "draft", currency: "GBP", period_end: "2026-02-01", payable_minor: 2000 } },
  white_label: { enabled: true, brand_configured: false, domain_status: "requested", domains: { active: 0, verified: 0, awaiting: 1 } },
  referrals: { codes: [], active_codes: 0, captured: 0, converted: 0 },
  setup_progress: [],
  profile_completion: COMPLETION,
  activation: { can_activate: false, missing: COMPLETION.missing },
};

/** A draft partner, with the completion answer given. */
function draft(completion: typeof COMPLETION | typeof COMPLETE) {
  return {
    detail: { ...DETAIL, status: "draft", activated_at: null },
    summary: {
      ...SUMMARY,
      partner: { ...SUMMARY.partner, status: "draft" },
      profile_completion: { ...completion, setup_status: completion.complete ? "ready" : "setup_required" },
      activation: { can_activate: completion.missing.length === 0, missing: completion.missing },
    },
  };
}

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

const EMPTY_PAGE = { results: [], next_cursor: null, has_more: false };

function routes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    [`GET /api/v1/admin/partners/${ID}`]: [ok(DETAIL)],
    [`GET /api/v1/admin/partners/${ID}/summary`]: [ok(SUMMARY)],
    [`GET /api/v1/admin/partners/${ID}/customers`]: [ok({ results: [customerRow(), customerRow({ link_id: "l2", organization_id: "o2", name: "Old Co", link_status: "ended", ended_at: "2026-02-15T00:00:00Z", subscription_status: "", products: [], organization_status: "", owner: { activation: "activated", email: "x@old.example", invitation_id: "i2", expires_at: "2026-01-01T00:00:00Z", resolved_at: "2026-01-02T00:00:00Z" } })], next_cursor: null, has_more: false })],
    [`GET /api/v1/admin/partners/${ID}/pricing`]: [ok(PRICING)],
    [`GET /api/v1/admin/partners/${ID}/commission`]: [ok(COMMISSION)],
    "GET /api/v1/admin/commission/accruals": [ok(EMPTY_PAGE)],
    "GET /api/v1/admin/partners/products": [ok({ results: [{ code: "crm", name: "CRM", status: "available", plans: [] }, { code: "books", name: "Books", status: "available", plans: [] }] })],
    [`GET /api/v1/admin/partners/${ID}/upgrades`]: [ok({ current: "reseller", eligible: ["white_label"], status: "none", requested: null, open: null, checklist: [], history: [], products: [] })],
    "GET /api/v1/admin/partners/capabilities": [ok({ results: [{ key: "customer.provision", label: "Create customer organizations", short_label: "Create customers", group: "customer_management" }], groups: [], presets: [] })],
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

const tabNames = () => within(screen.getByRole("tablist", { name: "Partner sections" })).getAllByRole("tab").map((t) => t.textContent);

// A visually hidden suffix ("Complete<span> Brand</span>") is named "Complete
// Brand" by browsers; jsdom's accessible-name computation drops the space at the
// start of the hidden span, so those names are matched with an optional space.

/** The header's completion banner (the Overview has its own section of the same name). */
async function banner(title: "Profile incomplete" | "Profile complete") {
  return (await screen.findByText(title)).closest("section")!;
}

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
});

describe("the header", () => {
  it("names the partner, shows the server's labels as badges and its status, and offers the way back", async () => {
    mockApi(routes());
    const { container } = mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    const badges = screen.getByRole("list", { name: "Partner type, from its capabilities" });
    expect(within(badges).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Reseller", "White-label"]);
    expect(screen.getByRole("heading", { level: 1, name: "Acme Partners" }).parentElement).toHaveTextContent("active");
    expect(screen.getByRole("link", { name: "All Partners" })).toHaveAttribute("href", "/admin/partners/all");
    expect(screen.getByText(/acme · preferred currency GBP · 2 live customers/)).toBeInTheDocument();
    await screen.findByRole("heading", { name: "Profile completion" });
    await expectNoViolations(container);
  });

  it("says an incomplete profile is incomplete, what is still needed, and leads to Complete Your Profile", async () => {
    mockApi(routes());
    mount();
    const notice = await banner("Profile incomplete");
    expect(notice).toHaveTextContent("57%");
    expect(within(notice).getByText(/Still needed: Administrator, Brand, Domain\./)).toBeInTheDocument();
    expect(within(notice).getByRole("link", { name: "Complete Your Profile" })).toHaveAttribute("href", `/admin/partners/${ID}/profile`);
    expect(screen.queryByText("Profile complete")).not.toBeInTheDocument();
  });

  it("offers no Complete Your Profile link to staff who may not manage partners", async () => {
    mockApi(routes());
    mount(VIEW_ONLY);
    const notice = await banner("Profile incomplete");
    expect(within(notice).getByText(/Still needed:/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Complete Your Profile" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
  });

  it("says a draft with an incomplete profile needs setup, and keeps Activate disabled with the reason on the page", async () => {
    const d = draft(COMPLETION);
    const calls = mockApi(routes({ [`GET /api/v1/admin/partners/${ID}`]: [ok(d.detail)], [`GET /api/v1/admin/partners/${ID}/summary`]: [ok(d.summary)] }));
    const { container } = mount();
    const notice = await banner("Profile incomplete");
    expect(notice).toHaveTextContent(/stays a draft/);
    expect(screen.getByText("Setup required")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Activate" })).toBeDisabled());
    expect(screen.getByText(/^The profile is incomplete, so this partner cannot be activated yet. Still needed:/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate" })).toHaveAccessibleDescription(/cannot be activated yet/);
    // A draft is not suspended or reactivated - only activated or terminated.
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Terminate" })).toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
    await expectNoViolations(container);
  });

  it("says a complete draft is ready, and Activate goes to the server with a recorded reason and its version", async () => {
    const d = draft(COMPLETE);
    const calls = mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok(d.detail)],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [ok(d.summary)],
        [`POST /api/v1/admin/partners/${ID}/status`]: [ok({ id: ID, status: "active" })],
      }),
    );
    mount();
    const notice = await banner("Profile complete");
    expect(within(notice).getByRole("link", { name: "Edit profile" })).toHaveAttribute("href", `/admin/partners/${ID}/profile`);
    expect(within(notice).getByText(/Activate the partner when you are ready/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Complete Your Profile" })).not.toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    const activate = await screen.findByRole("button", { name: "Activate" });
    expect(activate).toBeEnabled();
    fireEvent.click(activate);
    expect(screen.queryByLabelText(/reason for/i)).not.toBeInTheDocument();
    const ask = screen.queryByRole("group", { name: /^Confirm / });
    if (ask) fireEvent.click(within(ask).getAllByRole("button")[0]);
    await screen.findByText("Partner activated.");
    expect(calls.find((c) => c.key === `POST /api/v1/admin/partners/${ID}/status`)!.body).toEqual({
      status: "active",
      reason: "Activate - Super Admin console",
      expected_version: 3,
    });
  });

  it("says the partner was created, only when it has just been", async () => {
    query = "created=1";
    mockApi(routes());
    const { unmount } = mount();
    expect(await screen.findByText("Partner created successfully.")).toHaveAttribute("role", "status");
    unmount();
    query = "";
    mockApi(routes());
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    expect(screen.queryByText("Partner created successfully.")).not.toBeInTheDocument();
  });

  it("keeps the lifecycle actions above the tabs", async () => {
    mockApi(routes());
    mount();
    expect(await screen.findByRole("button", { name: "Suspend" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Terminate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
    const suspend = screen.getByRole("button", { name: "Suspend" });
    expect(suspend.compareDocumentPosition(screen.getByRole("tablist", { name: "Partner sections" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("the tabs", () => {
  it("shows the sections a reselling white-label partner has, in order, and starts on Overview", async () => {
    mockApi(routes());
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    expect(tabNames()).toEqual([
      "Overview",
      "Profile",
      "Products",
      "Pricing",
      "Commission",
      "Customers",
      "Branding",
      "Domains",
      "Payouts",
      "Users & Permissions",
      "Activity",
    ]);
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: "Profile completion" })).toBeInTheDocument();
  });

  it("shows all twelve sections for a partner holding all three kinds", async () => {
    const all = [RESELLER, WHITE_LABEL, AFFILIATE];
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok({ ...DETAIL, experience: all })],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [ok({ ...SUMMARY, experience: all })],
      }),
    );
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    await waitFor(() => expect(tabNames()).toContain("Referrals"));
    expect(tabNames()).toEqual([
      "Overview",
      "Profile",
      "Products",
      "Pricing",
      "Commission",
      "Customers",
      "Branding",
      "Domains",
      "Referrals",
      "Payouts",
      "Users & Permissions",
      "Activity",
    ]);
  });

  it("hides the tabs whose data the staff member may not read", async () => {
    mockApi(routes());
    mount(VIEW_ONLY);
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    const labels = tabNames();
    expect(labels).not.toContain("Payouts");
    expect(labels).not.toContain("Activity");
    expect(labels).toContain("Commission");
    expect(labels).toContain("Customers");
  });

  it("gives an affiliate-only partner no Pricing, Customers, Branding or Domains, and gives it Referrals", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok({ ...DETAIL, experience: [AFFILIATE] })],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [ok({ ...SUMMARY, experience: [AFFILIATE], capabilities: [] })],
      }),
    );
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    await waitFor(() => expect(tabNames()).toContain("Referrals"));
    expect(tabNames()).toEqual(["Overview", "Profile", "Products", "Commission", "Referrals", "Payouts", "Users & Permissions", "Activity"]);
  });

  it("gives a partner that holds only commission.earn the Commission tab, and nothing a seller has", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok({ ...DETAIL, experience: [] })],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [
          ok({ ...SUMMARY, experience: [], capabilities: [{ key: "commission.earn", label: "Earn commission", group: "money", granted_at: "2026-01-02T00:00:00Z", expires_at: null }] }),
        ],
      }),
    );
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    await waitFor(() => expect(tabNames()).toContain("Commission"));
    expect(tabNames()).toEqual(["Overview", "Profile", "Products", "Commission", "Payouts", "Users & Permissions", "Activity"]);
  });

  it("opens on the section named in the address and moves between tabs with the arrow keys", async () => {
    query = "tab=commission";
    mockApi(routes());
    mount();
    const tab = await screen.findByRole("tab", { name: "Commission" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tab, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Customers" })).toHaveAttribute("aria-selected", "true");
  });

  it.each([
    ["business", "Profile"],
    ["contacts", "Profile"],
    ["commissions", "Commission"],
    ["subscriptions", "Customers"],
  ])("opens an old ?tab=%s link on %s", async (old, now) => {
    query = `tab=${old}`;
    mockApi(routes({ [`GET /api/v1/admin/partners/${ID}/profile`]: [{ status: 404, body: { code: "not_found", detail: "none" } }] }));
    mount();
    expect(await screen.findByRole("tab", { name: now })).toHaveAttribute("aria-selected", "true");
  });

  it("opens a requested tab only when it is relevant", async () => {
    query = "tab=branding";
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok({ ...DETAIL, experience: [AFFILIATE] })],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [ok({ ...SUMMARY, experience: [AFFILIATE] })],
      }),
    );
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Overview", () => {
  /** One row of the Overview's completion list, by the section's label. */
  async function completionRow(label: string) {
    const section = (await screen.findByRole("heading", { name: "Profile completion" })).closest("section")!;
    const row = within(section)
      .getAllByRole("listitem")
      .find((li) => li.querySelector("strong")?.firstChild?.textContent === label);
    if (!row) throw new Error(`no completion row ${label}`);
    return row;
  }

  it("lists the profile's sections in the server's words, each unfinished one leading to where it is filled in", async () => {
    mockApi(routes());
    const { container } = mount();
    const section = (await screen.findByRole("heading", { name: "Profile completion" })).closest("section")!;
    expect(section).toHaveTextContent("57% complete");
    expect(section).toHaveTextContent("4 of 7 required sections");

    const business = await completionRow("Business details");
    expect(within(business).getByText("Complete")).toBeInTheDocument();
    expect(within(business).queryByRole("link")).not.toBeInTheDocument();
    expect(within(business).queryByRole("button")).not.toBeInTheDocument();

    // Optional sections are listed and say so, but never count.
    const pricing = await completionRow("Pricing");
    expect(within(pricing).getByText("Not required")).toBeInTheDocument();
    expect(pricing).toHaveTextContent("(optional)");
    expect(pricing).toHaveTextContent("Customers pay the list price.");

    const brand = await completionRow("Brand");
    expect(within(brand).getByText("Incomplete")).toBeInTheDocument();
    expect(within(brand).getByRole("link", { name: /^Complete ?Brand$/ })).toHaveAttribute("href", `/admin/partners/${ID}/profile?step=brand`);

    const domain = await completionRow("Domain");
    expect(within(domain).getByText("Waiting")).toBeInTheDocument();
    expect(within(domain).getByRole("link", { name: /^Complete ?Domain$/ })).toHaveAttribute("href", `/admin/partners/${ID}/profile?step=domain`);

    // The administrator is invited from Users & Permissions, not from a profile step.
    const admin = await completionRow("Administrator");
    expect(within(admin).getByText("Waiting")).toBeInTheDocument();
    expect(admin).toHaveTextContent("Waiting for them to choose a password.");
    expect(within(admin).queryByRole("link")).not.toBeInTheDocument();
    fireEvent.click(within(admin).getByRole("button", { name: /^Open Users & Permissions ?to deal with Administrator$/ }));
    expect(screen.getByRole("tab", { name: "Users & Permissions" })).toHaveAttribute("aria-selected", "true");
    await screen.findByRole("table", { name: "Members" });
    await expectNoViolations(container);
  });

  it("offers no profile step links to staff who may not manage partners", async () => {
    mockApi(routes());
    mount(VIEW_ONLY);
    const brand = await completionRow("Brand");
    expect(within(brand).queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows the commission rule and what awaits an owner", async () => {
    mockApi(routes());
    const { container } = mount();
    await screen.findByRole("heading", { name: "Profile completion" });
    expect(screen.getByText("Earns 10% (programme default).")).toBeInTheDocument();
    expect(screen.getByText("1 awaiting their owner.")).toBeInTheDocument();
    expect(container.textContent).toMatch(/50\.00.*earned/);
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
    await screen.findByRole("table", { name: "Customer subscriptions" });
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

  it("shows subscriptions beneath the customers and links to where they are changed - it writes nothing", async () => {
    const calls = mockApi(routes());
    mount();
    await openTab("Customers");
    const customers = await screen.findByRole("table", { name: "This partner's customers" });
    const table = await screen.findByRole("table", { name: "Customer subscriptions" });
    expect(customers.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(table).getAllByRole("link", { name: "Manage on the customer page" })[0]).toHaveAttribute("href", "/admin/customers/o1");
    expect(writes(calls)).toEqual([]);
    expect(calls.some((c) => c.url.includes("status=live"))).toBe(true);
  });
});

describe("Products", () => {
  it("allows a product from the server's list and can remove a grant", async () => {
    const calls = mockApi(
      routes({
        [`POST /api/v1/admin/partners/${ID}/product-grants`]: [{ status: 201, body: { id: "g2" } }],
        [`POST /api/v1/admin/partners/${ID}/product-grants/g1/revoke`]: [ok({ id: "g1", status: "revoked" })],
      }),
    );
    const { container } = mount();
    await openTab("Products");
    const grants = await screen.findByRole("table", { name: "Product grants" });
    expect(within(grants).getByRole("rowheader", { name: "crm" })).toBeInTheDocument();
    const select = await screen.findByLabelText("Allow a product");
    // The options come from the server's catalog, which loads after the select
    // first renders: choosing before they are there selects nothing.
    await screen.findByRole("option", { name: "Books" });
    // A product already granted is not offered again.
    expect(within(select).queryByRole("option", { name: "CRM" })).not.toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.change(select, { target: { value: "books" } });
    fireEvent.click(screen.getByRole("button", { name: "Allow product" }));
    await screen.findByText("Product allowed.");
    expect(calls.find((c) => c.key.endsWith("/product-grants"))!.body).toEqual({ product_code: "books" });
    fireEvent.click(screen.getByRole("button", { name: /^Remove ?crm$/ }));
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/g1/revoke"))).toBe(true));
    expect(await screen.findByText("Product removed.")).toBeInTheDocument();
  });

  it("offers no grant changes to staff who may not manage partners", async () => {
    mockApi(routes());
    mount(VIEW_ONLY);
    await openTab("Products");
    await screen.findByRole("table", { name: "Product grants" });
    expect(screen.queryByLabelText("Allow a product")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
  });

  it("lets staff assign the products an affiliate may promote, not products to sell (ADR-054)", async () => {
    const referral = { capability: "referral.create", granted_at: "2026-01-02T00:00:00Z", expires_at: null, is_effective: true };
    const calls = mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok({ ...DETAIL, experience: [AFFILIATE], product_grants: [], capabilities: [referral] })],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [
          ok({ ...SUMMARY, experience: [AFFILIATE], products: [], capabilities: [{ key: "referral.create", label: "Referral links", group: "", granted_at: "2026-01-02T00:00:00Z", expires_at: null }] }),
        ],
        [`GET /api/v1/admin/partners/${ID}/promotions`]: [
          ok({
            grants: [
              { id: "g1", product_code: "books", product_name: "Books", status: "active", granted_at: "2026-10-01T00:00:00Z", revoked_at: null },
              { id: "g0", product_code: "crm", product_name: "CRM", status: "revoked", granted_at: "2026-09-01T00:00:00Z", revoked_at: "2026-09-20T00:00:00Z" },
            ],
          }),
        ],
        [`POST /api/v1/admin/partners/${ID}/promotions`]: [{ status: 201, body: { id: "g2" } }],
      }),
    );
    mount();
    await openTab("Products");
    const promoted = (await screen.findByRole("heading", { name: "Products this partner may promote" })).closest("section")!;
    const table = await within(promoted).findByRole("table", { name: "Promotion grants" });
    expect(within(table).getByRole("rowheader", { name: "Books" })).toBeInTheDocument();
    expect(promoted).toHaveTextContent(/Promoting a product is not selling it/);
    expect(screen.queryByRole("table", { name: "Product grants" })).not.toBeInTheDocument();
    const select = within(promoted).getByLabelText("Assign a product to promote");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Choose…", "CRM"]); // Books is already assigned
    fireEvent.change(select, { target: { value: "crm" } });
    fireEvent.click(within(promoted).getByRole("button", { name: "Assign product" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST /api/v1/admin/partners/${ID}/promotions`)).toBe(true));
    expect(calls.find((c) => c.key === `POST /api/v1/admin/partners/${ID}/promotions`)!.body).toEqual({ product_code: "crm" });
  });
});

describe("Pricing", () => {
  it("shows the server's figures and saves a price as integer minor units", async () => {
    const calls = mockApi(routes({ [`POST /api/v1/admin/partners/${ID}/prices`]: [{ status: 201, body: { id: "pr", interval: "monthly", amount_minor: 4200 } }] }));
    const { container } = mount();
    await openTab("Pricing");
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
    await openTab("Pricing");
    const table = await screen.findByRole("table", { name: "Partner prices" });
    expect(within(table).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("refuses an amount that is not money and sends nothing", async () => {
    const calls = mockApi(routes());
    mount();
    await openTab("Pricing");
    await screen.findByRole("table", { name: "Partner prices" });
    fireEvent.change(screen.getByLabelText(/New partner price for CRM Professional/), { target: { value: "forty" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Enter an amount in GBP/);
    expect(writes(calls)).toEqual([]);
  });
});

describe("Commission", () => {
  it("assigns a commission with the version the server reported and basis points", async () => {
    const calls = mockApi(routes({ [`POST /api/v1/admin/partners/${ID}/commission`]: [{ status: 201, body: { id: "r2", code: "pc-abc", version: 3, rate_bp: 1750, effective_from: "2026-03-01T00:00:00Z", created: true } }] }));
    mount();
    await openTab("Commission");
    expect(await screen.findByText(/In force now: 15% \(partner\)/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("New rate (%)"), { target: { value: "17.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign rate" }));
    await screen.findByText("Commission rate assigned.");
    expect(calls.find((c) => c.key.endsWith("/commission") && c.key.startsWith("POST"))!.body).toEqual({
      rate_bp: 1750,
      reason: "Assign commission rate - Super Admin console",
      expected_version: 2,
    });
  });

  it("surfaces a stale-version refusal instead of overwriting", async () => {
    mockApi(routes({ [`POST /api/v1/admin/partners/${ID}/commission`]: [{ status: 409, body: { code: "concurrent_modification", detail: "The partner's commission rule was at version 3, not 2. Re-read it and retry." } }] }));
    mount();
    await openTab("Commission");
    await screen.findByText(/In force now/);
    fireEvent.change(screen.getByLabelText("New rate (%)"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign rate" }));
    expect(await screen.findByText(/was at version 3, not 2/)).toBeInTheDocument();
  });

  it("does not offer rate assignment without the commission permission", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view", "platform.staff_partner.manage", "platform.staff_commission.view"]);
    await openTab("Commission");
    expect(await screen.findByText(/needs the platform\.staff_commission\.manage permission/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Assign rate" })).not.toBeInTheDocument();
  });

  const ledger = {
    results: [
      { id: "a1", kind: "accrual", amount_minor: 500, currency: "GBP", event_at: "2026-02-01T00:00:00Z", clears_at: "2026-03-01T00:00:00Z", reason_code: "", organization_id: "o1", organization_name: "Widget Co", source_ref: "subscriptions.item:8f3a1c0d-1111", rule_code: "default", rule_version: 1, rate_bp: 1000, payout: null },
      { id: "a2", kind: "reversal", amount_minor: -200, currency: "GBP", event_at: "2026-02-10T00:00:00Z", clears_at: "2026-02-10T00:00:00Z", reason_code: "cancellation", organization_id: "o1", organization_name: "Widget Co", source_ref: "ledger.entry:a1", rule_code: "default", rule_version: 1, rate_bp: 1000, payout: { id: "po1", status: "draft" } },
    ],
    next_cursor: null,
    has_more: false,
  };

  it("shows the ledger's kinds, customer, sale, rule and payout linkage - and no invented lifecycle", async () => {
    mockApi(routes({ "GET /api/v1/admin/commission/accruals": [ok(ledger)] }));
    const { container } = mount();
    await openTab("Commission");
    expect(await screen.findByRole("heading", { name: "Commission earned" })).toBeInTheDocument();
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

  it("asks the server for this partner's ledger rows only", async () => {
    const calls = mockApi(routes({ "GET /api/v1/admin/commission/accruals": [ok(ledger)] }));
    mount();
    await openTab("Commission");
    await screen.findByRole("table", { name: "Commission ledger entries" });
    expect(calls.find((c) => c.key === "GET /api/v1/admin/commission/accruals")!.url).toContain(`partner_id=${ID}`);
  });

  it("shows no ledger to staff who may not read commission", async () => {
    const calls = mockApi(routes());
    mount(VIEW_ONLY);
    await openTab("Commission");
    await screen.findByText(/In force now/);
    expect(screen.queryByRole("heading", { name: "Commission earned" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.key === "GET /api/v1/admin/commission/accruals")).toBe(false);
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
  it("keeps the experience, capability and member sections, and shows who has not chosen a password", async () => {
    mockApi(routes());
    mount();
    await openTab("Users & Permissions");
    const experience = await screen.findByRole("heading", { name: "Experience" });
    const caps = await screen.findByRole("table", { name: "Granted capabilities" });
    // The experience comes first, then the capabilities and the people.
    expect(experience.compareDocumentPosition(caps) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const members = await screen.findByRole("table", { name: "Members" });
    const boss = within(members).getByText("boss@acme.example").closest("tr")!;
    expect(within(boss).getByText(/^Invitation sent/)).toBeInTheDocument();
    expect(within(boss).getByRole("button", { name: /Resend setup link/ })).toBeInTheDocument();
    const ops = within(members).getByText("ops@acme.example").closest("tr")!;
    expect(within(ops).getByText("Active")).toBeInTheDocument();
    expect(within(ops).queryByRole("button", { name: /Resend setup link/ })).not.toBeInTheDocument();
  });

  it("invites a person through the invitation path and can resend a setup link", async () => {
    const calls = mockApi(
      routes({
        [`POST /api/v1/admin/partners/${ID}/administrators`]: [{ status: 201, body: { id: "m3", role: "admin", invited: true } }],
        [`POST /api/v1/admin/partners/${ID}/members/m1/resend-setup`]: [ok({ sent: true })],
      }),
    );
    mount();
    await openTab("Users & Permissions");
    fireEvent.change(await screen.findByLabelText(/Email address/), { target: { value: "new@acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    await screen.findByText(/a link to choose one was emailed/);
    expect(calls.find((c) => c.key.endsWith("/administrators"))!.body).toEqual({ email: "new@acme.example", role: "admin" });
    fireEvent.click(screen.getByRole("button", { name: /Resend setup link/ }));
    await screen.findByText(/The old one no longer works/);
  });
});

describe("Activity", () => {
  it("lists the audit entries the server returned, in words", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}/activity`]: [
          ok({
            results: [
              {
                id: "e1",
                occurred_at: "2026-02-01T00:00:00Z",
                action: "partner.capability_granted",
                label: "Capability granted",
                actor: "staff@example.com",
                actor_kind: "staff",
                target: "Sell products",
                resource_type: "partners.capability_grant",
                resource_id: "x",
                outcome: "success",
                reason: "Set up",
              },
            ],
            next_cursor: null,
            has_more: false,
          }),
        ],
      }),
    );
    const { container } = mount();
    await openTab("Activity");
    const table = await screen.findByRole("table", { name: "Partner activity" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Timestamp", "Actor", "Action", "Target", "Reason"]);
    expect(within(table).getByText("Capability granted")).toBeInTheDocument();
    expect(within(table).getByText("staff@example.com")).toBeInTheDocument();
    expect(within(table).getByText("Sell products")).toBeInTheDocument();
    await expectNoViolations(container);
  });
});

describe("Branding and Domains", () => {
  it("shows the stored brand and links to the brand step of Complete Your Profile", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}/brand`]: [ok({ brand: { brand_name: "Acme Brand", logo_file_id: null, accent_color: "#1a56db", theme: {}, support_email: "help@acme.example", support_url: "", version: 0, updated_at: "2026-02-01T00:00:00Z" } })],
      }),
    );
    mount();
    await openTab("Branding");
    expect(await screen.findByText("Acme Brand")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit brand identity" })).toHaveAttribute("href", `/admin/partners/${ID}/profile?step=brand`);
    expect(screen.queryByRole("link", { name: /Set up white-label/ })).not.toBeInTheDocument();
  });

  it("shows domain DNS proof, disables through the existing command, and links to the domain step", async () => {
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
    expect(screen.getByRole("link", { name: "Request or verify a domain" })).toHaveAttribute("href", `/admin/partners/${ID}/profile?step=domain`);
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await screen.findByText("Domain disabled.");
    expect(calls.some((c) => c.key === "POST /api/v1/admin/domains/d1/disable")).toBe(true);
  });
});

describe("hostile text is text", () => {
  const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;
  it("in the header, the completion banner, the overview and the customer table", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok({ ...DETAIL, display_name: EVIL, contact_name: EVIL })],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [
          ok({
            ...SUMMARY,
            experience: [{ key: "reseller", label: EVIL }],
            products: [{ grant_id: "g", product_code: "x", product_name: EVIL, plan_code: null }],
            profile_completion: {
              ...COMPLETION,
              sections: [{ key: "business", label: EVIL, state: "incomplete", required: true, detail: EVIL }],
              missing: [{ key: "business", label: EVIL, detail: EVIL }],
            },
          }),
        ],
        [`GET /api/v1/admin/partners/${ID}/customers`]: [ok({ results: [customerRow({ name: EVIL, owner: { activation: "pending", email: EVIL, invitation_id: "i1", expires_at: null, resolved_at: null } })], next_cursor: null, has_more: false })],
      }),
    );
    const { container } = mount();
    await screen.findByRole("heading", { name: "Profile completion" });
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(EVIL);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).toContain("<img");
    await openTab("Customers");
    await screen.findByRole("table", { name: "This partner's customers" });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });
});

describe("Referrals", () => {
  const AFFILIATE_DETAIL = {
    ...DETAIL,
    capabilities: [
      { capability: "referral.create", granted_at: "2026-01-02T00:00:00Z", expires_at: null, is_effective: true },
      { capability: "commission.earn", granted_at: "2026-01-02T00:00:00Z", expires_at: null, is_effective: true },
    ],
    experience: [AFFILIATE],
  };
  const AFFILIATE_SUMMARY = {
    ...SUMMARY,
    experience: [AFFILIATE],
    white_label: { enabled: false, brand_configured: false, domain_status: "none", domains: { active: 0, verified: 0, awaiting: 0 } },
    referrals: { codes: ["K7Q2M9"], active_codes: 1, captured: 4, converted: 1 },
  };
  const REFERRALS = {
    summary: { captured: 3, converted: 1, expired: 0 },
    codes: [{ id: "c1", code: "K7Q2M9", label: "Spring", status: "active", created_at: "2026-02-01T00:00:00Z" }],
    results: [
      { id: "r1", code: "K7Q2M9", label: "Spring", status: "converted", captured_at: "2026-02-02T00:00:00Z", converted_at: "2026-02-03T00:00:00Z" },
      { id: "r2", code: "K7Q2M9", label: "Spring", status: "captured", captured_at: "2026-02-04T00:00:00Z", converted_at: null },
    ],
    next_cursor: null,
    has_more: false,
  };

  it("shows a partner wearing Affiliate its referrals, and says it is not white-label", async () => {
    mockApi(
      routes({
        [`GET /api/v1/admin/partners/${ID}`]: [ok(AFFILIATE_DETAIL)],
        [`GET /api/v1/admin/partners/${ID}/summary`]: [ok(AFFILIATE_SUMMARY)],
        [`GET /api/v1/admin/partners/${ID}/referrals`]: [ok(REFERRALS)],
      }),
    );
    const { container } = mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    // The overview says how white-label is added, and counts the referrals.
    expect(await screen.findByText(/Not a white-label partner\. Add the White-label experience under Users & Permissions/)).toBeInTheDocument();
    expect(screen.getByText("1 converted of 4 captured")).toBeInTheDocument();

    await openTab("Referrals");
    const codes = await screen.findByRole("table", { name: "Referral codes" });
    expect(within(codes).getByText("K7Q2M9")).toBeInTheDocument();
    const referrals = screen.getByRole("table", { name: "Referrals" });
    expect(within(referrals).getAllByRole("row")).toHaveLength(3); // header + 2
    // Counts by state, in words.
    expect(screen.getByText("Waiting to convert").nextElementSibling?.textContent).toBe("3");
    // No column could hold who was referred.
    expect(within(referrals).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Code", "Status", "Captured", "Converted", "Attribution"]);
    await expectNoViolations(container);
  });
});

// --- the upgrade request (ADR-049) ----------------------------------------------------------------------

describe("an open upgrade request", () => {
  const UPGRADES = {
    current: "affiliate",
    eligible: [],
    status: "pending_approval",
    requested: "reseller",
    open: {
      id: "r1",
      partner_id: ID,
      from_experience: "affiliate",
      to_experience: "reseller",
      status: "pending_approval",
      proposal: { products: ["crm"], pricing: { preference: "standard", notes: "" } },
      requested_at: "2026-10-01T10:00:00Z",
      submitted_at: "2026-10-02T10:00:00Z",
      decided_at: null,
      info_request_note: "",
      decision_note: "",
      capabilities_added: [],
      capabilities_removed: [],
      products_granted: [],
      version: 2,
      editable: false,
      requested_by: "boss@acme.example",
      decided_by: null,
      commercial_reference: "",
      correlation_id: null,
    },
    checklist: [{ key: "business", label: "Business details", required: true, done: true, detail: "" }],
    history: [],
    products: [{ code: "crm", name: "CRM" }],
  };
  const withUpgrade = () => routes({ [`GET /api/v1/admin/partners/${ID}/upgrades`]: [ok(UPGRADES)] });

  it("is flagged above the tabs and decided on Users & Permissions, beside the experience", async () => {
    mockApi(withUpgrade());
    const { container } = mount();
    const notice = await screen.findByRole("region", { name: "Upgrade request" });
    expect(notice).toHaveTextContent("Upgrade to Reseller requested");
    fireEvent.click(within(notice).getByRole("button", { name: "Review the upgrade request" }));
    expect(screen.getByRole("tab", { name: "Users & Permissions" })).toHaveAttribute("aria-selected", "true");
    const panel = await screen.findByRole("heading", { level: 2, name: "Upgrade" });
    expect(panel.closest("section")).toHaveTextContent("pending approval");
    expect(screen.getByRole("button", { name: "Approve upgrade" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Experience" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("leads to the partner's own tabs that the decision depends on", async () => {
    mockApi(withUpgrade());
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Review the upgrade request" }));
    const form = (await screen.findByRole("heading", { name: "Decision" })).closest("form")!;
    fireEvent.click(within(form).getByRole("button", { name: "Products" }));
    expect(screen.getByRole("tab", { name: "Products" })).toHaveAttribute("aria-selected", "true");
  });

  it("offers staff who may only view no decision", async () => {
    mockApi(withUpgrade());
    mount(VIEW_ONLY);
    fireEvent.click(await screen.findByRole("button", { name: "Review the upgrade request" }));
    expect(await screen.findByText("You can read this request but not decide it.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve upgrade" })).not.toBeInTheDocument();
  });

  it("changes nothing on the page when there is no request", async () => {
    mockApi(routes());
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    expect(screen.queryByRole("region", { name: "Upgrade request" })).not.toBeInTheDocument();
  });
});
