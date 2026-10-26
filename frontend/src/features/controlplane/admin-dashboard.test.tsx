import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminShell } from "./ControlShell";
import { Dashboard } from "./AdminDashboard";
import { AdminSearch } from "./AdminTopbar";
import { foldSlices } from "./dashboardCharts";
import { AdminContextForTest, SIGNED_IN, expectNoViolations, mockApi, ok } from "./testSupport";

/**
 * The Super Admin console redesign (2026-10-26): the dashboard's month
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

const thisMonth = () => {
  const now = new Date();
  return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
};

function overview(extra: Record<string, unknown> = {}) {
  const month = thisMonth();
  const days = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5)), 0)).getUTCDate();
  const daily = Array.from({ length: days }, (_, i) => (i < 3 ? i + 1 : null));
  return {
    month,
    first_day: `${month}-01`,
    last_day: `${month}-${days}`,
    compared_with: { first_day: "2026-09-01", last_day: "2026-09-03" },
    ...extra,
    ...(extra.users === undefined ? {} : { users: { ...(extra.users as object), daily } }),
  };
}

const BASE = {
  organizations: { active: 3 },
  subscriptions: { active: 7, cancelled: 2 },
  mrr: { available: false, reason: "No safe global figure exists." },
  outbox: { pending: 0 },
};

const EMPTY_PAGE = { results: [], next_cursor: null, has_more: false };

describe("the dashboard", () => {
  it("shows the month's figures, charts and latest rows when the grants cover them", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: true, user_id: "u1", email: "s@example.com", name: "Aman Gupta" })],
      "GET /api/v1/admin/dashboard": [
        ok({
          ...BASE,
          overview: overview({
            users: { total: 1248, new: 134, previous: 110 },
            partners: { total: 86, new: 6 },
            products: { total: 24, available: 20, new: 4 },
            orders: {
              paid: 12,
              previous: 10,
              by_product: [
                { code: "easy_crm", name: "Easy CRM", orders: 9 },
                { code: "easybook", name: "EasyBook", orders: 3 },
              ],
            },
            revenue: [
              { currency: "INR", amount_minor: 42895000, payments: 12, previous_minor: 36351700, daily: [100000, null] },
            ],
          }),
        }),
      ],
      "GET /api/v1/admin/users": [
        ok({
          ...EMPTY_PAGE,
          results: [
            {
              user_id: "u9",
              email: "rahul@example.com",
              name: "Rahul Sharma",
              status: "active",
              is_staff: false,
              created_at: "2026-10-25T10:00:00Z",
            },
          ],
        }),
      ],
      "GET /api/v1/admin/orders": [
        ok({
          ...EMPTY_PAGE,
          results: [
            {
              order_id: "o1",
              organization_id: "org",
              organization_name: "Rahul Co",
              number: "ORD-1025",
              kind: "plan",
              status: "paid",
              description: "Easy CRM Pro",
              product_codes: ["easy_crm"],
              currency: "INR",
              amount_due_minor: 500000,
              created_at: "2026-10-25T10:00:00Z",
              paid_at: "2026-10-25T10:01:00Z",
            },
          ],
        }),
      ],
      "GET /api/v1/admin/partners": [ok(EMPTY_PAGE)],
    });
    const { container } = render(
      <AdminContextForTest
        permissions={[
          "platform.staff_customer.view",
          "platform.staff_user.view",
          "platform.staff_payment.view",
          "platform.staff_partner.view",
        ]}
      >
        <main>
          <Dashboard />
        </main>
      </AdminContextForTest>,
    );
    expect(await screen.findByText("Total Users")).toBeInTheDocument();
    expect(screen.getByText(/Welcome back, Aman!/)).toBeInTheDocument();
    // Growth of the total: 134 added to the 1,114 there before is 12%.
    expect(screen.getByText("1,248")).toBeInTheDocument();
    expect(screen.getByText("+134 this month")).toBeInTheDocument();
    expect(screen.getByText("Total Users").parentElement).toHaveTextContent("up 12%");
    expect(screen.getByText("+6 this month")).toBeInTheDocument();
    expect(screen.getByText("Active Subscriptions").parentElement).toHaveTextContent("7");
    expect(screen.getByText("9 in all states")).toBeInTheDocument();
    // Revenue per currency, compared with the period before (+18%).
    const revenue = screen.getByRole("heading", { name: "Revenue Overview" }).closest("section")!;
    expect(revenue).toHaveTextContent("up 18%");
    expect(within(revenue).getByText(/12 payments/)).toBeInTheDocument();
    // The donut's legend names every product with its share.
    const sales = screen.getByRole("heading", { name: "Product-wise Sales" }).closest("section")!;
    expect(within(sales).getByText("Easy CRM")).toBeInTheDocument();
    expect(within(sales).getByText("75%")).toBeInTheDocument();
    // The latest rows, five at most.
    expect(await screen.findByRole("link", { name: "Rahul Sharma" })).toHaveAttribute("href", "/admin/users/u9");
    expect(await screen.findByText("ORD-1025")).toBeInTheDocument();
    expect(await screen.findByText("No partners yet.")).toBeInTheDocument();
    for (const key of ["GET /api/v1/admin/users", "GET /api/v1/admin/orders", "GET /api/v1/admin/partners"]) {
      expect(calls.find((c) => c.key === key)?.url).toContain("limit=5");
    }
    await expectNoViolations(container);
  });

  it("leaves out every section the staff member isn't granted, and asks for nothing more", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/dashboard": [ok({ ...BASE, overview: overview() })],
    });
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_customer.view"]}>
        <main>
          <Dashboard />
        </main>
      </AdminContextForTest>,
    );
    expect(await screen.findByText("Active Subscriptions")).toBeInTheDocument();
    for (const hidden of ["Total Users", "Total Partners", "Revenue Overview", "User Registration", "Recent Users", "Recent Orders"]) {
      expect(screen.queryByText(hidden)).not.toBeInTheDocument();
    }
    expect(calls.map((c) => c.key).filter((k) => k !== "GET /api/v1/auth/session")).toEqual(["GET /api/v1/admin/dashboard"]);
    await expectNoViolations(container);
  });

  it("asks for the month chosen", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/dashboard": [ok({ ...BASE, overview: overview() })],
    });
    render(
      <AdminContextForTest permissions={["platform.staff_customer.view"]}>
        <Dashboard />
      </AdminContextForTest>,
    );
    const picker = await screen.findByLabelText("Month shown");
    const options = within(picker).getAllByRole("option");
    expect(options).toHaveLength(12);
    const previous = (options[1] as HTMLOptionElement).value;
    fireEvent.change(picker, { target: { value: previous } });
    await waitFor(() => expect(calls.some((c) => c.url.includes(`month=${previous}`))).toBe(true));
  });

  it("says plainly when nothing was collected or sold", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/dashboard": [
        ok({ ...BASE, overview: overview({ orders: { paid: 0, previous: 0, by_product: [] }, revenue: [] }) }),
      ],
    });
    render(
      <AdminContextForTest permissions={["platform.staff_customer.view"]}>
        <Dashboard />
      </AdminContextForTest>,
    );
    expect(await screen.findByText(/No payments were collected in/)).toBeInTheDocument();
    expect(screen.getByText("No orders were paid this month.")).toBeInTheDocument();
  });

  it("folds a sixth and later product into Others, so no colour is generated", () => {
    const slices = Array.from({ length: 8 }, (_, i) => ({ key: `p${i}`, label: `P${i}`, value: 8 - i }));
    const folded = foldSlices(slices);
    expect(folded.map((s) => s.label)).toEqual(["P0", "P1", "P2", "P3", "P4", "Others"]);
    expect(folded[5].value).toBe(3 + 2 + 1);
  });
});

// --- the top bar -------------------------------------------------------------------------

function shellApi(permissions: string[], attention: unknown = { items: [] }) {
  return mockApi({
    "GET /api/v1/auth/session": [ok({ authenticated: true, user_id: "u1", email: "admin@example.com", name: "Aman Gupta" })],
    "GET /api/v1/admin/session": [ok({ is_staff: true, permissions })],
    "GET /api/v1/admin/attention": [ok(attention)],
  });
}

describe("the Super Admin top bar", () => {
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
    expect(await screen.findByRole("link", { name: "2 upgrade requests wait for a decision" })).toHaveAttribute("href", "/admin/partners/all");
    expect(screen.getByRole("link", { name: "1 partner is still a draft" })).toHaveAttribute("href", "/admin/partners/all?status=draft");
    // The name beside the avatar; the menu still opens from it.
    expect(screen.getByRole("button", { name: "Account menu for Aman Gupta" })).toHaveTextContent("Aman");
    await expectNoViolations(container);
  });

  it("offers in New only what the staff member may create, and nothing without a grant", async () => {
    shellApi(["platform.staff_customer.view", "platform.staff_partner.manage", "platform.staff_partner.view"]);
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "New" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Add partner"]);
  });

  it("has no New button and no settings shortcut for a read-only staff member", async () => {
    shellApi(["platform.staff_customer.view"]);
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    expect(screen.queryByRole("button", { name: "New" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Platform settings" })).not.toBeInTheDocument();
  });

  it("opens the section of the current page and hides a section with nothing in it", async () => {
    pathname = "/admin/orders";
    shellApi(["platform.staff_customer.view", "platform.staff_payment.view"]);
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    const nav = await screen.findByRole("navigation", { name: "Super Admin" });
    expect(within(nav).getByRole("button", { name: /Product Store/ })).toHaveAttribute("aria-expanded", "true");
    expect(within(nav).getByRole("link", { name: "Orders" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("button", { name: /User/ })).toHaveAttribute("aria-expanded", "false");
    expect(within(nav).queryByRole("button", { name: /Staff/ })).not.toBeInTheDocument();
    expect(within(nav).getByText("Admin panel")).toBeInTheDocument();
  });
});

describe("search anything", () => {
  it("finds pages by name and searches users or partners for the words typed", async () => {
    render(
      <AdminSearch permissions={["platform.staff_customer.view", "platform.staff_user.view", "platform.staff_catalog.view"]} />,
    );
    const box = screen.getByRole("combobox", { name: "Search anything" });
    fireEvent.change(box, { target: { value: "plan" } });
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options[0]).toContain("Plans & add-ons");
    expect(options).toContain("Search users for “plan”Users");
    // No partner grant, no partner search.
    expect(options.some((o) => o?.includes("Search partners"))).toBe(false);
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(box.getAttribute("aria-activedescendant")).toBe(screen.getAllByRole("option")[1].id);
    fireEvent.submit(box.closest("form")!);
    expect(nav.router.push).toHaveBeenCalledWith("/admin/users?q=plan");
  });

  it("jumps to the box on Ctrl+K", () => {
    render(<AdminSearch permissions={["platform.staff_customer.view"]} />);
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(screen.getByRole("combobox", { name: "Search anything" })).toHaveFocus();
  });
});
