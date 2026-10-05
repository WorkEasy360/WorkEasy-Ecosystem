import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminShell } from "./ControlShell";
import { PartnersOverview, ResellerList } from "./PartnerConsole";
import { AdminContextForTest, SIGNED_IN, expectNoViolations, mockApi, ok } from "./testSupport";

/**
 * Phase 10a: the Partners navigation, the overview and the reseller list. What
 * the backend cannot enforce for the browser is checked here: that labels are
 * only rendered (never derived), that every card leads to its list, and that
 * what the server says is shown as text.
 */

let pathname = "/admin/partners";
let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => pathname,
}));

beforeEach(() => {
  vi.restoreAllMocks();
  pathname = "/admin/partners";
  query = "";
});

const CARDS = [
  { key: "active_partners", label: "Active partners", count: 3 },
  { key: "active_resellers", label: "Active resellers", count: 2 },
  { key: "white_label_enabled", label: "White-label enabled", count: 1 },
  { key: "partner_customers", label: "Partner customers", count: 7 },
  { key: "active_partner_subscriptions", label: "Active partner subscriptions", count: 5 },
  {
    key: "pending_commissions",
    label: "Pending commissions",
    count: 4,
    amounts: [{ currency: "GBP", amount_minor: 12345, entries: 4 }],
  },
  { key: "pending_payouts", label: "Pending payouts", count: 1, amounts: [{ currency: "GBP", amount_minor: 500, entries: 1 }] },
  { key: "domains_awaiting_verification", label: "Domains awaiting verification", count: 2 },
];

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    code: "acme",
    display_name: "Acme Partners",
    payout_currency: "GBP",
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    experience: [{ key: "reseller", label: "Reseller" }],
    capability_count: 9,
    customers: 3,
    products: [{ product_code: "crm", plan_code: null }],
    active_subscriptions: 2,
    commission: [{ currency: "GBP", balance_minor: 5000, unpaid_minor: 2000, entries: 4 }],
    payouts: { open: 1, latest: { id: "x", status: "ready", currency: "GBP", period_end: "2026-02-01", payable_minor: 3000 } },
    last_activity_at: "2026-03-01T00:00:00Z",
    ...overrides,
  };
}

describe("the Partners navigation", () => {
  it("replaces the flat entry with one Partners section, gated by what the staff member holds", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [
        ok({ is_staff: true, aal: 1, permissions: ["platform.staff_partner.view", "platform.staff_commission.view"] }),
      ],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    const links = within(screen.getByRole("navigation")).getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual([
      "Dashboard",
      "Overview",
      "Resellers",
      "White-label",
      "Customers",
      "Products & Pricing",
      "Commissions",
      "Payouts",
    ]);
    expect(links).not.toContain("Agencies & partners");
  });

  it("hides commission and payout entries from someone who may only view partners", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [ok({ is_staff: true, aal: 1, permissions: ["platform.staff_partner.view"] })],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    const links = within(screen.getByRole("navigation")).getAllByRole("link").map((a) => a.textContent);
    expect(links).not.toContain("Commissions");
    expect(links).not.toContain("Payouts");
    expect(links).toContain("Resellers");
  });
});

describe("the Partners overview", () => {
  it("shows every card as a link to the list it counts, with server-summed money", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok({ as_of: "2026-03-01T00:00:00Z", cards: CARDS })] });
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("link", { name: /Active resellers: 2/ });
    const hrefs = Object.fromEntries(
      CARDS.map((c) => [c.key, screen.getByRole("link", { name: new RegExp(`^${c.label}: ${c.count}`) }).getAttribute("href")]),
    );
    expect(hrefs).toEqual({
      active_partners: "/admin/partners/resellers?scope=all&status=active",
      active_resellers: "/admin/partners/resellers?status=active",
      white_label_enabled: "/admin/partners/white-label",
      partner_customers: "/admin/partners/customers",
      active_partner_subscriptions: "/admin/partners/customers?subscription=active",
      pending_commissions: "/admin/commissions?unpaid=1",
      pending_payouts: "/admin/payouts?status=open",
      domains_awaiting_verification: "/admin/partners/white-label?domain_status=awaiting",
    });
    // 12345 minor units is formatted, never re-summed or converted.
    expect(container.textContent).toMatch(/123\.45/);
    await expectNoViolations(container);
  });

  it("invents no revenue, growth or recurring-revenue figure", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok({ as_of: "2026-03-01T00:00:00Z", cards: CARDS })] });
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("link", { name: /^Active partners: 3/ });
    const text = container.textContent ?? "";
    // The page says why they are absent; it must not show one.
    expect(text).not.toMatch(/\bMRR\b\s*[:£$\d]/);
    expect(text).not.toMatch(/growth\s*[:+\d]/i);
  });

  it("offers Add reseller only to someone who may manage partners", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok({ as_of: "2026-03-01T00:00:00Z", cards: CARDS })] });
    const { rerender } = render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("link", { name: /^Active partners: 3/ });
    expect(screen.queryByRole("link", { name: "Add reseller" })).not.toBeInTheDocument();
    rerender(
      <AdminContextForTest permissions={["platform.staff_partner.view", "platform.staff_partner.manage"]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    expect(screen.getByRole("link", { name: "Add reseller" })).toHaveAttribute("href", "/admin/partners/resellers/new");
  });

  it("says so when the caller has no access, instead of showing zeros", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [{ status: 403, body: { code: "permission_denied" } }] });
    render(
      <AdminContextForTest permissions={[]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(/don.t have access/);
  });
});

