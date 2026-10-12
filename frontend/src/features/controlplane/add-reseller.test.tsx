import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AddReseller, suggestCode } from "./AddReseller";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * The Add Reseller wizard (nine steps). What the backend cannot enforce for the
 * browser is checked here: that the browser sends selections and integers and
 * never a computed figure, that nothing is created before Save draft or
 * Activate, that required and optional fields behave as marked, that the server's
 * problems land on the fields they name, and that a command which finished is
 * not repeated after a verification prompt or a retry.
 */

const { push } = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push, refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/partners/resellers/new",
}));

const MANAGE = ["platform.staff_partner.view", "platform.staff_partner.manage", "platform.staff_pricing.manage", "platform.staff_commission.manage"];

const PRESET = [
  "customer.provision",
  "customer.manage",
  "subscription.sell",
  "subscription.manage",
  "pricing.view",
  "commission.earn",
  "commission.view",
  "payout.view",
  "support.access",
];

const CATALOG = {
  results: [
    { key: "customer.provision", label: "Create customer organizations", short_label: "Create customers", group: "customer_management" },
    { key: "customer.manage", label: "Manage the commercial setup of related customers", short_label: "Manage customers", group: "customer_management" },
    { key: "subscription.sell", label: "Sell approved products and plans to related customers", short_label: "Sell products", group: "products_sales" },
    { key: "subscription.manage", label: "Change, cancel and activate related customers' subscriptions", short_label: "Manage subscriptions", group: "products_sales" },
    { key: "pricing.view", label: "See the prices approved for this partner", short_label: "View pricing", group: "products_sales" },
    { key: "commission.earn", label: "Earn commission on attributed sales", short_label: "Earn commissions", group: "commercial" },
    { key: "commission.view", label: "View earned commission", short_label: "View commissions", group: "commercial" },
    { key: "payout.view", label: "View payouts", short_label: "View payouts", group: "commercial" },
    { key: "support.access", label: "Run read-only access diagnostics for related customers", short_label: "Customer support access", group: "support" },
    { key: "branding.manage", label: "Configure white-label branding", short_label: "Manage branding", group: "white_label" },
    { key: "domain.manage", label: "Register and activate custom domains", short_label: "Manage custom domains", group: "white_label" },
    { key: "referral.create", label: "Create referral codes", short_label: "Create referral codes", group: "referrals" },
  ],
  groups: [
    { key: "customer_management", label: "Customer management", capabilities: ["customer.provision", "customer.manage"] },
    { key: "products_sales", label: "Products and sales", capabilities: ["subscription.sell", "subscription.manage", "pricing.view"] },
    { key: "commercial", label: "Commercial", capabilities: ["commission.earn", "commission.view", "payout.view"] },
    { key: "support", label: "Support", capabilities: ["support.access"] },
    { key: "white_label", label: "White-label", capabilities: ["branding.manage", "domain.manage"] },
    { key: "referrals", label: "Referrals", capabilities: ["referral.create"] },
  ],
  presets: [
    { key: "standard_reseller", label: "Standard Reseller", description: "Create and manage customers and sell approved products.", capabilities: PRESET },
  ],
};

const PRODUCTS = {
  results: [
    {
      code: "crm",
      name: "CRM",
      status: "available",
      plans: [{ plan_version_id: "pv-pro", plan_code: "crm_professional", plan_name: "CRM Professional", included_seats: 10, trial_days: 14 }],
      trial: { enabled: true, duration_days: 14, seat_limit: 5, payment_method_required: false, eligible_plan_codes: ["crm_professional"] },
    },
    { code: "books", name: "Books", status: "available", plans: [{ plan_version_id: "pv-books", plan_code: "books_basic", plan_name: "Books Basic", included_seats: 3, trial_days: 0 }], trial: null },
    { code: "old", name: "Old product", status: "draft", plans: [], trial: null },
  ],
};

const PRICING_ROW = {
  product_code: "crm",
  product_name: "CRM",
  plan_version_id: "pv-pro",
  plan_code: "crm_professional",
  plan_name: "CRM Professional",
  interval: "monthly",
  currency: "GBP",
  list_price_minor: 4999,
  partner_price_minor: 4500,
  partner_price_source: "proposed",
  discount_minor: 499,
  discount_bp: 998,
  commission_bp: 1500,
  commission_minor: 675,
  platform_net_minor: 3825,
};

function reviewBody(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    problems: [],
    partner: { code: "acme-resell", display_name: "Acme Resell", legal_name: "", contact_name: "Priya Shah", contact_email: "priya@acme.example" },
    profile: {
      business: { business_email: "hello@acme.example", country: "GB" },
      contacts: { primary: { name: "Priya Shah", job_title: "", email: "priya@acme.example", phone: "" }, billing: null, technical: null, support: null },
      support: {},
      payout: { frequency: "manual" },
      has_internal_notes: false,
    },
    capabilities: PRESET.map((key) => ({ key, label: CATALOG.results.find((c) => c.key === key)!.label })),
    experience: [{ key: "reseller", label: "Reseller" }],
    products: [{ code: "crm", name: "CRM", plans: [{ plan_code: "crm_professional", plan_name: "CRM Professional" }] }],
    pricing_mode: "custom",
    pricing: [PRICING_ROW],
    trials: [
      {
        product_code: "crm",
        product_name: "CRM",
        platform_trial_enabled: true,
        duration_days: 14,
        seat_limit: 5,
        eligible_plan_codes: ["crm_professional"],
        partner_allows: true,
        available_to_customers: true,
        portal_visible: true,
      },
    ],
    commission: {
      mode: "custom",
      rate_bp: 1500,
      programme_default_bp: 1000,
      source: "custom",
      product_rates: { crm: 1500 },
      method: "percentage",
      scope: "all",
      product_codes: [],
      effective_from: null,
      hold_days: 30,
      example: { currency: "GBP", purchase_minor: 1000000, commission_minor: 150000, platform_net_minor: 850000, rate_bp: 1500 },
    },
    administrator: { email: "boss@acme.example", role: "admin", first_name: "Bo", last_name: "Boss", send_invitation: "on_activation" },
    white_label: { requested: false, capabilities_added: [] },
    warnings: [],
    readiness: { can_activate: true, missing: [] },
    missing_optional: [
      { key: "website", label: "Website" },
      { key: "white_label", label: "White-label" },
    ],
    ...overrides,
  };
}

const PARTNER = "/api/v1/admin/partners/new-partner";

function routes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    "GET /api/v1/admin/partners/capabilities": [ok(CATALOG)],
    "GET /api/v1/admin/partners/products": [ok(PRODUCTS)],
    "POST /api/v1/admin/partners/reseller-review": [ok(reviewBody())],
    "POST /api/v1/admin/partners": [{ status: 201, body: { id: "new-partner", code: "acme-resell", version: 3, status: "draft" } }],
    [`PUT ${PARTNER}/profile`]: [ok({ partner_id: "new-partner", version: 4 })],
    [`POST ${PARTNER}/capabilities`]: [{ status: 201, body: { id: "g", capability: "x" } }],
    [`POST ${PARTNER}/product-grants`]: [{ status: 201, body: { id: "pg" } }],
    [`POST ${PARTNER}/prices`]: [{ status: 201, body: { id: "pr", interval: "monthly", amount_minor: 4500 } }],
    [`POST ${PARTNER}/commission`]: [{ status: 201, body: { id: "r", version: 1, created: true } }],
    [`POST ${PARTNER}/administrators`]: [{ status: 201, body: { id: "m", role: "admin", invited: true } }],
    [`POST ${PARTNER}/status`]: [{ status: 200, body: { id: "new-partner", status: "active" } }],
    ...extra,
  };
}

