import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { AdminContext, AdminShell } from "./ControlShell";
import { AuditScreen, CustomerDetailScreen, SecurityScreen, SupportScreen } from "./AdminScreens";
import { UserDetailScreen, Users } from "./AdminUsers";
import { EntitlementsScreen, PlatformSettingsScreen, StaffOrdersScreen, StaffRolesPanel, SubscriptionsScreen } from "./AdminCommerce";

/**
 * Phase 8 extension: the Admin console screens (plan builder, pricing,
 * payments, customer billing, users, communication). What the browser sends -
 * and never sends - plus the same non-visual axe pass per state as the other
 * consoles. Contrast, focus visibility and reflow remain manual.
 */

let pathname = "/admin";
// One router object, as Next.js returns; the address (`nav.query`) is the test's to set.
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }, query: "" }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(nav.query),
  usePathname: () => pathname,
}));

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

const bodyOf = (calls: Call[], key: string) => calls.find((c) => c.key === key)?.body as Record<string, unknown>;

function asStaff(permissions: string[], ui: React.ReactNode) {
  return render(
    <AdminContext.Provider value={{ permissions }}>
      <main>{ui}</main>
    </AdminContext.Provider>,
  );
}

const SIGNED_IN: Reply = { status: 200, body: { authenticated: true, user_id: "u1", email: "s@example.com" } };
const T = "2026-09-01T10:00:00Z";
const ORG = "22222222-2222-2222-2222-222222222222";

beforeEach(() => {
  vi.restoreAllMocks();
  pathname = "/admin";
  nav.router.replace.mockReset();
  nav.query = "";
});

// --- navigation ------------------------------------------------------------------------

describe("the Admin navigation", () => {
  const modules = () =>
    within(screen.getByRole("navigation", { name: "Admin Portal" }))
      .getAllByRole("link")
      .map((a) => a.textContent ?? "");

  it("shows the new areas to staff holding their grants", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [
        {
          status: 200,
          body: {
            is_staff: true,
            permissions: [
              "platform.staff_user.view",
              "platform.staff_customer.view",
              "platform.staff_catalog.view",
              "platform.staff_payment.view",
              "platform.staff_communication.view",
            ],
          },
        },
      ],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    expect(modules()).toEqual(["Dashboard", "Customers", "Products & Pricing", "Billing", "Settings"]);
  });

  it("hides them from staff without the grants", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [
        { status: 200, body: { is_staff: true, permissions: ["platform.staff_customer.view"] } },
      ],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    expect(modules()).toEqual(["Dashboard", "Customers", "Billing"]);
  });
});

// --- amounts -------------------------------------------------------------------------------

const CUSTOMER = {
  organization: { organization_id: ORG, slug: "acme", display_name: "Acme Ltd", status: "active", created_at: T, suspension_reason: "" },
  members: { owner: 1 },
  subscriptions: [],
  entitlements: {},
  licenses: [],
  entitlement_overrides: [],
  billing: null,
  partner: null,
  audit: [],
};

const OPEN_INVOICE = {
  invoice_id: "inv-1",
  number: "INV-1",
  status: "open",
  currency: "INR",
  total_minor: 49999,
  amount_paid_minor: 0,
  amount_refunded_minor: 0,
  amount_due_minor: 49999,
  billing_interval: "monthly",
  period_start: T,
  period_end: "2026-10-01T10:00:00Z",
  issued_at: T,
  due_at: "2026-09-15T10:00:00Z",
  paid_at: null,
  dunning_stage: "reminder_1",
  version: 3,
  attempts: [],
};

const PAID_INVOICE = {
  ...OPEN_INVOICE,
  invoice_id: "inv-2",
  number: "INV-2",
  status: "paid",
  total_minor: 100000,
  amount_paid_minor: 100000,
  amount_refunded_minor: 20000,
  amount_due_minor: 0,
  dunning_stage: null,
  paid_at: T,
  version: 5,
  attempts: [
    {
      attempt_id: "att-1",
      invoice_id: "inv-2",
      status: "succeeded",
      amount_minor: 100000,
      currency: "INR",
      provider: "stripe-eu",
      checkout_url: null,
      failure_code: null,
      expires_at: T,
      amount_refunded_minor: 20000,
      created_at: T,
      refunds: [
        {
          refund_id: "r1",
          attempt_id: "att-1",
          amount_minor: 20000,
          currency: "INR",
          status: "succeeded",
          reason: "Duplicate charge",
          failure_code: null,
          created_at: T,
          completed_at: T,
        },
      ],
    },
  ],
};

const BILLING_STAFF = [
  "platform.staff_customer.view",
  "platform.staff_customer.manage",
  "platform.staff_payment.view",
  "platform.staff_payment.manage",
];

function billingRoutes(): Record<string, Reply[]> {
  return {
    [`GET /api/v1/admin/customers/${ORG}`]: [{ status: 200, body: CUSTOMER }],
    [`GET /api/v1/admin/customers/${ORG}/billing`]: [{ status: 200, body: { invoices: [OPEN_INVOICE, PAID_INVOICE] } }],
    [`POST /api/v1/admin/customers/${ORG}/invoices/inv-1/mark-paid`]: [{ status: 200, body: {} }],
    [`POST /api/v1/admin/customers/${ORG}/invoices/inv-1/void`]: [{ status: 200, body: {} }],
    [`POST /api/v1/admin/customers/${ORG}/payments/att-1/refund`]: [{ status: 202, body: {} }],
    [`POST /api/v1/admin/customers/${ORG}/network-policy/reset`]: [
      { status: 200, body: { organization_id: ORG, ip_allowlist_enabled: false } },
    ],
  };
}

/** Runs an audited action: no reason is typed (the button records one), and a
 * destructive action is confirmed once. */
/** Runs an audited action. A destructive one asks in a dialog; its reason is
 * optional, and left blank the console records one itself. */
