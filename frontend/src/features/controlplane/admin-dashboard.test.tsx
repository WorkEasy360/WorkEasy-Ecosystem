import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminShell } from "./ControlShell";
import { Dashboard } from "./AdminDashboard";
import { AdminSearch } from "./AdminTopbar";
import { foldSlices } from "./dashboardCharts";
import { AdminContextForTest, SIGNED_IN, expectNoViolations, mockApi, ok } from "./testSupport";

/**
 * The Admin console redesign (2026-10-26): the dashboard's month
 * overview, the latest rows, and the top bar (search, New, the bell). Real
 * figures only, and only what the backend sent for the staff member's grants.
 */

let pathname = "/admin";
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => pathname,
}));

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.push.mockReset();
  pathname = "/admin";
});

const ALL = [
  "platform.staff_customer.view",
  "platform.staff_partner.view",
  "platform.staff_payment.view",
  "platform.staff_kyc.view",
  "platform.staff_wallet.view",
];

const wallet = (kind: string, label: string, balance: number, currency = "INR") => ({
  kind,
  label,
  currency,
  balance,
  wallets: 2,
  other_currencies: [],
});

const OVERVIEW = {
  period: { from: "2026-10-11", to: "2026-11-09", days: 30, compared_from: "2026-09-11", compared_to: "2026-10-10" },
  definitions: { sales: "Invoices issued in the period.", collections: "Money received less refunds." },
  currency: "INR",
  currencies: ["INR"],
  cards: {
    customers: { total: 412, at_start: 400, added: 12, change_percent: 3 },
    partners: { total: 28, at_start: 28, added: 0, change_percent: 0 },
    active_subscriptions: { total: 97, at_start: 0, added: 97, change_percent: null },
    collections: { amount_minor: 123456, previous_minor: 100000, change_percent: 23.5 },
    sales: { amount_minor: 200000, previous_minor: 0, change_percent: null },
  },
  wallets: {
    platform: wallet("platform", "Platform Wallet", 50000),
    ai_credits: wallet("ai_credits", "AI Credits", 4200, ""),
    consumption: wallet("consumption", "Consumption Wallet", 7000),
  },
  pending: [
    { key: "kyc_reviews", label: "Partner KYC Reviews", hint: "Waiting", count: 3, action: "Review", href: "/admin/partners/applications" },
    { key: "failed_payments", label: "Failed Payments / Activations", hint: "Waiting", count: 1, action: "Resolve", href: "/admin/payments/failed" },
  ],
  activity: [
    { kind: "customer_registered", at: "2026-11-09T01:00:00Z", title: "New customer: Acme Logistics", href: "/admin/customers/o1" },
    { kind: "payment_failed", at: "2026-11-09T00:30:00Z", title: "Payment failed for BrightMart", amount_minor: 2400000, currency: "INR", href: "/admin/customers/o2" },
  ],
  top_products: [{ code: "easy_crm", name: "Easy CRM", active_subscriptions: 40, revenue_minor: 150000, currency: "INR" }],
};

const GROWTH = { months: ["2026-10", "2026-11"], customers: [400, 412], subscriptions: [0, 97], partners: [28, 28] };
const MONEY = { months: ["2026-10", "2026-11"], currency: "INR", currencies: ["INR"], sales: [100000, 200000], collections: [90000, 123456] };

function dashboardApi(overview: unknown = OVERVIEW) {
  return mockApi({
    "GET /api/v1/admin/dashboard/overview": [ok(overview)],
    "GET /api/v1/admin/dashboard/growth": [ok(GROWTH)],
    "GET /api/v1/admin/dashboard/money": [ok(MONEY)],
  });
}

