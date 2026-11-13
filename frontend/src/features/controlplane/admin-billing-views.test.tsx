import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import CommissionsPage from "@/app/admin/commissions/page";
import InvoicesPage from "@/app/admin/invoices/page";
import PayoutsPage from "@/app/admin/payouts/page";
import SubscriptionsPage from "@/app/admin/subscriptions/page";
import {
  BillingSubscriptionsScreen,
  CommissionEntriesScreen,
  CreditNotesScreen,
  FailedAndStuckScreen,
  InvoicesScreen,
  PayoutsScreen,
  RefundsScreen,
} from "./AdminBillingViews";
import { ToastProvider } from "./kit";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "./testSupport";

/**
 * Billing & Subscriptions (2026-11-09): invoices, failed and stuck payments, refunds,
 * credit notes, subscriptions, commission entries and payouts. Real rows, every filter
 * sent to the server, the exact requests behind each action, the server's own refusals,
 * and actions hidden from staff who lack the grant.
 */

let query = "";
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/admin",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.push.mockReset();
  query = "";
});

const PAYMENT_VIEW = "platform.staff_payment.view";
const PAYMENT_MANAGE = "platform.staff_payment.manage";
const CUSTOMER_VIEW = "platform.staff_customer.view";
const COMMISSION_VIEW = "platform.staff_commission.view";
const COMMISSION_MANAGE = "platform.staff_commission.manage";
const PAYOUT_MANAGE = "platform.staff_payout.manage";
const PAYOUT_APPROVE = "platform.staff_payout.approve";

const PARTNER = "0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11";

const page = (results: unknown[], extra: Record<string, unknown> = {}): Reply =>
  ok({ results, next_cursor: null, has_more: false, ...extra });
const BROKEN: Reply = { status: 500, body: {} };
const FORBIDDEN: Reply = { status: 403, body: { detail: "No." } };

function mount(permissions: string[], ui: React.ReactNode) {
  return render(
    <ToastProvider>
      <AdminContextForTest permissions={permissions}>{ui}</AdminContextForTest>
    </ToastProvider>,
  );
}

const rowOf = (table: HTMLElement, name: RegExp) => within(table).getByRole("row", { name });

// --- invoices ----------------------------------------------------------------------------

const INVOICE = {
  invoice_id: "i1",
  organization_id: "o1",
  organization_name: "Acme Ltd",
  order_id: null,
  subscription_id: null,
  number: "INV-0001",
  status: "open",
  currency: "INR",
  total_minor: 123400,
  amount_paid_minor: 23400,
  amount_refunded_minor: 0,
  issued_at: "2026-10-01T06:00:00Z",
  due_at: "2026-10-15T06:00:00Z",
  paid_at: null,
  dunning_stage: "",
};

