import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { ConsoleAccess } from "@/lib/api/console";
import { OrgAccessContext } from "./OrgAccess";
import { UserDashboard, auditText, kpiMoney, percentChange, periodBounds, spendIn } from "./UserDashboard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/o/acme/dashboard",
}));

const access = (permissions: string[]): ConsoleAccess => ({
  organization: { organization_id: "o1", slug: "acme", display_name: "Acme Enterprises", status: "active", write_allowed: true },
  membership: { membership_id: "m1", is_owner: false, roles: ["admin"] },
  permissions: ["platform.organization.view", ...permissions],
  console: "admin",
});

const ADMIN = [
  "commercial.subscription.view",
  "platform.billing.view",
  "platform.billing.manage",
  "platform.member.view",
  "platform.member.invite",
  "platform.audit.view",
];

const soon = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString();
const thisMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1, 12).toISOString();

const APP = (code: string, name: string, extra: Record<string, unknown> = {}) => ({
  product_code: code,
  display_name: name,
  description: "",
  licensing_mode: "per_seat",
  state: "active",
  can_launch: true,
  entitlement_status: "active",
  slug: code,
  tagline: `${name} tagline`,
  category: "sales",
  icon: "users",
  sso: "enabled",
  health: "healthy",
  trial: null,
  plan: { code: "pro", name: "Professional" },
  seats: null,
  renews_at: null,
  cancel_at: null,
  sign_in: null,
  ...extra,
});

const WALLETS = {
  currency: "INR",
  can_spend: true,
  platform: { exists: true, currency: "INR", balance_minor: 1_840_000_00, low_balance_threshold: null, is_low: false, recent: [], other_currencies: [] },
  ai_credits: {
    exists: true,
    balance: 482_000,
    low_balance_threshold: 1000,
    is_low: false,
    included_per_month: 1000,
    used_this_month: 10,
    purchased_total: 500_000,
    resets_at: soon(20),
    recent: [],
  },
  consumption: { exists: true, currency: "INR", balance_minor: 628_500_00, low_balance_threshold: 1_000_000_00, is_low: true, this_month: [], recent: [] },
  credit_packs: [],
};

const ROUTES: Record<string, unknown> = {
  "/api/v1/orgs/acme/apps": {
    apps: [APP("easy_crm", "Easy CRM"), APP("easybook", "EasyBook", { entitlement_status: "trial", trial: { id: "t1", ends_at: soon(5), days_left: 5 } }), APP("easy_project", "Easy Project", { state: "no_entitlement", entitlement_status: null })],
  },
  "/api/v1/orgs/acme/setup": { results: [{ id: "s1", product_code: "easy_crm", status: "pending" }] },
  "/api/v1/orgs/acme/explore": { currency: "INR", products: [], bundles: [] },
  "/api/v1/orgs/acme/subscriptions": {
    results: [
      {
        id: "sub1",
        status: "active",
        currency: "INR",
        billing_interval: "monthly",
        renewal_policy: "auto_renew",
        current_period_start: thisMonth,
        current_period_end: soon(12),
        trial_ends_at: null,
        cancel_at: null,
        version: 1,
        items: [
          { id: "i1", product_code: "easy_crm", plan_code: "pro", plan_name: "CRM Advanced", bundle_code: "", quantity: 10, added_at: thisMonth, removed_at: null },
        ],
      },
    ],
  },
  "/api/v1/orgs/acme/dashboard": {
    window_days: 30,
    generated_at: thisMonth,
    organization: null,
    members: { active_members: 12, suspended_members: 0, invited_members: 1, members_added: 2, pending_invitations: 1, expired_invitations: 0, invitations_accepted: 2 },
  },
  "/api/v1/orgs/acme/wallets": WALLETS,
  "/api/v1/orgs/acme/orders": {
    results: [
      { order_id: "o1", number: "ORD-1", status: "fulfilled", currency: "INR", amount_due_minor: 64_800_00, created_at: thisMonth, paid_at: thisMonth },
    ],
    next_cursor: null,
    has_more: false,
  },
  "/api/v1/orgs/acme/audit": { results: [{ id: "a1", occurred_at: thisMonth, action: "subscription.activated" }], next_cursor: null, has_more: false },
  "/api/v1/auth/activity": { events: [{ kind: "signed_in", occurred_at: thisMonth, reason: "", ip_prefix: "", device_label: "" }] },
  "/api/v1/orgs/acme/notifications": {
    items: [{ id: "n1", category: "low_balance", tone: "warning", title: "Consumption Wallet is low", detail: "Recharge to keep messages flowing.", href: "wallets/consumption", occurred_at: null }],
    count: 1,
  },
};

const requested: string[] = [];

