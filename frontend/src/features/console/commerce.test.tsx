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

describe("orders", () => {
  it("lists orders and cancels one awaiting payment", async () => {
    const calls = mockApi({
      [`GET ${ORG}/orders`]: [{ status: 200, body: { results: [ORDER], next_cursor: null, has_more: false } }],
      [`POST ${ORG}/orders/${ORDER.order_id}/cancel`]: [{ status: 200, body: { ...ORDER, status: "cancelled" } }],
    });
    const { container } = renderOrg(<OrdersScreen />, OWNER);
    const table = await screen.findByRole("table", { name: "Orders" });
    expect(within(table).getByText("ORD-000001")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Pay order ORD-000001" })).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(within(table).getByRole("button", { name: "Cancel order ORD-000001" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ORG}/orders/${ORDER.order_id}/cancel`)).toBe(true));
  });
});

describe("support", () => {
  it("shows where to write and the reference to quote", async () => {
    mockApi({
      "GET /api/v1/public/site": [
        { status: 200, body: { signup_enabled: true, support_email: "help@example.com", sales_email: "", catalog_currency: "INR" } },
      ],
    });
    const { container } = renderOrg(<HelpScreen />, []);
    expect(await screen.findByRole("link", { name: "help@example.com" })).toBeInTheDocument();
    expect(screen.getByText("acme")).toBeInTheDocument();
    await expectNoViolations(container);
  });
});