describe("Invoices", () => {
  it("lists real rows, with the customer linked and the row opening the customer", async () => {
    mockApi({ "GET /api/v1/admin/invoices": [page([INVOICE])] });
    const { container } = mount([PAYMENT_VIEW], <InvoicesScreen />);
    expect(screen.getByRole("heading", { level: 1, name: "Invoices" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Invoices" });
    const row = await within(table).findByRole("row", { name: /INV-0001/ });
    expect(within(row).getByRole("link", { name: "Acme Ltd" })).toHaveAttribute("href", "/admin/customers/o1");
    expect(row).toHaveTextContent("₹1,234.00");
    expect(row).toHaveTextContent("₹234.00");
    expect(row).toHaveTextContent("Open");
    expect(row).toHaveTextContent("1 Oct 2026");
    expect(row).toHaveTextContent("15 Oct 2026");
    fireEvent.click(within(row).getAllByRole("cell")[2]);
    expect(nav.router.push).toHaveBeenCalledWith("/admin/customers/o1");
    await expectNoViolations(container);
  });

  it("sends the status and the search to the server, and the next page's cursor", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/invoices": [
        ok({ results: [INVOICE], next_cursor: "next-1", has_more: true }),
        page([{ ...INVOICE, invoice_id: "i2", number: "INV-0002" }]),
      ],
    });
    mount([PAYMENT_VIEW], <InvoicesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await screen.findByRole("row", { name: /INV-0002/ });
    expect(calls[1].url).toContain("cursor=next-1");

    fireEvent.click(within(screen.getByRole("group", { name: "Invoice status" })).getByRole("button", { name: "Paid" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=paid"))).toBe(true));
    fireEvent.change(screen.getByLabelText("Search by invoice number"), { target: { value: "INV-9" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=paid") && c.url.includes("q=INV-9"))).toBe(true));
  });

  it("reads the search from the address, where the global search links", async () => {
    query = "q=INV-PLAN";
    const calls = mockApi({ "GET /api/v1/admin/invoices": [page([INVOICE])] });
    mount([PAYMENT_VIEW], <InvoicesPage />);
    await screen.findByRole("table", { name: "Invoices" });
    expect(calls[0].url).toContain("q=INV-PLAN");
    expect(screen.getByLabelText("Search by invoice number")).toHaveValue("INV-PLAN");
  });

  it("says so when there are none, and when they can't be loaded or read", async () => {
    mockApi({ "GET /api/v1/admin/invoices": [page([])] });
    const { unmount } = mount([PAYMENT_VIEW], <InvoicesScreen />);
    expect(await screen.findByText("No invoices")).toBeInTheDocument();
    expect(screen.getByText("Invoices appear here when customers are billed.")).toBeInTheDocument();
    unmount();

    mockApi({ "GET /api/v1/admin/invoices": [BROKEN] });
    const second = mount([PAYMENT_VIEW], <InvoicesScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load invoices.");
    second.unmount();

    mockApi({ "GET /api/v1/admin/invoices": [FORBIDDEN] });
    mount([], <InvoicesScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to invoices.");
  });

  it("keeps the rows and says so when a later page fails, until a retry works", async () => {
    mockApi({
      "GET /api/v1/admin/invoices": [
        ok({ results: [INVOICE], next_cursor: "next-1", has_more: true }),
        BROKEN,
        page([{ ...INVOICE, invoice_id: "i2", number: "INV-0002" }]),
      ],
    });
    mount([PAYMENT_VIEW], <InvoicesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load more invoices.");
    expect(screen.getByRole("row", { name: /INV-0001/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByRole("row", { name: /INV-0002/ });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

// --- failed and stuck ----------------------------------------------------------------------

const FAILED_PAYMENT = {
  attempt_id: "a1",
  organization_id: "o1",
  organization_name: "Acme Ltd",
  invoice_id: "i1",
  invoice_number: "INV-0001",
  invoice_status: "open",
  provider: "razorpay",
  status: "failed",
  amount_minor: 50000,
  currency: "INR",
  amount_refunded_minor: 0,
  failure_code: "card_declined",
  created_at: "2026-10-02T06:00:00Z",
  completed_at: null,
};

const PAID_ORDER = {
  order_id: "ord1",
  organization_id: "o2",
  organization_name: "Beta Co",
  number: "ORD-1",
  status: "paid",
  description: "Easy CRM, Growth",
  amount_due_minor: 99900,
  currency: "INR",
  paid_at: "2026-10-03T06:00:00Z",
};

const STUCK_ROUTES = (failed: Reply, stuck: Reply[], refunds: Reply = page([])) => ({
  "GET /api/v1/admin/payments/all": [failed],
  "GET /api/v1/admin/orders/stuck": stuck,
  "GET /api/v1/admin/refunds": [refunds],
});

describe("Failed & stuck", () => {
  it("lists failed payments with a way to the customer's billing, and asks the server for open failures only", async () => {
    const calls = mockApi(STUCK_ROUTES(page([FAILED_PAYMENT]), [ok({ results: [PAID_ORDER] })]));
    const { container } = mount([PAYMENT_VIEW, PAYMENT_MANAGE], <FailedAndStuckScreen />);
    expect(screen.getByRole("heading", { level: 1, name: "Failed & stuck" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Failed payments" });
    const row = await within(table).findByRole("row", { name: /Acme Ltd/ });
    expect(row).toHaveTextContent("INV-0001");
    expect(row).toHaveTextContent("Razorpay");
    expect(row).toHaveTextContent("₹500.00");
    expect(row).toHaveTextContent("card_declined");
    expect(within(row).getByRole("link", { name: "Open billing for Acme Ltd" })).toHaveAttribute("href", "/admin/customers/o1#billing-h");
    expect(calls.find((c) => c.key === "GET /api/v1/admin/payments/all")!.url).toContain("failed=1");
    expect(screen.getByRole("button", { name: "About this page" })).toBeInTheDocument();
    expect(screen.getByText(/A failure later paid by a retry leaves this list/)).toBeInTheDocument();
    await screen.findByRole("table", { name: "Paid orders not activated" });
    await expectNoViolations(container);
  });

  it("offers a retry on a paid order and lists the refunds that need a person", async () => {
    const calls = mockApi(
      STUCK_ROUTES(
        page([]),
        [ok({ results: [PAID_ORDER] })],
        page([
          {
            refund_id: "rf1",
            organization_id: "o3",
            organization_name: "Gamma Co",
            attempt_id: "a1",
            invoice_id: "i1",
            invoice_number: "INV-0009",
            provider: "razorpay",
            amount_minor: 50000,
            original_amount_minor: 50000,
            currency: "INR",
            reason: "The order could not be fulfilled after payment.",
            status: "failed",
            failure_code: "gateway_down",
            credit_note: "",
            created_at: "2026-11-01T10:00:00Z",
            completed_at: null,
          },
        ]),
      ),
    );
    mount([PAYMENT_VIEW, PAYMENT_MANAGE], <FailedAndStuckScreen />);
    const orders = await screen.findByRole("table", { name: "Paid orders not activated" });
    expect(within(rowOf(orders, /Beta Co/)).getByRole("button", { name: "Retry activation for order ORD-1" })).toBeInTheDocument();
    const refunds = await screen.findByRole("table", { name: "Refunds that need attention" });
    expect(await within(refunds).findByRole("row", { name: /Gamma Co/ })).toHaveTextContent("INV-0009");
    expect(calls.find((c) => c.key === "GET /api/v1/admin/refunds")!.url).toContain("waiting=1");
  });

  it("retries an activation with the exact request, says what came of it and refreshes", async () => {
    const calls = mockApi({
      ...STUCK_ROUTES(page([]), [ok({ results: [PAID_ORDER] }), ok({ results: [] })]),
      "POST /api/v1/admin/orders/ord1/retry-activation": [ok({ order_id: "ord1", outcome: "fulfilled" })],
    });
    mount([PAYMENT_VIEW, PAYMENT_MANAGE], <FailedAndStuckScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry activation for order ORD-1" }));
    expect(await screen.findByText("Order ORD-1 is activated.")).toBeInTheDocument();
    const post = calls.find((c) => c.key === "POST /api/v1/admin/orders/ord1/retry-activation")!;
    expect(post.body).toEqual({});
    expect(await screen.findByText("No paid orders are waiting for activation")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === "GET /api/v1/admin/orders/stuck")).toHaveLength(2);
  });

  it("says when a retry could not activate the order, because that refunds it", async () => {
    mockApi({
      ...STUCK_ROUTES(page([]), [ok({ results: [PAID_ORDER] })]),
      "POST /api/v1/admin/orders/ord1/retry-activation": [ok({ order_id: "ord1", outcome: "failed" })],
    });
    mount([PAYMENT_VIEW, PAYMENT_MANAGE], <FailedAndStuckScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry activation for order ORD-1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Order ORD-1 could not be activated, so its payment is being refunded automatically.");
  });

  it("shows the server's message when a retry is refused", async () => {
    mockApi({
      ...STUCK_ROUTES(page([]), [ok({ results: [PAID_ORDER] })]),
      "POST /api/v1/admin/orders/ord1/retry-activation": [
        { status: 400, body: { detail: "Only a paid order that has not been activated can be retried." } },
      ],
    });
    mount([PAYMENT_VIEW, PAYMENT_MANAGE], <FailedAndStuckScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry activation for order ORD-1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only a paid order that has not been activated can be retried.");
  });

  it("hides the retry from staff who can only look", async () => {
    mockApi(STUCK_ROUTES(page([]), [ok({ results: [PAID_ORDER] })]));
    mount([PAYMENT_VIEW], <FailedAndStuckScreen />);
    const table = await screen.findByRole("table", { name: "Paid orders not activated" });
    expect(rowOf(table, /Beta Co/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry activation/ })).not.toBeInTheDocument();
  });

  it("says nothing is waiting when both lists are empty", async () => {
    mockApi(STUCK_ROUTES(page([]), [ok({ results: [] })]));
    mount([PAYMENT_VIEW], <FailedAndStuckScreen />);
    expect(await screen.findByText("No failed payments are waiting")).toBeInTheDocument();
    expect(await screen.findByText("No paid orders are waiting for activation")).toBeInTheDocument();
    expect(await screen.findByText("No refunds need attention")).toBeInTheDocument();
  });

  it("keeps one list when the other can't be loaded, and says when more orders exist than it shows", async () => {
    const many = Array.from({ length: 100 }, (_, i) => ({ ...PAID_ORDER, order_id: `o${i}`, number: `ORD-${i}` }));
    mockApi(STUCK_ROUTES(BROKEN, [ok({ results: many })]));
    const { unmount } = mount([PAYMENT_VIEW], <FailedAndStuckScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load failed payments.");
    expect(await screen.findByText(/Showing the 100 most recently changed/)).toBeInTheDocument();
    unmount();

    mockApi(STUCK_ROUTES(page([FAILED_PAYMENT]), [BROKEN]));
    mount([PAYMENT_VIEW], <FailedAndStuckScreen />);
    await screen.findByRole("table", { name: "Failed payments" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});

// --- refunds -------------------------------------------------------------------------------

const REFUND = {
  refund_id: "r1",
  organization_id: "o1",
  organization_name: "Acme Ltd",
  attempt_id: "a1",
  invoice_id: "i1",
  invoice_number: "INV-0001",
  provider: "razorpay",
  original_amount_minor: 50000,
  credit_note: "CN-0001",
  amount_minor: 20000,
  currency: "INR",
  reason: "Customer asked",
  status: "succeeded",
  failure_code: "",
  created_at: "2026-10-04T06:00:00Z",
  completed_at: null,
};

describe("Refunds", () => {
  it("lists real rows and sends each status chip to the server", async () => {
    const calls = mockApi({ "GET /api/v1/admin/refunds": [page([REFUND])] });
    const { container } = mount([PAYMENT_VIEW], <RefundsScreen />);
    const table = await screen.findByRole("table", { name: "Refunds" });
    const row = await within(table).findByRole("row", { name: /Acme Ltd/ });
    expect(row).toHaveTextContent("INV-0001");
    expect(row).toHaveTextContent("₹200.00");
    expect(row).toHaveTextContent("₹500.00");
    expect(row).toHaveTextContent("Customer asked");
    expect(row).toHaveTextContent("Succeeded");
    expect(row).toHaveTextContent("CN-0001");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await expectNoViolations(container);
    for (const [label, value] of [
      ["Requested", "requested"],
      ["Submitting", "submitting"],
      ["Pending", "pending"],
      ["Failed", "failed"],
      ["Needs review", "needs_review"],
    ]) {
      fireEvent.click(screen.getByRole("button", { name: label }));
      await waitFor(() => expect(calls.some((c) => c.url.includes(`status=${value}`))).toBe(true));
    }
  });

  it("makes a refund that needs review stand out and says to confirm it with the provider", async () => {
    mockApi({ "GET /api/v1/admin/refunds": [page([{ ...REFUND, status: "needs_review", failure_code: "outcome_unknown", credit_note: "" }])] });
    const { container } = mount([PAYMENT_VIEW], <RefundsScreen />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Refunds below need review");
    expect(alert).toHaveTextContent("Confirm each one in the provider's dashboard.");
    const row = screen.getByRole("row", { name: /Acme Ltd/ });
    expect(row).toHaveTextContent("Needs review");
    expect(row).toHaveTextContent("Confirm in the provider's dashboard");
    await expectNoViolations(container);
  });

  it("says so when there are none, and when they can't be loaded", async () => {
    mockApi({ "GET /api/v1/admin/refunds": [page([])] });
    const { unmount } = mount([PAYMENT_VIEW], <RefundsScreen />);
    expect(await screen.findByText("No refunds")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Needs review" }));
    expect(await screen.findByText("No refunds need review")).toBeInTheDocument();
    unmount();

    mockApi({ "GET /api/v1/admin/refunds": [BROKEN] });
    mount([PAYMENT_VIEW], <RefundsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load refunds.");
  });
});

// --- credit notes ----------------------------------------------------------------------------

const CREDIT_NOTE = {
  id: "cn1",
  number: "CN-0001",
  organization_id: "o1",
  organization_name: "Acme Ltd",
  invoice_id: "i1",
  invoice_number: "INV-0001",
  refund_id: "7c1d7f3e-1111-4222-8333-444455556666",
  attempt_id: "a1",
  amount_minor: 20000,
  currency: "INR",
  reason: "Customer asked",
  issued_at: "2026-10-05T06:00:00Z",
};

describe("Credit notes", () => {
  it("lists real rows, with the refund's id, and pages through the server", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/credit-notes": [
        ok({ results: [CREDIT_NOTE], next_cursor: "next-1", has_more: true }),
        page([{ ...CREDIT_NOTE, id: "cn2", number: "CN-0002" }]),
      ],
    });
    const { container } = mount([PAYMENT_VIEW], <CreditNotesScreen />);
    const table = await screen.findByRole("table", { name: "Credit notes" });
    const row = await within(table).findByRole("row", { name: /CN-0001/ });
    expect(within(row).getByRole("link", { name: "Acme Ltd" })).toHaveAttribute("href", "/admin/customers/o1");
    expect(row).toHaveTextContent("INV-0001");
    expect(row).toHaveTextContent("7c1d7f3e…");
    expect(within(row).getByRole("button", { name: "Copy refund ID" })).toBeInTheDocument();
    expect(row).toHaveTextContent("₹200.00");
    expect(row).toHaveTextContent("5 Oct 2026");
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByRole("row", { name: /CN-0002/ });
    expect(calls[1].url).toContain("cursor=next-1");
  });

  it("sends the search to the server", async () => {
    const calls = mockApi({ "GET /api/v1/admin/credit-notes": [page([CREDIT_NOTE])] });
    mount([PAYMENT_VIEW], <CreditNotesScreen />);
    await screen.findByRole("table", { name: "Credit notes" });
    fireEvent.change(screen.getByLabelText("Search by credit note or invoice number"), { target: { value: "CN-9" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=CN-9"))).toBe(true));
  });

  it("says so when there are none, and when they can't be loaded", async () => {
    mockApi({ "GET /api/v1/admin/credit-notes": [page([])] });
    const { unmount } = mount([PAYMENT_VIEW], <CreditNotesScreen />);
    expect(await screen.findByText("No credit notes")).toBeInTheDocument();
    unmount();

    mockApi({ "GET /api/v1/admin/credit-notes": [BROKEN] });
    mount([PAYMENT_VIEW], <CreditNotesScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load credit notes.");
  });
});

// --- subscriptions -----------------------------------------------------------------------------

const SUBSCRIPTION = {
  subscription_id: "s1",
  organization_id: "o1",
  organization_name: "Acme Ltd",
  status: "active",
  currency: "INR",
  billing_interval: "annual",
  renewal_policy: "auto_renew",
  current_period_end: "2027-01-10T06:00:00Z",
  trial_ends_at: null,
  activated_at: "2026-01-10T06:00:00Z",
  cancel_at: "2027-01-10T06:00:00Z",
  ended_at: null,
  lines: [
    { product_code: "easy_crm", plan_code: "growth", bundle_code: "", quantity: 5, total_amount_minor: 100, currency: "INR", interval: "annual" },
    { product_code: "easybook", plan_code: "", bundle_code: "", quantity: 1, total_amount_minor: 100, currency: "INR", interval: "annual" },
  ],
};
const COUNTS = { active: 3, trialing: 1, past_due: 2, grace: 0, suspended: 0, cancelled: 4, expired: 1 };
const PRODUCTS: Reply = ok({
  results: [
    { id: "p1", code: "easy_crm", name: "Easy CRM (easy_crm)", status: "active" },
    { id: "p2", code: "easy_project", name: "Easy Project", status: "active" },
  ],
});

describe("Subscriptions", () => {
  it("is what the route shows: every subscription with its lines, seats and dates", async () => {
    mockApi({
      "GET /api/v1/admin/subscriptions": [page([SUBSCRIPTION], { counts: COUNTS })],
      "GET /api/v1/admin/catalog/products": [PRODUCTS],
    });
    const { container } = mount([CUSTOMER_VIEW], <SubscriptionsPage />);
    const table = await screen.findByRole("table", { name: "Subscriptions" });
    const row = await within(table).findByRole("row", { name: /Acme Ltd/ });
    await waitFor(() => expect(row).toHaveTextContent("Easy CRM"));
    expect(row).toHaveTextContent("Growth · 5 seats");
    expect(row).toHaveTextContent("Easybook");
    expect(row).toHaveTextContent("1 seat");
    expect(row).toHaveTextContent("Active");
    expect(row).toHaveTextContent("Annual (360 days)");
    expect(row).toHaveTextContent("Auto renew");
    expect(row).toHaveTextContent("10 Jan 2027");
    fireEvent.click(within(row).getAllByRole("cell")[1]);
    expect(nav.router.push).toHaveBeenCalledWith("/admin/customers/o1");
    expect(screen.getByRole("link", { name: "Customers > Products & Setup" })).toHaveAttribute("href", "/admin/customers/setup");
    await expectNoViolations(container);
  });

  it("counts on the chips, and sends every filter to the server", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/subscriptions": [page([SUBSCRIPTION], { counts: COUNTS })],
      "GET /api/v1/admin/catalog/products": [PRODUCTS],
    });
    mount([CUSTOMER_VIEW], <BillingSubscriptionsScreen />);
    await screen.findByRole("table", { name: "Subscriptions" });
    expect(await screen.findByRole("button", { name: "All 11" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Trial 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Past due 2" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Trial 1" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=trialing"))).toBe(true));

    fireEvent.click(await screen.findByRole("combobox", { name: "Product" }));
    expect(screen.queryByRole("option", { name: /easy_crm/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: "Easy CRM" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("product=easy_crm"))).toBe(true));

    fireEvent.click(screen.getByRole("combobox", { name: "Renewing within" }));
    fireEvent.click(screen.getByRole("option", { name: "30 days" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("renewing=30"))).toBe(true));

    fireEvent.change(screen.getByLabelText("Search customers"), { target: { value: "acme" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=acme"))).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => {
      const last = calls[calls.length - 1].url;
      expect(last).not.toContain("q=");
      expect(last).not.toContain("product=");
      expect(last).not.toContain("renewing=");
    });
  });

  it("still lists subscriptions when the catalog is out of this person's reach", async () => {
    mockApi({
      "GET /api/v1/admin/subscriptions": [page([SUBSCRIPTION], { counts: COUNTS })],
      "GET /api/v1/admin/catalog/products": [FORBIDDEN],
    });
    mount([CUSTOMER_VIEW], <BillingSubscriptionsScreen />);
    const row = await screen.findByRole("row", { name: /Acme Ltd/ });
    await waitFor(() => expect(row).toHaveTextContent("Easy CRM"));
    expect(screen.queryByRole("combobox", { name: "Product" })).not.toBeInTheDocument();
  });

  it("says so when there are none, and when they can't be loaded", async () => {
    mockApi({
      "GET /api/v1/admin/subscriptions": [page([], { counts: {} })],
      "GET /api/v1/admin/catalog/products": [PRODUCTS],
    });
    const { unmount } = mount([CUSTOMER_VIEW], <BillingSubscriptionsScreen />);
    expect(await screen.findByText("No subscriptions")).toBeInTheDocument();
    unmount();

    mockApi({
      "GET /api/v1/admin/subscriptions": [BROKEN],
      "GET /api/v1/admin/catalog/products": [PRODUCTS],
    });
    mount([CUSTOMER_VIEW], <BillingSubscriptionsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load subscriptions.");
  });
});

// --- commission entries ------------------------------------------------------------------------

const ACCRUAL = {
  id: "e1",
  kind: "accrual",
  amount_minor: 50000,
  currency: "INR",
  event_at: "2026-09-01T06:00:00Z",
  clears_at: "2026-10-01T06:00:00Z",
  reason_code: "",
  reverses: null,
  partner_id: PARTNER,
  organization_id: "o1",
  organization_name: "Acme Ltd",
  source_ref: "payments.invoice_line:1",
  rule_code: "default",
  rule_version: 1,
  rate_bp: 1000,
  status: "approved",
  reversed_minor: 0,
  payout: null,
};
const REVERSAL = {
  ...ACCRUAL,
  id: "e2",
  kind: "reversal",
  amount_minor: -20000,
  event_at: "2026-09-05T06:00:00Z",
  reason_code: "refund",
  reverses: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  organization_name: "Beta Co",
  organization_id: "o2",
};
const REVERSE_URL = "POST /api/v1/admin/commission/accruals/e1/reverse";

describe("Commission entries", () => {
  it("is what the route shows, not a redirect, and says these are earnings records, not a wallet", async () => {
    mockApi({ "GET /api/v1/admin/commission/accruals": [page([ACCRUAL, REVERSAL])] });
    const { container } = mount([COMMISSION_VIEW], <CommissionsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Commission entries" })).toBeInTheDocument();
    expect(screen.getByText(/Earnings records, not a wallet/)).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Commission entries" });
    const row = await within(table).findByRole("row", { name: /Acme Ltd/ });
    expect(within(row).getByRole("link", { name: "Acme Ltd" })).toHaveAttribute("href", "/admin/customers/o1");
    expect(within(row).getByRole("link", { name: `Partner ${PARTNER}` })).toHaveAttribute("href", `/admin/partners/${PARTNER}`);
    expect(row).toHaveTextContent("Accrual");
    expect(row).toHaveTextContent("₹500.00");
    expect(row).toHaveTextContent("1 Sep 2026");
    expect(row).toHaveTextContent("1 Oct 2026");
    const reversal = within(table).getByRole("row", { name: /Beta Co/ });
    expect(reversal).toHaveTextContent("Reversal");
    expect(reversal).toHaveTextContent("Refund");
    expect(reversal).toHaveTextContent("-₹200.00");
    expect(reversal).toHaveTextContent("aaaaaaaa…");
    await expectNoViolations(container);
  });

  it("shows how an entry earned when the server says", async () => {
    mockApi({ "GET /api/v1/admin/commission/accruals": [page([{ ...ACCRUAL, earning: "margin", partner_name: "Northwind" }])] });
    mount([COMMISSION_VIEW], <CommissionEntriesScreen />);
    const row = await screen.findByRole("row", { name: /Acme Ltd/ });
    expect(screen.getByRole("columnheader", { name: "Earning" })).toBeInTheDocument();
    expect(row).toHaveTextContent("Margin");
    expect(within(row).getByRole("link", { name: "Northwind" })).toBeInTheDocument();
  });

  it("filters on one partner from the address and on entries in no payout, both at the server", async () => {
    query = `partner=${PARTNER}`;
    const calls = mockApi({ "GET /api/v1/admin/commission/accruals": [page([ACCRUAL])] });
    mount([COMMISSION_VIEW], <CommissionsPage />);
    await screen.findByRole("table", { name: "Commission entries" });
    expect(calls[0].url).toContain(`partner_id=${PARTNER}`);
    expect(calls[0].url).not.toContain("unpaid=");
    expect(screen.getByRole("link", { name: "Show every partner" })).toHaveAttribute("href", "/admin/commissions");
    fireEvent.click(screen.getByRole("button", { name: "Not in a payout" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("unpaid=1") && c.url.includes(`partner_id=${PARTNER}`))).toBe(true));
  });

  it("ignores a partner value that is not an id, rather than failing", async () => {
    query = "partner=not-an-id";
    const calls = mockApi({ "GET /api/v1/admin/commission/accruals": [page([ACCRUAL])] });
    mount([COMMISSION_VIEW], <CommissionsPage />);
    await screen.findByRole("table", { name: "Commission entries" });
    expect(calls[0].url).not.toContain("partner_id=");
    expect(screen.queryByRole("link", { name: "Show every partner" })).not.toBeInTheDocument();
  });

  it("confirms, then reverses with the exact request and a command id, and refreshes", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/commission/accruals": [page([ACCRUAL]), page([{ ...ACCRUAL, status: "reversed", reversed_minor: 50000 }])],
      [REVERSE_URL]: [{ status: 201, body: { reversed: true } }],
    });
    mount([COMMISSION_VIEW, COMMISSION_MANAGE], <CommissionEntriesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /^Reverse ₹500.00 earned/ }));
    const popup = screen.getByRole("dialog", { name: "Reverse this commission entry?" });
    expect(popup).toHaveTextContent("This adds a reversal of ₹500.00");
    expect(calls.some((c) => c.key === REVERSE_URL)).toBe(false);

    fireEvent.click(within(popup).getByRole("combobox", { name: /Why it is being reversed/ }));
    fireEvent.click(screen.getByRole("option", { name: "Chargeback" }));
    fireEvent.click(within(popup).getByRole("button", { name: "Reverse entry" }));

    expect(await screen.findByText("Reversal of ₹500.00 recorded.")).toBeInTheDocument();
    const post = calls.find((c) => c.key === REVERSE_URL)!;
    expect(post.body).toEqual({ reason_code: "chargeback", reason: "Reverse commission entry - Admin console", amount_minor: 50000 });
    expect(post.headers["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() => expect(calls.filter((c) => c.key === "GET /api/v1/admin/commission/accruals")).toHaveLength(2));
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Reverse / })).not.toBeInTheDocument());
    expect(screen.getByRole("row", { name: /Acme Ltd/ })).toHaveTextContent("₹500.00 reversed");
  });

  it("sends what the person typed as the reason, and only what is left of a part-reversed entry", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/commission/accruals": [page([{ ...ACCRUAL, reversed_minor: 20000 }])],
      [REVERSE_URL]: [{ status: 201, body: { reversed: true } }],
    });
    mount([COMMISSION_VIEW, COMMISSION_MANAGE], <CommissionEntriesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /^Reverse ₹300.00 earned/ }));
    const popup = screen.getByRole("dialog", { name: "Reverse this commission entry?" });
    fireEvent.change(within(popup).getByLabelText(/^Reason/), { target: { value: "Sale was booked twice by mistake" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Reverse entry" }));
    await waitFor(() => expect(calls.some((c) => c.key === REVERSE_URL)).toBe(true));
    expect(calls.find((c) => c.key === REVERSE_URL)!.body).toEqual({
      reason_code: "correction",
      reason: "Sale was booked twice by mistake",
      amount_minor: 30000,
    });
  });

  it("keeps the dialog open with the server's message when a reversal is refused, and a retry is the same command", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/commission/accruals": [page([ACCRUAL])],
      [REVERSE_URL]: [{ status: 409, body: { detail: "Reversals cannot exceed the commission they reverse." } }],
    });
    mount([COMMISSION_VIEW, COMMISSION_MANAGE], <CommissionEntriesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /^Reverse ₹500.00 earned/ }));
    const popup = screen.getByRole("dialog", { name: "Reverse this commission entry?" });
    fireEvent.click(within(popup).getByRole("button", { name: "Reverse entry" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("Reversals cannot exceed the commission they reverse.");
    fireEvent.click(within(popup).getByRole("button", { name: "Reverse entry" }));
    await waitFor(() => expect(calls.filter((c) => c.key === REVERSE_URL)).toHaveLength(2));
    const [first, second] = calls.filter((c) => c.key === REVERSE_URL);
    expect(second.headers["idempotency-key"]).toBe(first.headers["idempotency-key"]);
  });

  it("offers a reversal only on an earning with something left, and only to staff who may", async () => {
    const rows = [ACCRUAL, REVERSAL, { ...ACCRUAL, id: "e3", organization_name: "Gamma Co", status: "reversed", reversed_minor: 50000 }];
    mockApi({ "GET /api/v1/admin/commission/accruals": [page(rows)] });
    const { unmount } = mount([COMMISSION_VIEW, COMMISSION_MANAGE], <CommissionEntriesScreen />);
    const table = await screen.findByRole("table", { name: "Commission entries" });
    expect(within(rowOf(table, /Acme Ltd/)).getByRole("button", { name: /^Reverse / })).toBeInTheDocument();
    expect(within(rowOf(table, /Beta Co/)).queryByRole("button", { name: /^Reverse / })).not.toBeInTheDocument();
    expect(within(rowOf(table, /Gamma Co/)).queryByRole("button", { name: /^Reverse / })).not.toBeInTheDocument();
    unmount();

    mockApi({ "GET /api/v1/admin/commission/accruals": [page(rows)] });
    mount([COMMISSION_VIEW], <CommissionEntriesScreen />);
    await screen.findByRole("table", { name: "Commission entries" });
    expect(screen.queryByRole("button", { name: /^Reverse / })).not.toBeInTheDocument();
  });

  it("says so when there are none, and when they can't be loaded", async () => {
    mockApi({ "GET /api/v1/admin/commission/accruals": [page([])] });
    const { unmount } = mount([COMMISSION_VIEW], <CommissionEntriesScreen />);
    expect(await screen.findByText("No commission entries")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Not in a payout" }));
    expect(await screen.findByText("Every entry is in a payout")).toBeInTheDocument();
    unmount();

    mockApi({ "GET /api/v1/admin/commission/accruals": [BROKEN] });
    mount([COMMISSION_VIEW], <CommissionEntriesScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load commission entries.");
  });
});

// --- payouts -------------------------------------------------------------------------------------

const PAYOUT = {
  id: "po1",
  status: "draft",
  currency: "INR",
  period_end: "2026-09-30",
  gross_minor: 100000,
  adjustments_minor: -5000,
  payable_minor: 95000,
  line_count: 4,
  version: 3,
  partner_id: PARTNER,
  paid_reference: "",
};
const PAYOUTS = "GET /api/v1/admin/payouts";

describe("Payouts", () => {
  it("is what the route shows, not a redirect: real rows, and a status chip for each state", async () => {
    const calls = mockApi({ [PAYOUTS]: [page([PAYOUT])] });
    const { container } = mount([COMMISSION_VIEW, PAYOUT_MANAGE, PAYOUT_APPROVE], <PayoutsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Payouts" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Payouts" });
    const row = await within(table).findByRole("row", { name: /30 Sep 2026/ });
    expect(within(row).getByRole("link", { name: `Partner ${PARTNER}` })).toHaveAttribute("href", `/admin/partners/${PARTNER}`);
    expect(row).toHaveTextContent("Draft");
    expect(row).toHaveTextContent("₹1,000.00");
    expect(row).toHaveTextContent("-₹50.00");
    expect(row).toHaveTextContent("₹950.00");
    expect(screen.getByRole("link", { name: "Withdrawal requests" })).toHaveAttribute("href", "/admin/partners/withdrawals");
    await expectNoViolations(container);
    for (const [label, value] of [
      ["Ready", "ready"],
      ["Approved", "approved"],
      ["Paid", "paid"],
      ["Cancelled", "cancelled"],
      ["Draft", "draft"],
    ]) {
      fireEvent.click(within(screen.getByRole("group", { name: "Payout status" })).getByRole("button", { name: label }));
      await waitFor(() => expect(calls.some((c) => c.url.includes(`status=${value}`))).toBe(true));
    }
  });

  it("marks a draft ready with the exact request and refreshes", async () => {
    const calls = mockApi({
      [PAYOUTS]: [page([PAYOUT]), page([{ ...PAYOUT, status: "ready", version: 4 }])],
      "POST /api/v1/admin/payouts/po1/ready": [ok({ ...PAYOUT, status: "ready", version: 4 })],
    });
    mount([COMMISSION_VIEW, PAYOUT_MANAGE], <PayoutsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark ready" }));
    expect(await screen.findByText("Marked ready for approval.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/admin/payouts/po1/ready")!.body).toEqual({ expected_version: 3 });
    await waitFor(() => expect(screen.getByRole("row", { name: /30 Sep 2026/ })).toHaveTextContent("Ready"));
  });

  it("records an approved payout as paid, with only the transfer reference", async () => {
    const approved = { ...PAYOUT, status: "approved" };
    const calls = mockApi({
      [PAYOUTS]: [page([approved])],
      "POST /api/v1/admin/payouts/po1/paid": [ok({ ...approved, status: "paid" })],
    });
    mount([COMMISSION_VIEW, PAYOUT_MANAGE], <PayoutsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Record the payout for 2026-09-30 as paid" }));
    const popup = screen.getByRole("dialog", { name: "Record a payout as paid" });
    fireEvent.change(within(popup).getByLabelText(/Transfer reference/), { target: { value: " NEFT-77 " } });
    fireEvent.click(within(popup).getByRole("button", { name: "Record as paid" }));
    expect(await screen.findByText("Recorded as paid.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/admin/payouts/po1/paid")!.body).toEqual({ expected_version: 3, reference: "NEFT-77" });
  });

  it("confirms before cancelling", async () => {
    const calls = mockApi({
      [PAYOUTS]: [page([PAYOUT])],
      "POST /api/v1/admin/payouts/po1/cancel": [ok({ ...PAYOUT, status: "cancelled" })],
    });
    mount([COMMISSION_VIEW, PAYOUT_MANAGE], <PayoutsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel payout" }));
    expect(calls.some((c) => c.key === "POST /api/v1/admin/payouts/po1/cancel")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel payout" }));
    expect(await screen.findByText("Payout cancelled; its entries are payable again.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/admin/payouts/po1/cancel")!.body).toEqual({
      expected_version: 3,
      reason: "Cancel payout - Admin console",
    });
  });

  it("shows the server's message when an approval is refused", async () => {
    const ready = { ...PAYOUT, status: "ready" };
    mockApi({
      [PAYOUTS]: [page([ready])],
      "POST /api/v1/admin/payouts/po1/approve": [{ status: 409, body: { detail: "The person who prepared a payout cannot approve it." } }],
    });
    mount([COMMISSION_VIEW, PAYOUT_APPROVE], <PayoutsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The person who prepared a payout cannot approve it.");
  });

  it("offers each action only to the grant that allows it", async () => {
    const ready = { ...PAYOUT, status: "ready" };
    mockApi({ [PAYOUTS]: [page([ready])] });
    const first = mount([COMMISSION_VIEW, PAYOUT_MANAGE], <PayoutsScreen />);
    await screen.findByRole("row", { name: /30 Sep 2026/ });
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel payout" })).toBeInTheDocument();
    first.unmount();

    mockApi({ [PAYOUTS]: [page([ready])] });
    const second = mount([COMMISSION_VIEW, PAYOUT_APPROVE], <PayoutsScreen />);
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel payout" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Withdrawal requests" })).not.toBeInTheDocument();
    second.unmount();

    mockApi({ [PAYOUTS]: [page([ready])] });
    mount([COMMISSION_VIEW], <PayoutsScreen />);
    await screen.findByRole("row", { name: /30 Sep 2026/ });
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel payout" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Actions" })).not.toBeInTheDocument();
  });

  it("says so when there are none, and when they can't be loaded", async () => {
    mockApi({ [PAYOUTS]: [page([])] });
    const { unmount } = mount([COMMISSION_VIEW], <PayoutsScreen />);
    expect(await screen.findByText("No payouts")).toBeInTheDocument();
    unmount();

    mockApi({ [PAYOUTS]: [BROKEN] });
    mount([COMMISSION_VIEW], <PayoutsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load payouts.");
  });
});