const writes = (calls: Call[]) => calls.filter((c) => /^(POST|PUT|PATCH) /.test(c.key) && !c.key.endsWith("/reseller-review"));
const reviews = (calls: Call[]) => calls.filter((c) => c.key === "POST /api/v1/admin/partners/reseller-review");
const bodyOf = (call: Call) => call.body as Record<string, unknown>;
const lastReview = (calls: Call[]) => bodyOf(reviews(calls).at(-1)!);
const endpoint = (call: Call) => call.key.replace("/api/v1/admin/partners", "").replace("/new-partner", "");

function mount(permissions = MANAGE) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <AddReseller />
    </AdminContextForTest>,
  );
}

const cont = () => fireEvent.click(screen.getByRole("button", { name: "Save & continue" }));
const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const stepHeading = (n: number) => screen.findByRole("heading", { name: new RegExp(`^Step ${n} of 9`) });

async function fillBusiness(name = "Acme Resell") {
  fireEvent.change(await screen.findByLabelText(/Company or partner name/), { target: { value: name } });
  type(/^Business email/, "hello@acme.example");
  type(/^Country/, "GB");
}

async function fillContacts() {
  fireEvent.change(await screen.findByLabelText(/Primary contact name/), { target: { value: "Priya Shah" } });
  type(/Primary contact email/, "priya@acme.example");
}

async function toContacts() {
  await fillBusiness();
  cont();
  await stepHeading(2);
}
async function toCapabilities() {
  await toContacts();
  await fillContacts();
  cont();
  await stepHeading(3);
  await screen.findByRole("checkbox", { name: "Create customers" });
}
async function toProducts() {
  await toCapabilities();
  cont();
  await stepHeading(4);
  await screen.findByRole("checkbox", { name: "CRM" });
}
async function toPricing() {
  await toProducts();
  fireEvent.click(screen.getByRole("checkbox", { name: "CRM" }));
  cont();
  await stepHeading(5);
}
async function toCommission() {
  await toPricing();
  cont();
  await stepHeading(6);
}
async function toAdministrator() {
  await toCommission();
  cont();
  await stepHeading(7);
}
async function toWhiteLabel() {
  await toAdministrator();
  type("First name (optional)", "Bo");
  type("Last name (optional)", "Boss");
  type(/Administrator's email/, "boss@acme.example");
  cont();
  await stepHeading(8);
}
async function toReview() {
  await toWhiteLabel();
  cont();
  await stepHeading(9);
  await screen.findByRole("button", { name: "Save draft" });
}

beforeEach(() => {
  vi.restoreAllMocks();
  push.mockClear();
});

describe("the wizard frame", () => {
  it("shows nine steps as an ordered list with the current one marked", async () => {
    mockApi(routes());
    mount();
    await screen.findByLabelText(/Company or partner name/);
    const nav = screen.getByRole("navigation", { name: "Setup steps" });
    const items = within(nav).getAllByRole("listitem");
    expect(items).toHaveLength(9);
    expect(items.map((li) => li.getAttribute("aria-current"))).toEqual(["step", null, null, null, null, null, null, null, null]);
    expect(nav.querySelector("ol")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Step 1 of 9: Business details" })).toBeInTheDocument();
    await fillBusiness();
    cont();
    await stepHeading(2);
    expect(within(nav).getAllByRole("listitem").map((li) => li.getAttribute("aria-current"))).toEqual([
      null,
      "step",
      null,
      null,
      null,
      null,
      null,
      null,
      null,
    ]);
  });

  it("moves focus to the step heading when the step changes", async () => {
    mockApi(routes());
    mount();
    await fillBusiness();
    cont();
    const heading = await stepHeading(2);
    await waitFor(() => expect(heading).toHaveFocus());
  });

  it("goes back, and reopens a step already passed from the progress list", async () => {
    mockApi(routes());
    mount();
    await toCapabilities();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await stepHeading(2);
    expect(screen.getByLabelText(/Primary contact name/)).toHaveValue("Priya Shah");
    cont();
    await stepHeading(3);
    fireEvent.click(within(screen.getByRole("navigation", { name: "Setup steps" })).getByRole("button", { name: /Business details/ }));
    await stepHeading(1);
    expect(screen.getByLabelText(/Company or partner name/)).toHaveValue("Acme Resell");
    // A step not yet reached is not a button.
    expect(within(screen.getByRole("navigation", { name: "Setup steps" })).queryByRole("button", { name: /Review/ })).not.toBeInTheDocument();
  });

  it("warns before the tab is closed while there is unsaved work, and not before", async () => {
    mockApi(routes());
    mount();
    const name = await screen.findByLabelText(/Company or partner name/);
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    fireEvent.change(name, { target: { value: "Acme" } });
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  it("says so plainly for someone who may not manage partners", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view"]);
    expect(await screen.findByRole("alert")).toHaveTextContent(/permission to create and change partners/);
    expect(screen.queryByLabelText(/Company or partner name/)).not.toBeInTheDocument();
  });
});

describe("step 1: business details", () => {
  it("suggests a code from the name until the code is edited by hand", async () => {
    mockApi(routes());
    mount();
    const name = await screen.findByLabelText(/Company or partner name/);
    fireEvent.change(name, { target: { value: "Acme Résell & Co." } });
    expect(screen.getByLabelText(/^Code/)).toHaveValue("acme-resell-co");
    fireEvent.change(screen.getByLabelText(/^Code/), { target: { value: "my-code" } });
    fireEvent.change(name, { target: { value: "Something else" } });
    expect(screen.getByLabelText(/^Code/)).toHaveValue("my-code");
    expect(suggestCode("123 Numbers")).toBe("p-123-numbers");
    expect(suggestCode("!!!")).toBe("");
  });

  it("marks what is required and says what is optional", async () => {
    mockApi(routes());
    mount();
    await screen.findByLabelText(/Company or partner name/);
    for (const label of [/^Company or partner name/, /^Code/, /^Payout currency/, /^Business email/, /^Country/]) {
      const input = screen.getByLabelText(label);
      expect(input).toBeRequired();
      expect(input.closest("div")?.querySelector("label")?.textContent).toMatch(/\*$/);
    }
    for (const label of [
      "Legal business name (optional)",
      "Alternative email (optional)",
      "Website (optional)",
      "Business phone (optional)",
      "Registration or company number (optional)",
      "Tax, GST or VAT number (optional)",
    ]) {
      expect(screen.getByLabelText(label)).not.toBeRequired();
    }
    expect(screen.getByText("Address (optional)")).toBeInTheDocument();
  });

  it("offers a currency fixed at creation and the partner status, defaulting to Draft", async () => {
    mockApi(routes());
    mount();
    await screen.findByLabelText(/Company or partner name/);
    expect(screen.getByLabelText(/^Payout currency/)).toHaveValue("GBP");
    expect(screen.getByText(/fixed when the partner is created/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Draft" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Active (activate when set up)" }));
    expect(screen.getByRole("radio", { name: "Active (activate when set up)" })).toBeChecked();
  });

  it("will not leave the step without the required fields, and puts focus on what to fix", async () => {
    mockApi(routes());
    mount();
    await screen.findByLabelText(/Company or partner name/);
    cont();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Enter the company or partner name/);
    expect(alert).toHaveTextContent(/code must be 3-48 characters/);
    expect(alert).toHaveTextContent(/Enter the business email address/);
    expect(alert).toHaveTextContent(/Choose the country/);
    await waitFor(() => expect(alert).toHaveFocus());
    expect(screen.getByRole("heading", { name: /Step 1 of 9/ })).toBeInTheDocument();
    // The same messages sit on the fields, linked to them.
    const name = screen.getByLabelText(/Company or partner name/);
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription(/Enter the company or partner name/);
    // Fixing a field clears its message.
    fireEvent.change(name, { target: { value: "Acme" } });
    expect(name).not.toHaveAttribute("aria-invalid");
  });

  it("refuses an address that is not an address, or a website that is not https", async () => {
    mockApi(routes());
    mount();
    await fillBusiness();
    type(/^Business email/, "not-an-email");
    type("Website (optional)", "http://acme.example");
    cont();
    await screen.findByRole("alert");
    expect(screen.getByLabelText(/^Business email/)).toHaveAccessibleDescription("Enter a valid email address.");
    expect(screen.getByLabelText("Website (optional)")).toHaveAccessibleDescription(/Enter a plain https:\/\/ address/);
    expect(screen.getByRole("heading", { name: /Step 1 of 9/ })).toBeInTheDocument();
  });

  it("offers no field the partner model does not have", async () => {
    mockApi(routes());
    mount();
    await screen.findByLabelText(/Company or partner name/);
    const labels = screen
      .getAllByRole("textbox")
      .map((el) => ((el as HTMLInputElement).labels?.[0]?.textContent ?? "").replace(/\s*\*$/, ""))
      .sort();
    expect(labels).toEqual(
      [
        "Address line 1",
        "Address line 2",
        "Alternative email (optional)",
        "Business email",
        "Business phone (optional)",
        "City",
        "Code",
        "Company or partner name",
        "Legal business name (optional)",
        "Postal or ZIP code",
        "Registration or company number (optional)",
        "State or province",
        "Tax, GST or VAT number (optional)",
        "Website (optional)",
      ].sort(),
    );
  });
});

describe("step 2: contacts", () => {
  it("needs the primary contact, and lets billing, technical and support follow it", async () => {
    mockApi(routes());
    mount();
    await toContacts();
    const same = screen.getAllByRole("checkbox", { name: "Same as primary contact" });
    expect(same).toHaveLength(3);
    for (const box of same) expect(box).toBeChecked();
    expect(screen.getByLabelText(/Primary contact name/)).toBeRequired();
    expect(screen.getByLabelText(/Primary contact job title \(optional\)/)).not.toBeRequired();
    cont();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/primary contact's name/);
    expect(alert).toHaveTextContent(/primary contact's email/);
    expect(screen.getByRole("heading", { name: /Step 2 of 9/ })).toBeInTheDocument();
  });

  it("shows a contact's own fields when it is not the primary one", async () => {
    mockApi(routes());
    mount();
    await toContacts();
    expect(screen.queryByLabelText(/Billing contact name/)).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "Same as primary contact" })[0]);
    expect(screen.getByLabelText(/Billing contact name/)).not.toBeRequired();
  });
});

