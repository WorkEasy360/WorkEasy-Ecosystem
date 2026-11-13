import { describe, it, expect, vi, beforeEach } from "vitest";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import type { ConsoleAccess } from "@/lib/api/console";
import { OrgAccessContext } from "./OrgAccess";
import { checkoutNavigation } from "./BillingScreen";
import { CheckoutScreen, HelpScreen, HomeScreen, OrdersScreen, orderRequestFrom } from "./CommerceScreens";

/**
 * The customer's commerce screens (Phase 9): the home everyone lands on, the
 * checkout (a server quote, then an order, then the hosted payment page) and
 * the order history. The browser sends identifiers and choices - never a
 * price - and follows only a checkout address the server gave it.
 */

const nav = vi.hoisted(() => ({
  router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() },
  search: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => nav.search,
  usePathname: () => "/o/acme/dashboard",
  useParams: () => ({ orgSlug: "acme" }),
}));

configure({ asyncUtilTimeout: 4000 });

const RULESET = { runOnly: { type: "tag" as const, values: ["wcag2a", "wcag2aa"] } };

async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, RULESET);
  const detail = violations.map((v: Result) => `${v.id}: ${v.help}\n    ${v.nodes[0]?.html ?? ""}`).join("\n  ");
  expect(violations, detail).toEqual([]);
}

type Reply = { status: number; body?: unknown };
type Call = { key: string; url: string; body: unknown; headers: Record<string, string> };