describe("the dashboard", () => {
  it("shows the six figures, what is waiting, what happened and the top products; passes axe", async () => {
    dashboardApi();
    const { container } = render(
      <AdminContextForTest permissions={ALL}>
        <Dashboard />
      </AdminContextForTest>,
    );
    const figures = await screen.findByRole("group", { name: "Figures" });
    for (const label of ["Customers", "Partners", "Active Subscriptions", "Collections", "Sales", "Wallet Summary"]) {
      expect(within(figures).getByText(label)).toBeInTheDocument();
    }
    expect(within(figures).getByText("412")).toBeInTheDocument();
    expect(within(figures).getByText("₹1,234.56")).toBeInTheDocument();
    // Platform + Consumption only: credits are not money and are never added in.
    expect(within(figures).getByText("₹570.00")).toBeInTheDocument();
    expect(within(figures).getByRole("link", { name: /^Customers/ })).toHaveAttribute("href", "/admin/customers");
    expect(within(figures).getByRole("link", { name: /^Wallet Summary/ })).toHaveAttribute("href", "/admin/wallets");
    // No earlier figure means no invented percentage.
    expect(within(figures).getByRole("link", { name: /^Sales/ }).textContent).toMatch(/No earlier figure/);

    const pending = screen.getByRole("region", { name: "Pending Actions" });
    expect(within(pending).getByText("Partner KYC Reviews", { selector: "strong" })).toBeInTheDocument();
    expect(within(pending).getByRole("link", { name: /Review/ })).toHaveAttribute("href", "/admin/partners/applications");

    const activity = screen.getByRole("region", { name: "Recent Activities" });
    expect(within(activity).getByRole("link", { name: /Payment failed for BrightMart \(₹24,000\.00\)/ })).toHaveAttribute("href", "/admin/customers/o2");

    const products = screen.getByRole("region", { name: "Top Products" });
    expect(within(products).getByRole("rowheader", { name: "Easy CRM" })).toBeInTheDocument();

    const wallets = screen.getByRole("region", { name: "Wallet Balance Summary" });
    expect(within(wallets).getByText("4,200 credits")).toBeInTheDocument();
    expect(screen.queryByText(/commission/i, { selector: "strong" })).toBeNull();

    await screen.findByRole("table", { name: /Customers, subscriptions and partners/ });
    await screen.findByRole("table", { name: /Collections and sales per month in INR/ });
    await expectNoViolations(container);
  });

  it("leaves out what the grants do not cover, and asks for nothing more", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/dashboard/overview": [
        ok({
          period: OVERVIEW.period,
          definitions: OVERVIEW.definitions,
          cards: { customers: OVERVIEW.cards.customers, active_subscriptions: OVERVIEW.cards.active_subscriptions },
          pending: [],
          activity: [],
        }),
      ],
      "GET /api/v1/admin/dashboard/growth": [ok({ months: GROWTH.months, customers: GROWTH.customers, subscriptions: GROWTH.subscriptions })],
    });
    render(
      <AdminContextForTest permissions={["platform.staff_customer.view"]}>
        <Dashboard />
      </AdminContextForTest>,
    );
    const figures = await screen.findByRole("group", { name: "Figures" });
    expect(within(figures).queryByText("Sales")).toBeNull();
    expect(within(figures).queryByText("Wallet Summary")).toBeNull();
    expect(screen.queryByRole("region", { name: "Top Products" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Collections vs Sales" })).toBeNull();
    await waitFor(() => expect(calls.map((c) => c.key)).toContain("GET /api/v1/admin/dashboard/growth"));
    expect(calls.map((c) => c.key)).not.toContain("GET /api/v1/admin/dashboard/money");
  });

  it("asks for the period chosen, including a custom range", async () => {
    const calls = dashboardApi();
    render(
      <AdminContextForTest permissions={ALL}>
        <Dashboard />
      </AdminContextForTest>,
    );
    await screen.findByRole("group", { name: "Figures" });
    fireEvent.click(screen.getByRole("combobox", { name: "Period" }));
    fireEvent.click(screen.getByRole("option", { name: "Last 90 Days" }));
    await waitFor(() =>
      expect(calls.some((c) => c.key === "GET /api/v1/admin/dashboard/overview" && c.url.includes("days=90"))).toBe(true),
    );
    fireEvent.click(screen.getByRole("combobox", { name: "Period" }));
    fireEvent.click(screen.getByRole("option", { name: "Custom range" }));
    await waitFor(() =>
      expect(calls.some((c) => c.key === "GET /api/v1/admin/dashboard/overview" && c.url.includes("from="))).toBe(true),
    );
  });

  it("each chart has its own range", async () => {
    const calls = dashboardApi();
    render(
      <AdminContextForTest permissions={ALL}>
        <Dashboard />
      </AdminContextForTest>,
    );
    await screen.findByRole("group", { name: "Figures" });
    fireEvent.click(await screen.findByRole("combobox", { name: "Trend range" }));
    fireEvent.click(screen.getByRole("option", { name: "Last 12 Months" }));
    await waitFor(() =>
      expect(calls.some((c) => c.key === "GET /api/v1/admin/dashboard/growth" && c.url.includes("months=12"))).toBe(true),
    );
    expect(calls.some((c) => c.key === "GET /api/v1/admin/dashboard/money" && c.url.includes("months=12"))).toBe(false);
  });

  it("offers a currency only when there is more than one, and asks for the one chosen", async () => {
    const calls = dashboardApi({ ...OVERVIEW, currencies: ["INR", "USD"] });
    render(
      <AdminContextForTest permissions={ALL}>
        <Dashboard />
      </AdminContextForTest>,
    );
    fireEvent.click(await screen.findByRole("combobox", { name: "Currency" }));
    fireEvent.click(screen.getByRole("option", { name: "USD" }));
    await waitFor(() =>
      expect(calls.some((c) => c.key === "GET /api/v1/admin/dashboard/overview" && c.url.includes("currency=USD"))).toBe(true),
    );
  });

  it("says plainly when there is nothing yet, and shows an error rather than zeros when it fails", async () => {
    const empty = {
      ...OVERVIEW,
      pending: [],
      activity: [],
      top_products: [],
      cards: {
        ...OVERVIEW.cards,
        collections: { amount_minor: 0, previous_minor: 0, change_percent: null },
        sales: { amount_minor: 0, previous_minor: 0, change_percent: null },
      },
    };
    mockApi({
      "GET /api/v1/admin/dashboard/overview": [ok(empty)],
      "GET /api/v1/admin/dashboard/growth": [ok({ months: GROWTH.months, customers: [0, 0], subscriptions: [0, 0], partners: [0, 0] })],
      "GET /api/v1/admin/dashboard/money": [ok({ ...MONEY, sales: [0, 0], collections: [0, 0] })],
    });
    const view = render(
      <AdminContextForTest permissions={ALL}>
        <Dashboard />
      </AdminContextForTest>,
    );
    expect(await screen.findByText("No activity yet")).toBeInTheDocument();
    expect(screen.getByText("No products sold yet")).toBeInTheDocument();
    expect(await screen.findByText("Nothing to chart yet")).toBeInTheDocument();
    expect(await screen.findByText("No money movement yet")).toBeInTheDocument();
    view.unmount();

    mockApi({ "GET /api/v1/admin/dashboard/overview": [{ status: 500, body: { code: "boom" } }] });
    render(
      <AdminContextForTest permissions={ALL}>
        <Dashboard />
      </AdminContextForTest>,
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Figures" })).toBeNull();
  });

  it("folds a sixth and later product into Others, so no colour is generated", () => {
    const slices = Array.from({ length: 8 }, (_, i) => ({ key: `p${i}`, label: `P${i}`, value: 1 }));
    expect(foldSlices(slices).map((s) => s.label)).toEqual(["P0", "P1", "P2", "P3", "P4", "Others"]);
  });
});

