import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerContext } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import { money } from "@/features/controlplane/ui";
import type { PortalPartner } from "@/lib/api/controlPlane";
import { MyTickets } from "./Helpdesk";
import { UnifiedDashboard } from "./UnifiedDashboard";
import { Wallet } from "./Wallet";

/**
 * The unified partner dashboard's own pages (2026-11-03): the dashboard's
 * figures and their change, the product split and the earnings card; the
 * Wallet & Payouts page; the helpdesk. The API is mocked - every figure here is
 * the server's, and these tests check what is drawn and what is sent.
 */

let search = "";
const nav = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav,
  useSearchParams: () => new URLSearchParams(search),
  usePathname: () => "/partner/reseller/support-manager",
}));

const ID = "55555555-5555-5555-5555-555555555555";
const API = `/api/v1/partner/${ID}`;
const BASE = "/partner/reseller";
const perm = (...keys: string[]) => keys.map((k) => `platform.partner_${k}`);

function partner(): PortalPartner {
  return {
    partner_id: ID,
    code: "rs",
    display_name: "Resell Co",
    status: "active",
    role: "owner",
    is_admin: true,
    capabilities: [],
    level: "reseller",
    permissions: perm(
      "dashboard.view",
      "profile.view",
      "product.view",
      "customer.view",
      "customer.provision",
      "subscription.sell",
      "commission.view",
      "payout.view",
      "payout.request",
    ),
  };
}

function mount(ui: React.ReactNode, routes: Record<string, Reply[]>) {
  const calls = mockApi(routes);
  const view = render(<PartnerContext.Provider value={{ partner: partner(), base: BASE }}>{ui}</PartnerContext.Provider>);
  return { calls, ...view };
}

const DASHBOARD = {
  partner: { id: ID, name: "Resell Co", currency: "INR", status: "active" },
  range: { key: "this_month", from: "2026-10-01T00:00:00Z", to: "2026-10-04T00:00:00Z" },
  previous: {
    from: "2026-09-28T00:00:00Z",
    to: "2026-10-01T00:00:00Z",
    new_customers: 2,
    sales: {
      transactions: 2,
      gross: [{ currency: "INR", amount_minor: 50000 }],
      commission_earned: [{ currency: "INR", amount_minor: 20000 }],
    },
  },
  products: {
    active: 3,
    sales: [
      { product_code: "crm", product_name: "CRM", currency: "INR", sales: 3, gross_minor: 75000, commission_minor: 7500 },
      { product_code: "desk", product_name: "Help Desk", currency: "INR", sales: 2, gross_minor: 25000, commission_minor: 2500 },
    ],
    // The server's split (partner_portal.product_split): shares in basis points.
    split: {
      INR: {
        total_minor: 100000,
        products: 2,
        parts: [
          { product_code: "crm", product_name: "CRM", currency: "INR", sales: 3, gross_minor: 75000, commission_minor: 7500, share_bp: 7500 },
          { product_code: "desk", product_name: "Help Desk", currency: "INR", sales: 2, gross_minor: 25000, commission_minor: 2500, share_bp: 2500 },
        ],
      },
    },
  },
  tickets: { open: 2, breached: 1 },
  customers: { total: 10, active: 8, suspended: 1, ended: 1, active_subscriptions: 7, new: 4 },
  sales: {
    transactions: 5,
    gross: [{ currency: "INR", amount_minor: 100000 }],
    commission_earned: [{ currency: "INR", amount_minor: 10000 }],
    source: "commission_ledger",
  },
  money: {
    balances: [],
    pending_commission: [],
    pending_payout: [],
    commission: [{ currency: "INR", total_minor: 30000, approved_minor: 20000, pending_minor: 10000, available_minor: 15000 }],
  },
  charts: {
    gross: { INR: [{ date: "2026-10-02", amount_minor: 100000 }] },
    commission: { INR: [{ date: "2026-10-02", amount_minor: 10000 }] },
    transactions: [{ date: "2026-10-02", count: 5 }],
  },
  sub_partners: null,
  earning: { model: "commission", discount_bp: null },
  recent: {
    customers: [{ organization_id: "o1", name: "Beta Ltd", link_status: "active", started_at: "2026-10-02T10:00:00Z", products: ["crm"] }],
    commissions: null,
    payouts: null,
  },
  not_implemented: {},
};

