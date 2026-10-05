import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AddReseller, suggestCode } from "./AddReseller";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * Phase 10b: the Add Reseller wizard. What the backend cannot enforce for the
 * browser is checked here: that the browser sends selections and integers and
 * never a computed figure, that nothing is created before the confirmation,
 * and that a step which finished is not repeated after a verification prompt.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
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
    { key: "customer.provision", label: "Create customer organizations", group: "customer_management" },
    { key: "customer.manage", label: "Manage the commercial setup of related customers", group: "customer_management" },
    { key: "subscription.sell", label: "Sell approved products and plans to related customers", group: "sales" },
    { key: "subscription.manage", label: "Change, cancel and activate related customers' subscriptions", group: "subscriptions" },
    { key: "pricing.view", label: "See the prices approved for this partner", group: "pricing" },
    { key: "commission.earn", label: "Earn commission on attributed sales", group: "commissions" },
    { key: "commission.view", label: "View earned commission", group: "commissions" },
    { key: "payout.view", label: "View payouts", group: "payouts" },
    { key: "support.access", label: "Run read-only access diagnostics for related customers", group: "support" },
    { key: "branding.manage", label: "Configure white-label branding", group: "white_label" },
  ],
  groups: [
    { key: "customer_management", label: "Customer management", capabilities: ["customer.provision", "customer.manage"] },
    { key: "sales", label: "Sales", capabilities: ["subscription.sell"] },
    { key: "subscriptions", label: "Subscriptions", capabilities: ["subscription.manage"] },
    { key: "pricing", label: "Pricing", capabilities: ["pricing.view"] },
    { key: "commissions", label: "Commissions", capabilities: ["commission.earn", "commission.view"] },
    { key: "payouts", label: "Payouts", capabilities: ["payout.view"] },
    { key: "support", label: "Support", capabilities: ["support.access"] },
    { key: "white_label", label: "White-label", capabilities: ["branding.manage"] },
  ],
  presets: [
    { key: "standard_reseller", label: "Standard Reseller", description: "Create and manage customers and sell approved products.", capabilities: PRESET },
  ],
};

const PRODUCTS = {
  results: [
    { code: "crm", name: "CRM", status: "available", plans: [{ plan_version_id: "pv-pro", plan_code: "crm_professional", plan_name: "CRM Professional", included_seats: 10, trial_days: 14 }] },
    { code: "books", name: "Books", status: "available", plans: [] },
    { code: "old", name: "Old product", status: "draft", plans: [] },
  ],
};

function reviewBody(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    problems: [],
    partner: { code: "acme-resell", display_name: "Acme Resell", legal_name: "", contact_name: "Priya Shah", contact_email: "priya@acme.example" },
    capabilities: PRESET.map((key) => ({ key, label: key })),
    experience: [{ key: "reseller", label: "Reseller" }],
    products: [{ code: "crm", name: "CRM", plans: [{ plan_code: "crm_professional", plan_name: "CRM Professional" }] }],
    pricing: [
      {
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
      },
    ],
    commission: { mode: "custom", rate_bp: 1500, programme_default_bp: 1000, source: "custom", product_rates: { crm: 1500 } },
    administrator: { email: "boss@acme.example", role: "admin" },
    warnings: [],
    ...overrides,
  };
}

function routes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    "GET /api/v1/admin/partners/capabilities": [ok(CATALOG)],
    "GET /api/v1/admin/partners/products": [ok(PRODUCTS)],
    "POST /api/v1/admin/partners/reseller-review": [ok(reviewBody())],
    "POST /api/v1/admin/partners": [{ status: 201, body: { id: "new-partner", code: "acme-resell", version: 0, status: "draft" } }],
    "POST /api/v1/admin/partners/new-partner/capabilities": [{ status: 201, body: { id: "g", capability: "x" } }],
    "POST /api/v1/admin/partners/new-partner/product-grants": [{ status: 201, body: { id: "pg" } }],
    "POST /api/v1/admin/partners/new-partner/prices": [{ status: 201, body: { id: "pr", interval: "monthly", amount_minor: 4500 } }],
    "POST /api/v1/admin/partners/new-partner/commission": [{ status: 201, body: { id: "r", version: 1, created: true } }],
    "POST /api/v1/admin/partners/new-partner/administrators": [{ status: 201, body: { id: "m", role: "admin", invited: true } }],
    "POST /api/v1/admin/partners/new-partner/status": [{ status: 200, body: { id: "new-partner", status: "active" } }],
    ...extra,
  };
}