function shellApi(permissions: string[], attention: unknown = { items: [] }) {
  return mockApi({
    "GET /api/v1/auth/session": [ok({ authenticated: true, user_id: "u1", email: "admin@example.com", name: "Aman Gupta" })],
    "GET /api/v1/admin/session": [ok({ is_staff: true, permissions })],
    "GET /api/v1/admin/attention": [ok(attention)],
  });
}

describe("the Admin top bar", () => {
  it("shows the bell's count and where each waiting item is dealt with", async () => {
    shellApi(["platform.staff_customer.view", "platform.staff_partner.view"], {
      items: [
        { key: "partner_upgrades", count: 2 },
        { key: "draft_partners", count: 1 },
      ],
    });
    const { container } = render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    const bell = await screen.findByRole("button", { name: "Notifications: 3 waiting" });
    fireEvent.click(bell);
    expect(await screen.findByRole("link", { name: /^2 upgrade requests wait for a decision/ })).toHaveAttribute("href", "/admin/partners/all");
    expect(screen.getByRole("link", { name: /^1 partner is still a draft/ })).toHaveAttribute("href", "/admin/partners/all?status=draft");
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/admin/notifications");
    // Unread until marked read on this browser.
    expect(screen.getAllByText("Unread")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    expect(screen.queryByText("Unread")).not.toBeInTheDocument();
    // Avatar, full name and role beside it; the menu opens from it.
    const account = screen.getByRole("button", { name: "Account menu for Aman Gupta" });
    expect(account).toHaveTextContent("Aman Gupta");
    expect(account).toHaveTextContent("Staff (read-only)");
    await expectNoViolations(container);
    fireEvent.click(account);
    const menu = screen.getByRole("menu", { name: "Account menu for Aman Gupta" });
    expect(within(menu).getByText("admin@example.com")).toBeInTheDocument();
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "Sign-in preferences",
      "My organizations",
      "Help & support",
      "Sign out",
    ]);
  });

  it("offers in Create only what the staff member may create, and nothing without a grant", async () => {
    shellApi(["platform.staff_customer.view", "platform.staff_partner.manage", "platform.staff_partner.view"]);
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Create" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["New partner"]);
  });

  it("has no Create button and no settings shortcut for a read-only staff member", async () => {
    shellApi(["platform.staff_customer.view"]);
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    expect(screen.queryByRole("button", { name: "Create" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Platform settings" })).not.toBeInTheDocument();
  });

  it("marks the module holding the page, shows its sections and pages as tabs, and hides a module with nothing in it", async () => {
    pathname = "/admin/orders";
    shellApi(["platform.staff_customer.view", "platform.staff_payment.view"]);
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    const nav = await screen.findByRole("navigation", { name: "Admin Portal" });
    expect(within(nav).getByRole("link", { name: "Billing & Subscriptions" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Customers" })).not.toHaveAttribute("aria-current");
    // Nothing beneath the nine entries: no disclosure buttons, no sub-pages.
    expect(within(nav).queryAllByRole("button").filter((b) => b.hasAttribute("aria-expanded"))).toEqual([]);
    expect(within(nav).queryByRole("link", { name: "Orders" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
    expect(within(nav).getByText("Admin Portal")).toBeInTheDocument();
    const sections = screen.getByRole("navigation", { name: "Billing & Subscriptions sections" });
    expect(within(sections).getByRole("link", { name: "Orders & Payments" })).toHaveAttribute("aria-current", "true");
    const pages = screen.getByRole("navigation", { name: "Orders & Payments pages" });
    expect(within(pages).getByRole("link", { name: "Orders" })).toHaveAttribute("aria-current", "page");
    expect(within(pages).getByRole("link", { name: "Payments" })).toHaveAttribute("href", "/admin/payments");
  });
});

describe("the command palette", () => {
  it("finds pages by name and records from the server, grouped, and opens the chosen one", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/search": [
        ok({
          query: "plan",
          results: [
            { kind: "user", id: "u9", label: "Pla Nner", hint: "planner@example.com", href: "/admin/users/u9" },
            { kind: "invoice", id: "i1", label: "INV-PLAN", hint: "open", href: "/admin/invoices?q=INV-PLAN" },
          ],
        }),
      ],
    });
    render(<AdminSearch permissions={["platform.staff_customer.view", "platform.staff_user.view", "platform.staff_catalog.view"]} />);
    fireEvent.click(screen.getByRole("button", { name: /Search customers, partners/ }));
    const box = screen.getByRole("combobox", { name: "Search anything" });
    fireEvent.change(box, { target: { value: "plan" } });
    expect(screen.getAllByRole("option")[0]).toHaveTextContent("Plans & add-ons");
    const person = await screen.findByRole("option", { name: /Pla Nner/ });
    expect(screen.getByRole("option", { name: /INV-PLAN/ })).toBeInTheDocument();
    expect(calls.find((c) => c.key === "GET /api/v1/admin/search")?.url).toContain("q=plan");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(box.getAttribute("aria-activedescendant")).toBe(screen.getAllByRole("option")[1].id);
    fireEvent.mouseDown(person);
    expect(nav.router.push).toHaveBeenCalledWith("/admin/users/u9");
  });

  it("opens on Ctrl+K with the search box focused", () => {
    render(<AdminSearch permissions={["platform.staff_customer.view"]} />);
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Search anything" })).toHaveFocus();
  });
});

