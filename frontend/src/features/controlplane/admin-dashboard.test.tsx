import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminShell } from "./ControlShell";
import { Dashboard, rangeLabel } from "./AdminDashboard";
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
    expect(await screen.findByText("Total users")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByText(/Welcome back, Aman\./)).toBeInTheDocument();
    // Growth of the total: 134 added to the 1,114 there before is 12%.
    expect(screen.getByText("1,248")).toBeInTheDocument();
    expect(screen.getByText("+134 this month")).toBeInTheDocument();
    expect(screen.getByText("Total users").parentElement).toHaveTextContent(/Up\s*12%/);
    expect(screen.getByText("+6 this month")).toBeInTheDocument();
    expect(screen.getByText("Active subscriptions").parentElement).toHaveTextContent("7");
    expect(screen.getByText("9 in all states")).toBeInTheDocument();
    // Six cards (3 + 3 or 6 across): no card is left alone on a row.
    expect(screen.getByRole("group", { name: "Figures" })).toHaveAttribute("data-count", "6");
    expect(screen.getByRole("group", { name: "Charts" })).toHaveAttribute("data-count", "3");
    // Revenue per currency, compared with the period before (+18%).
    const revenue = screen.getByRole("heading", { name: "Revenue overview" }).closest("section")!;
    expect(revenue).toHaveTextContent(/Up\s*18%/);
    expect(within(revenue).getByText(/12 payments/)).toBeInTheDocument();
    // The donut's legend names every product with its share.
    const sales = screen.getByRole("heading", { name: "Product-wise sales" }).closest("section")!;
    expect(within(sales).getByText("Easy CRM")).toBeInTheDocument();
    expect(within(sales).getByText("75%")).toBeInTheDocument();
    // The latest rows, five at most.
    expect(await screen.findByRole("link", { name: "Rahul Sharma" })).toHaveAttribute("href", "/admin/users/u9");
    // Avatar, name and the email under it, cut to fit (shown whole on hover).
    expect(screen.getByTitle("rahul@example.com")).toBeInTheDocument();
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
    expect(await screen.findByText("Active subscriptions")).toBeInTheDocument();
    // Two cards: two columns, no orphan.
    expect(screen.getByRole("group", { name: "Figures" })).toHaveAttribute("data-count", "2");
    for (const hidden of ["Total users", "Total partners", "Revenue overview", "User registrations", "Recent users", "Recent orders"]) {
      expect(screen.queryByText(hidden)).not.toBeInTheDocument();
    }
    expect(calls.map((c) => c.key).filter((k) => k !== "GET /api/v1/auth/session")).toEqual(["GET /api/v1/admin/dashboard"]);
    await expectNoViolations(container);
  });

  it("labels this month up to today, never the whole month", () => {
    expect(rangeLabel("2026-11", new Date(Date.UTC(2026, 10, 7)))).toBe("1 Nov – 7 Nov 2026");
    expect(rangeLabel("2026-10", new Date(Date.UTC(2026, 10, 7)))).toBe("1 Oct – 31 Oct 2026");
  });

  it("asks for the period chosen in the header's one period control", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/dashboard": [ok({ ...BASE, overview: overview() })],
    });
    render(
      <AdminContextForTest permissions={["platform.staff_customer.view"]}>
        <Dashboard />
      </AdminContextForTest>,
    );
    const picker = await screen.findByRole("combobox", { name: "Period" });
    expect(picker).toHaveTextContent(/^This month · 1 \w{3} – \d{1,2} \w{3} \d{4}/);
    fireEvent.click(picker);
    const options = within(screen.getByRole("listbox", { name: "Period" })).getAllByRole("option");
    expect(options).toHaveLength(12);
    expect(options[1]).toHaveTextContent(/^Last month · /);
    fireEvent.click(options[1]);
    const now = new Date();
    const previous = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1)).toISOString().slice(0, 7);
    await waitFor(() => expect(calls.some((c) => c.url.includes(`month=${previous}`))).toBe(true));
    // One period control: no card carries its own month chip or picker.
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
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
    // An empty state, not bare axes.
    expect(await screen.findByText("No payments yet")).toBeInTheDocument();
    expect(screen.getByText(/No payments were collected in/)).toBeInTheDocument();
    expect(screen.getByText("No sales yet")).toBeInTheDocument();
    expect(screen.getByText("No orders were paid this month.")).toBeInTheDocument();
    expect(document.querySelector("svg[viewBox='0 0 400 210']")).toBeNull();
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
  it("finds pages by name and live users, grouped, and opens the chosen one", async () => {
    mockApi({
      "GET /api/v1/admin/users": [ok({ results: [{ user_id: "u9", email: "planner@example.com", name: "Pla Nner" }], next_cursor: null, has_more: false })],
      "GET /api/v1/admin/catalog/products": [ok({ results: [{ id: "p1", code: "easy_plan", name: "Easy Plan (easy_plan)", status: "active", licensing_mode: "seat" }] })],
    });
    render(
      <AdminSearch permissions={["platform.staff_customer.view", "platform.staff_user.view", "platform.staff_catalog.view"]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Search customers, partners/ }));
    const box = screen.getByRole("combobox", { name: "Search anything" });
    fireEvent.change(box, { target: { value: "plan" } });
    expect(screen.getAllByRole("option")[0]).toHaveTextContent("Plans & add-ons");
    await waitFor(() => expect(screen.getByRole("option", { name: /Pla Nner/ })).toBeInTheDocument());
    expect(screen.getByRole("option", { name: /^Easy Plan/ })).toBeInTheDocument();
    // No partner grant: no partner results were asked for.
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(box.getAttribute("aria-activedescendant")).toBe(screen.getAllByRole("option")[1].id);
    const target = screen.getByRole("option", { name: /Pla Nner/ });
    fireEvent.mouseDown(target);
    expect(nav.router.push).toHaveBeenCalledWith("/admin/users/u9");
  });

  it("opens on Ctrl+K with the search box focused", () => {
    render(<AdminSearch permissions={["platform.staff_customer.view"]} />);
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Search anything" })).toHaveFocus();
  });
});