const UPGRADE = {
  current: "reseller",
  eligible: ["white_label"],
  can_request: true,
  open: null,
  recent: [],
  steps: { reseller: [], white_label: [] },
  products: [],
  checklist: [],
  fees: { reseller: { amount_minor: 2000000, currency: "INR" }, white_label: { amount_minor: 10000000, currency: "INR" } },
};

const NOTIFICATIONS = { view: "unread", items: [], counts: { all: 0, unread: 0, important: 0, important_unread: 0 }, window: 100, truncated: false, not_implemented: {} };

const dashboardRoutes = (): Record<string, Reply[]> => ({
  [`GET ${API}/dashboard`]: [ok(DASHBOARD)],
  [`GET ${API}/upgrade`]: [ok(UPGRADE)],
  [`GET ${API}/notifications`]: [ok(NOTIFICATIONS)],
  [`GET ${API}/products`]: [ok({ products: [], not_implemented: {} })],
});

const WALLET = {
  currency: "INR",
  balances: [{ currency: "INR", balance_minor: 30000, pending_minor: 10000, available_minor: 15000, in_payouts_minor: 5000, paid_minor: 0, entries: 4 }],
  method: null,
  rules: { minimum_minor: 100000, tds_rate_bp: 200, tds_section: "194H", pan_required: true, verification: "manual" },
  preview: { amount_minor: 15000, tds_minor: 300, net_minor: 14700 },
  can_manage: true,
  can_request: false,
  blocker: "Add your payout details first.",
  withdrawals: [],
};

beforeEach(() => {
  search = "";
  nav.push.mockReset();
});

describe("the unified dashboard", () => {
  it("draws the ribbon with each figure's change against the previous period", async () => {
    mount(<UnifiedDashboard level="reseller" />, dashboardRoutes());
    const ribbon = await screen.findByRole("list", { name: "Key figures" });
    const metric = (label: string) => within(ribbon).getByText(label).closest("li")!;
    expect(metric("Total customers")).toHaveTextContent("10");
    expect(metric("Total customers")).toHaveTextContent("↑ 100%");
    expect(metric("Active products")).toHaveTextContent("3");
    expect(metric("Total sales")).toHaveTextContent(money(100000, "INR"));
    expect(metric("Total sales")).toHaveTextContent("↑ 100%");
    expect(metric("My commission")).toHaveTextContent(money(10000, "INR"));
    expect(metric("My commission")).toHaveTextContent("↓ 50%");
    expect(metric("Open tickets")).toHaveTextContent("2");
    expect(metric("Open tickets")).toHaveTextContent("1 past their deadline");
  });

  it("splits the sales by product with each one's share", async () => {
    mount(<UnifiedDashboard level="reseller" />, dashboardRoutes());
    const section = (await screen.findByRole("heading", { name: "Product-wise sales" })).closest("section")!;
    const items = within(section).getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("CRM");
    expect(items[0]).toContain("75%");
    expect(items[1]).toContain("Help Desk");
    expect(items[1]).toContain("25%");
    expect(section).toHaveTextContent(money(100000, "INR"));
  });

  it("shows the earnings split and the way to withdraw", async () => {
    mount(<UnifiedDashboard level="reseller" />, dashboardRoutes());
    const section = (await screen.findByRole("heading", { name: "My earnings" })).closest("section")!;
    for (const [label, minor] of [
      ["Total commission", 30000],
      ["Approved commission", 20000],
      ["Pending commission", 10000],
      ["Available balance", 15000],
    ] as const) {
      expect(within(section).getByText(label).closest("div")).toHaveTextContent(money(minor, "INR"));
    }
    expect(within(section).getByRole("link", { name: "Withdraw earnings" })).toHaveAttribute("href", `${BASE}/wallet`);
  });

  it("offers the next level with its fee, and the quick actions the role allows", async () => {
    mount(<UnifiedDashboard level="reseller" />, dashboardRoutes());
    const levels = await screen.findByRole("list", { name: "Partner levels" });
    await waitFor(() => expect(levels).toHaveTextContent(money(10000000, "INR")));
    expect(levels).toHaveTextContent("Free");
    const actions = screen.getByRole("navigation", { name: "Quick actions" });
    const labels = within(actions).getAllByRole("link").map((a) => a.textContent);
    expect(labels).toEqual(["Create Customer", "Add Order", "My Commission", "Raise Support Ticket"]);
  });

  it("has no detectable violations", async () => {
    const { container } = mount(<UnifiedDashboard level="reseller" />, dashboardRoutes());
    await screen.findByRole("heading", { name: "My earnings" });
    await screen.findByRole("list", { name: "Partner levels" });
    await expectNoViolations(container);
  });
});