describe("step 3: capabilities", () => {
  it("starts from Standard Reseller, in the server's groups, with short names and the full sentence as help", async () => {
    mockApi(routes());
    const { container } = mount();
    await toCapabilities();
    for (const group of ["Customer management", "Products and sales", "Commercial", "Support", "White-label", "Referrals"]) {
      expect(screen.getByRole("group", { name: group })).toBeInTheDocument();
    }
    for (const key of PRESET) {
      const short = CATALOG.results.find((c) => c.key === key)!.short_label;
      expect(screen.getByRole("checkbox", { name: short })).toBeChecked();
    }
    expect(screen.getByRole("checkbox", { name: "Manage branding" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Standard Reseller" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Create customers" })).toHaveAccessibleDescription("Create customer organizations");
    // Friendly names by default; the exact keys only under Advanced permissions.
    expect(container.textContent).not.toContain("customer.provision");
    fireEvent.click(screen.getByRole("checkbox", { name: "Advanced permissions" }));
    expect(container.textContent).toContain("customer.provision");
    expect(screen.getByRole("checkbox", { name: "Create customers" })).toHaveAccessibleDescription(/customer\.provision/);
    // A capability is only ever chosen from the list, never typed.
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });

  it("switches to Custom when the set is changed, and back to the preset", async () => {
    mockApi(routes());
    mount();
    await toCapabilities();
    fireEvent.click(screen.getByRole("checkbox", { name: "Manage branding" }));
    expect(screen.getByRole("radio", { name: "Custom" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Standard Reseller" }));
    expect(screen.getByRole("checkbox", { name: "Manage branding" })).not.toBeChecked();
  });

  it("needs at least one capability", async () => {
    mockApi(routes());
    mount();
    await toCapabilities();
    for (const key of PRESET) {
      fireEvent.click(screen.getByRole("checkbox", { name: CATALOG.results.find((c) => c.key === key)!.short_label }));
    }
    expect(screen.getByText("0 selected.")).toBeInTheDocument();
    cont();
    expect((await screen.findAllByText(/Choose at least one capability, or start from Standard Reseller/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: /Step 3 of 9/ })).toBeInTheDocument();
  });
});

describe("step 4: products and trials", () => {
  it("lists every catalog product with its status, plans and the platform trial", async () => {
    mockApi(routes());
    mount();
    await toProducts();
    const crm = screen.getByRole("checkbox", { name: "CRM" });
    expect(crm).toHaveAccessibleDescription(/Plans: CRM Professional\. Platform trial: 14-day trial on CRM Professional, up to 5 seats/);
    expect(screen.getByRole("checkbox", { name: "Books" })).toHaveAccessibleDescription(/Platform trial: No trial configured/);
    // Not available to sell: listed, but it cannot be allowed.
    const old = screen.getByRole("checkbox", { name: "Old product" });
    expect(old).toBeDisabled();
    expect(old).toHaveAccessibleDescription(/Not available to sell yet/);
    expect(screen.getByText("0 of 2 available products allowed.")).toBeInTheDocument();
    expect(screen.getByText(/the right to sell it\. With no product allowed the partner can sell nothing/)).toBeInTheDocument();
  });

  it("can be left empty: no product means no right to sell, and the step still continues", async () => {
    mockApi(routes());
    mount();
    await toProducts();
    cont();
    await stepHeading(5);
    expect(screen.getByRole("status")).toHaveTextContent(/nothing to price/);
  });

  it("offers a trial toggle for an allowed product, on by default", async () => {
    mockApi(routes());
    mount();
    await toProducts();
    expect(screen.queryByRole("checkbox", { name: /Allow free trial/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "CRM" }));
    const trial = screen.getByRole("checkbox", { name: /Allow free trial for this partner's customers \(CRM\)/ });
    expect(trial).toBeChecked();
    expect(trial).toBeEnabled();
  });

  it("disables the trial toggle, and says why, when the platform has no trial: a partner can only narrow one", async () => {
    mockApi(routes());
    mount();
    await toProducts();
    fireEvent.click(screen.getByRole("checkbox", { name: "Books" }));
    const trial = screen.getByRole("checkbox", { name: /Allow free trial for this partner's customers \(Books\)/ });
    expect(trial).toBeDisabled();
    expect(trial).not.toBeChecked();
    expect(trial).toHaveAccessibleDescription(/only narrow the platform's trial, never add one/);
  });

  it("sends the trial choice per product, and the review body carries only selections", async () => {
    const calls = mockApi(routes());
    mount();
    await toProducts();
    fireEvent.click(screen.getByRole("checkbox", { name: "CRM" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Books" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Allow free trial for this partner's customers \(CRM\)/ }));
    cont();
    await stepHeading(5);
    await waitFor(() => expect(reviews(calls).length).toBeGreaterThan(0));
    const body = lastReview(calls) as { products: string[]; product_options: Record<string, unknown> };
    expect(body.products).toEqual(["crm", "books"]);
    expect(body.product_options).toEqual({
      crm: { trial_allowed: false, portal_visible: true },
      books: { trial_allowed: true, portal_visible: true },
    });
    expect(JSON.stringify(body)).not.toMatch(/commission_minor|discount|platform_net|list_price/);
  });
});

describe("step 5: pricing", () => {
  it("defaults to standard list pricing and creates no partner price", async () => {
    const calls = mockApi(routes());
    mount();
    await toPricing();
    expect(screen.getByRole("radio", { name: "Standard partner pricing (list price)" })).toBeChecked();
    expect(screen.getByText(/never changes the global list price/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/sold at its list price/);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await waitFor(() => expect(reviews(calls).length).toBeGreaterThan(0));
    expect(lastReview(calls)).toMatchObject({ pricing_mode: "standard", partner_prices: [] });
  });

  it("shows what the server calculated for custom pricing, and never computes a figure", async () => {
    const calls = mockApi(routes());
    const { container } = mount();
    await toPricing();
    fireEvent.click(screen.getByRole("radio", { name: "Custom partner pricing" }));
    const table = await screen.findByRole("table", { name: /Prices per billing cycle/ });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Product / plan",
      "Billing",
      "Currency",
      "List price",
      "Partner price",
      "Discount",
      "Partner margin",
      "Platform keeps",
    ]);
    expect(within(table).getByText("CRM — CRM Professional")).toBeInTheDocument();
    expect(within(table).getByText("Monthly")).toBeInTheDocument();
    expect(within(table).getByText("GBP")).toBeInTheDocument();
    expect(within(table).getByText("£49.99")).toBeInTheDocument(); // list
    expect(within(table).getByText("£4.99 (9.98%)")).toBeInTheDocument(); // discount, from the server
    expect(within(table).getByText("£6.75 at 15%")).toBeInTheDocument(); // the partner's margin
    expect(within(table).getByText("£38.25")).toBeInTheDocument(); // what the platform keeps
    expect(screen.queryByText(/recommended retail/i)).not.toBeInTheDocument();
    await expectNoViolations(container);

    // A typed price goes to the server as an integer, and the figures shown stay
    // the server's until it answers.
    const before = reviews(calls).length;
    const input = screen.getByLabelText(/Partner price for CRM Professional in GBP/);
    fireEvent.change(input, { target: { value: "10.00" } });
    expect(within(table).getByText("£4.99 (9.98%)")).toBeInTheDocument();
    fireEvent.blur(input);
    await waitFor(() => expect(reviews(calls).length).toBe(before + 1));
    const body = lastReview(calls) as { partner_prices: unknown[]; products: string[]; pricing_mode: string };
    expect(body.pricing_mode).toBe("custom");
    expect(body.products).toEqual(["crm"]);
    expect(body.partner_prices).toEqual([{ plan_version_id: "pv-pro", interval: "monthly", amount_minor: 1000 }]);
    expect(Number.isInteger((body.partner_prices[0] as { amount_minor: number }).amount_minor)).toBe(true);
  });

  it("does not ask again when a field was left without changing anything", async () => {
    const calls = mockApi(routes());
    mount();
    await toPricing();
    fireEvent.click(screen.getByRole("radio", { name: "Custom partner pricing" }));
    await screen.findByRole("table", { name: /Prices per billing cycle/ });
    const input = screen.getByLabelText(/Partner price for CRM Professional in GBP/);
    // Choosing custom pricing changed what the server would answer, so the first blur asks once.
    const first = reviews(calls).length;
    fireEvent.blur(input);
    await waitFor(() => expect(reviews(calls).length).toBe(first + 1));
    const before = reviews(calls).length;
    fireEvent.focus(input);
    fireEvent.blur(input);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(reviews(calls).length).toBe(before);
  });

  it("refuses an amount that is not money, on the row it belongs to", async () => {
    mockApi(routes());
    mount();
    await toPricing();
    fireEvent.click(screen.getByRole("radio", { name: "Custom partner pricing" }));
    await screen.findByRole("table", { name: /Prices per billing cycle/ });
    fireEvent.change(screen.getByLabelText(/Partner price for CRM Professional in GBP/), { target: { value: "45,00 GBP" } });
    cont();
    expect(await screen.findByRole("alert")).toHaveTextContent(/not an amount in GBP/);
    expect(screen.getByLabelText(/Partner price for CRM Professional in GBP/)).toHaveAccessibleDescription(/not an amount in GBP/);
    expect(screen.getByRole("heading", { name: /Step 5 of 9/ })).toBeInTheDocument();
  });

  it("offers custom pricing only to someone who may set prices, and says why", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view", "platform.staff_partner.manage"]);
    await toPricing();
    expect(screen.getByRole("radio", { name: "Custom partner pricing" })).toBeDisabled();
    expect(screen.getByText(/need the platform\.staff_pricing\.manage permission/)).toBeInTheDocument();
  });
});

describe("step 6: commission and payouts", () => {
  it("says what the programme default is, as the server reports it, and shows the hold period", async () => {
    mockApi(routes());
    mount();
    await toCommission();
    expect(await screen.findByText(/programme default, currently 10%/i)).toBeInTheDocument();
    expect(screen.getByText(/a percentage of each sale/)).toBeInTheDocument();
    expect(screen.getByText(/30 days/)).toBeInTheDocument();
    expect(screen.getByText(/platform setting/)).toBeInTheDocument();
    expect(screen.getByText(/never changes an old sale/)).toBeInTheDocument();
  });

  it("shows the server's worked example, formatted as money", async () => {
    mockApi(routes());
    const { container } = mount();
    await toCommission();
    const example = await screen.findByRole("group", { name: "Worked example" });
    expect(within(example).getByText("Customer purchase").nextSibling).toHaveTextContent("£10,000.00");
    expect(within(example).getByText("Commission").nextSibling).toHaveTextContent("£1,500.00");
    expect(within(example).getByText("Platform net").nextSibling).toHaveTextContent("£8,500.00");
    await expectNoViolations(container);
  });

  it("shows no example when the server returns none", async () => {
    mockApi(
      routes({
        "POST /api/v1/admin/partners/reseller-review": [ok(reviewBody({ commission: { ...reviewBody().commission, example: null } }))],
      }),
    );
    mount();
    await toCommission();
    await screen.findByText(/30 days/);
    expect(screen.queryByRole("group", { name: "Worked example" })).not.toBeInTheDocument();
  });

  it("sends whole basis points for a custom rate and refuses a bad percentage", async () => {
    const calls = mockApi(routes());
    mount();
    await toCommission();
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    type(/Commission rate \(%\)/, "twelve");
    cont();
    expect(await screen.findByRole("alert")).toHaveTextContent(/percentage from 0 to 100/);
    expect(screen.getByLabelText(/Commission rate \(%\)/)).toHaveAccessibleDescription(/percentage from 0 to 100/);
    type(/Commission rate \(%\)/, "12.5");
    fireEvent.blur(screen.getByLabelText(/Commission rate \(%\)/));
    await waitFor(() => expect((lastReview(calls) as { commission: { rate_bp?: number } }).commission.rate_bp).toBe(1250));
    expect((lastReview(calls) as { commission: unknown }).commission).toEqual({ mode: "custom", rate_bp: 1250 });
  });

  it("limits a rate to selected products and sends them as product_codes", async () => {
    const calls = mockApi(routes());
    mount();
    await toCommission();
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    type(/Commission rate \(%\)/, "15");
    expect(screen.getByRole("radio", { name: "All products" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Selected products" }));
    const group = screen.getByRole("group", { name: "Products this rate applies to" });
    expect(within(group).getByRole("checkbox", { name: "CRM" })).toBeChecked();
    fireEvent.click(within(group).getByRole("checkbox", { name: "CRM" }));
    cont();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Choose at least one product/);
    fireEvent.click(within(group).getByRole("checkbox", { name: "CRM" }));
    fireEvent.blur(screen.getByLabelText(/Commission rate \(%\)/));
    await waitFor(() => expect((lastReview(calls) as { commission: { product_codes?: string[] } }).commission.product_codes).toEqual(["crm"]));
  });

  it("takes an effective date, never in the past, as a timestamp with its zone", async () => {
    const calls = mockApi(routes());
    mount();
    await toCommission();
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    type(/Commission rate \(%\)/, "15");
    expect(screen.getByLabelText("Effective from (optional)")).not.toBeRequired();
    type("Effective from (optional)", "2020-01-01T09:30");
    cont();
    expect(await screen.findByRole("alert")).toHaveTextContent(/not in the past/);
    type("Effective from (optional)", "2099-01-01T09:30");
    fireEvent.blur(screen.getByLabelText("Effective from (optional)"));
    await waitFor(() =>
      expect((lastReview(calls) as { commission: { effective_from?: string } }).commission.effective_from).toBe(new Date("2099-01-01T09:30").toISOString()),
    );
  });

  it("collects payout preferences, with the currency as a fact, and states what is not automatic", async () => {
    mockApi(routes());
    mount();
    await toCommission();
    expect(screen.getByText(/Draft → Ready → Approved → Paid/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing is transferred automatically, and no bank details are collected/)).toBeInTheDocument();
    expect(screen.getByLabelText("Payout frequency")).toHaveValue("manual");
    expect(screen.getByLabelText("Payout contact email (optional)")).not.toBeRequired();
    expect(screen.queryByLabelText(/bank|account number|sort code|iban/i)).not.toBeInTheDocument();
    const paidIn = screen.getByText(/Paid in/);
    expect(paidIn).toHaveTextContent("GBP");
    // The currency is shown, not asked for again.
    expect(screen.queryByLabelText(/currency/i)).not.toBeInTheDocument();
  });

  it("offers a rate for this partner only to someone who may assign one", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view", "platform.staff_partner.manage", "platform.staff_pricing.manage"]);
    await toCommission();
    expect(screen.getByRole("radio", { name: "A rate for this partner" })).toBeDisabled();
  });
});

describe("step 7: administrator and support", () => {
  it("never asks for or shows a password", async () => {
    mockApi(routes());
    const { container } = mount();
    await toAdministrator();
    expect(container.querySelector("input[type=password]")).toBeNull();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    expect(screen.getByText(/You never set or see a password/)).toBeInTheDocument();
  });

  it("is optional for a draft: it continues empty", async () => {
    mockApi(routes());
    mount();
    await toAdministrator();
    expect(screen.getByLabelText("First name (optional)")).not.toBeRequired();
    cont();
    await stepHeading(8);
  });

  it("asks for an email once the administrator has been started", async () => {
    mockApi(routes());
    mount();
    await toAdministrator();
    type("First name (optional)", "Bo");
    cont();
    expect(await screen.findByRole("alert")).toHaveTextContent(/administrator's email address/);
    type(/Administrator's email/, "nope");
    cont();
    await waitFor(() => expect(screen.getByLabelText(/Administrator's email/)).toHaveAccessibleDescription(/Enter a valid email address\./));
  });

  it("defaults the invitation to after activation, and offers sending now", async () => {
    mockApi(routes());
    mount();
    await toAdministrator();
    expect(screen.getByRole("radio", { name: "After the partner is activated" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Send now" })).not.toBeChecked();
    expect(screen.getByLabelText("Role")).toHaveValue("admin");
  });

  it("takes customer-facing support details and staff-only notes, all optional", async () => {
    mockApi(routes());
    mount();
    await toAdministrator();
    for (const label of ["Support email", "Support phone", "Support page", "Internal notes (staff only)"]) {
      expect(screen.getByLabelText(label)).not.toBeRequired();
    }
    type("Support email", "bad");
    cont();
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Support email")).toHaveAccessibleDescription(/Enter a valid email address\./);
  });
});

describe("step 8: white-label", () => {
  it("defaults to not now, and explains it is the same partner gaining two capabilities", async () => {
    const calls = mockApi(routes());
    mount();
    await toWhiteLabel();
    expect(screen.getByRole("radio", { name: "Not now" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Yes, configure now" })).not.toBeChecked();
    expect(screen.getByText(/not a second account/)).toBeInTheDocument();
    cont();
    await stepHeading(9);
    expect(lastReview(calls)).toMatchObject({ white_label: false });
  });

  it("sends white_label: true when chosen", async () => {
    const calls = mockApi(routes());
    mount();
    await toWhiteLabel();
    fireEvent.click(screen.getByRole("radio", { name: "Yes, configure now" }));
    cont();
    await stepHeading(9);
    expect(lastReview(calls)).toMatchObject({ white_label: true });
  });
});

describe("step 9: the review", () => {
  it("reads back everything, and creates nothing until Save draft or Activate", async () => {
    const calls = mockApi(
      routes({
        "POST /api/v1/admin/partners/reseller-review": [
          ok(reviewBody({ white_label: { requested: true, capabilities_added: ["branding.manage", "domain.manage"] } })),
        ],
      }),
    );
    const { container } = mount();
    await toReview();
    const sections = ["Business details", "Contacts", "Capabilities", "Products and trials", "Pricing", "Commission", "Payouts", "Administrator", "Support", "White-label"];
    for (const title of sections) expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    const section = (title: string) => within(screen.getByRole("heading", { name: title }).closest("section")!);
    expect(section("Business details").getByText(/Acme Resell/)).toBeInTheDocument();
    expect(section("Business details").getByText("hello@acme.example")).toBeInTheDocument();
    expect(section("Business details").getByText("United Kingdom")).toBeInTheDocument();
    expect(section("Contacts").getByText("Priya Shah · priya@acme.example")).toBeInTheDocument();
    expect(section("Contacts").getAllByText("Same as primary contact")).toHaveLength(3);
    expect(section("Capabilities").getByText("Create customers")).toBeInTheDocument();
    expect(section("Products and trials").getByText(/Customers can start a free trial: 14-day trial on CRM Professional, up to 5 seats/)).toBeInTheDocument();
    expect(section("Pricing").getByText(/CRM — CRM Professional \(monthly\): £45\.00 · list £49\.99 · discount £4\.99 \(9\.98%\) · partner margin £6\.75 · platform keeps £38\.25/)).toBeInTheDocument();
    expect(section("Commission").getByText("15% for this partner")).toBeInTheDocument();
    expect(section("Commission").getByText(/On a customer purchase of £10,000\.00 at 15%: commission £1,500\.00, platform net £8,500\.00/)).toBeInTheDocument();
    expect(section("Commission").getByText(/30 days before a commission becomes payable/)).toBeInTheDocument();
    expect(section("Payouts").getByText("Manual - when staff prepare one")).toBeInTheDocument();
    expect(section("Administrator").getByText("boss@acme.example")).toBeInTheDocument();
    expect(section("Administrator").getByText("Sent when the partner is activated")).toBeInTheDocument();
    expect(section("White-label").getByText(/Manage branding and Manage custom domains/)).toBeInTheDocument();
    const notSet = within(screen.getByRole("heading", { name: "Not set (optional)" }).closest("section")!);
    expect(notSet.getByText("Website")).toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
    await expectNoViolations(container);
  });

  it("lets a section be edited from the review", async () => {
    mockApi(routes());
    mount();
    await toReview();
    fireEvent.click(screen.getByRole("button", { name: "Edit Contacts" }));
    await stepHeading(2);
  });

  it("blocks both actions on a server problem, and takes you to the field it names", async () => {
    mockApi(
      routes({
        "POST /api/v1/admin/partners/reseller-review": [
          ok(reviewBody({ ok: false, problems: [{ field: "business.website", message: "Enter a plain https:// address." }] })),
        ],
      }),
    );
    mount();
    await toReview();
    expect(await screen.findByRole("alert")).toHaveTextContent(/found a problem/);
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Activate reseller" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Fix this on Business details/ }));
    await stepHeading(1);
    const website = screen.getByLabelText("Website (optional)");
    expect(website).toHaveAttribute("aria-invalid", "true");
    expect(website).toHaveAccessibleDescription(/Enter a plain https:\/\/ address/);
  });

  it("lets an unready partner be saved as a draft but not activated, and says what is missing", async () => {
    const calls = mockApi(
      routes({
        "POST /api/v1/admin/partners/reseller-review": [
          ok(
            reviewBody({
              readiness: {
                can_activate: false,
                missing: [{ key: "administrator", label: "Partner administrator", detail: "Name the person who will run the account." }],
              },
              administrator: null,
            }),
          ),
        ],
      }),
    );
    mount();
    await toReview();
    expect(screen.getByRole("button", { name: "Activate reseller" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Activate reseller" })).toHaveAccessibleDescription(/Still needed: Partner administrator/);
    expect(screen.getByText(/Name the person who will run the account/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Go to Administrator and support" }));
    await stepHeading(7);
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/partners/new-partner"));
    expect(writes(calls).map((c) => c.key)).not.toContain(`POST ${PARTNER}/status`);
  });

  it("shows the server's warnings", async () => {
    mockApi(routes({ "POST /api/v1/admin/partners/reseller-review": [ok(reviewBody({ warnings: ["Without 'Earn commissions' this partner earns nothing on its sales."] }))] }));
    mount();
    await toReview();
    expect(screen.getByText(/earns nothing on its sales/)).toBeInTheDocument();
  });

  it("highlights the Activate action when the partner status was set to Active", async () => {
    mockApi(routes());
    mount();
    await fillBusiness();
    fireEvent.click(screen.getByRole("radio", { name: "Active (activate when set up)" }));
    cont();
    await stepHeading(2);
    await fillContacts();
    cont();
    await stepHeading(3);
    await screen.findByRole("checkbox", { name: "Create customers" });
    for (let n = 4; n <= 9; n++) {
      cont();
      await stepHeading(n);
    }
    expect(await screen.findByRole("button", { name: "Activate reseller" })).toHaveClass("w360-btn-primary");
    expect(screen.getByRole("button", { name: "Save draft" })).toHaveClass("w360-btn-secondary");
  });

  it("makes Save draft the primary action by default", async () => {
    mockApi(routes());
    mount();
    await toReview();
    expect(screen.getByRole("button", { name: "Save draft" })).toHaveClass("w360-btn-primary");
    expect(screen.getByRole("button", { name: "Activate reseller" })).toHaveClass("w360-btn-secondary");
  });
});

describe("saving a draft", () => {
  it("runs every command but activation, in order, with the profile's version, then opens the partner", async () => {
    const calls = mockApi(routes());
    mount();
    await toPricing();
    fireEvent.click(screen.getByRole("radio", { name: "Custom partner pricing" }));
    await screen.findByRole("table");
    fireEvent.change(screen.getByLabelText(/Partner price for CRM Professional in GBP/), { target: { value: "45.00" } });
    cont();
    await stepHeading(6);
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    type(/Commission rate \(%\)/, "15");
    fireEvent.click(screen.getByRole("radio", { name: "Selected products" }));
    cont();
    await stepHeading(7);
    type("First name (optional)", "Bo");
    type("Last name (optional)", "Boss");
    type(/Administrator's email/, "boss@acme.example");
    fireEvent.click(screen.getByRole("radio", { name: "Send now" }));
    cont();
    await stepHeading(8);
    cont();
    await stepHeading(9);
    expect(writes(calls)).toEqual([]);
    fireEvent.click(await screen.findByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/partners/new-partner"));

    const order = writes(calls).map(endpoint);
    expect(order.slice(0, 2)).toEqual(["POST ", "PUT /profile"]);
    expect(order.slice(2, 11).every((e) => e === "POST /capabilities")).toBe(true);
    expect(order.slice(11)).toEqual(["POST /product-grants", "POST /prices", "POST /commission", "POST /administrators"]);
    expect(order).not.toContain("POST /status");

    const create = bodyOf(calls.find((c) => c.key === "POST /api/v1/admin/partners")!);
    expect(create).toEqual({
      code: "acme-resell",
      display_name: "Acme Resell",
      payout_currency: "GBP",
      contact_name: "Priya Shah",
      contact_email: "priya@acme.example",
    });
    const profile = bodyOf(calls.find((c) => c.key === `PUT ${PARTNER}/profile`)!);
    expect(profile.expected_version).toBe(3); // the version the create returned
    expect(profile.business).toMatchObject({ display_name: "Acme Resell", business_email: "hello@acme.example", country: "GB" });
    expect(profile.contacts).toMatchObject({
      primary: { name: "Priya Shah", email: "priya@acme.example" },
      billing: { same_as_primary: true },
      technical: { same_as_primary: true },
      support: { same_as_primary: true },
    });
    expect(profile.payout).toEqual({ frequency: "manual", contact_email: "" });
    expect(profile.support).toEqual({ email: "", phone: "", url: "" });
    expect(profile).toHaveProperty("internal_notes", "");
    const granted = calls.filter((c) => c.key === `POST ${PARTNER}/capabilities`).map((c) => bodyOf(c).capability);
    expect(granted).toEqual(PRESET);
    expect(calls.find((c) => c.key === `POST ${PARTNER}/product-grants`)!.body).toEqual({
      product_code: "crm",
      trial_allowed: true,
      portal_visible: true,
    });
    expect(calls.find((c) => c.key === `POST ${PARTNER}/prices`)!.body).toEqual({ plan_version_id: "pv-pro", interval: "monthly", amount_minor: 4500 });
    expect(calls.find((c) => c.key === `POST ${PARTNER}/commission`)!.body).toMatchObject({ rate_bp: 1500, product_code: "crm", expected_version: 0 });
    expect(calls.find((c) => c.key === `POST ${PARTNER}/administrators`)!.body).toEqual({
      email: "boss@acme.example",
      role: "admin",
      first_name: "Bo",
      last_name: "Boss",
      send_invitation: "now",
    });
    // Nothing the browser sends carries a computed figure, an actor or a status stamp.
    for (const call of writes(calls)) {
      expect(JSON.stringify(call.body)).not.toMatch(/commission_minor|discount|platform_net|created_by|approved_by|list_price/);
    }
    expect(await screen.findByRole("heading", { name: "Reseller saved as a draft" })).toBeInTheDocument();
  });

  it("is offered from any step once the name, code and currency are valid, and saves only what is filled in", async () => {
    const calls = mockApi(routes());
    mount();
    const save = await screen.findByRole("button", { name: "Save draft" });
    expect(save).toBeDisabled();
    expect(screen.getByText(/available once the name, code and payout currency are filled in/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Company or partner name/), { target: { value: "Acme Resell" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/partners/new-partner"));
    // No contact, capability, product or administrator was chosen, so none is created.
    expect(writes(calls).map(endpoint)).toEqual(["POST ", "PUT /profile"]);
    const body = lastReview(calls) as { capabilities: string[]; products: string[]; administrator?: unknown };
    expect(body.capabilities).toEqual([]);
    expect(body.products).toEqual([]);
    expect(body.administrator).toBeUndefined();
  });

  it("counts the preset as chosen once the capabilities step has been seen", async () => {
    const calls = mockApi(routes());
    mount();
    await toProducts();
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(calls.filter((c) => c.key === `POST ${PARTNER}/capabilities`)).toHaveLength(9);
    expect(writes(calls).map(endpoint)).not.toContain("POST /status");
  });

  it("does not send anything while something typed is wrong", async () => {
    const calls = mockApi(routes());
    mount();
    fireEvent.change(await screen.findByLabelText(/Company or partner name/), { target: { value: "Acme Resell" } });
    type("Website (optional)", "http://acme.example");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Enter a plain https:\/\/ address/);
    expect(reviews(calls)).toHaveLength(0);
    expect(writes(calls)).toEqual([]);
  });

  it("does not send anything when the server finds a problem, and shows it on its field", async () => {
    const calls = mockApi(
      routes({
        "POST /api/v1/admin/partners/reseller-review": [
          ok(reviewBody({ ok: false, problems: [{ field: "code", message: "Partner code 'acme-resell' is already in use." }] })),
        ],
      }),
    );
    mount();
    fireEvent.change(await screen.findByLabelText(/Company or partner name/), { target: { value: "Acme Resell" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already in use/);
    expect(screen.getByLabelText(/^Code/)).toHaveAccessibleDescription(/already in use/);
    expect(writes(calls)).toEqual([]);
    expect(push).not.toHaveBeenCalled();
  });

  it("jumps to the step a server problem belongs to", async () => {
    mockApi(
      routes({
        "POST /api/v1/admin/partners/reseller-review": [
          ok(reviewBody({ ok: false, problems: [{ field: "contacts.primary.email", message: "Enter a valid email address." }] })),
        ],
      }),
    );
    mount();
    await toProducts();
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await stepHeading(2);
    expect(screen.getByLabelText(/Primary contact email/)).toHaveAccessibleDescription("Enter a valid email address.");
  });
});

describe("activating", () => {
  it("asks for a confirmation first, then runs every command in order, quoting the version the profile save returned", async () => {
    const calls = mockApi(routes());
    mount();
    await toReview();
    fireEvent.click(screen.getByRole("button", { name: "Activate reseller" }));
    expect(writes(calls)).toEqual([]); // asked, not done
    const confirm = screen.getByRole("group", { name: "Confirm" });
    expect(confirm).toHaveTextContent(/Activate Acme Resell\?/);
    expect(confirm).toHaveTextContent(/boss@acme\.example/);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(writes(calls)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Activate reseller" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate" }));
    expect(await screen.findByRole("heading", { name: "Reseller created" })).toBeInTheDocument();

    const order = writes(calls).map(endpoint);
    expect(order.slice(0, 2)).toEqual(["POST ", "PUT /profile"]);
    expect(order.slice(2, 11).every((e) => e === "POST /capabilities")).toBe(true);
    expect(order.slice(11)).toEqual(["POST /product-grants", "POST /administrators", "POST /status"]);
    expect(calls.find((c) => c.key === `PUT ${PARTNER}/profile`)!.body).toHaveProperty("expected_version", 3);
    // The partner's version moved when the profile was saved: activation quotes the new one.
    expect(calls.find((c) => c.key === `POST ${PARTNER}/status`)!.body).toMatchObject({ status: "active", expected_version: 4 });
    expect(calls.find((c) => c.key === `POST ${PARTNER}/administrators`)!.body).toMatchObject({ send_invitation: "on_activation" });
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Open Acme Resell" })).toHaveAttribute("href", "/admin/partners/new-partner");
    expect(screen.queryByRole("link", { name: /white-label setup/ })).not.toBeInTheDocument();
  });

  it("grants the two white-label capabilities when asked, and offers the white-label setup afterwards", async () => {
    const calls = mockApi(routes());
    mount();
    await toWhiteLabel();
    fireEvent.click(screen.getByRole("radio", { name: "Yes, configure now" }));
    cont();
    await stepHeading(9);
    fireEvent.click(await screen.findByRole("button", { name: "Activate reseller" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate" }));
    await screen.findByRole("heading", { name: "Reseller created" });
    const granted = calls.filter((c) => c.key === `POST ${PARTNER}/capabilities`).map((c) => bodyOf(c).capability as string);
    expect(granted).toEqual([...PRESET, "branding.manage", "domain.manage"]);
    expect(screen.getByRole("link", { name: "Continue to white-label setup" })).toHaveAttribute(
      "href",
      "/admin/partners/white-label/new-partner",
    );
  });

  it("assigns one commission rule per product in scope, or one for all products, with the effective date", async () => {
    const calls = mockApi(routes());
    mount();
    await toProducts();
    fireEvent.click(screen.getByRole("checkbox", { name: "CRM" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Books" }));
    cont();
    await stepHeading(5);
    cont();
    await stepHeading(6);
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    type(/Commission rate \(%\)/, "12.5");
    type("Effective from (optional)", "2099-01-01T09:30");
    fireEvent.click(screen.getByRole("radio", { name: "Selected products" }));
    cont();
    await stepHeading(7);
    cont();
    await stepHeading(8);
    cont();
    await stepHeading(9);
    fireEvent.click(await screen.findByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    const rules = calls.filter((c) => c.key === `POST ${PARTNER}/commission`).map(bodyOf);
    expect(rules).toEqual([
      { rate_bp: 1250, reason: "Set up through the Add Reseller wizard", expected_version: 0, product_code: "crm", effective_from: new Date("2099-01-01T09:30").toISOString() },
      { rate_bp: 1250, reason: "Set up through the Add Reseller wizard", expected_version: 0, product_code: "books", effective_from: new Date("2099-01-01T09:30").toISOString() },
    ]);
  });

  it("assigns a single rule for all products without a product code", async () => {
    const calls = mockApi(routes());
    mount();
    await toCommission();
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    type(/Commission rate \(%\)/, "15");
    cont();
    await stepHeading(7);
    cont();
    await stepHeading(8);
    cont();
    await stepHeading(9);
    fireEvent.click(await screen.findByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    const rules = calls.filter((c) => c.key === `POST ${PARTNER}/commission`).map(bodyOf);
    expect(rules).toEqual([{ rate_bp: 1500, reason: "Set up through the Add Reseller wizard", expected_version: 0 }]);
  });

  it("grants a product with the trial choice the person made", async () => {
    const calls = mockApi(routes());
    mount();
    await toProducts();
    fireEvent.click(screen.getByRole("checkbox", { name: "CRM" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Allow free trial for this partner's customers \(CRM\)/ }));
    cont();
    await stepHeading(5);
    cont();
    await stepHeading(6);
    cont();
    await stepHeading(7);
    cont();
    await stepHeading(8);
    cont();
    await stepHeading(9);
    fireEvent.click(await screen.findByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(calls.find((c) => c.key === `POST ${PARTNER}/product-grants`)!.body).toEqual({
      product_code: "crm",
      trial_allowed: false,
      portal_visible: true,
    });
  });

  it("resumes after a second-factor prompt without repeating what was done", async () => {
    const stepUp: Reply = { status: 401, body: { code: "step_up_required", detail: "Verify again." } };
    const calls = mockApi(
      routes({
        [`POST ${PARTNER}/capabilities`]: [stepUp, { status: 201, body: { id: "g", capability: "x" } }],
        "POST /api/v1/auth/mfa/step-up": [ok({ aal: 2 })],
      }),
    );
    mount();
    await toReview();
    fireEvent.click(screen.getByRole("button", { name: "Activate reseller" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate" }));
    const code = await screen.findByLabelText(/needs a recent verification/);
    expect(screen.getAllByText("Waiting for your verification").length).toBeGreaterThan(0);
    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByRole("heading", { name: "Reseller created" });
    expect(calls.filter((c) => c.key === "POST /api/v1/admin/partners")).toHaveLength(1);
    expect(calls.filter((c) => c.key === `PUT ${PARTNER}/profile`)).toHaveLength(1);
    expect(calls.filter((c) => c.key.endsWith("/administrators"))).toHaveLength(1);
    expect(calls.filter((c) => c.key.endsWith("/status"))).toHaveLength(1);
  });

  it("says which step failed, keeps what was done, and carries on from there on Try again", async () => {
    const calls = mockApi(
      routes({
        [`POST ${PARTNER}/product-grants`]: [
          { status: 400, body: { code: "catalog_validation_error", detail: "No such product." } },
          { status: 201, body: { id: "pg" } },
        ],
      }),
    );
    mount();
    await toReview();
    fireEvent.click(screen.getByRole("button", { name: "Activate reseller" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and activate" }));
    expect(await screen.findByRole("heading", { name: "Setup did not finish" })).toBeInTheDocument();
    expect((await screen.findAllByText("No such product.")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "Reseller created" })).not.toBeInTheDocument();
    // The form is not editable under a half-finished setup.
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the partner" })).toHaveAttribute("href", "/admin/partners/new-partner");
    expect(calls.filter((c) => c.key === "POST /api/v1/admin/partners")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("heading", { name: "Reseller created" });
    expect(calls.filter((c) => c.key === "POST /api/v1/admin/partners")).toHaveLength(1);
    expect(calls.filter((c) => c.key === `PUT ${PARTNER}/profile`)).toHaveLength(1);
    expect(calls.filter((c) => c.key === `POST ${PARTNER}/capabilities`)).toHaveLength(9);
    expect(calls.filter((c) => c.key === `POST ${PARTNER}/product-grants`)).toHaveLength(2);
    expect(calls.filter((c) => c.key.endsWith("/status"))).toHaveLength(1);
  });
});

describe("accessibility", () => {
  it("has no axe violations on the first four steps", async () => {
    mockApi(routes());
    const { container } = mount();
    await screen.findByLabelText(/Company or partner name/);
    await expectNoViolations(container);
    await fillBusiness();
    cont();
    await stepHeading(2);
    await expectNoViolations(container);
    await fillContacts();
    cont();
    await stepHeading(3);
    await screen.findByRole("checkbox", { name: "Create customers" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Advanced permissions" }));
    await expectNoViolations(container);
    cont();
    await stepHeading(4);
    await screen.findByRole("checkbox", { name: "CRM" });
    fireEvent.click(screen.getByRole("checkbox", { name: "CRM" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Books" }));
    await expectNoViolations(container);
  }, 60_000);

  it("has no axe violations on the last five steps, with the errors showing", async () => {
    mockApi(routes());
    const { container } = mount();
    await toProducts();
    fireEvent.click(screen.getByRole("checkbox", { name: "CRM" }));
    cont();
    await stepHeading(5);
    fireEvent.click(screen.getByRole("radio", { name: "Custom partner pricing" }));
    await screen.findByRole("table");
    fireEvent.change(screen.getByLabelText(/Partner price for CRM Professional in GBP/), { target: { value: "oops" } });
    cont();
    await screen.findByRole("alert");
    await expectNoViolations(container);
    fireEvent.change(screen.getByLabelText(/Partner price for CRM Professional in GBP/), { target: { value: "" } });
    cont();
    await stepHeading(6);
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    fireEvent.click(screen.getByRole("radio", { name: "Selected products" }));
    cont();
    await screen.findByRole("alert");
    await expectNoViolations(container);
    type(/Commission rate \(%\)/, "15");
    cont();
    await stepHeading(7);
    await expectNoViolations(container);
    cont();
    await stepHeading(8);
    await expectNoViolations(container);
    cont();
    await stepHeading(9);
    await screen.findByRole("button", { name: "Save draft" });
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Activate reseller" }));
    await expectNoViolations(container);
  }, 60_000);
});