function mockApi(routes: Record<string, Reply[]>) {
  const calls: Call[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const raw = init?.body;
    calls.push({
      key,
      url,
      body: typeof raw === "string" ? JSON.parse(raw) : undefined,
      headers: Object.fromEntries(new Headers(init?.headers).entries()),
    });
    const queue = routes[key];
    if (!queue) throw new Error(`unexpected request ${key}`);
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

const ORG = "/api/v1/orgs/acme";
const PV = "11111111-1111-4111-8111-111111111111";
const OWNER = [
  "commercial.subscription.view",
  "commercial.subscription.manage",
  "platform.billing.view",
  "platform.billing.manage",
];

function access(permissions: string[], consoleKind: "admin" | "member" = "member"): ConsoleAccess {
  return {
    organization: { organization_id: "o1", slug: "acme", display_name: "Acme Ltd", status: "active", write_allowed: true },
    membership: { membership_id: "m1", is_owner: false, roles: [] },
    permissions,
    console: consoleKind,
  };
}

function renderOrg(ui: React.ReactNode, permissions: string[], consoleKind: "admin" | "member" = "member") {
  return render(
    <OrgAccessContext.Provider
      value={{ slug: "acme", access: access(permissions, consoleKind), reload: vi.fn(), memberName: "Ada Lovelace" }}
    >
      <main>{ui}</main>
    </OrgAccessContext.Provider>,
  );
}

function app(
  code: string,
  name: string,
  state: string,
  entitlement: string | null = "active",
  plan: { code: string; name: string } | null = null,
) {
  return {
    product_code: code,
    display_name: name,
    description: `${name} for teams.`,
    licensing_mode: "per_organization",
    state,
    can_launch: state === "active",
    entitlement_status: entitlement,
    slug: code,
    tagline: "",
    category: "",
    icon: "",
    sso: "connected",
    health: { status: "unknown", checked_at: null },
    trial: entitlement === "trial" ? { id: "t1", ends_at: "2026-09-15T00:00:00Z", days_left: 3 } : null,
    plan,
    seats: null,
    renews_at: null,
    cancel_at: null,
  };
}

const QUOTE = {
  kind: "plan",
  description: "CRM Pro x1 (monthly)",
  product_codes: ["crm"],
  currency: "INR",
  billing_interval: "monthly",
  quantity: 1,
  recurring_amount_minor: 99900,
  amount_due_minor: 99900,
  prorated: false,
  is_trial: false,
  trial_days: 0,
  requires_payment: true,
};

const ORDER = {
  order_id: "22222222-2222-4222-8222-222222222222",
  number: "ORD-000001",
  kind: "plan",
  status: "pending_payment",
  description: "CRM Pro x1 (monthly)",
  product_codes: ["crm"],
  currency: "INR",
  billing_interval: "monthly",
  quantity: 1,
  recurring_amount_minor: 99900,
  amount_due_minor: 99900,
  is_trial: false,
  requires_payment: true,
  invoice_id: "inv1",
  invoice_number: "INV-000009",
  created_at: "2026-09-26T10:00:00Z",
  expires_at: "2026-09-27T10:00:00Z",
  paid_at: null,
  fulfilled_at: null,
  cancelled_at: null,
  failure_reason: "",
};

beforeEach(() => {
  nav.router.push.mockReset();
  nav.search = new URLSearchParams();
});

describe("the order request from the address", () => {
  it("accepts only identifiers it can validate", () => {
    expect(orderRequestFrom(new URLSearchParams(`plan=${PV}&interval=annual&trial=1`))).toEqual({
      plan_version_id: PV,
      billing_interval: "annual",
      with_trial: true,
    });
    expect(orderRequestFrom(new URLSearchParams("plan=../../x"))).toBeNull();
    expect(orderRequestFrom(new URLSearchParams(`plan=${PV}&interval=weekly`))).toEqual({ plan_version_id: PV });
    expect(orderRequestFrom(new URLSearchParams(`plan=${PV}&quantity=3`))).toEqual({ plan_version_id: PV, quantity: 3 });
    for (const q of ["0", "1", "2.5", "x", "100001"]) {
      expect(orderRequestFrom(new URLSearchParams(`plan=${PV}&quantity=${q}`))).toEqual({ plan_version_id: PV });
    }
    expect(orderRequestFrom(new URLSearchParams(`activate=${PV}&trial=1`))).toEqual({ activate_subscription_id: PV });
  });
});

describe("the dashboard", () => {
  it("shows what the organization holds first, with plan and trial status, and passes axe", async () => {
    // No session route: the greeting comes from the shell's session, and an
    // unrouted request would throw (2026-10 audit removed the second fetch).
    const calls = mockApi({
      [`GET ${ORG}/apps`]: [
        {
          status: 200,
          body: {
            apps: [
              app("crm", "CRM", "active", "trial", { code: "crm_pro", name: "CRM Pro" }),
              app("books", "Books", "no_entitlement", null),
            ],
          },
        },
      ],
      [`GET ${ORG}/subscriptions`]: [
        {
          status: 200,
          body: {
            results: [
              {
                id: "s1",
                status: "trialing",
                currency: "INR",
                billing_interval: "monthly",
                renewal_policy: "auto_renew",
                current_period_start: "2026-09-01T00:00:00Z",
                current_period_end: "2026-10-01T00:00:00Z",
                trial_ends_at: "2026-09-15T00:00:00Z",
                cancel_at: null,
                version: 1,
                items: [{ id: "i1", product_code: "crm", plan_code: "crm_pro", plan_name: "CRM Pro", bundle_code: "", quantity: 1, added_at: "2026-09-01T00:00:00Z", removed_at: null }],
              },
            ],
          },
        },
      ],
      "GET /api/v1/auth/activity": [{ status: 200, body: { events: [] } }],
    });
    const { container } = renderOrg(<HomeScreen />, OWNER);
    expect(await screen.findByRole("heading", { level: 1, name: "Welcome, Ada" })).toBeInTheDocument();
    expect(calls.map((c) => c.key)).not.toContain("GET /api/v1/auth/session");
    const card = await screen.findByRole("article", { name: "CRM" });
    expect(within(card).getByText("CRM Pro")).toBeInTheDocument();
    expect(within(card).getByText("Trial", { selector: "li" })).toBeInTheDocument(); // the status badge
    expect(within(card).getByText("3 days left")).toBeInTheDocument();
    // Books is not held: offered under Available products, not My products.
    expect(screen.queryByRole("article", { name: "Books" })).not.toBeInTheDocument();
    expect(screen.getByText("Books")).toBeInTheDocument();
    // A member console: no administrator insights.
    expect(screen.queryByRole("heading", { name: "Your organization" })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });
});

describe("checkout", () => {
  it("quotes on the server, places the order with a key, then goes to the hosted page", async () => {
    nav.search = new URLSearchParams(`plan=${PV}&interval=monthly`);
    const assign = vi.spyOn(checkoutNavigation, "assign").mockImplementation(() => undefined);
    const calls = mockApi({
      [`GET ${ORG}/billing-account`]: [{ status: 200, body: { billing_account: { id: "b1", currency: "INR", collection_mode: "automatic", billing_email: "", billing_region: "" } } }],
      [`POST ${ORG}/orders/quote`]: [{ status: 200, body: QUOTE }],
      [`POST ${ORG}/orders`]: [{ status: 201, body: ORDER }],
      [`POST ${ORG}/billing/invoices/inv1/pay`]: [
        {
          status: 202,
          body: { attempt_id: "a1", invoice_id: "inv1", status: "awaiting_payment", amount_minor: 99900, currency: "INR", provider: "stripe", checkout_url: "https://checkout.example.com/pay/1", failure_code: null, expires_at: "2026-09-26T11:00:00Z" },
        },
      ],
    });
    const { container } = renderOrg(<CheckoutScreen />, OWNER);
    expect(await screen.findByText("CRM Pro x1 (monthly)")).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Place order and pay" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.example.com/pay/1"));
    const quote = calls.find((c) => c.key === `POST ${ORG}/orders/quote`);
    expect(quote?.body).toEqual({ plan_version_id: PV, billing_interval: "monthly" });
    const placed = calls.find((c) => c.key === `POST ${ORG}/orders`);
    expect(placed?.body).toEqual({ plan_version_id: PV, billing_interval: "monthly" });
    expect(placed?.headers["idempotency-key"]).toBeTruthy();
    assign.mockRestore();
  });

  it("shows the coupon and GST the server applied, drops a refused coupon, and takes a new one (ADR-058)", async () => {
    nav.search = new URLSearchParams(`plan=${PV}&interval=annual&coupon=gone10`);
    const priced = {
      ...QUOTE,
      billing_interval: "annual",
      recurring_amount_minor: 839900,
      amount_due_minor: 991100,
      subtotal_minor: 839900,
      setup_fee_minor: 0,
      coupon: null,
      discount_minor: 0,
      tax: { label: "GST", rate_bp: 1800, amount_minor: 151200, description: "GST (18%)" },
      tax_minor: 151200,
    };
    const withCoupon = {
      ...priced,
      amount_due_minor: 892000,
      coupon: { code: "SAVE10", label: "10% off", discount_minor: 83990 },
      discount_minor: 83990,
      tax: { ...priced.tax, amount_minor: 136100 },
      tax_minor: 136100,
    };
    const calls = mockApi({
      [`GET ${ORG}/billing-account`]: [{ status: 200, body: { billing_account: { id: "b1", currency: "INR", collection_mode: "automatic", billing_email: "", billing_region: "" } } }],
      [`POST ${ORG}/orders/quote`]: [
        { status: 409, body: { code: "order_not_allowed", detail: "The coupon GONE10 has expired.", reason: "coupon_invalid", coupon_reason: "expired" } },
        { status: 200, body: priced },
        { status: 200, body: withCoupon },
      ],
    });
    const { container } = renderOrg(<CheckoutScreen />, OWNER);
    expect(await screen.findByText(/The order is priced without it/)).toBeInTheDocument();
    expect(screen.getByText("GST (18%)").nextSibling?.textContent).toMatch(/1,512/);
    expect(screen.getByText("Total payable now").nextSibling?.textContent).toMatch(/9,911/);
    await expectNoViolations(container);

    fireEvent.change(screen.getByRole("textbox", { name: "Coupon code" }), { target: { value: "save10" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText(/Coupon SAVE10 \(10% off\)/)).toBeInTheDocument();
    expect(screen.getByText("Total payable now").nextSibling?.textContent).toMatch(/8,920/);
    const quotes = calls.filter((c) => c.key === `POST ${ORG}/orders/quote`).map((c) => c.body);
    expect(quotes).toEqual([
      { plan_version_id: PV, billing_interval: "annual", coupon_code: "GONE10" },
      { plan_version_id: PV, billing_interval: "annual" },
      { plan_version_id: PV, billing_interval: "annual", coupon_code: "SAVE10" },
    ]);
  });

  it("asks for billing details first when the organization has none", async () => {
    nav.search = new URLSearchParams(`plan=${PV}`);
    mockApi({ [`GET ${ORG}/billing-account`]: [{ status: 200, body: { billing_account: null } }] });
    renderOrg(<CheckoutScreen />, OWNER);
    expect(await screen.findByLabelText("Currency")).toBeInTheDocument();
    expect(screen.queryByLabelText(/region/i)).not.toBeInTheDocument();
  });

  it("only the owner can buy, and nothing chosen is a clear message", async () => {
    nav.search = new URLSearchParams(`plan=${PV}`);
    renderOrg(<CheckoutScreen />, ["platform.billing.view"]);
    expect(screen.getByRole("alert")).toHaveTextContent("Only the organization owner can buy");
    nav.search = new URLSearchParams("");
  });
});

describe("the cart (ADR-052)", () => {
  const PV2 = "33333333-3333-4333-8333-333333333333";
  const CART_QUOTE = {
    ...QUOTE,
    kind: "cart",
    description: "CRM Pro, Books Plus (monthly)",
    product_codes: ["books", "crm"],
    recurring_amount_minor: 149800,
    amount_due_minor: 149800,
    lines: [
      { product_code: "crm", plan_name: "CRM Pro", quantity: 1, recurring_amount_minor: 99900, amount_due_minor: 99900 },
      { product_code: "books", plan_name: "Books Plus", quantity: 1, recurring_amount_minor: 49900, amount_due_minor: 49900 },
    ],
  };

  it("reads a cart from the address only when every id is valid and there are 2 to 10", () => {
    expect(orderRequestFrom(new URLSearchParams(`cart=${PV},${PV2}&interval=monthly&trial=1`))).toEqual({
      cart_items: [{ plan_version_id: PV }, { plan_version_id: PV2 }],
      billing_interval: "monthly",
    });
    expect(orderRequestFrom(new URLSearchParams(`cart=${PV},../x`))).toBeNull();
    expect(orderRequestFrom(new URLSearchParams(`cart=${PV}&plan=${PV2}`))).toEqual({ plan_version_id: PV2 });
  });

  it("shows each product's server price, places one order, then empties the cart", async () => {
    window.sessionStorage.setItem(
      "w360.cart.acme",
      JSON.stringify({
        interval: "monthly",
        items: [
          { plan_version_id: PV, product_code: "crm", product_name: "CRM", plan_name: "CRM Pro" },
          { plan_version_id: PV2, product_code: "books", product_name: "Books", plan_name: "Books Plus" },
        ],
      }),
    );
    nav.search = new URLSearchParams(`cart=${PV},${PV2}&interval=monthly`);
    const assign = vi.spyOn(checkoutNavigation, "assign").mockImplementation(() => undefined);
    const calls = mockApi({
      [`GET ${ORG}/billing-account`]: [{ status: 200, body: { billing_account: { id: "b1", currency: "INR", collection_mode: "automatic", billing_email: "", billing_region: "" } } }],
      [`POST ${ORG}/orders/quote`]: [{ status: 200, body: CART_QUOTE }],
      [`POST ${ORG}/orders`]: [{ status: 201, body: { ...ORDER, kind: "cart", amount_due_minor: 149800 } }],
      [`POST ${ORG}/billing/invoices/inv1/pay`]: [
        {
          status: 202,
          body: { attempt_id: "a1", invoice_id: "inv1", status: "awaiting_payment", amount_minor: 149800, currency: "INR", provider: "stripe", checkout_url: "https://checkout.example.com/pay/2", failure_code: null, expires_at: "2026-09-26T11:00:00Z" },
        },
      ],
    });
    const { container } = renderOrg(<CheckoutScreen />, OWNER);
    const table = await screen.findByRole("table", { name: "Products in this order" });
    expect(within(table).getByRole("rowheader", { name: "CRM Pro" })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: "Books Plus" })).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Place order and pay" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.example.com/pay/2"));
    const expected = { cart_items: [{ plan_version_id: PV }, { plan_version_id: PV2 }], billing_interval: "monthly" };
    expect(calls.find((c) => c.key === `POST ${ORG}/orders/quote`)?.body).toEqual(expected);
    expect(calls.find((c) => c.key === `POST ${ORG}/orders`)?.body).toEqual(expected);
    expect(window.sessionStorage.getItem("w360.cart.acme")).toBeNull();
    assign.mockRestore();
  });
});