const writes = (calls: Call[]) => calls.filter((c) => c.key.startsWith("POST ") && !c.key.endsWith("/reseller-review"));
const reviews = (calls: Call[]) => calls.filter((c) => c.key === "POST /api/v1/admin/partners/reseller-review");

function mount(permissions = MANAGE) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <AddReseller />
    </AdminContextForTest>,
  );
}

const next = () => fireEvent.click(screen.getByRole("button", { name: "Next" }));

async function fillDetails(name = "Acme Resell") {
  fireEvent.change(await screen.findByLabelText(/Company or display name/), { target: { value: name } });
  fireEvent.change(screen.getByLabelText("Primary contact (optional)"), { target: { value: "Priya Shah" } });
  fireEvent.change(screen.getByLabelText("Contact email (optional)"), { target: { value: "priya@acme.example" } });
}

async function toStep(title: RegExp) {
  return screen.findByRole("heading", { name: title });
}

/** Steps 1-3: details, preset capabilities, the CRM product. */
async function toPricing() {
  await fillDetails();
  next();
  await toStep(/Step 2 of 7/);
  await screen.findByRole("checkbox", { name: /Create customer organizations/ });
  next();
  await toStep(/Step 3 of 7/);
  fireEvent.click(await screen.findByRole("checkbox", { name: /CRM/ }));
  next();
  await toStep(/Step 4 of 7/);
}

async function toReview() {
  await toPricing();
  next(); // pricing -> commission
  await toStep(/Step 5 of 7/);
  next(); // commission (programme default) -> administrator
  await toStep(/Step 6 of 7/);
  fireEvent.change(screen.getByLabelText(/Administrator's email/), { target: { value: "boss@acme.example" } });
  next();
  await toStep(/Step 7 of 7/);
  await screen.findByRole("button", { name: "Create reseller…" });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the details step", () => {
  it("suggests a code from the name until the code is edited by hand", async () => {
    mockApi(routes());
    mount();
    const name = await screen.findByLabelText(/Company or display name/);
    fireEvent.change(name, { target: { value: "Acme Résell & Co." } });
    expect(screen.getByLabelText("Code")).toHaveValue("acme-resell-co");
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "my-code" } });
    fireEvent.change(name, { target: { value: "Something else" } });
    expect(screen.getByLabelText("Code")).toHaveValue("my-code");
    expect(suggestCode("123 Numbers")).toBe("p-123-numbers");
    expect(suggestCode("!!!")).toBe("");
  });

  it("will not leave the step without a name and a valid code", async () => {
    mockApi(routes());
    mount();
    await screen.findByLabelText(/Company or display name/);
    next();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Enter the company or display name/);
    expect(alert).toHaveTextContent(/code must be 3-48 characters/);
    expect(screen.getByRole("heading", { name: /Step 1 of 7/ })).toBeInTheDocument();
  });

  it("offers no field the partner model does not have", async () => {
    mockApi(routes());
    mount();
    await screen.findByLabelText(/Company or display name/);
    const labels = screen.getAllByRole("textbox").map((el) => (el as HTMLInputElement).labels?.[0]?.textContent ?? "");
    expect(labels.sort()).toEqual(
      ["Code", "Company or display name", "Contact email (optional)", "Legal name (optional)", "Primary contact (optional)"].sort(),
    );
  });

  it("has no axe violations", async () => {
    mockApi(routes());
    const { container } = mount();
    await screen.findByLabelText(/Company or display name/);
    await expectNoViolations(container);
  });
});

