import { beforeEach, describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call } from "./testSupport";
import { PartnerTicketsScreen, WithdrawalsScreen } from "./PartnerDesk";

/**
 * Super Admin's side of the unified partner dashboard (ADR-057): the partner
 * ticket desk (answer, internal note, status) and the withdrawal queue (verify
 * payout details, start a payout, reject with a reason). A staff member who may
 * only look gets no answer form and no money buttons.
 */

// One router object, as Next.js returns; the address is the test's to set.
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }, query: "", pathname: "/admin/tickets" }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(nav.query),
  usePathname: () => nav.pathname,
}));

beforeEach(() => {
  nav.router.replace.mockReset();
  nav.query = "";
  nav.pathname = "/admin/tickets";
});

const urlsOf = (calls: Call[], key: string) => calls.filter((c) => c.key === key).map((c) => new URL(c.url, "https://x.invalid"));

const SLA = { first_response_due_at: "2026-11-03T10:00:00Z", resolve_due_at: "2026-11-04T10:00:00Z", first_response: "on_track", resolution: "on_track", state: "on_track" };
const TICKET = {
  id: "t1",
  number: 7,
  reference: "#7",
  direction: "platform",
  subject: "Commission missing",
  category: "commission",
  priority: "high",
  status: "open",
  related_ref: "",
  opened_at: "2026-11-03T08:00:00Z",
  updated_at: "2026-11-03T08:00:00Z",
  first_response_at: null,
  resolved_at: null,
  closed_at: null,
  sla: SLA,
  partner_name: "Acme Partners",
};
const DETAIL = {
  ...TICKET,
  partner: { id: "p1", name: "Acme Partners", code: "ACME" },
  may_answer: true,
  messages: [{ id: "m1", side: "partner", author: "Acme Partners", mine: false, body: "Order 42 earned nothing.", internal: false, created_at: "2026-11-03T08:00:00Z" }],
};
const PAGE = { items: [TICKET], counts: { open: 1 }, total: 1, priorities: ["low", "normal", "high", "urgent"], categories: ["general", "commission"] };

const desk = (permissions: string[]) =>
  render(
    <AdminContextForTest permissions={permissions}>
      <PartnerTicketsScreen />
    </AdminContextForTest>,
  );