describe("wallet & payouts", () => {
  it("says why a withdrawal cannot be requested, and does not offer it", async () => {
    mount(<Wallet />, { [`GET ${API}/withdrawals`]: [ok(WALLET)] });
    expect(await screen.findByText("Add your payout details first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request withdrawal" })).toBeDisabled();
    expect(screen.getByText(/TDS 2.00% \(Section 194H\)/)).toBeInTheDocument();
  });

  it("saves payout details with a PUT, the server's answer redrawn", async () => {
    const { calls } = mount(<Wallet />, {
      [`GET ${API}/withdrawals`]: [ok(WALLET)],
      [`PUT ${API}/withdrawals/method`]: [ok(WALLET)],
    });
    fireEvent.click(await screen.findByRole("button", { name: "Add payout details" }));
    fireEvent.change(screen.getByLabelText("Account holder name"), { target: { value: "Resell Co" } });
    fireEvent.change(screen.getByLabelText("IFSC"), { target: { value: "HDFC0001234" } });
    fireEvent.change(screen.getByLabelText("Account number"), { target: { value: "50100123456789" } });
    fireEvent.change(screen.getByLabelText("Confirm account number"), { target: { value: "50100123456789" } });
    fireEvent.change(screen.getByLabelText("PAN"), { target: { value: "ABCDE1234F" } });
    fireEvent.click(screen.getByRole("button", { name: "Save payout details" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${API}/withdrawals/method`)).toBe(true));
    const put = calls.find((c) => c.key === `PUT ${API}/withdrawals/method`)!;
    expect(put.body).toMatchObject({
      method: "bank",
      account_holder: "Resell Co",
      ifsc: "HDFC0001234",
      account_number: "50100123456789",
      account_number_confirm: "50100123456789",
      pan: "ABCDE1234F",
    });
    // No amount is ever sent.
    expect(JSON.stringify(put.body)).not.toContain("amount");
  });

  it("requests a withdrawal with an empty body when it may", async () => {
    const ready = { ...WALLET, can_request: true, blocker: "", method: { method: "upi", account_holder: "R", bank_name: "", ifsc: "", account_hint: "", upi_vpa: "r@ok", pan_on_file: true, pan_hint: "234F", verification: "verified", verification_note: "", verified_at: null, updated_at: "2026-10-01T00:00:00Z", destination: "UPI r@ok" } };
    const { calls } = mount(<Wallet />, {
      [`GET ${API}/withdrawals`]: [ok(ready)],
      [`POST ${API}/withdrawals/request`]: [ok(ready)],
    });
    fireEvent.click(await screen.findByRole("button", { name: "Request withdrawal" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/withdrawals/request`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${API}/withdrawals/request`)!.body).toEqual({});
  });

  it("has no detectable violations", async () => {
    const { container } = mount(<Wallet />, { [`GET ${API}/withdrawals`]: [ok(WALLET)] });
    await screen.findByRole("heading", { name: "Withdrawal requests" });
    await expectNoViolations(container);
  });
});

const TICKET = {
  id: "t1",
  number: 1,
  reference: "#1",
  direction: "platform",
  subject: "Commission missing",
  category: "commission",
  priority: "high",
  status: "waiting",
  related_ref: "",
  opened_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T12:00:00Z",
  first_response_at: "2026-10-01T11:00:00Z",
  resolved_at: null,
  closed_at: null,
  sla: { first_response_due_at: "2026-10-01T14:00:00Z", resolve_due_at: "2026-10-02T10:00:00Z", first_response: "met", resolution: "on_track", state: "on_track" },
};

const SUPPORT = { contact: { email: "partners@example.com", available: true }, help_desk: { name: "Easy Help Desk", available: false, url: "", suspended: false }, partner: { name: "Resell Co" }, priorities: [], can_send: true, requests: null, not_implemented: {} };

describe("the helpdesk", () => {
  it("lists the partner's tickets with their SLA and raises a new one", async () => {
    const { calls } = mount(<MyTickets />, {
      [`GET ${API}/tickets`]: [ok({ items: [TICKET], counts: { waiting: 1 }, total: 1, priorities: ["low", "normal", "high", "urgent"], categories: ["general", "commission"] })],
      [`GET ${API}/support`]: [ok(SUPPORT)],
      [`POST ${API}/tickets`]: [ok({ ...TICKET, id: "t2", number: 2, reference: "#2", messages: [] })],
    });
    const table = await screen.findByRole("table", { name: "Your tickets to Workeasy360" });
    expect(table).toHaveTextContent("#1");
    expect(table).toHaveTextContent("Commission missing");
    expect(table).toHaveTextContent("On track");
    expect(screen.getByRole("button", { name: "Open (1)" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Raise a ticket" }));
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Payout late" } });
    fireEvent.change(screen.getByLabelText("What do you need?"), { target: { value: "Where is October's payout?" } });
    fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "urgent" } });
    fireEvent.click(screen.getByRole("button", { name: "Send ticket" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/tickets`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${API}/tickets`)!.body).toMatchObject({
      subject: "Payout late",
      message: "Where is October's payout?",
      priority: "urgent",
      category: "general",
    });
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/partner/reseller/support-manager?ticket=t2"));
  });

  it("opens a ticket named in the address and shows its conversation, never a staff note", async () => {
    search = "ticket=t1";
    mount(<MyTickets />, {
      [`GET ${API}/tickets`]: [ok({ items: [TICKET], counts: {}, total: 1, priorities: [], categories: [] })],
      [`GET ${API}/support`]: [ok(SUPPORT)],
      [`GET ${API}/tickets/t1`]: [
        ok({
          ...TICKET,
          messages: [
            { id: "m1", side: "partner", author: "Resell Co", mine: true, body: "Order 42 earned nothing.", internal: false, created_at: "2026-10-01T10:00:00Z" },
            { id: "m2", side: "platform", author: "Workeasy360 Support", mine: false, body: "Fixed - it shows now.", internal: false, created_at: "2026-10-01T11:00:00Z" },
          ],
        }),
      ],
    });
    const thread = await screen.findByRole("list", { name: "Conversation" });
    expect(within(thread).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      expect.stringContaining("Order 42 earned nothing."),
      expect.stringContaining("Fixed - it shows now."),
    ]);
    expect(thread).toHaveTextContent("You");
    expect(thread).toHaveTextContent("Workeasy360 Support");
    // The asking side may only close its ticket.
    const status = screen.getByLabelText("Status");
    expect(within(status).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["", "closed"]);
  });
});