describe("the reseller list", () => {
  const routes = (rows: unknown[], extra: Record<string, ReturnType<typeof ok>[]> = {}) => ({
    "GET /api/v1/admin/partners/resellers": [ok({ results: rows, next_cursor: null, has_more: false })],
    "GET /api/v1/admin/partners/products": [ok({ results: [{ code: "crm", name: "CRM", status: "available", plans: [] }] })],
    "GET /api/v1/admin/partners/capabilities": [
      ok({
        results: [{ key: "customer.provision", label: "Create customer organizations", group: "customer_management" }],
        groups: [],
        presets: [],
      }),
    ],
    ...extra,
  });

  it("shows the eight columns and renders the labels the server derived", async () => {
    mockApi(routes([row(), row({ id: "p2", code: "both", display_name: "Both Co", experience: [{ key: "reseller", label: "Reseller" }, { key: "white_label", label: "White-label" }] })]));
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    const table = await screen.findByRole("table", { name: "Resellers" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Reseller",
      "Status",
      "Customers",
      "Products allowed",
      "Active subscriptions",
      "Commission",
      "Payouts",
      "Last activity",
    ]);
    // One account, two labels - and the row is the same account.
    const both = within(table).getByRole("link", { name: "Both Co" }).closest("tr")!;
    expect(within(both).getByText("Reseller")).toBeInTheDocument();
    expect(within(both).getByText("White-label")).toBeInTheDocument();
    expect(within(table).getAllByRole("link", { name: /Acme|Both/ })).toHaveLength(2);
    // Commission is per currency, from the server's figures.
    expect(table.textContent).toMatch(/50\.00.*earned/);
    expect(table.textContent).toMatch(/20\.00.*unpaid/);
    await expectNoViolations(container);
  });

  it("sends the chosen filters to the server and never filters locally", async () => {
    const calls = mockApi(routes([row()]));
    render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    await screen.findByRole("table", { name: "Resellers" });
    await screen.findByRole("option", { name: "CRM" });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "active" } });
    fireEvent.change(screen.getByLabelText("Product"), { target: { value: "crm" } });
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "all" } });
    await waitFor(() => {
      const last = calls.filter((c) => c.key === "GET /api/v1/admin/partners/resellers").pop()!;
      expect(last.url).toContain("status=active");
      expect(last.url).toContain("product=crm");
      expect(last.url).toContain("scope=all");
    });
    expect(await screen.findByRole("heading", { name: "All partners" })).toBeInTheDocument();
  });

  it("starts from the filters in the address (an overview card's link)", async () => {
    query = "scope=all&status=active";
    const calls = mockApi(routes([row()]));
    render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    await screen.findByRole("table", { name: "Partner accounts" });
    const first = calls.find((c) => c.key === "GET /api/v1/admin/partners/resellers")!;
    expect(first.url).toContain("scope=all");
    expect(first.url).toContain("status=active");
    expect((screen.getByLabelText("Status") as HTMLSelectElement).value).toBe("active");
  });

  it("explains an empty list and how to widen it", async () => {
    mockApi(routes([]));
    render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    expect(await screen.findByText(/No resellers match/)).toBeInTheDocument();
  });

  it("does not depend on the filter menus: a failing product list leaves the list working", async () => {
    mockApi(
      routes([row()], {
        "GET /api/v1/admin/partners/products": [{ status: 403, body: { code: "permission_denied" } }],
      }),
    );
    render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    expect(await screen.findByRole("table", { name: "Resellers" })).toBeInTheDocument();
  });

  it("never renders a partner type", async () => {
    mockApi(routes([row()]));
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    await screen.findByRole("table", { name: "Resellers" });
    expect(container.textContent).not.toMatch(/partner type|type:/i);
  });
});