describe("the capabilities step", () => {
  it("starts from the Standard Reseller preset and shows friendly names", async () => {
    mockApi(routes());
    const { container } = mount();
    await fillDetails();
    next();
    await screen.findByRole("checkbox", { name: /Create customer organizations/ });
    for (const key of PRESET) {
      const label = CATALOG.results.find((c) => c.key === key)!.label;
      expect(screen.getByRole("checkbox", { name: new RegExp(label.slice(0, 20)) })).toBeChecked();
    }
    // Not part of the preset, so not ticked.
    expect(screen.getByRole("checkbox", { name: /Configure white-label branding/ })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Standard Reseller" })).toBeChecked();
    // Friendly names by default; technical keys only on request.
    expect(container.textContent).not.toContain("customer.provision");
    fireEvent.click(screen.getByRole("checkbox", { name: /Show technical capability names/ }));
    expect(container.textContent).toContain("customer.provision");
    await expectNoViolations(container);
  });

  it("switches to a custom selection when the set is changed", async () => {
    mockApi(routes());
    mount();
    await fillDetails();
    next();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Configure white-label branding/ }));
    expect(screen.getByRole("radio", { name: "Custom selection" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Standard Reseller" }));
    expect(screen.getByRole("checkbox", { name: /Configure white-label branding/ })).not.toBeChecked();
  });

  it("needs at least one capability", async () => {
    mockApi(routes());
    mount();
    await fillDetails();
    next();
    await screen.findByRole("checkbox", { name: /Create customer organizations/ });
    for (const box of screen.getAllByRole("checkbox")) {
      if ((box as HTMLInputElement).checked && !/technical/.test(box.parentElement?.textContent ?? "")) fireEvent.click(box);
    }
    next();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Choose at least one capability/);
  });
});