const NO_PROVIDER = { provider: null, items: [], counts: {}, total: 0, priorities: ["low", "normal", "high", "urgent"], categories: ["general", "technical", "billing", "commission", "payout", "account", "product"] };
const PAID_ORDER = {
  ...ORDER,
  order_id: "33333333-3333-4333-8333-333333333333",
  number: "ORD-000002",
  status: "fulfilled",
  invoice_id: "inv2",
  invoice_number: "INV-000010",
  paid_at: "2026-09-20T10:00:00Z",
  fulfilled_at: "2026-09-20T10:01:00Z",
  lines: [{ product_code: "crm", plan_name: "CRM Pro", quantity: 1, recurring_amount_minor: 99900, amount_due_minor: 99900 }],
};
const ordersRoutes = (extra: Record<string, Reply[]> = {}) => ({
  [`GET ${ORG}/orders`]: [{ status: 200, body: { results: [ORDER, PAID_ORDER], next_cursor: null, has_more: false } }],
  [`GET ${ORG}/billing/invoices`]: [{ status: 200, body: { results: [] } }],
  ...extra,
});

describe("orders & payments", () => {
  it("lists orders with their payment and activation status, and cancels one awaiting payment after a confirm", async () => {
    const calls = mockApi(
      ordersRoutes({ [`POST ${ORG}/orders/${ORDER.order_id}/cancel`]: [{ status: 200, body: { ...ORDER, status: "cancelled" } }] }),
    );
    const { container } = renderOrg(<OrdersScreen />, OWNER);
    expect(screen.getByRole("heading", { level: 1, name: "Orders & Payments" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Orders" });
    expect(within(table).getByText("ORD-000001")).toBeInTheDocument();
    expect(within(table).getByText("Awaiting payment")).toBeInTheDocument();
    expect(within(table).getByText("Activated")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Pay order ORD-000001" })).toBeInTheDocument();
    // The invoices are on this page too.
    expect(await screen.findByRole("heading", { name: "Invoices" })).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(within(table).getByRole("button", { name: "Cancel order ORD-000001" }));
    expect(calls.some((c) => c.key === `POST ${ORG}/orders/${ORDER.order_id}/cancel`)).toBe(false);
    fireEvent.click(within(screen.getByRole("dialog", { name: "Cancel order ORD-000001?" })).getByRole("button", { name: "Cancel order" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ORG}/orders/${ORDER.order_id}/cancel`)).toBe(true));
  });

  it("filters by status and searches the loaded orders", async () => {
    mockApi(ordersRoutes());
    renderOrg(<OrdersScreen />, OWNER);
    const table = await screen.findByRole("table", { name: "Orders" });
    fireEvent.click(within(screen.getByRole("group", { name: "Order status" })).getByRole("button", { name: /^Complete/ }));
    expect(within(table).queryByText("ORD-000001")).not.toBeInTheDocument();
    expect(within(table).getByText("ORD-000002")).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("group", { name: "Order status" })).getByRole("button", { name: /^All/ }));
    const search = screen.getByLabelText("Search by order, invoice or item");
    fireEvent.change(search, { target: { value: "INV-000009" } });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Orders" })).queryByText("ORD-000002")).not.toBeInTheDocument());
  });

  it("shows an order's payment details: lines, dates and the payment attempts", async () => {
    mockApi(
      ordersRoutes({
        [`GET ${ORG}/billing/invoices/inv2`]: [
          {
            status: 200,
            body: {
              invoice_id: "inv2",
              number: "INV-000010",
              status: "paid",
              currency: "INR",
              lines: [],
              attempts: [
                { attempt_id: "a1aaaaaa-0000", invoice_id: "inv2", status: "succeeded", amount_minor: 99900, currency: "INR", provider: "razorpay", checkout_url: null, failure_code: null, expires_at: "2026-09-21T00:00:00Z" },
              ],
            },
          },
        ],
      }),
    );
    renderOrg(<OrdersScreen />, OWNER);
    fireEvent.click(await screen.findByRole("button", { name: "Payment details of order ORD-000002" }));
    const details = screen.getByRole("dialog", { name: "Order ORD-000002" });
    expect(within(details).getByRole("table", { name: "Products in order ORD-000002" })).toBeInTheDocument();
    expect(await within(details).findByText("razorpay")).toBeInTheDocument();
    await expectNoViolations(document.body);
  });

  it("a refund is asked for as a billing ticket to whoever sold the order, prefilled", async () => {
    const calls = mockApi(
      ordersRoutes({
        [`GET ${ORG}/provider-tickets`]: [{ status: 200, body: NO_PROVIDER }],
        [`GET ${ORG}/support-tickets`]: [{ status: 200, body: { items: [], counts: {} } }],
        [`POST ${ORG}/support-tickets`]: [{ status: 201, body: { id: "t9", reference: "#9", subject: "Refund request: order ORD-000002", status: "open", updated_at: "2026-09-26T10:00:00Z" } }],
      }),
    );
    renderOrg(<OrdersScreen />, OWNER);
    // Only a paid order offers it.
    await screen.findByRole("table", { name: "Orders" });
    expect(screen.queryByRole("button", { name: "Request a refund for order ORD-000001" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Request a refund for order ORD-000002" }));
    const dialog = await screen.findByRole("dialog", { name: /Request a refund/ });
    expect(within(dialog).getByLabelText("Subject")).toHaveValue("Refund request: order ORD-000002");
    expect(within(dialog).getByLabelText("Category")).toHaveValue("billing");
    fireEvent.change(within(dialog).getByLabelText("How can we help?"), { target: { value: "Please refund order ORD-000002. Reason: bought twice" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send ticket" }));
    expect(await screen.findByText(/Refund request for order ORD-000002 sent/)).toBeInTheDocument();
    const sent = calls.find((c) => c.key === `POST ${ORG}/support-tickets`)!;
    expect(sent.body).toEqual({
      subject: "Refund request: order ORD-000002",
      message: "Please refund order ORD-000002. Reason: bought twice",
      category: "billing",
      priority: "normal",
    });
  });

  it("someone who may not pay sees Contact Organization Admin instead of Pay or a refund", async () => {
    mockApi(ordersRoutes());
    renderOrg(<OrdersScreen />, ["platform.billing.view"]);
    await screen.findByRole("table", { name: "Orders" });
    expect(screen.queryByRole("button", { name: /^Pay order/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Request a refund/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Cancel order/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("Contact Organization Admin").length).toBeGreaterThan(0);
  });
});

const helpRoutes = (extra: Record<string, Reply[]> = {}) => ({
  "GET /api/v1/public/site": [
    { status: 200, body: { signup_enabled: true, support_email: "help@example.com", sales_email: "", catalog_currency: "INR" } },
  ],
  [`GET ${ORG}/provider-tickets`]: [{ status: 200, body: NO_PROVIDER }],
  [`GET ${ORG}/support-tickets`]: [{ status: 200, body: { items: [], counts: {} } }],
  [`GET ${ORG}/setup`]: [{ status: 200, body: { results: [] } }],
  "GET /api/v1/public/help": [
    {
      status: 200,
      body: {
        results: [
          { slug: "s", kind: "setup_guide", title: "Connect your CRM", body: "Open CRM settings." },
          { slug: "b", kind: "faq", title: "How do I pay an invoice?", body: "Open Orders & Payments." },
          { slug: "o", kind: "faq", title: "How do I export?", body: "Open Reports." },
        ],
      },
    },
  ],
  ...extra,
});

describe("help & support", () => {
  it("shows who answers, where to write, the reference to quote, and the guides", async () => {
    mockApi(helpRoutes());
    const { container } = renderOrg(<HelpScreen />, []);
    expect(await screen.findByRole("link", { name: "help@example.com" })).toBeInTheDocument();
    expect(screen.getByText("acme")).toBeInTheDocument();
    expect(await screen.findByText(/The WorkEasy360 team answers your tickets/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "My tickets" })).toBeInTheDocument();
    // Guides: setup, billing and the rest, from the published library.
    const setup = await screen.findByRole("region", { name: "Setup guides" });
    expect(within(setup).getByText("Connect your CRM")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Billing guides" })).getByText("How do I pay an invoice?")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Common questions" })).getByText("How do I export?")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("?new=1 opens Create Support Ticket on arrival, and points a product's pending setup to My Products", async () => {
    nav.search = new URLSearchParams("new=1");
    mockApi(
      helpRoutes({
        [`GET ${ORG}/setup`]: [{ status: 200, body: { results: [{ id: "s1", product_code: "crm", status: "pending", answers: {}, decision_reason: "", requirements: [], editable: true }] } }],
      }),
    );
    renderOrg(<HelpScreen />, []);
    expect(await screen.findByRole("dialog", { name: "Create support ticket" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Open Pending Setup" })).toHaveAttribute("href", "/o/acme/apps?filter=setup");
  });
});