beforeEach(() => {
  requested.length = 0;
  global.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = (typeof input === "string" ? input : input.toString()).split("?")[0];
    requested.push(path);
    if (!(path in ROUTES)) return new Response(JSON.stringify({ code: "not_found" }), { status: 404, headers: { "content-type": "application/json" } });
    return new Response(JSON.stringify(ROUTES[path]), { status: 200, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
});

function open(permissions: string[]) {
  return render(
    <OrgAccessContext.Provider value={{ slug: "acme", access: access(permissions), reload: vi.fn(), memberName: "Manoj Kumar" }}>
      <UserDashboard />
    </OrgAccessContext.Provider>,
  );
}

describe("the User Admin dashboard", () => {
  it("greets the member and summarises the organization from real answers", async () => {
    open(ADMIN);
    expect(await screen.findByRole("heading", { level: 1, name: /Welcome, Manoj/ })).toBeInTheDocument();
    const products = await screen.findByRole("link", { name: /Active Products/ });
    // Two held, one of them a trial: one active, and the trial and pending setup said beside it.
    expect(within(products).getByText("1")).toBeInTheDocument();
    expect(within(products).getByText(/1 in trial · 1 awaiting setup/)).toBeInTheDocument();
    expect(within(await screen.findByRole("link", { name: /Team Members/ })).getByText("12")).toBeInTheDocument();
    const spend = await screen.findByRole("link", { name: /Total Spend/ });
    // Whole rupees without the ".00", as in the reference image; the period beside it.
    expect(await within(spend).findByText("₹64,800")).toBeInTheDocument();
    expect(within(spend).getByText("This Month · paid orders")).toBeInTheDocument();
    const renewals = screen.getByRole("link", { name: /Renewals Due/ });
    expect(within(renewals).getByText(/Next in 12 days/)).toBeInTheDocument();
  });

  it("keeps the three wallets apart and never adds them up", async () => {
    open(ADMIN);
    const panel = (await screen.findByRole("heading", { name: "Wallets & Credits" })).closest("section")!;
    expect(await within(panel).findByText("₹18,40,000.00")).toBeInTheDocument();
    expect(within(panel).getByText("4,82,000 Credits")).toBeInTheDocument();
    expect(within(panel).getByText("₹6,28,500.00")).toBeInTheDocument();
    // The consumption wallet is below its threshold.
    expect(within(panel).getByText("Low balance")).toBeInTheDocument();
    // No combined figure anywhere: 18,40,000 + 6,28,500 = 24,68,500.
    expect(screen.queryByText(/24,68,500/)).toBeNull();
    expect(within(panel).getByRole("link", { name: /Top Up/ })).toHaveAttribute("href", "/o/acme/wallets/platform?action=top-up");
  });

  it("shows pending actions from the server's notifications", async () => {
    open(ADMIN);
    expect(await screen.findByText("Consumption Wallet is low")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("href", "/o/acme/wallets/consumption");
  });

  it("lists subscriptions per product, with auto-renewal said rather than a Renew button", async () => {
    open(ADMIN);
    const table = await screen.findByRole("table", { name: "Subscriptions" });
    expect(within(table).getByRole("rowheader", { name: /Easy CRM/ })).toBeInTheDocument();
    expect(within(table).getByText(/CRM Advanced/)).toBeInTheDocument();
    expect(await screen.findByText("Auto-renews")).toBeInTheDocument();
  });

  it("gives a member without billing rights no balances, no spend and no spending action", async () => {
    open([]);
    expect(await screen.findByRole("heading", { level: 1, name: /Welcome, Manoj/ })).toBeInTheDocument();
    expect(screen.getByText(/Wallet balances are shown to your organization.s owner and Billing Managers/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Total Spend/ })).toBeNull();
    expect(screen.queryByRole("link", { name: "Add Money to Wallet" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Invite Team Member" })).toBeNull();
    expect(screen.getByRole("link", { name: "Raise Support Ticket" })).toHaveAttribute("href", "/o/acme/help?new=1");
    // It never asked for what it may not see.
    await screen.findByText(/Signed in/);
    expect(requested).not.toContain("/api/v1/orgs/acme/wallets");
    expect(requested).not.toContain("/api/v1/orgs/acme/orders");
    expect(requested).not.toContain("/api/v1/orgs/acme/subscriptions");
  });
});

describe("the dashboard's arithmetic", () => {
  it("sums paid orders of one currency inside the window", () => {
    const at = "2026-11-05T10:00:00Z";
    const order = (status: string, currency: string, amount: number, paid = at) =>
      ({ status, currency, amount_due_minor: amount, created_at: paid, paid_at: paid }) as never;
    const rows = [order("paid", "INR", 100), order("fulfilled", "INR", 50), order("pending_payment", "INR", 999), order("paid", "USD", 7), order("paid", "INR", 5, "2026-10-01T00:00:00Z")];
    expect(spendIn(rows, Date.parse("2026-11-01"), Date.parse("2026-12-01"), "INR")).toBe(150);
  });

  it("compares with the period before only when there was spend then", () => {
    expect(percentChange(118, 100)).toBe(18);
    expect(percentChange(50, 0)).toBeNull();
  });

  it("takes calendar months for the month periods", () => {
    const now = new Date(2026, 10, 11, 9);
    const b = periodBounds("last_month", now);
    expect(new Date(b.start).getMonth()).toBe(9);
    expect(new Date(b.end).getMonth()).toBe(10);
    expect(new Date(b.prevStart).getMonth()).toBe(8);
  });

  it("words audit actions as sentences", () => {
    expect(auditText("subscription.activated")).toBe("Subscription activated");
    expect(auditText("wallet.top_up_posted")).toBe("Wallet top up posted");
    expect(auditText("billing.payment_succeeded")).toBe("Payment received");
    // The module a record comes from is left out.
    expect(auditText("commercial.seat.released")).toBe("Seat released");
  });

  it("drops the paise of a whole amount on a card, and keeps any others", () => {
    expect(kpiMoney(64_800_00, "INR")).toBe("₹64,800");
    expect(kpiMoney(64_800_50, "INR")).toBe("₹64,800.50");
  });
});