describe("the products step", () => {
  it("lists only products that are available, filters them and counts the choice", async () => {
    mockApi(routes());
    mount();
    await fillDetails();
    next();
    await screen.findByRole("checkbox", { name: /Create customer organizations/ });
    next();
    await screen.findByRole("checkbox", { name: /CRM/ });
    expect(screen.queryByRole("checkbox", { name: /Old product/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search products"), { target: { value: "boo" } });
    expect(screen.queryByRole("checkbox", { name: /CRM/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /Books/ }));
    expect(screen.getByText("1 selected.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search products"), { target: { value: "zzz" } });
    expect(screen.getByRole("status")).toHaveTextContent(/No product matches/);
  });
});

describe("the pricing step", () => {
  it("shows what the server calculated and asks the server again rather than computing", async () => {
    const calls = mockApi(routes());
    const { container } = mount();
    await toPricing();
    const table = await screen.findByRole("table", { name: /Prices per month/ });
    expect(within(table).getByText("£49.99")).toBeInTheDocument(); // list
    expect(within(table).getByText("£4.99 (9.98%)")).toBeInTheDocument(); // discount, from the server
    expect(within(table).getByText("£6.75 at 15%")).toBeInTheDocument(); // commission
    expect(within(table).getByText("£38.25")).toBeInTheDocument(); // what the platform keeps
    expect(reviews(calls).length).toBeGreaterThanOrEqual(1);
    await expectNoViolations(container);

    // Typing a different price sends an integer to the server, and the figures
    // shown stay the server's until it answers - the page derives nothing.
    const before = reviews(calls).length;
    const input = screen.getByLabelText(/Partner price for CRM Professional in GBP/);
    fireEvent.change(input, { target: { value: "10.00" } });
    expect(within(table).getByText("£4.99 (9.98%)")).toBeInTheDocument();
    fireEvent.blur(input);
    await waitFor(() => expect(reviews(calls).length).toBe(before + 1));
    const body = reviews(calls).at(-1)!.body as { partner_prices: unknown[]; products: string[] };
    expect(body.products).toEqual(["crm"]);
    expect(body.partner_prices).toEqual([{ plan_version_id: "pv-pro", interval: "monthly", amount_minor: 1000 }]);
    expect(Number.isInteger((body.partner_prices[0] as { amount_minor: number }).amount_minor)).toBe(true);
  });

  it("does not ask again when a field was left without changing anything", async () => {
    const calls = mockApi(routes());
    mount();
    await toPricing();
    await screen.findByRole("table", { name: /Prices per month/ });
    const before = reviews(calls).length;
    const input = screen.getByLabelText(/Partner price for CRM Professional in GBP/);
    fireEvent.focus(input);
    fireEvent.blur(input);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(reviews(calls).length).toBe(before);
  });

  it("refuses an amount that is not money", async () => {
    mockApi(routes());
    mount();
    await toPricing();
    await screen.findByRole("table", { name: /Prices per month/ });
    fireEvent.change(screen.getByLabelText(/Partner price for CRM Professional in GBP/), { target: { value: "45,00 GBP" } });
    next();
    expect(await screen.findByRole("alert")).toHaveTextContent(/not an amount in GBP/);
    expect(screen.getByRole("heading", { name: /Step 4 of 7/ })).toBeInTheDocument();
  });

  it("disables partner prices without the pricing permission and says why", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view", "platform.staff_partner.manage"]);
    await toPricing();
    expect(await screen.findByText(/needs the platform\.staff_pricing\.manage permission/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Partner price for CRM Professional in GBP/)).toBeDisabled();
  });

  it("has nothing to price when no product was chosen", async () => {
    mockApi(routes());
    mount();
    await fillDetails();
    next();
    await screen.findByRole("checkbox", { name: /Create customer organizations/ });
    next();
    await screen.findByRole("checkbox", { name: /CRM/ });
    next();
    await toStep(/Step 4 of 7/);
    expect(screen.getByRole("status")).toHaveTextContent(/nothing to price/);
  });
});

describe("the commission step", () => {
  async function toCommission() {
    await toPricing();
    next();
    await toStep(/Step 5 of 7/);
  }

  it("says what the programme default is, as the server reports it", async () => {
    mockApi(routes());
    mount();
    await toCommission();
    expect(await screen.findByText(/programme default, currently 10%/i)).toBeInTheDocument();
  });

  it("sends whole basis points for a custom rate and refuses a bad percentage", async () => {
    const calls = mockApi(routes());
    mount();
    await toCommission();
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    fireEvent.change(screen.getByLabelText("Commission rate (%)"), { target: { value: "twelve" } });
    next();
    expect(await screen.findByRole("alert")).toHaveTextContent(/percentage from 0 to 100/);
    fireEvent.change(screen.getByLabelText("Commission rate (%)"), { target: { value: "12.5" } });
    next();
    await toStep(/Step 6 of 7/);
    fireEvent.change(screen.getByLabelText(/Administrator's email/), { target: { value: "boss@acme.example" } });
    next();
    await toStep(/Step 7 of 7/);
    await screen.findByRole("button", { name: "Create reseller…" });
    const body = reviews(calls).at(-1)!.body as { commission: { mode: string; rate_bp: number } };
    expect(body.commission).toEqual({ mode: "custom", rate_bp: 1250 });
  });

  it("disables a custom rate without the commission permission", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view", "platform.staff_partner.manage", "platform.staff_pricing.manage"]);
    await toCommission();
    expect(screen.getByRole("radio", { name: "A rate for this partner" })).toBeDisabled();
  });
});

describe("the review and the creation", () => {
  it("shows what the server returned, and no button creates anything yet", async () => {
    const calls = mockApi(routes());
    const { container } = mount();
    await toReview();
    expect(screen.getByText("Acme Resell")).toBeInTheDocument();
    expect(screen.getByText(/CRM Professional: £45\.00/)).toBeInTheDocument();
    expect(screen.getByText(/commission £6\.75/)).toBeInTheDocument();
    expect(screen.getByText(/boss@acme\.example \(administrator\)/)).toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
    await expectNoViolations(container);
  });

  it("blocks creation and names the problem when the server finds one", async () => {
    mockApi(
      routes({
        "POST /api/v1/admin/partners/reseller-review": [
          ok(reviewBody({ ok: false, problems: [{ field: "code", message: "Partner code 'acme-resell' is already in use." }] })),
        ],
      }),
    );
    mount();
    await toPricing();
    next();
    await toStep(/Step 5 of 7/);
    next();
    await toStep(/Step 6 of 7/);
    fireEvent.change(screen.getByLabelText(/Administrator's email/), { target: { value: "boss@acme.example" } });
    next();
    await toStep(/Step 7 of 7/);
    expect(await screen.findByText(/already in use/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create reseller…" })).toBeDisabled();
  });

  it("shows the server's warnings", async () => {
    mockApi(routes({ "POST /api/v1/admin/partners/reseller-review": [ok(reviewBody({ warnings: ["Without 'Earn commission' this partner earns nothing on its sales."] }))] }));
    mount();
    await toReview();
    expect(screen.getByText(/earns nothing on its sales/)).toBeInTheDocument();
  });

  it("needs a confirmation, then creates in order with selections only", async () => {
    const calls = mockApi(routes());
    mount();
    await toReview();
    fireEvent.click(screen.getByRole("button", { name: "Create reseller…" }));
    expect(writes(calls)).toEqual([]); // asked, not done
    expect(screen.getByRole("group", { name: "Confirm" })).toHaveTextContent(/Create Acme Resell\?/);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(writes(calls)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Create reseller…" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and create" }));
    expect(await screen.findByRole("heading", { name: "Reseller created" })).toBeInTheDocument();

    const keys = writes(calls).map((c) => c.key);
    expect(keys[0]).toBe("POST /api/v1/admin/partners");
    expect(keys.slice(1, 10).every((k) => k === "POST /api/v1/admin/partners/new-partner/capabilities")).toBe(true);
    expect(keys.slice(10)).toEqual([
      "POST /api/v1/admin/partners/new-partner/product-grants",
      "POST /api/v1/admin/partners/new-partner/administrators",
      "POST /api/v1/admin/partners/new-partner/status",
    ]);
    const create = calls.find((c) => c.key === "POST /api/v1/admin/partners")!.body as Record<string, unknown>;
    expect(create).toEqual({
      code: "acme-resell",
      display_name: "Acme Resell",
      payout_currency: "GBP",
      contact_name: "Priya Shah",
      contact_email: "priya@acme.example",
    });
    const granted = calls
      .filter((c) => c.key.endsWith("/capabilities") && c.key.includes("new-partner"))
      .map((c) => (c.body as { capability: string }).capability);
    expect(granted.sort()).toEqual([...PRESET].sort());
    expect(calls.find((c) => c.key.endsWith("/product-grants"))!.body).toEqual({ product_code: "crm" });
    expect(calls.find((c) => c.key.endsWith("/administrators"))!.body).toEqual({ email: "boss@acme.example", role: "admin" });
    expect(calls.find((c) => c.key.endsWith("/status"))!.body).toMatchObject({ status: "active", expected_version: 0 });
    // Nothing the browser sends carries a computed figure, an actor or a status stamp.
    for (const call of writes(calls)) {
      const text = JSON.stringify(call.body);
      expect(text).not.toMatch(/commission_minor|discount|platform_net|created_by|approved_by|list_price/);
    }
  });

  it("sets a partner price and a custom commission through their own commands, as integers", async () => {
    const calls = mockApi(routes());
    mount();
    await toPricing();
    fireEvent.change(screen.getByLabelText(/Partner price for CRM Professional in GBP/), { target: { value: "45.00" } });
    next();
    await toStep(/Step 5 of 7/);
    fireEvent.click(screen.getByRole("radio", { name: "A rate for this partner" }));
    fireEvent.change(screen.getByLabelText("Commission rate (%)"), { target: { value: "15" } });
    next();
    await toStep(/Step 6 of 7/);
    fireEvent.change(screen.getByLabelText(/Administrator's email/), { target: { value: "boss@acme.example" } });
    next();
    await toStep(/Step 7 of 7/);
    await screen.findByRole("button", { name: "Create reseller…" });
    fireEvent.click(screen.getByRole("button", { name: "Create reseller…" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and create" }));
    await screen.findByRole("heading", { name: "Reseller created" });
    expect(calls.find((c) => c.key.endsWith("/prices"))!.body).toEqual({
      plan_version_id: "pv-pro",
      interval: "monthly",
      amount_minor: 4500,
    });
    expect(calls.find((c) => c.key.endsWith("/commission"))!.body).toMatchObject({ rate_bp: 1500, expected_version: 0 });
    const order = writes(calls).map((c) => c.key.split("/").pop());
    expect(order.indexOf("prices")).toBeLessThan(order.indexOf("commission"));
    expect(order.indexOf("commission")).toBeLessThan(order.indexOf("administrators"));
    expect(order.indexOf("administrators")).toBeLessThan(order.indexOf("status"));
  });

  it("can leave the partner as a draft", async () => {
    const calls = mockApi(routes());
    mount();
    await toReview();
    fireEvent.click(screen.getByRole("checkbox", { name: /Activate the partner/ }));
    // Choosing not to activate does not invalidate the reviewed figures.
    expect(screen.getByRole("button", { name: "Create reseller…" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Create reseller…" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and create" }));
    await screen.findByRole("heading", { name: "Reseller created" });
    expect(writes(calls).map((c) => c.key)).not.toContain("POST /api/v1/admin/partners/new-partner/status");
  });

  it("resumes after a second-factor prompt without creating the partner twice", async () => {
    const stepUp: Reply = { status: 401, body: { code: "step_up_required", detail: "Verify again." } };
    const calls = mockApi(
      routes({
        "POST /api/v1/admin/partners/new-partner/capabilities": [stepUp, { status: 201, body: { id: "g", capability: "x" } }],
        "POST /api/v1/auth/mfa/step-up": [ok({ aal: 2 })],
      }),
    );
    mount();
    await toReview();
    fireEvent.click(screen.getByRole("button", { name: "Create reseller…" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and create" }));
    const code = await screen.findByLabelText(/needs a recent verification/);
    expect(screen.getByText("Waiting for your verification")).toBeInTheDocument();
    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByRole("heading", { name: "Reseller created" });
    expect(calls.filter((c) => c.key === "POST /api/v1/admin/partners")).toHaveLength(1);
    expect(calls.filter((c) => c.key.endsWith("/administrators"))).toHaveLength(1);
  });

  it("says which step failed and keeps what was done", async () => {
    const calls = mockApi(
      routes({ "POST /api/v1/admin/partners/new-partner/product-grants": [{ status: 400, body: { code: "catalog_validation_error", detail: "No such product." } }] }),
    );
    mount();
    await toReview();
    fireEvent.click(screen.getByRole("button", { name: "Create reseller…" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and create" }));
    expect((await screen.findAllByText("No such product.")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "Reseller created" })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.key === "POST /api/v1/admin/partners")).toHaveLength(1);
  });
});

describe("access", () => {
  it("says so plainly for someone who may not manage partners", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view"]);
    expect(await screen.findByRole("alert")).toHaveTextContent(/permission to create and change partners/);
    expect(screen.queryByLabelText(/Company or display name/)).not.toBeInTheDocument();
  });
});