async function confirmReason(label: string) {
  fireEvent.click(await screen.findByRole("button", { name: label }));
  const confirm = screen.queryByRole("group", { name: `Confirm ${label}` });
  if (confirm) {
    expect(screen.getByLabelText(/^Reason \(optional/)).toHaveValue("");
    fireEvent.click(within(confirm).getByRole("button", { name: /^Confirm/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  }
  expect(screen.queryByLabelText(/^Reason/)).not.toBeInTheDocument();
}

/** Opens one of the customer page's tabs (admin-ux phase 2). */
async function openTab(name: string) {
  fireEvent.click(await screen.findByRole("tab", { name }));
}

/** Chooses an option of a styled select. */
function choose(name: string | RegExp, option: string | RegExp) {
  fireEvent.click(screen.getByRole("combobox", { name }));
  fireEvent.click(screen.getByRole("option", { name: option }));
}

const recorded = (action: string) => `${action} - Admin console`;

describe("a customer's billing", () => {
  it("lists invoices with attempts and refunds, and passes axe", async () => {
    mockApi(billingRoutes());
    const { container } = asStaff(BILLING_STAFF, <CustomerDetailScreen organizationId={ORG} />);
    await openTab("Billing");
    const invoices = await screen.findByRole("table", { name: "Invoices" });
    expect(within(invoices).getByRole("row", { name: /INV-1/ })).toHaveTextContent("reminder 1");
    const payments = screen.getByRole("table", { name: "Payments for invoice INV-2" });
    expect(payments).toHaveTextContent("Duplicate charge");
    expect(screen.getByRole("button", { name: "Void INV-1" })).toBeInTheDocument();
    // Nothing was paid on INV-2 by hand, and it isn't open: no mark-paid or void.
    expect(screen.queryByRole("button", { name: "Void INV-2" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark INV-2 paid" })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("is hidden from staff without payment access, as are its actions from view-only staff", async () => {
    mockApi(billingRoutes());
    const { unmount } = asStaff(["platform.staff_customer.view"], <CustomerDetailScreen organizationId={ORG} />);
    await screen.findByRole("heading", { level: 1 });
    await openTab("Billing");
    // The billing account line stays; the invoices section needs payment access.
    expect(screen.getByRole("heading", { name: "Billing account" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Billing" })).not.toBeInTheDocument();
    // No manage grant: no More actions at all.
    expect(screen.queryByRole("button", { name: "More actions" })).not.toBeInTheDocument();
    unmount();

    mockApi(billingRoutes());
    asStaff(["platform.staff_customer.view", "platform.staff_payment.view"], <CustomerDetailScreen organizationId={ORG} />);
    await openTab("Billing");
    await screen.findByRole("table", { name: "Invoices" });
    expect(screen.queryByRole("button", { name: /Void|Mark|Refund/ })).not.toBeInTheDocument();
  });

  it("mark paid sends the reference, a recorded reason and the invoice version", async () => {
    const calls = mockApi(billingRoutes());
    asStaff(BILLING_STAFF, <CustomerDetailScreen organizationId={ORG} />);
    await openTab("Billing");
    fireEvent.change(await screen.findByLabelText("Payment reference for INV-1"), { target: { value: "TRF-2026-001" } });
    await confirmReason("Mark INV-1 paid");
    await screen.findByText("Invoice INV-1 is marked paid.");
    expect(bodyOf(calls, `POST /api/v1/admin/customers/${ORG}/invoices/inv-1/mark-paid`)).toEqual({
      reference: "TRF-2026-001",
      reason: recorded("Mark INV-1 paid"),
      version: 3,
    });
  });

  it("void sends a recorded reason and the invoice version", async () => {
    const calls = mockApi(billingRoutes());
    asStaff(BILLING_STAFF, <CustomerDetailScreen organizationId={ORG} />);
    await openTab("Billing");
    await confirmReason("Void INV-1");
    await screen.findByText("Invoice INV-1 is void.");
    expect(bodyOf(calls, `POST /api/v1/admin/customers/${ORG}/invoices/inv-1/void`)).toEqual({
      reason: recorded("Void INV-1"),
      version: 3,
    });
  });

  it("refund sends integer minor units within what remains, with a recorded reason and a command id", async () => {
    const calls = mockApi(billingRoutes());
    asStaff(BILLING_STAFF, <CustomerDetailScreen organizationId={ORG} />);
    await openTab("Billing");
    const amount = await screen.findByLabelText("Refund amount (INR) on INV-2");
    expect(amount).toHaveValue("800.00"); // 1000.00 paid, 200.00 already refunded
    fireEvent.change(amount, { target: { value: "900" } });
    expect(screen.getByText(/no more than/)).toBeInTheDocument();
    fireEvent.change(amount, { target: { value: "10.50" } });
    await confirmReason("Refund on INV-2");
    await screen.findByText(/Refund of .* requested/);
    const refund = calls.find((c) => c.key === `POST /api/v1/admin/customers/${ORG}/payments/att-1/refund`)!;
    expect(refund.body).toEqual({ amount_minor: 1050, reason: recorded("Refund on INV-2") });
    expect(refund.headers["idempotency-key"]).toBeTruthy();
  });

  it("the network break-glass, under More actions, records a reason without asking for one", async () => {
    const calls = mockApi(billingRoutes());
    asStaff(BILLING_STAFF, <CustomerDetailScreen organizationId={ORG} />);
    fireEvent.click(await screen.findByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Reset network allow-list" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await screen.findByText(/allow-list is off/);
    expect(bodyOf(calls, `POST /api/v1/admin/customers/${ORG}/network-policy/reset`)).toEqual({
      reason: recorded("Reset network allow-list"),
    });
  });

  it("grants a staff override from a popup, recording a reason without asking", async () => {
    const calls = mockApi({
      ...billingRoutes(),
      [`POST /api/v1/admin/customers/${ORG}/entitlement-overrides`]: [{ status: 201, body: {} }],
    });
    asStaff([...BILLING_STAFF, "platform.staff_subscription.manage"], <CustomerDetailScreen organizationId={ORG} />);
    await openTab("Products & access");
    fireEvent.click(await screen.findByRole("button", { name: "Grant override" }));
    const popup = screen.getByRole("dialog", { name: "Grant an override" });
    const grant = within(popup).getByRole("button", { name: "Grant override" });
    expect(grant).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Product code"), { target: { value: "crm" } });
    choose(/^Expires after/, "7 days");
    fireEvent.click(grant);
    await screen.findByText("Override granted; access was recomputed.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const body = bodyOf(calls, `POST /api/v1/admin/customers/${ORG}/entitlement-overrides`);
    expect(body).toMatchObject({ product_code: "crm", reason: recorded("Grant override") });
    expect(Object.keys(body).sort()).toEqual(["expires_at", "product_code", "reason"]);
  });
});

describe("staff access", () => {
  it("lists permissions readably, filters them and revokes from the row menu after confirming", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/security/roles": [{ status: 200, body: { results: [] } }],
      "GET /api/v1/admin/security/grants": [
        {
          status: 200,
          body: {
            results: [
              { id: "g1", email: "pat@example.com", permission: "platform.staff_customer.view", granted_at: T, expires_at: null, reason: "Support rota" },
              { id: "g2", email: "sam@example.com", permission: "platform.staff_audit.view", granted_at: T, expires_at: T, reason: "Quarterly review" },
            ],
          },
        },
      ],
      "POST /api/v1/admin/security/grants/g1/revoke": [{ status: 200, body: {} }],
    });
    asStaff(["platform.staff_security.view", "platform.staff_security.manage"], <SecurityScreen />);
    // Roles and individual permissions are two sections of one page (admin-ux phase 1).
    const table = await screen.findByRole("table", { name: "Active grants" });
    expect(within(table).getByText("Customer · View")).toBeInTheDocument();
    choose(/^Area/, "Audit");
    expect(within(table).queryByText("pat@example.com")).not.toBeInTheDocument();
    choose(/^Area/, "All");
    fireEvent.click(within(table).getByRole("button", { name: "Actions for pat@example.com, platform.staff_customer.view" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Revoke" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(calls.some((c) => c.key.startsWith("POST /api/v1/admin/security/grants/g1"))).toBe(true));
  });

  it("grants a permission from a popup and shows it was granted", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/security/roles": [{ status: 200, body: { results: [] } }],
      "GET /api/v1/admin/security/grants": [{ status: 200, body: { results: [] } }],
      "POST /api/v1/admin/security/grants": [{ status: 201, body: {} }],
    });
    const { container } = asStaff(["platform.staff_security.view", "platform.staff_security.manage"], <SecurityScreen />);
    // Roles first, then the individual permissions, on one page - no tabs.
    const headings = (await screen.findAllByRole("heading", { level: 2 })).map((h) => h.textContent);
    expect(headings.indexOf("Roles")).toBeGreaterThanOrEqual(0);
    expect(headings.indexOf("Roles")).toBeLessThan(headings.indexOf("Individual permissions"));
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Grant a permission" }));
    const popup = screen.getByRole("dialog", { name: "Grant a permission" });
    fireEvent.change(within(popup).getByLabelText(/Staff member.s email/), { target: { value: "pat@example.com" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Grant" }));
    await screen.findByText("Permission granted.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(bodyOf(calls, "POST /api/v1/admin/security/grants")).toEqual({
      email: "pat@example.com",
      permission: "platform.staff_customer.view",
      reason: recorded("Grant permission"),
    });
    await expectNoViolations(container);
  });
});

// --- users ---------------------------------------------------------------------------------

function userDetail(id: string, extra: Record<string, unknown> = {}) {
  return {
    user_id: id,
    email: `${id}@example.com`,
    name: `Person ${id}`,
    status: "active",
    is_staff: false,
    locked: false,
    organizations: 1,
    email_verified: true,
    acquisition_source: "self_signup",
    created_at: T,
    last_sign_in_at: T,
    lifecycle: "customer",
    products: ["books"],
    subscription_status: "active",
    organization_names: ["Acme Ltd"],
    orders: [],
    memberships: [
      {
        organization_id: ORG,
        slug: "acme",
        name: "Acme Ltd",
        is_owner: true,
        access_class: "customer",
        subscription_status: "active",
        products: ["books"],
      },
    ],
    sessions: [
      {
        session_id: "s1",
        signed_in_at: T,
        last_active_at: T,
        expires_at: T,
        ip_prefix: "203.0.113.0/24",
        device_label: "Firefox on Linux",
      },
    ],
    activity: [{ occurred_at: T, kind: "login_succeeded", reason: "", ip_prefix: "203.0.113.0/24", device_label: "Firefox on Linux" }],
    ...extra,
  };
}

describe("users", () => {
  it("offers View, Sign out everywhere and Deactivate in each row's menu, and confirms before deactivating", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/users": [{ status: 200, body: { results: [userDetail("u2")], next_cursor: null, has_more: false } }],
      "POST /api/v1/admin/users/u2/disable": [{ status: 200, body: userDetail("u2", { status: "disabled" }) }],
    });
    asStaff(["platform.staff_user.view", "platform.staff_user.manage"], <Users />);
    const table = await screen.findByRole("table", { name: "User accounts" });
    // Deactivate waits until the session says who is signed in.
    await waitFor(() => {
      fireEvent.click(within(table).getByRole("button", { name: "Actions for Person u2" }));
      expect(screen.getByRole("menuitem", { name: "Deactivate" })).toBeInTheDocument();
    });
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["View", "Sign out everywhere", "Deactivate"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Deactivate" }));
    const ask = screen.getByRole("dialog", { name: "Deactivate Person u2?" });
    fireEvent.click(within(ask).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(bodyOf(calls, "POST /api/v1/admin/users/u2/disable")).toEqual({ reason: recorded("Deactivate") }));
  });

  it("the directory searches on submit and passes axe", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/users": [
        {
          status: 200,
          body: { results: [{ ...userDetail("u2"), is_staff: true }], next_cursor: null, has_more: false },
        },
      ],
    });
    const { container } = asStaff(["platform.staff_user.view"], <Users />);
    const table = await screen.findByRole("table", { name: "User accounts" });
    expect(within(table).getByRole("link", { name: "Person u2" })).toHaveAttribute("href", "/admin/users/u2");
    expect(table).toHaveTextContent("Staff");
    expect(table).toHaveTextContent("Verified");
    // Sentence-case headings, and no Search button: the box searches as you type.
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toContain("Last active");
    expect(screen.queryByRole("button", { name: "Search" })).not.toBeInTheDocument();
    await expectNoViolations(container);
    const before = calls.length;
    fireEvent.change(screen.getByLabelText("Search by name or email"), { target: { value: "per" } });
    fireEvent.change(screen.getByLabelText("Search by name or email"), { target: { value: "pers" } });
    expect(calls.length).toBe(before);
    await waitFor(() => expect(calls.length).toBe(before + 1));
    expect(calls[calls.length - 1].url).toContain("q=pers");
  });

  it("never offers Deactivate on your own account", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/users/u1": [{ status: 200, body: userDetail("u1", { is_staff: true }) }],
    });
    const { container } = asStaff(["platform.staff_user.view", "platform.staff_user.manage"], <UserDetailScreen userId="u1" />);
    await screen.findByText(/your own account/);
    expect(screen.getByRole("button", { name: "Sign out everywhere" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
    expect(screen.getByText("Staff member")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("offers Unlock and Deactivate on someone else's locked account, recording a reason", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/users/u2": [{ status: 200, body: userDetail("u2", { locked: true }) }],
      "POST /api/v1/admin/users/u2/disable": [{ status: 200, body: userDetail("u2", { status: "disabled" }) }],
    });
    asStaff(["platform.staff_user.view", "platform.staff_user.manage"], <UserDetailScreen userId="u2" />);
    expect(await screen.findByRole("button", { name: "Unlock" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Users");
    // No negative chips: "Not staff" and "Not locked" are simply absent.
    expect(screen.queryByText("Not staff")).not.toBeInTheDocument();
    expect(screen.getAllByText("Acme Ltd")[0].closest("li")).toHaveTextContent("Owner");
    fireEvent.click(screen.getByRole("tab", { name: /Sessions/ }));
    expect(screen.getByRole("table", { name: "Signed-in sessions" })).toHaveTextContent("Firefox on Linux");
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    const events = screen.getByRole("table", { name: "Recent account events" });
    expect(events).toHaveTextContent("Login succeeded");
    // A column empty in every row is not drawn.
    expect(within(events).queryByRole("columnheader", { name: /Detail/ })).not.toBeInTheDocument();
    await confirmReason("Deactivate");
    await screen.findByText(/deactivated and signed out everywhere/);
    expect(bodyOf(calls, "POST /api/v1/admin/users/u2/disable")).toEqual({ reason: recorded("Deactivate") });
  });

  it("shows no account actions to view-only staff", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/users/u2": [{ status: 200, body: userDetail("u2", { locked: true }) }],
    });
    asStaff(["platform.staff_user.view"], <UserDetailScreen userId="u2" />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: /Sign out|Unlock|Deactivate/ })).not.toBeInTheDocument();
  });
});

// --- communication ----------------------------------------------------------------------------

describe("users by lifecycle", () => {
  it("filters by segment and shows lifecycle, products and subscription", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/users": [
        { status: 200, body: { results: [userDetail("u2")], next_cursor: null, has_more: false } },
      ],
    });
    const { container } = asStaff(["platform.staff_user.view"], <Users />);
    const table = await screen.findByRole("table", { name: "User accounts" });
    expect(table).toHaveTextContent("Customer");
    expect(table).toHaveTextContent("books");
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Explorers" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("segment=explorers"))).toBe(true));
    expect(screen.getByRole("button", { name: "Explorers" })).toHaveAttribute("aria-pressed", "true");
  });

  it("filters paying customers apart from the free tier", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/users": [
        { status: 200, body: { results: [userDetail("u2")], next_cursor: null, has_more: false } },
      ],
    });
    asStaff(["platform.staff_user.view"], <Users />);
    await screen.findByRole("table", { name: "User accounts" });
    const segments = within(screen.getByRole("group", { name: "Segment" }));
    expect(segments.getAllByRole("button").map((b) => b.textContent?.trim())).toEqual([
      "All users",
      "Subscribed",
      "Free tier",
      "Trial",
      "Explorers",
      "Unverified",
      "Suspended",
      "Deactivated",
    ]);
    fireEvent.click(segments.getByRole("button", { name: "Subscribed" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("segment=customers"))).toBe(true));
    fireEvent.click(segments.getByRole("button", { name: "Free tier" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("segment=free"))).toBe(true));
    expect(segments.getByRole("button", { name: "Free tier" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("users: filters in the address, on the server (admin-ux phase 3)", () => {
  const USERS = "GET /api/v1/admin/users";
  const CATALOG = "GET /api/v1/admin/catalog/products";
  const ONE = { status: 200, body: { results: [userDetail("u2")], next_cursor: null, has_more: false } };
  const sent = (calls: Call[], i = -1) => Object.fromEntries(new URL(calls.filter((c) => c.key === USERS).at(i)!.url, "http://x").searchParams);

  it("sends the segment, product and signed-up days to the server, keeps them in the address and lists them as removable tags", async () => {
    pathname = "/admin/users";
    const calls = mockApi({
      [USERS]: [ONE],
      [CATALOG]: [{ status: 200, body: { results: [{ id: "p1", code: "easy_crm", name: "Easy CRM (easy_crm)" }] } }],
    });
    asStaff(["platform.staff_user.view", "platform.staff_catalog.view"], <Users />);
    const table = await screen.findByRole("table", { name: "User accounts" });
    fireEvent.click(within(screen.getByRole("group", { name: "Segment" })).getByRole("button", { name: "Trial" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/users?segment=trial", { scroll: false });
    await waitFor(() => expect(sent(calls)).toEqual({ segment: "trial" }));
    // The product list is the catalog's: choosing one asks the server.
    await waitFor(() => expect(calls.some((c) => c.key === CATALOG)).toBe(true));
    choose("Product", "Easy CRM");
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/users?segment=trial&product=easy_crm", { scroll: false });
    await waitFor(() => expect(sent(calls)).toEqual({ segment: "trial", product: "easy_crm" }));
    fireEvent.click(screen.getByRole("button", { name: /^Signed up: Any time/ }));
    const panel = screen.getByRole("dialog", { name: "Signed up between" });
    fireEvent.change(within(panel).getByLabelText("From"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(panel).getByLabelText("To"), { target: { value: "2026-11-10" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(sent(calls)).toEqual({ segment: "trial", product: "easy_crm", from: "2026-11-01", to: "2026-11-10" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/users?segment=trial&product=easy_crm&from=2026-11-01&to=2026-11-10", { scroll: false });
    // The server decides who matches: the row it sent stays, whatever it holds or whenever it signed up.
    expect(within(table).getByRole("link", { name: "Person u2" })).toBeInTheDocument();
    const tags = screen.getByRole("list", { name: "Active filters" });
    expect(within(tags).getAllByRole("listitem").map((t) => t.textContent)).toEqual([
      "Product: Easy CRM",
      expect.stringMatching(/^Signed up: .*1.*Nov.*2026.*10.*Nov.*2026$/),
    ]);
    fireEvent.click(within(tags).getByRole("button", { name: "Remove filter Product: Easy CRM" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/users?segment=trial&from=2026-11-01&to=2026-11-10", { scroll: false });
    // Reset filters keeps the segment: it is the list, not a filter on it.
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/users?segment=trial", { scroll: false });
    await waitFor(() => expect(sent(calls)).toEqual({ segment: "trial" }));
  });

  it("reads its filters back from the address (the console's search box, a link to a segment), dropping what it does not know", async () => {
    nav.query = "q=pat&segment=explorers&product=easy_crm&from=2026-11-01&to=2026-11-10";
    let calls = mockApi({ [USERS]: [ONE] });
    const view = asStaff(["platform.staff_user.view"], <Users />);
    await screen.findByRole("table", { name: "User accounts" });
    expect(sent(calls, 0)).toEqual({ q: "pat", segment: "explorers", product: "easy_crm", from: "2026-11-01", to: "2026-11-10" });
    expect(within(screen.getByRole("group", { name: "Segment" })).getByRole("button", { name: "Explorers" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Search by name or email")).toHaveValue("pat");
    expect(screen.getByRole("button", { name: /^Signed up: .*Nov.*2026/ })).toBeInTheDocument();
    view.unmount();

    nav.query = "segment=vip&from=2026-02-30&q=sam";
    calls = mockApi({ [USERS]: [ONE] });
    asStaff(["platform.staff_user.view"], <Users />);
    await screen.findByRole("table", { name: "User accounts" });
    expect(sent(calls, 0)).toEqual({ q: "sam" });
    expect(screen.getByText(/were not recognised and were left out/)).toBeInTheDocument();
  });

  it("offers Try again when the directory cannot be read", async () => {
    const calls = mockApi({ [USERS]: [{ status: 500, body: {} }, ONE] });
    asStaff(["platform.staff_user.view"], <Users />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load users.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "Person u2" })).toBeInTheDocument();
    expect(calls.filter((c) => c.key === USERS)).toHaveLength(2);
  });

  it("asks before Sign out everywhere and Reactivate, in the row's menu in the Actions column", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      [USERS]: [{ status: 200, body: { results: [userDetail("u2"), userDetail("u3", { status: "disabled" })], next_cursor: null, has_more: false } }],
      "POST /api/v1/admin/users/u2/sign-out": [{ status: 200, body: userDetail("u2") }],
      "POST /api/v1/admin/users/u3/enable": [{ status: 200, body: userDetail("u3") }],
    });
    asStaff(["platform.staff_user.view", "platform.staff_user.manage"], <Users />);
    const table = await screen.findByRole("table", { name: "User accounts" });
    expect(within(table).getAllByRole("columnheader").at(-1)).toHaveTextContent("Actions");

    fireEvent.click(within(table).getByRole("button", { name: "Actions for Person u2" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out everywhere" }));
    const signOut = screen.getByRole("dialog", { name: "Sign Person u2 out everywhere?" });
    expect(within(signOut).getByText("Every session of this account ends now.")).toBeInTheDocument();
    expect(calls.some((c) => c.key.startsWith("POST "))).toBe(false);
    fireEvent.click(within(signOut).getByRole("button", { name: "Sign out everywhere" }));
    await waitFor(() => expect(bodyOf(calls, "POST /api/v1/admin/users/u2/sign-out")).toEqual({ reason: recorded("Sign out everywhere") }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(within(table).getByRole("button", { name: "Actions for Person u3" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Reactivate" }));
    const reactivate = screen.getByRole("dialog", { name: "Reactivate Person u3?" });
    expect(calls.some((c) => c.key === "POST /api/v1/admin/users/u3/enable")).toBe(false);
    fireEvent.click(within(reactivate).getByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(bodyOf(calls, "POST /api/v1/admin/users/u3/enable")).toEqual({ reason: recorded("Reactivate") }));
  });
});

describe("staff orders", () => {
  it("lists orders across customers and filters by status", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/orders": [
        {
          status: 200,
          body: {
            results: [
              {
                order_id: "o-1",
                organization_id: ORG,
                organization_name: "Acme Ltd",
                number: "ORD-000007",
                kind: "plan",
                status: "pending_payment",
                description: "CRM Pro x1 (monthly)",
                product_codes: ["crm"],
                currency: "INR",
                amount_due_minor: 99900,
                created_at: T,
                paid_at: null,
              },
            ],
            next_cursor: null,
            has_more: false,
          },
        },
      ],
    });
    const { container } = asStaff(["platform.staff_payment.view"], <StaffOrdersScreen />);
    const table = await screen.findByRole("table", { name: "Orders" });
    expect(within(table).getByRole("link", { name: "Acme Ltd" })).toHaveAttribute("href", `/admin/customers/${ORG}`);
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Failed" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=failed"))).toBe(true));
  });

  it("shows the purchase source and its partner, filters by source on the server, and links an unpaid order to its verification", async () => {
    const order = {
      order_id: "o-2",
      organization_id: ORG,
      organization_name: "Acme Ltd",
      number: "ORD-000008",
      kind: "plan",
      status: "pending_payment",
      description: "CRM Pro x1 (monthly)",
      product_codes: ["crm"],
      currency: "JPY",
      amount_due_minor: 4900,
      created_at: T,
      paid_at: null,
      channel: "partner",
      partner_id: "p-9",
      partner_name: "North Resellers",
    };
    const calls = mockApi({
      "GET /api/v1/admin/orders": [
        { status: 200, body: { results: [order], next_cursor: null, has_more: false } },
        { status: 200, body: { results: [order], next_cursor: null, has_more: false } },
      ],
    });
    asStaff(["platform.staff_payment.view"], <StaffOrdersScreen />);
    const table = await screen.findByRole("table", { name: "Orders" });
    const row = within(table).getByRole("row", { name: /ORD-000008/ });
    expect(row).toHaveTextContent("Partner activation");
    expect(within(row).getByRole("link", { name: "North Resellers" })).toHaveAttribute("href", "/admin/partners/p-9");
    expect(within(row).getByRole("link", { name: "Verify payment" })).toHaveAttribute("href", `/admin/customers/${ORG}?tab=billing#billing-h`);
    choose("Purchase source", "Partner activation");
    await waitFor(() => expect(calls.some((c) => c.url.includes("channel=partner"))).toBe(true));
  });

  // Admin-ux phase 3: the search and the dates are the server's, never a filter over
  // the orders loaded so far, and every filter is kept in the address.
  const ORDER = {
    order_id: "o-3",
    organization_id: ORG,
    organization_name: "Acme Ltd",
    number: "ORD-000009",
    kind: "plan",
    status: "fulfilled",
    description: "CRM Pro x1 (monthly)",
    product_codes: ["crm"],
    currency: "INR",
    amount_due_minor: 99900,
    created_at: T,
    paid_at: T,
  };
  const ORDERS_PAGE: Reply = { status: 200, body: { results: [ORDER], next_cursor: null, has_more: false } };
  const orderParams = (calls: Call[]) =>
    calls.filter((c) => c.key === "GET /api/v1/admin/orders").map((c) => Object.fromEntries(new URL(c.url, "http://x").searchParams));
  const orderTags = () => within(screen.getByRole("list", { name: "Active filters" })).getAllByRole("listitem").map((t) => t.textContent);

  it("sends the search and the created dates to the server, keeps them in the address and lists them as tags", async () => {
    const calls = mockApi({ "GET /api/v1/admin/orders": [ORDERS_PAGE] });
    asStaff(["platform.staff_payment.view"], <StaffOrdersScreen />);
    await screen.findByRole("row", { name: /ORD-000009/ });
    expect(screen.queryByRole("combobox", { name: "Created" })).not.toBeInTheDocument();

    // The server decides what matches: the order it sends stays, whatever the search says.
    fireEvent.change(screen.getByLabelText("Search by order number or customer"), { target: { value: "Globex" } });
    await waitFor(() => expect(orderParams(calls).at(-1)).toEqual({ q: "Globex" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin?q=Globex", { scroll: false });
    expect(await screen.findByRole("row", { name: /ORD-000009/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Created: Any time" }));
    const panel = screen.getByRole("dialog", { name: "Created between" });
    fireEvent.change(within(panel).getByLabelText("From"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(panel).getByLabelText("To"), { target: { value: "2026-10-31" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(orderParams(calls).at(-1)).toEqual({ q: "Globex", from: "2026-10-01", to: "2026-10-31" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin?q=Globex&from=2026-10-01&to=2026-10-31", { scroll: false });

    choose("Purchase source", "Partner storefront");
    await waitFor(() => expect(orderParams(calls).at(-1)).toMatchObject({ channel: "storefront" }));
    const tags = orderTags();
    expect(tags.slice(0, 2)).toEqual(["Search: “Globex”", "Source: Partner storefront"]);
    expect(tags[2]).toMatch(/^Created: .*2026.*–.*2026$/);

    fireEvent.click(screen.getByRole("button", { name: "Remove filter Search: “Globex”" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin?source=storefront&from=2026-10-01&to=2026-10-31", { scroll: false });
    await waitFor(() => expect(orderParams(calls).at(-1)).toEqual({ channel: "storefront", from: "2026-10-01", to: "2026-10-31" }));

    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin", { scroll: false });
    await waitFor(() => expect(orderParams(calls).at(-1)).toEqual({}));
  });

  it("reads its filters from the address, where the command palette's ?q and ?source links point", async () => {
    nav.query = "q=ORD-000009&source=partner&status=fulfilled&from=2026-10-01&to=2026-10-31";
    const calls = mockApi({ "GET /api/v1/admin/orders": [ORDERS_PAGE] });
    asStaff(["platform.staff_payment.view"], <StaffOrdersScreen />);
    await screen.findByRole("row", { name: /ORD-000009/ });
    expect(orderParams(calls)[0]).toEqual({ status: "fulfilled", channel: "partner", q: "ORD-000009", from: "2026-10-01", to: "2026-10-31" });
    expect(screen.getByLabelText("Search by order number or customer")).toHaveValue("ORD-000009");
    expect(screen.getByRole("combobox", { name: "Purchase source" })).toHaveTextContent("Partner activation");
    expect(within(screen.getByRole("group", { name: "Order status" })).getByRole("button", { name: "Fulfilled" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Created: .*2026/ })).toBeInTheDocument();
  });

  it("leaves out a source or a date the list does not know, and says so", async () => {
    nav.query = "source=shop&from=yesterday&q=ORD-1";
    const calls = mockApi({ "GET /api/v1/admin/orders": [ORDERS_PAGE] });
    asStaff(["platform.staff_payment.view"], <StaffOrdersScreen />);
    await screen.findByRole("row", { name: /ORD-000009/ });
    expect(orderParams(calls)[0]).toEqual({ q: "ORD-1" });
    expect(screen.getByRole("alert")).toHaveTextContent("Some filters in this page's address were not recognised and were left out.");
  });

  it("offers Try again when the orders can't be loaded", async () => {
    const calls = mockApi({ "GET /api/v1/admin/orders": [{ status: 500, body: {} }, ORDERS_PAGE] });
    asStaff(["platform.staff_payment.view"], <StaffOrdersScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load orders.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("row", { name: /ORD-000009/ })).toBeInTheDocument();
    expect(orderParams(calls)).toHaveLength(2);
  });
});

describe("platform settings", () => {
  const SETTINGS = {
    signup_enabled: true,
    partner_applications_enabled: true,
    support_email: "help@example.com",
    sales_email: "",
    catalog_currency: "INR",
    public_catalog_currency: "",
    default_currency: "INR",
    currencies: ["INR", "USD"],
    company_name: "",
    company_legal_name: "",
    company_address: "",
    company_tax_id: "",
    tax_rate_bp: null,
    default_tax_rate_bp: 1800,
    notifications_email: "",
    version: 3,
  };

  it("saves with the version it read, and view-only staff cannot edit", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/settings": [{ status: 200, body: SETTINGS }],
      "PUT /api/v1/admin/settings": [{ status: 200, body: { ...SETTINGS, signup_enabled: false, version: 4 } }],
    });
    const { container, unmount } = asStaff(["platform.staff_settings.view", "platform.staff_settings.manage"], <PlatformSettingsScreen />);
    // The settings in force are text in sections; each section is edited in place.
    expect((await screen.findByText("Self-service signup")).nextElementSibling).toHaveTextContent("Open");
    expect(screen.getByText("Support email").nextElementSibling).toHaveTextContent("help@example.com");
    // An empty value says so and offers to add it.
    expect(screen.getByText("Sales email").nextElementSibling).toHaveTextContent("Not set");
    expect(screen.getByRole("button", { name: "Add a sales email" })).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Edit contact emails" }));
    expect(screen.getByLabelText("Support email")).toHaveValue("help@example.com");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Support email")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit signup" }));
    fireEvent.click(screen.getByLabelText(/Self-service signup is open/));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.key === "PUT /api/v1/admin/settings")).toBe(true));
    await waitFor(() => expect(screen.queryByLabelText(/Self-service signup is open/)).not.toBeInTheDocument());
    expect(calls.find((c) => c.key === "PUT /api/v1/admin/settings")?.body).toEqual({
      signup_enabled: false,
      partner_applications_enabled: true,
      support_email: "help@example.com",
      sales_email: "",
      public_catalog_currency: "",
      company_name: "",
      company_legal_name: "",
      company_address: "",
      company_tax_id: "",
      tax_rate_bp: null,
      notifications_email: "",
      partner_legacy_kyc_policy: "allow",
      partner_suspended_commission_policy: "accrue",
      version: 3,
    });
    unmount();
    mockApi({ "GET /api/v1/admin/settings": [{ status: 200, body: SETTINGS }] });
    asStaff(["platform.staff_settings.view"], <PlatformSettingsScreen />);
    expect((await screen.findByText("Self-service signup")).nextElementSibling).toHaveTextContent("Open");
    expect(screen.queryByLabelText(/Self-service signup is open/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Add/ })).not.toBeInTheDocument();
  });
});

describe("staff roles", () => {
  it("shows each role's holders and grants a role without asking a reason", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/security/roles": [
        {
          status: 200,
          body: {
            results: [
              {
                key: "SUPPORT_ADMIN",
                name: "Support Admin",
                description: "Read what a support case needs.",
                permissions: ["platform.staff_support.view"],
                holders: [{ user_id: "u9", email: "sam@example.com" }],
              },
            ],
          },
        },
      ],
      "POST /api/v1/admin/security/roles": [{ status: 201, body: { user_id: "u8", role: "SUPPORT_ADMIN", added: [] } }],
    });
    const { container } = asStaff(["platform.staff_security.view", "platform.staff_security.manage"], <StaffRolesPanel />);
    const table = await screen.findByRole("table", { name: "Roles and who holds them" });
    expect(table).toHaveTextContent("sam@example.com");
    await expectNoViolations(container);
    // "Manage" lists the holders, each removable after a confirmation.
    fireEvent.click(within(table).getByRole("button", { name: "Manage Support Admin" }));
    const holders = screen.getByRole("dialog", { name: "Support Admin" });
    expect(within(holders).getByRole("button", { name: "Remove Support Admin from sam@example.com" })).toBeInTheDocument();
    fireEvent.click(within(holders).getByRole("button", { name: "Done" }));
    fireEvent.click(screen.getByRole("button", { name: "Grant a role" }));
    const popup = screen.getByRole("dialog", { name: "Grant a role" });
    expect(within(popup).getByRole("button", { name: "Grant role" })).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText(/Staff member.s email/), { target: { value: "new@example.com" } });
    expect(screen.queryByLabelText(/^Reason/)).not.toBeInTheDocument();
    fireEvent.click(within(popup).getByRole("button", { name: "Grant role" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/security/roles")).toBe(true));
    expect(await screen.findByText("Role granted.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/admin/security/roles")?.body).toEqual({
      email: "new@example.com",
      role: "SUPPORT_ADMIN",
      reason: recorded("Grant role"),
    });
  });
});

// --- 2026-11-07 design system: the redesigned list and lookup pages ---------------------------

const CUSTOMER_ROW = {
  organization_id: ORG,
  slug: "acme",
  display_name: "Acme Ltd",
  status: "active",
  created_at: T,
  owner_email: "owner@acme.test",
  subscription_status: "active",
  products: ["easy_crm"],
  seat_capacity: 5,
  summary_as_of: T,
  access_class: "customer",
  partner: null,
};
const page = (results: unknown[]) => ({ status: 200, body: { results, next_cursor: null, has_more: false } });

describe("subscriptions and entitlements", () => {
  it("opens on All, not on Active, and filters by status", async () => {
    const calls = mockApi({ "GET /api/v1/admin/customers": [page([CUSTOMER_ROW])] });
    const { container } = asStaff(["platform.staff_customer.view"], <SubscriptionsScreen />);
    await screen.findByRole("table", { name: "Customers" });
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(calls[0].url).not.toContain("subscription=");
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Past due" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("subscription=past_due"))).toBe(true));
  });

  it("names products without their code and says where each holder's access comes from", async () => {
    mockApi({
      "GET /api/v1/admin/catalog/products": [
        { status: 200, body: { results: [{ id: "p1", code: "easy_crm", name: "Easy CRM (easy_crm)", status: "active", licensing_mode: "seat" }] } },
      ],
      "GET /api/v1/admin/customers": [
        page([
          { ...CUSTOMER_ROW, product_source: { kind: "subscription", status: "active", effective_to: null, effective_now: true } },
          {
            ...CUSTOMER_ROW,
            organization_id: "o2",
            display_name: "Beta",
            subscription_status: "",
            product_source: { kind: "trial", status: "trial", effective_to: "2026-11-09T00:00:00Z", effective_now: false },
          },
          {
            ...CUSTOMER_ROW,
            organization_id: "o3",
            display_name: "Gamma",
            subscription_status: "",
            product_source: { kind: "grant", status: "active", effective_to: null, effective_now: true },
          },
        ]),
      ],
    });
    asStaff(["platform.staff_customer.view"], <EntitlementsScreen />);
    const table = await screen.findByRole("table", { name: "Customers holding Easy CRM" });
    expect(screen.getByRole("combobox", { name: "Product" })).toHaveTextContent("Easy CRM");
    expect(screen.getByRole("combobox", { name: "Product" })).not.toHaveTextContent("easy_crm");
    expect(await within(table).findByRole("row", { name: /Acme Ltd/ })).toHaveTextContent("Subscription");
    // 2026-11-10: a product trial is named as one, never as an override.
    expect(within(table).getByRole("row", { name: /Beta/ })).toHaveTextContent("Product trial, ended");
    expect(within(table).getByRole("row", { name: /Beta/ })).not.toHaveTextContent("Override");
    expect(within(table).getByRole("row", { name: /Gamma/ })).toHaveTextContent("Staff override");
  });

  // Admin-ux phase 3: the chosen product is kept in the address, and a list that
  // can't be loaded offers Try again.
  const TWO_PRODUCTS: Reply = {
    status: 200,
    body: {
      results: [
        { id: "p1", code: "easy_crm", name: "Easy CRM (easy_crm)", status: "active", licensing_mode: "seat" },
        { id: "p2", code: "easy_project", name: "Easy Project", status: "active", licensing_mode: "seat" },
      ],
    },
  };
  const holderProducts = (calls: Call[]) =>
    calls.filter((c) => c.key === "GET /api/v1/admin/customers").map((c) => new URL(c.url, "http://x").searchParams.get("product"));

  it("reads the product from the address and writes a chosen one back", async () => {
    nav.query = "product=easy_project";
    const calls = mockApi({ "GET /api/v1/admin/catalog/products": [TWO_PRODUCTS], "GET /api/v1/admin/customers": [page([CUSTOMER_ROW])] });
    asStaff(["platform.staff_customer.view"], <EntitlementsScreen />);
    await screen.findByRole("table", { name: "Customers holding Easy Project" });
    expect(holderProducts(calls)).toEqual(["easy_project"]);
    expect(screen.getByRole("combobox", { name: "Product" })).toHaveTextContent("Easy Project");
    choose("Product", "Easy CRM");
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin?product=easy_crm", { scroll: false });
    await screen.findByRole("table", { name: "Customers holding Easy CRM" });
    await waitFor(() => expect(holderProducts(calls).at(-1)).toBe("easy_crm"));
  });

  it("shows the first product when the address names one the catalog does not list, and says so", async () => {
    nav.query = "product=retired_product";
    const calls = mockApi({ "GET /api/v1/admin/catalog/products": [TWO_PRODUCTS], "GET /api/v1/admin/customers": [page([CUSTOMER_ROW])] });
    asStaff(["platform.staff_customer.view"], <EntitlementsScreen />);
    await screen.findByRole("table", { name: "Customers holding Easy CRM" });
    expect(holderProducts(calls)).toEqual(["easy_crm"]);
    expect(screen.getByRole("alert")).toHaveTextContent("Some filters in this page's address were not recognised and were left out.");
  });

  it("offers Try again when the holders can't be loaded", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/catalog/products": [TWO_PRODUCTS],
      "GET /api/v1/admin/customers": [{ status: 500, body: {} }, page([CUSTOMER_ROW])],
    });
    asStaff(["platform.staff_customer.view"], <EntitlementsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load holders.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("row", { name: /Acme Ltd/ })).toBeInTheDocument();
    expect(holderProducts(calls)).toEqual(["easy_crm", "easy_crm"]);
  });
});

describe("diagnostics", () => {
  it("finds the organization by name and runs the read-only check from a visible button", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/customers": [page([CUSTOMER_ROW])],
      [`POST /api/v1/admin/customers/${ORG}/diagnose`]: [
        {
          status: 200,
          body: {
            organization_id: ORG,
            product_code: "easy_crm",
            can_launch: false,
            blocking_step: "entitlement",
            chain: [
              { step: "organization_active", ok: true, code: "ok" },
              { step: "entitlement", ok: false, code: "no_entitlement" },
            ],
            read_only: true,
          },
        },
      ],
    });
    const { container } = asStaff(["platform.staff_support.view"], <SupportScreen />);
    expect(screen.getByRole("button", { name: "Run diagnostics" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Find an organization"), { target: { value: "acme" } });
    fireEvent.click(await screen.findByRole("button", { name: /Acme Ltd/ }));
    fireEvent.change(screen.getByLabelText("Product code"), { target: { value: "easy_crm" } });
    fireEvent.click(screen.getByRole("button", { name: "Run diagnostics" }));
    expect(await screen.findByText("Blocked at: entitlement.")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Steps checked" })).toHaveTextContent("Organization active");
    expect(bodyOf(calls, `POST /api/v1/admin/customers/${ORG}/diagnose`)).toEqual({ product_code: "easy_crm" });
    await expectNoViolations(container);
  });
});

describe("audit log", () => {
  it("writes actions in words with the key beside them, and filters by area through the server", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/audit": [
        page([
          { id: "a1", occurred_at: T, action: "commercial.invoice.issued", actor_kind: "staff", resource_type: "invoice", resource_id: "inv-1", outcome: "success", reason: "Monthly run" },
          { id: "a2", occurred_at: T, action: "staff.signed_in", actor_kind: "system", resource_type: "", resource_id: "", outcome: "success", reason: "" },
        ]),
      ],
    });
    const { container } = asStaff(["platform.staff_audit.view"], <AuditScreen />);
    const table = await screen.findByRole("table", { name: "Audit entries" });
    expect(within(table).getByText("Invoice issued")).toBeInTheDocument();
    expect(within(table).getByText("commercial.invoice.issued")).toBeInTheDocument();
    expect(within(table).getByText("Staff signed in")).toBeInTheDocument();
    await expectNoViolations(container);
    choose(/^By/, "System");
    expect(within(table).queryByText("Invoice issued")).not.toBeInTheDocument();
    choose(/^Area/, "Partners");
    await waitFor(() => expect(calls.some((c) => c.url.includes("action=partner."))).toBe(true));
  });
});

describe("a customer's partner and product trials (2026-11-10)", () => {
  const PARTNERS = ["platform.staff_customer.view", "platform.staff_partner.view", "platform.staff_partner.manage"];
  const partnerUrl = `/api/v1/admin/customers/${ORG}/partner`;
  const LINK = {
    link_id: "l1",
    partner_id: "p1",
    partner_code: "north",
    partner_name: "North Resellers",
    status: "active",
    started_at: T,
    ended_at: null,
    end_reason: "",
    started_by_name: "Asha Rao",
    ended_by_name: "",
  };

  it("lists product trials apart from subscriptions and names each product's source", async () => {
    mockApi({
      [`GET /api/v1/admin/customers/${ORG}`]: [
        {
          status: 200,
          body: {
            ...CUSTOMER,
            entitlements: { easy_crm: { status: "trial", seats: 3, source_kind: "trial" } },
            product_trials: [
              {
                id: "t1",
                product_code: "easy_crm",
                plan_code: "crm_trial",
                seats: 3,
                status: "active",
                in_effect: false,
                started_at: "2026-11-02T00:00:00Z",
                ends_at: "2026-11-09T00:00:00Z",
                ended_at: null,
              },
            ],
          },
        },
      ],
    });
    asStaff(["platform.staff_customer.view"], <CustomerDetailScreen organizationId={ORG} />);
    await openTab("Products & access");
    const trials = await screen.findByRole("table", { name: "Product trials" });
    // Past its end: ended, even while the record still says active.
    expect(within(trials).getByRole("row", { name: /easy_crm/ })).toHaveTextContent("Ended");
    const held = screen.getByRole("table", { name: "Products this customer holds" });
    expect(within(held).getByRole("row", { name: /easy_crm/ })).toHaveTextContent("Product trial");
  });

  it("assigns a partner from the picker with a reason, and shows the history", async () => {
    const calls = mockApi({
      // The customer's page reloads after a change, and the card with it.
      [`GET /api/v1/admin/customers/${ORG}`]: [{ status: 200, body: CUSTOMER }, { status: 200, body: CUSTOMER }],
      [`GET ${partnerUrl}`]: [
        { status: 200, body: { current: null, history: [], may_manage: true } },
        { status: 200, body: { current: LINK, history: [LINK], may_manage: true } },
        { status: 200, body: { current: LINK, history: [LINK], may_manage: true } },
      ],
      "GET /api/v1/admin/partners/assignable": [{ status: 200, body: { results: [{ id: "p1", code: "north", display_name: "North Resellers" }] } }],
      [`POST ${partnerUrl}`]: [{ status: 200, body: { current: LINK, history: [LINK], may_manage: true } }],
    });
    asStaff(PARTNERS, <CustomerDetailScreen organizationId={ORG} />);
    expect(await screen.findByText("No partner is assigned.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Assign partner" }));
    const dialog = await screen.findByRole("dialog", { name: "Assign partner" });
    await within(dialog).findByRole("option", { name: "North Resellers (north)" });
    fireEvent.change(within(dialog).getByLabelText(/^Partner/), { target: { value: "p1" } });
    fireEvent.change(within(dialog).getByLabelText(/^Reason/), { target: { value: "The customer asked for this reseller." } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Assign partner" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${partnerUrl}`)).toBe(true));
    expect(bodyOf(calls, `POST ${partnerUrl}`)).toEqual({ partner_id: "p1", reason: "The customer asked for this reseller." });
    await waitFor(() => expect(screen.getByRole("table", { name: "Partner assignment history" })).toHaveTextContent("North Resellers"));
    expect(screen.getByRole("table", { name: "Partner assignment history" })).toHaveTextContent("Asha Rao");
  });

  it("offers no assignment to staff who may only view partners", async () => {
    mockApi({
      [`GET /api/v1/admin/customers/${ORG}`]: [{ status: 200, body: CUSTOMER }],
      [`GET ${partnerUrl}`]: [{ status: 200, body: { current: LINK, history: [LINK], may_manage: false } }],
    });
    asStaff(["platform.staff_customer.view", "platform.staff_partner.view"], <CustomerDetailScreen organizationId={ORG} />);
    await screen.findByRole("table", { name: "Partner assignment history" });
    expect(screen.queryByRole("button", { name: /Assign partner|Change partner/ })).not.toBeInTheDocument();
  });
});
