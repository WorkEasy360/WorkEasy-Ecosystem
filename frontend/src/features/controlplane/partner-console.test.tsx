import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminShell } from "./ControlShell";
import { AffiliateList, AllPartnersList, PartnersOverview, ResellerList } from "./PartnerConsole";
import { PartnerCenterLayout } from "./PartnerCenter";
import { AdminContextForTest, SIGNED_IN, expectNoViolations, mockApi, ok } from "./testSupport";

/**
 * The Partner Control Center: one "Partners" entry in the Super Admin sidebar,
 * the Control Center's own navigation, the dashboard (cards, quick actions,
 * recent activity) and the three partner lists. What the backend cannot
 * enforce for the browser is checked here: that labels are only rendered
 * (never derived), that every card leads to its list, that money the server
 * withheld is not shown as "nothing", and that what the server says is shown
 * as text.
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

const VIEW = "platform.staff_partner.view";
const MANAGE = "platform.staff_partner.manage";
const MONEY = "platform.staff_commission.view";

const CARDS = [
  { key: "total_partners", label: "Total partners", count: 4 },
  { key: "active_partners", label: "Active partners", count: 3 },
  { key: "active_resellers", label: "Active resellers", count: 2 },
  { key: "white_label_enabled", label: "White-label enabled", count: 1 },
  { key: "active_affiliates", label: "Active affiliates", count: 1 },
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

const RECENT = {
  as_of: "2026-03-01T00:00:00Z",
  partners: [
    {
      id: "p9",
      code: "newest",
      display_name: "Newest Partner",
      status: "draft",
      created_at: "2026-02-28T00:00:00Z",
      experience: [{ key: "affiliate", label: "Affiliate" }],
    },
  ],
  provisioning: [
    {
      organization_id: "o1",
      name: "Customer One",
      partner_id: "p1",
      partner_name: "Acme Partners",
      link_status: "active",
      started_at: "2026-02-20T00:00:00Z",
    },
  ],
  sales: [
    {
      id: "a1",
      kind: "accrual",
      amount_minor: 500,
      currency: "GBP",
      event_at: "2026-02-21T00:00:00Z",
      partner_id: "p1",
      partner_name: "Acme Partners",
      organization_name: "Customer One",
    },
  ],
  commission: [],
  payouts: [],
  domains: [
    {
      id: "d1",
      hostname: "app.brand-example.com",
      status: "verification_pending",
      requested_at: "2026-02-22T00:00:00Z",
      partner_id: "p2",
      partner_name: "Brand Co",
    },
  ],
};

const dashboardRoutes = (recent: unknown = RECENT) => ({
  "GET /api/v1/admin/partners/overview": [ok({ as_of: "2026-03-01T00:00:00Z", cards: CARDS })],
  "GET /api/v1/admin/partners/recent": [ok(recent)],
});

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
    payouts: {
      open: 1,
      latest: { id: "x", status: "ready", currency: "GBP", period_end: "2026-02-01", payable_minor: 3000 },
      paid: [],
    },
    referrals: { codes: [], active_codes: 0, captured: 0, converted: 0 },
    last_activity_at: "2026-03-01T00:00:00Z",
    ...overrides,
  };
}

const listRoutes = (rows: unknown[], extra: Record<string, ReturnType<typeof ok>[]> = {}) => ({
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

// --- navigation ------------------------------------------------------------------------------

describe("the Super Admin sidebar", () => {
  async function sidebarLinks(permissions: string[]) {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [ok({ is_staff: true, aal: 1, permissions })],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    return within(screen.getByRole("navigation", { name: /Super Admin/ })).getAllByRole("link");
  }

  it("has one Partners entry instead of a link per partner screen", async () => {
    const links = await sidebarLinks([VIEW, MONEY]);
    expect(links.map((a) => a.textContent)).toEqual(["Dashboard", "Partners"]);
    expect(links[1]).toHaveAttribute("href", "/admin/partners");
    for (const old of ["Overview", "Resellers", "White-label", "Commissions", "Payouts", "Agencies & partners"]) {
      expect(links.map((a) => a.textContent)).not.toContain(old);
    }
  });

  it("stays current on every page of the Control Center", async () => {
    pathname = "/admin/partners/affiliates";
    const links = await sidebarLinks([VIEW]);
    expect(links.find((a) => a.textContent === "Partners")).toHaveAttribute("aria-current", "page");
  });

  it("opens the first section the staff member may read, and is absent with none", async () => {
    const moneyOnly = await sidebarLinks([MONEY]);
    expect(moneyOnly.find((a) => a.textContent === "Partners")).toHaveAttribute("href", "/admin/partners/commissions");
  });

  it("is absent for someone with no partner or commission grant", async () => {
    const links = await sidebarLinks(["platform.staff_customer.view"]);
    expect(links.map((a) => a.textContent)).not.toContain("Partners");
  });
});

describe("the Partner Control Center navigation", () => {
  function renderCenter(permissions: string[]) {
    return render(
      <AdminContextForTest permissions={permissions}>
        <PartnerCenterLayout>
          <h1>Body</h1>
        </PartnerCenterLayout>
      </AdminContextForTest>,
    );
  }

  it("lists the nine sections in order, marking the current one", async () => {
    pathname = "/admin/partners/resellers";
    const { container } = renderCenter([VIEW, MONEY]);
    const nav = screen.getByRole("navigation", { name: "Partner Control Center" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual([
      "Dashboard",
      "All Partners",
      "Resellers",
      "White-label",
      "Affiliates",
      "Customers",
      "Products & Pricing",
      "Commissions",
      "Payouts",
    ]);
    expect(within(nav).getByRole("link", { name: "Resellers" })).toHaveAttribute("aria-current", "page");
    expect(links.filter((a) => a.getAttribute("aria-current") === "page")).toHaveLength(1);
    // The page keeps the only h1; the Control Center header is not a second one.
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Body"]);
    await expectNoViolations(container);
  });

  it("marks the dashboard only on its own page", () => {
    pathname = "/admin/partners/all";
    renderCenter([VIEW]);
    const nav = screen.getByRole("navigation", { name: "Partner Control Center" });
    expect(within(nav).getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
    expect(within(nav).getByRole("link", { name: "All Partners" })).toHaveAttribute("aria-current", "page");
  });

  it("keeps a sub-page under its section (a reseller being added)", () => {
    pathname = "/admin/partners/resellers/new";
    renderCenter([VIEW]);
    expect(screen.getByRole("link", { name: "Resellers" })).toHaveAttribute("aria-current", "page");
  });

  it("hides the money sections from someone who may only view partners", () => {
    renderCenter([VIEW]);
    const labels = within(screen.getByRole("navigation", { name: "Partner Control Center" }))
      .getAllByRole("link")
      .map((a) => a.textContent);
    expect(labels).not.toContain("Commissions");
    expect(labels).not.toContain("Payouts");
    expect(labels).toContain("Affiliates");
  });

  it("shows only the money sections to someone who may only see commission", () => {
    pathname = "/admin/partners/commissions";
    renderCenter([MONEY]);
    const labels = within(screen.getByRole("navigation", { name: "Partner Control Center" }))
      .getAllByRole("link")
      .map((a) => a.textContent);
    expect(labels).toEqual(["Commissions", "Payouts"]);
  });
});

// --- the dashboard -------------------------------------------------------------------------------

describe("the Partner dashboard", () => {
  it("shows every card as a link to the list it counts, with server-summed money", async () => {
    mockApi(dashboardRoutes());
    const { container } = render(
      <AdminContextForTest permissions={[VIEW, MONEY]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("link", { name: /Active resellers: 2/ });
    await screen.findByRole("heading", { name: "Recent partners" });
    const hrefs = Object.fromEntries(
      CARDS.map((c) => [c.key, screen.getByRole("link", { name: new RegExp(`^${c.label}: ${c.count}`) }).getAttribute("href")]),
    );
    expect(hrefs).toEqual({
      total_partners: "/admin/partners/all",
      active_partners: "/admin/partners/all?status=active",
      active_resellers: "/admin/partners/resellers?status=active",
      white_label_enabled: "/admin/partners/white-label",
      active_affiliates: "/admin/partners/affiliates?status=active",
      partner_customers: "/admin/partners/customers",
      active_partner_subscriptions: "/admin/partners/customers?subscription=active",
      pending_commissions: "/admin/partners/commissions?unpaid=1",
      pending_payouts: "/admin/partners/payouts?status=open",
      domains_awaiting_verification: "/admin/partners/white-label?domain_status=awaiting",
    });
    // 12345 minor units is formatted, never re-summed or converted.
    expect(container.textContent).toMatch(/123\.45/);
    await expectNoViolations(container);
  });

  it("invents no revenue, growth or recurring-revenue figure", async () => {
    mockApi(dashboardRoutes());
    const { container } = render(
      <AdminContextForTest permissions={[VIEW]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("link", { name: /^Active partners: 3/ });
    const text = container.textContent ?? "";
    // The page says why they are absent; it must not show one.
    expect(text).not.toMatch(/\bMRR\b\s*[:£$\d]/);
    expect(text).not.toMatch(/\bARR\b\s*[:£$\d]/);
    expect(text).not.toMatch(/growth\s*[:+\d]/i);
  });

  it("offers the quick actions that match the staff member's grants", async () => {
    mockApi(dashboardRoutes());
    const { rerender } = render(
      <AdminContextForTest permissions={[VIEW]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("link", { name: /^Active partners: 3/ });
    expect(screen.queryByRole("navigation", { name: "Quick actions" })).not.toBeInTheDocument();

    rerender(
      <AdminContextForTest permissions={[VIEW, MANAGE, MONEY]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    const actions = within(screen.getByRole("navigation", { name: "Quick actions" }));
    expect(actions.getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Add partner", "/admin/partners/new"],
      ["Add reseller", "/admin/partners/resellers/new"],
      ["Enable white-label", "/admin/partners/white-label#wl-enable"],
      ["Create affiliate", "/admin/partners/new?purpose=affiliate"],
      ["Review payouts", "/admin/partners/payouts?status=open"],
    ]);
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

describe("recent activity", () => {
  it("links every row to the screen that explains it", async () => {
    mockApi(dashboardRoutes());
    const { container } = render(
      <AdminContextForTest permissions={[VIEW, MONEY]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    const partners = (await screen.findByRole("heading", { name: "Recent partners" })).closest("section")!;
    expect(within(partners).getByRole("link", { name: "Newest Partner" })).toHaveAttribute("href", "/admin/partners/p9");
    expect(within(partners).getByText("Affiliate")).toBeInTheDocument();

    const provisioning = screen.getByRole("heading", { name: "Recent customer provisioning" }).closest("section")!;
    expect(within(provisioning).getByRole("link", { name: "Customer One" })).toHaveAttribute("href", "/admin/partners/p1?tab=customers");

    const sales = screen.getByRole("heading", { name: "Recent partner sales" }).closest("section")!;
    expect(within(sales).getByRole("link", { name: "Acme Partners" })).toHaveAttribute("href", "/admin/partners/p1?tab=commissions");
    expect(sales.textContent).toMatch(/Sale to Customer One/);
    expect(sales.textContent).toMatch(/5\.00/);

    const domains = screen.getByRole("heading", { name: "Domain verification required" }).closest("section")!;
    expect(within(domains).getByRole("link", { name: "app.brand-example.com" })).toHaveAttribute("href", "/admin/partners/p2?tab=domains");

    // Empty sections say so in words.
    const payouts = screen.getByRole("heading", { name: "Pending payouts" }).closest("section")!;
    expect(payouts.textContent).toMatch(/No payout is waiting/);
    await expectNoViolations(container);
  });

  it("does not show money sections the server withheld, rather than showing them empty", async () => {
    mockApi(dashboardRoutes({ ...RECENT, sales: null, commission: null, payouts: null }));
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("heading", { name: "Recent partners" });
    expect(screen.queryByRole("heading", { name: "Recent partner sales" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Pending payouts" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Recent commission activity" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Domain verification required" })).toBeInTheDocument();
  });
});

// --- the lists ----------------------------------------------------------------------------------

describe("the reseller list", () => {
  it("shows its columns and renders the labels the server derived", async () => {
    mockApi(
      listRoutes([
        row(),
        row({
          id: "p2",
          code: "both",
          display_name: "Both Co",
          experience: [
            { key: "reseller", label: "Reseller" },
            { key: "white_label", label: "White-label" },
          ],
        }),
      ]),
    );
    const { container } = render(
      <AdminContextForTest permissions={[VIEW]}>
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
      "White-label",
      "Last activity",
      "Actions",
    ]);
    // One account, two labels - and the row is the same account.
    const both = within(table).getByRole("link", { name: "Both Co" }).closest("tr")!;
    expect(within(both).getByText("Reseller")).toBeInTheDocument();
    expect(within(both).getAllByText("White-label").length).toBeGreaterThan(0);
    expect(within(both).getByText("Enabled")).toBeInTheDocument();
    const acme = within(table).getByRole("link", { name: "Acme Partners" }).closest("tr")!;
    expect(within(acme).getByText("Not enabled")).toBeInTheDocument();
    expect(within(acme).getByRole("link", { name: "Manage Acme Partners" })).toHaveAttribute("href", "/admin/partners/p1");
    // Commission is per currency, from the server's figures.
    expect(table.textContent).toMatch(/50\.00.*earned/);
    expect(table.textContent).toMatch(/20\.00.*unpaid/);
    await expectNoViolations(container);
  });

  it("sends the chosen filters to the server and never filters locally", async () => {
    const calls = mockApi(listRoutes([row()]));
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    await screen.findByRole("table", { name: "Resellers" });
    await screen.findByRole("option", { name: "CRM" });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "active" } });
    fireEvent.change(screen.getByLabelText("Product"), { target: { value: "crm" } });
    await waitFor(() => {
      const last = calls.filter((c) => c.key === "GET /api/v1/admin/partners/resellers").pop()!;
      expect(last.url).toContain("status=active");
      expect(last.url).toContain("product=crm");
      expect(last.url).toContain("scope=resellers");
    });
    // All partners is its own section now, not a filter of this one.
    expect(screen.queryByLabelText("Show")).not.toBeInTheDocument();
  });

  it("explains an empty list and how to widen it", async () => {
    mockApi(listRoutes([]));
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    expect(await screen.findByText(/No resellers match/)).toBeInTheDocument();
  });

  it("does not depend on the filter menus: a failing product list leaves the list working", async () => {
    mockApi(listRoutes([row()], { "GET /api/v1/admin/partners/products": [{ status: 403, body: { code: "permission_denied" } }] }));
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    expect(await screen.findByRole("table", { name: "Resellers" })).toBeInTheDocument();
  });

  it("never renders a partner type", async () => {
    mockApi(listRoutes([row()]));
    const { container } = render(
      <AdminContextForTest permissions={[VIEW]}>
        <ResellerList />
      </AdminContextForTest>,
    );
    await screen.findByRole("table", { name: "Resellers" });
    expect(container.textContent).not.toMatch(/partner type|type:/i);
  });
});

describe("all partners", () => {
  it("lists every account with its labels in their own column, from the address's filters", async () => {
    query = "status=active";
    const calls = mockApi(
      listRoutes([
        row({ id: "d1", code: "draft-co", display_name: "Draft Co", experience: [], capability_count: 0, status: "draft" }),
        row({
          id: "all3",
          code: "all-three",
          display_name: "All Three",
          experience: [
            { key: "reseller", label: "Reseller" },
            { key: "white_label", label: "White-label" },
            { key: "affiliate", label: "Affiliate" },
          ],
        }),
      ]),
    );
    const { container } = render(
      <AdminContextForTest permissions={[VIEW, MANAGE]}>
        <AllPartnersList />
      </AdminContextForTest>,
    );
    const table = await screen.findByRole("table", { name: "Partner accounts" });
    const first = calls.find((c) => c.key === "GET /api/v1/admin/partners/resellers")!;
    expect(first.url).toContain("scope=all");
    expect(first.url).toContain("status=active");
    expect((screen.getByLabelText("Status") as HTMLSelectElement).value).toBe("active");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Partner",
      "Labels",
      "Status",
      "Customers",
      "Products",
      "Subscriptions",
      "Commission",
      "White-label",
      "Created",
      "Last activity",
    ]);
    const draft = within(table).getByRole("link", { name: "Draft Co" }).closest("tr")!;
    expect(draft.textContent).toMatch(/No capabilities yet/);
    const allThree = within(table).getByRole("link", { name: "All Three" }).closest("tr")!;
    expect(within(allThree).getByRole("list", { name: "Derived from capabilities" }).textContent).toBe(
      "ResellerWhite-labelAffiliate",
    );
    // One row for the account that wears three labels.
    expect(within(table).getAllByRole("link", { name: "All Three" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Add partner" })).toHaveAttribute("href", "/admin/partners/new");
    await expectNoViolations(container);
  });
});

describe("affiliates", () => {
  it("shows referral codes, conversions and money from the server, never a prospect", async () => {
    const calls = mockApi(
      listRoutes([
        row({
          id: "aff",
          code: "aff-co",
          display_name: "Aff Co",
          experience: [{ key: "affiliate", label: "Affiliate" }],
          commission: [{ currency: "GBP", balance_minor: 1500, unpaid_minor: 700, entries: 3 }],
          payouts: { open: 1, latest: null, paid: [{ currency: "GBP", amount_minor: 800, payouts: 1 }] },
          referrals: { codes: ["K7Q2M9", "Z3X8P4", "A1B2C3"], active_codes: 5, captured: 12, converted: 3 },
        }),
      ]),
    );
    const { container } = render(
      <AdminContextForTest permissions={[VIEW, MANAGE]}>
        <AffiliateList />
      </AdminContextForTest>,
    );
    const table = await screen.findByRole("table", { name: "Affiliates" });
    expect(calls.find((c) => c.key === "GET /api/v1/admin/partners/resellers")!.url).toContain("scope=affiliates");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Affiliate",
      "Status",
      "Referral codes",
      "Conversions",
      "Commission",
      "Pending payout",
      "Paid",
      "Last activity",
    ]);
    const cells = within(table).getAllByRole("cell").map((c) => c.textContent);
    expect(cells[1]).toBe("K7Q2M9Z3X8P4A1B2C3and 2 more");
    expect(cells[2]).toBe("3 of 12 referrals");
    expect(cells[3]).toMatch(/15\.00/);
    expect(cells[4]).toMatch(/7\.00 unpaid/);
    expect(cells[4]).toMatch(/1 open payout/);
    expect(cells[5]).toMatch(/8\.00/);
    expect(screen.getByRole("link", { name: "Add affiliate" })).toHaveAttribute("href", "/admin/partners/new?purpose=affiliate");
    await expectNoViolations(container);
  });

  it("explains an empty list", async () => {
    mockApi(listRoutes([]));
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <AffiliateList />
      </AdminContextForTest>,
    );
    expect(await screen.findByText(/No affiliates match/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Add affiliate" })).not.toBeInTheDocument();
  });
});