describe("Partner Tickets", () => {
  it("lists partners' tickets with their SLA and answers one", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/partner-tickets": [ok(PAGE)],
      "GET /api/v1/admin/partner-tickets/t1": [ok(DETAIL)],
      "POST /api/v1/admin/partner-tickets/t1": [ok({ ...DETAIL, status: "in_progress" })],
    });
    const { container } = desk(["platform.staff_partner.view", "platform.staff_partner.manage"]);
    expect(await screen.findByText("Acme Partners")).toBeInTheDocument();
    expect(screen.getByText("On track")).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "#7" }));
    expect(await screen.findByText("Order 42 earned nothing.")).toBeInTheDocument();
    // The answer is written in a popup (2026-11-05).
    expect(screen.queryByLabelText("Answer")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    const popup = screen.getByRole("dialog", { name: "Reply to #7" });
    expect(within(popup).getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Answer"), { target: { value: "Looking into it." } });
    fireEvent.click(within(popup).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partner-tickets/t1")).toBe(true));
    const sent = calls.find((c) => c.key === "POST /api/v1/admin/partner-tickets/t1")!;
    expect(sent.body).toEqual({ message: "Looking into it.", internal: false });
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a viewer reads but gets no answer form", async () => {
    mockApi({
      "GET /api/v1/admin/partner-tickets": [ok(PAGE)],
      "GET /api/v1/admin/partner-tickets/t1": [ok({ ...DETAIL, may_answer: false })],
    });
    desk(["platform.staff_partner.view"]);
    fireEvent.click(await screen.findByRole("button", { name: "#7" }));
    expect(await screen.findByText(/answering needs the partner management permission/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Answer")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reply" })).toBeNull();
  });

  it("keeps the status and the search in the address, and lists the search as a removable tag (admin-ux phase 3)", async () => {
    const calls = mockApi({ "GET /api/v1/admin/partner-tickets": [ok(PAGE)] });
    desk(["platform.staff_partner.view"]);
    await screen.findByText("Acme Partners");
    const desks = () => urlsOf(calls, "GET /api/v1/admin/partner-tickets");
    expect(desks()[0].searchParams.get("status")).toBe("open");
    fireEvent.click(within(screen.getByRole("group", { name: "Ticket status" })).getByRole("button", { name: /^Resolved/ }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=resolved", { scroll: false });
    await waitFor(() => expect(desks().at(-1)!.searchParams.get("status")).toBe("resolved"));
    fireEvent.change(screen.getByLabelText("Search partner, subject or #number"), { target: { value: "acme" } });
    await waitFor(() => expect(desks().at(-1)!.searchParams.get("q")).toBe("acme"));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=resolved&q=acme", { scroll: false });
    const tags = screen.getByRole("list", { name: "Active filters" });
    expect(within(tags).getByRole("listitem")).toHaveTextContent("Search: “acme”");
    fireEvent.click(within(tags).getByRole("button", { name: "Remove filter Search: “acme”" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=resolved", { scroll: false });
    // Open, the first view, is no parameter at all.
    fireEvent.click(within(screen.getByRole("group", { name: "Ticket status" })).getByRole("button", { name: /^Open/ }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets", { scroll: false });
  });

  it("reads the status and the search back from the address, and drops a status it does not know", async () => {
    nav.query = "status=closed&q=acme";
    let calls = mockApi({ "GET /api/v1/admin/partner-tickets": [ok(PAGE)] });
    const view = desk(["platform.staff_partner.view"]);
    await screen.findByText("Acme Partners");
    expect(Object.fromEntries(urlsOf(calls, "GET /api/v1/admin/partner-tickets")[0].searchParams)).toEqual({ status: "closed", q: "acme" });
    expect(within(screen.getByRole("group", { name: "Ticket status" })).getByRole("button", { name: /^Closed/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Search partner, subject or #number")).toHaveValue("acme");
    view.unmount();

    nav.query = "status=everything";
    calls = mockApi({ "GET /api/v1/admin/partner-tickets": [ok(PAGE)] });
    desk(["platform.staff_partner.view"]);
    await screen.findByText("Acme Partners");
    expect(urlsOf(calls, "GET /api/v1/admin/partner-tickets")[0].searchParams.get("status")).toBe("open");
    expect(screen.getByText(/were not recognised and were left out/)).toBeInTheDocument();
  });

  it("offers Try again when the desk cannot be read", async () => {
    const calls = mockApi({ "GET /api/v1/admin/partner-tickets": [{ status: 500, body: { detail: "The desk is unavailable." } }, ok(PAGE)] });
    desk(["platform.staff_partner.view"]);
    expect(await screen.findByRole("alert")).toHaveTextContent("The desk is unavailable.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Acme Partners")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === "GET /api/v1/admin/partner-tickets")).toHaveLength(2);
  });
});

const METHOD = {
  method: "bank",
  account_holder: "Acme Partners Ltd",
  bank_name: "HDFC Bank",
  ifsc: "HDFC0001234",
  account_hint: "6789",
  upi_vpa: "",
  pan_on_file: true,
  pan_hint: "234F",
  verification: "pending",
  verification_note: "",
  verified_at: null,
  updated_at: "2026-11-03T08:00:00Z",
  destination: "Bank ****6789 (HDFC0001234)",
  partner: { id: "p1", name: "Acme Partners", code: "ACME" },
};
const WITHDRAWAL = {
  id: "w1",
  currency: "INR",
  amount_minor: 500000,
  tds_rate_bp: 200,
  tds_minor: 10000,
  net_minor: 490000,
  status: "requested",
  destination: "Bank ****6789 (HDFC0001234)",
  requested_at: "2026-11-03T08:00:00Z",
  decided_at: null,
  decision_note: "",
  paid_at: null,
  partner: { id: "p1", name: "Acme Partners", code: "ACME" },
};
const QUEUE = { items: [WITHDRAWAL], counts: { requested: 1, processing: 0, rejected: 0, cancelled: 0 }, pending_methods: [METHOD] };

const queue = (permissions: string[]) =>
  render(
    <AdminContextForTest permissions={permissions}>
      <WithdrawalsScreen />
    </AdminContextForTest>,
  );

describe("Withdrawals", () => {
  it("verifies payout details and starts a payout - never sending an amount", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/partner-withdrawals": [ok(QUEUE)],
      "POST /api/v1/admin/partners/p1/payout-method": [ok({ ...METHOD, verification: "verified" })],
      "POST /api/v1/admin/partner-withdrawals/w1": [ok({ ...WITHDRAWAL, status: "processing" })],
    });
    const { container } = queue(["platform.staff_payout.manage"]);
    expect(await screen.findByText("Payout details to verify (1)")).toBeInTheDocument();
    expect(screen.getAllByText("Bank ****6789 (HDFC0001234)").length).toBe(2);
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p1/payout-method")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p1/payout-method")!.body).toEqual({ decision: "verified", note: "" });
    fireEvent.click(screen.getByRole("button", { name: "Start payout for Acme Partners" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partner-withdrawals/w1")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partner-withdrawals/w1")!.body).toEqual({ action: "start_payout" });
  });

  it("puts Start payout and Reject in the requests' Actions column, last (admin-ux phase 3)", async () => {
    mockApi({ "GET /api/v1/admin/partner-withdrawals": [ok(QUEUE)] });
    queue(["platform.staff_payout.manage"]);
    const requests = await screen.findByRole("table", { name: "Withdrawal requests" });
    const headers = within(requests).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Partner", "Requested", "Amount", "TDS", "Net", "To", "Status", "Actions"]);
    const row = within(requests).getByRole("row", { name: /Acme Partners/ });
    const cells = within(row).getAllByRole("cell");
    const last = cells[cells.length - 1];
    expect(within(last).getByRole("button", { name: "Start payout for Acme Partners" })).toBeInTheDocument();
    expect(within(last).getByRole("button", { name: "Reject the withdrawal of Acme Partners" })).toHaveTextContent("Reject…");
    expect(row).toHaveTextContent("₹5,000.00");
    expect(row).toHaveTextContent("₹4,900.00");
  });

  it("keeps the status chosen in the address, reads it back, and drops one it does not know", async () => {
    nav.pathname = "/admin/partners/withdrawals";
    let calls = mockApi({ "GET /api/v1/admin/partner-withdrawals": [ok(QUEUE)] });
    let view = queue(["platform.staff_payout.manage"]);
    await screen.findByText("Payout details to verify (1)");
    const statuses = () => urlsOf(calls, "GET /api/v1/admin/partner-withdrawals").map((u) => u.searchParams.get("status"));
    expect(statuses()).toEqual(["requested"]);
    fireEvent.click(within(screen.getByRole("group", { name: "Withdrawal status" })).getByRole("button", { name: /^Rejected/ }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/partners/withdrawals?status=rejected", { scroll: false });
    await waitFor(() => expect(statuses().at(-1)).toBe("rejected"));
    view.unmount();

    nav.query = "status=cancelled";
    calls = mockApi({ "GET /api/v1/admin/partner-withdrawals": [ok(QUEUE)] });
    view = queue(["platform.staff_payout.manage"]);
    await screen.findByText("Payout details to verify (1)");
    expect(statuses()).toEqual(["cancelled"]);
    expect(within(screen.getByRole("group", { name: "Withdrawal status" })).getByRole("button", { name: /^Cancelled/ })).toHaveAttribute("aria-pressed", "true");
    // Waiting, the first view, is no parameter at all.
    fireEvent.click(within(screen.getByRole("group", { name: "Withdrawal status" })).getByRole("button", { name: /^Waiting/ }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/partners/withdrawals", { scroll: false });
    view.unmount();

    nav.query = "status=paid";
    calls = mockApi({ "GET /api/v1/admin/partner-withdrawals": [ok(QUEUE)] });
    queue(["platform.staff_payout.manage"]);
    await screen.findByText("Payout details to verify (1)");
    expect(statuses()).toEqual(["requested"]);
    expect(screen.getByText(/were not recognised and were left out/)).toBeInTheDocument();
  });

  it("offers Try again when the queue cannot be read", async () => {
    const calls = mockApi({ "GET /api/v1/admin/partner-withdrawals": [{ status: 500, body: { detail: "The queue is unavailable." } }, ok(QUEUE)] });
    queue(["platform.staff_payout.manage"]);
    expect(await screen.findByRole("alert")).toHaveTextContent("The queue is unavailable.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Payout details to verify (1)")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === "GET /api/v1/admin/partner-withdrawals")).toHaveLength(2);
  });

  it("rejects a withdrawal and payout details with a reason typed in a popup", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/partner-withdrawals": [ok(QUEUE)],
      "POST /api/v1/admin/partner-withdrawals/w1": [ok({ ...WITHDRAWAL, status: "rejected" })],
      "POST /api/v1/admin/partners/p1/payout-method": [ok({ ...METHOD, verification: "rejected" })],
    });
    queue(["platform.staff_payout.manage"]);
    await screen.findByText("Payout details to verify (1)");
    expect(screen.queryByLabelText("Why")).toBeNull();
    expect(screen.queryByLabelText("Reason if rejecting")).toBeNull();

    const requests = screen.getByRole("table", { name: "Withdrawal requests" });
    fireEvent.click(within(requests).getByRole("button", { name: "Reject the withdrawal of Acme Partners" }));
    let popup = screen.getByRole("dialog", { name: "Reject the withdrawal of Acme Partners" });
    // The confirmation says what will happen before anything is sent.
    expect(within(popup).getByText(/The request is closed and no money is sent/)).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /api/v1/admin/partner-withdrawals/w1")).toBe(false);
    expect(within(popup).getByRole("button", { name: "Reject" })).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Why"), { target: { value: "Bank details do not match" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Request rejected.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partner-withdrawals/w1")!.body).toEqual({ action: "reject", note: "Bank details do not match" });

    fireEvent.click(screen.getByRole("button", { name: "Reject the payout details of Acme Partners" }));
    popup = screen.getByRole("dialog", { name: "Reject the payout details of Acme Partners" });
    expect(within(popup).getByText(/must update their payout details/)).toBeInTheDocument();
    fireEvent.change(within(popup).getByLabelText("Reason if rejecting"), { target: { value: "PAN unreadable" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p1/payout-method")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p1/payout-method")!.body).toEqual({ decision: "rejected", note: "PAN unreadable" });
  });

  it("offers no money buttons without the payout permission", async () => {
    mockApi({ "GET /api/v1/admin/partner-withdrawals": [ok(QUEUE)] });
    queue(["platform.staff_partner.view"]);
    expect((await screen.findAllByRole("link", { name: "Acme Partners" })).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /^Start payout/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
    // ("Rejected" is a status filter chip, not an action.)
    expect(screen.queryByRole("button", { name: /^Reject the withdrawal/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Reject the payout details/ })).toBeNull();
    // No actions at all: no Actions column.
    expect(within(screen.getByRole("table", { name: "Withdrawal requests" })).queryByRole("columnheader", { name: "Actions" })).toBeNull();
  });
});
