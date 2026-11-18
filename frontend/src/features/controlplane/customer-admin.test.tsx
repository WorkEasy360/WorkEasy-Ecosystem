import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { CustomerDetailScreen, Customers } from "./AdminScreens";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * What an Admin does for a customer beyond reading it (2026-11-09): create one, see its
 * members and history, assign a plan. Members and history load only when opened, so the
 * customer's page asks for nothing more on its first render.
 */

const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }, query: "", pathname: "/admin/customers" }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(nav.query),
  usePathname: () => nav.pathname,
}));

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.replace.mockReset();
  nav.query = "";
  nav.pathname = "/admin/customers";
  window.location.hash = "";
});

const ORG = "22222222-2222-4222-8222-222222222222";
const LIST = "/api/v1/admin/customers";
const CREATE = "/api/v1/admin/customers/create";
const CUSTOMER = `${LIST}/${ORG}`;
const MEMBERS = `${CUSTOMER}/members`;
const HISTORY = `${CUSTOMER}/assignment-history`;
const ASSIGN = `${CUSTOMER}/assign-plan`;
const CATALOG = "/api/v1/admin/catalog/overview";
const T = "2026-09-01T10:00:00Z";

const VIEW = ["platform.staff_customer.view"];
const MANAGE = [...VIEW, "platform.staff_customer.manage"];
const SUBSCRIPTIONS = [...VIEW, "platform.staff_subscription.manage"];

const NO_ROWS = ok({ results: [], next_cursor: null, has_more: false });

const open = (permissions: string[], ui: React.ReactNode) =>
  render(
    <AdminContextForTest permissions={permissions}>
      <main>{ui}</main>
    </AdminContextForTest>,
  );

const bodyOf = (calls: Call[], key: string) => calls.find((c) => c.key === key)?.body;

describe("a new customer", () => {
  const fill = (popup: HTMLElement, reason = "Signed the enterprise agreement.") => {
    fireEvent.change(within(popup).getByLabelText("Organization name"), { target: { value: " Acme Ltd " } });
    fireEvent.change(within(popup).getByLabelText("Owner's name"), { target: { value: "Asha Rao" } });
    fireEvent.change(within(popup).getByLabelText("Owner's email"), { target: { value: "asha@acme.example" } });
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: reason } });
  };

  it("creates one, says the owner was invited and refreshes; passes axe", async () => {
    const calls = mockApi({
      [`GET ${LIST}`]: [NO_ROWS],
      [`POST ${CREATE}`]: [{ status: 201, body: { organization_id: ORG, slug: "acme-ltd", owner_invited: true } }],
    });
    const { container } = open(MANAGE, <Customers />);
    await screen.findByText("No customers match.");
    fireEvent.click(screen.getByRole("button", { name: "New customer" }));
    const popup = screen.getByRole("dialog", { name: "New customer" });
    await expectNoViolations(popup);
    const create = within(popup).getByRole("button", { name: "Create customer" });
    expect(create).toBeDisabled();
    fill(popup);
    fireEvent.click(create);
    expect(await screen.findByText("Customer created. The owner was emailed a link to set a password.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${CREATE}`)).toEqual({ name: "Acme Ltd", owner_name: "Asha Rao", owner_email: "asha@acme.example", reason: "Signed the enterprise agreement." });
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${LIST}`).length).toBe(2));
    await expectNoViolations(container);
  });

  it("says when the owner already had an account and nobody was invited", async () => {
    mockApi({
      [`GET ${LIST}`]: [NO_ROWS],
      [`POST ${CREATE}`]: [{ status: 201, body: { organization_id: ORG, slug: "acme-ltd", owner_invited: false } }],
    });
    open(MANAGE, <Customers />);
    fireEvent.click(await screen.findByRole("button", { name: "New customer" }));
    const popup = screen.getByRole("dialog", { name: "New customer" });
    fill(popup);
    fireEvent.click(within(popup).getByRole("button", { name: "Create customer" }));
    expect(await screen.findByText(/no invitation was sent/)).toBeInTheDocument();
  });

  it("needs a reason of 10 characters", async () => {
    mockApi({ [`GET ${LIST}`]: [NO_ROWS] });
    open(MANAGE, <Customers />);
    fireEvent.click(await screen.findByRole("button", { name: "New customer" }));
    const popup = screen.getByRole("dialog", { name: "New customer" });
    fill(popup, "too short");
    expect(within(popup).getByRole("button", { name: "Create customer" })).toBeDisabled();
  });

  it("keeps a refusal in the popup", async () => {
    mockApi({
      [`GET ${LIST}`]: [NO_ROWS],
      [`POST ${CREATE}`]: [{ status: 400, body: { detail: "That account cannot own an organization." } }],
    });
    open(MANAGE, <Customers />);
    fireEvent.click(await screen.findByRole("button", { name: "New customer" }));
    const popup = screen.getByRole("dialog", { name: "New customer" });
    fill(popup);
    fireEvent.click(within(popup).getByRole("button", { name: "Create customer" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("That account cannot own an organization.");
  });

  it("is hidden without the manage grant", async () => {
    mockApi({ [`GET ${LIST}`]: [NO_ROWS] });
    open(VIEW, <Customers />);
    await screen.findByText("No customers match.");
    expect(screen.queryByRole("button", { name: "New customer" })).not.toBeInTheDocument();
  });
});

const DETAIL = {
  organization: { id: ORG, display_name: "Acme Ltd", slug: "acme-ltd", status: "active", created_at: T, suspension_reason: "" },
  members: { owner: 1 },
  subscriptions: [],
  entitlements: {},
  entitlement_overrides: [],
  licenses: [],
  partner: null,
  billing: null,
  audit: [],
};

const MEMBER = { membership_id: "m1", user_id: "u1", email: "asha@acme.example", name: "Asha Rao", status: "active", is_owner: true, joined_at: T };
const LINE = {
  item_id: "i1",
  subscription_id: "s1",
  subscription_status: "active",
  product_code: "easy_crm",
  plan_code: "crm_starter",
  bundle_code: "",
  quantity: 3,
  price_reason: "",
  total_amount_minor: 150000,
  currency: "INR",
  interval: "monthly",
  added_at: T,
  removed_at: null,
};

const price = (interval: string, partner_id: string | null = null) => ({ price_id: `p-${interval}`, plan_version_id: "v2", bundle_version_id: null, price_book_id: "b", price_book_code: "b", channel: "direct", partner_id, currency: "INR", interval, model: "flat", amount_minor: 1000, valid_from: T });
const version = (id: string, n: number, extra: Record<string, unknown> = {}) => ({ id, version: n, published_at: T, effective_from: T, effective_to: null, included_seats: 1, max_seats: null, trial_days: 0, features: {}, prices: [], ...extra });
const plan = (code: string, name: string, extra: Record<string, unknown> = {}) => ({
  id: code,
  code,
  name,
  caption: "",
  description: "",
  status: "available",
  is_public: true,
  sort_order: 0,
  billing_type: "paid",
  kind: "standard",
  product: { id: "p1", code: "easy_crm", name: "Easy CRM (easy_crm)" },
  addon_for: null,
  versions: [],
  ...extra,
});

const OVERVIEW = {
  products: [],
  plans: [
    plan("crm_starter", "Starter", { versions: [version("v1", 1, { effective_to: T }), version("v2", 2, { prices: [price("annual"), price("monthly"), price("quarterly", "someone")] })] }),
    plan("crm_free", "Free", { versions: [version("v3", 1)] }),
    plan("crm_private", "Private", { is_public: false, versions: [version("v4", 1)] }),
    plan("crm_retired", "Retired", { status: "retired", versions: [version("v5", 1)] }),
    plan("crm_unpublished", "Unpublished", { versions: [version("v6", 1, { published_at: null })] }),
  ],
  bundles: [],
  price_books: [],
  currencies: ["INR"],
  intervals: ["monthly", "quarterly", "semi_annual", "annual"],
  billing_types: ["paid"],
};

function detailRoutes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return { [`GET ${CUSTOMER}`]: [ok(DETAIL)], ...extra };
}

async function openDetail(permissions: string[]) {
  const view = open(permissions, <CustomerDetailScreen organizationId={ORG} />);
  await screen.findByRole("heading", { level: 1, name: "Acme Ltd" });
  return view;
}

/** Opens one of the customer page's tabs (admin-ux phase 2). */
const openTab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));

describe("a customer's page in tabs (admin-ux phase 2)", () => {
  const MANAGER = [...VIEW, "platform.staff_customer.manage", "platform.staff_subscription.manage"];
  const PARTNER = { partner_id: "p1", display_name: "North Resellers", link_status: "active" };

  it("has the brief's six tabs, opens on Overview and keeps the chosen tab in the address", async () => {
    nav.pathname = `/admin/customers/${ORG}`;
    mockApi(detailRoutes());
    const { container } = await openDetail(VIEW);
    const tabs = screen.getByRole("tablist", { name: "Customer sections" });
    expect(within(tabs).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Overview",
      "Products & access",
      "Billing",
      "Members",
      "Activity",
      "Troubleshoot",
    ]);
    expect(within(tabs).getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Subscription" })).toBeInTheDocument();
    // One tab at a time: the audit trail and the check are on their own tabs.
    expect(screen.queryByRole("heading", { name: "Recent audit trail" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Why can't someone launch a product?" })).not.toBeInTheDocument();
    await expectNoViolations(container);
    openTab("Activity");
    expect(screen.getByRole("heading", { name: "Recent audit trail" })).toBeInTheDocument();
    expect(nav.router.replace).toHaveBeenLastCalledWith(`/admin/customers/${ORG}?tab=activity`, { scroll: false });
    openTab("Troubleshoot");
    expect(screen.getByRole("heading", { name: "Why can't someone launch a product?" })).toBeInTheDocument();
    openTab("Overview");
    expect(nav.router.replace).toHaveBeenLastCalledWith(`/admin/customers/${ORG}`, { scroll: false });
  });

  it("opens the tab the address names, and Billing for an old #billing-h link", async () => {
    nav.query = "tab=troubleshoot";
    mockApi(detailRoutes());
    const first = await openDetail(VIEW);
    expect(screen.getByRole("tab", { name: "Troubleshoot" })).toHaveAttribute("aria-selected", "true");
    first.unmount();

    nav.query = "tab=nonsense";
    window.location.hash = "#billing-h";
    mockApi(detailRoutes({ [`GET ${HISTORY}`]: [ok({ results: [] })] }));
    await openDetail(VIEW);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Billing" })).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("heading", { name: "Billing account" })).toBeInTheDocument();
  });

  it("puts Assign plan first in the header and the organization's actions under More actions", async () => {
    mockApi(detailRoutes({ [`POST ${CUSTOMER}/suspend`]: [ok({})] }));
    await openDetail(MANAGER);
    const header = screen.getByRole("heading", { level: 1, name: "Acme Ltd" }).closest("header") as HTMLElement;
    expect(within(header).getByRole("button", { name: "Assign plan" }).className).toMatch(/primary/);
    // No longer buttons on the page.
    expect(screen.queryByRole("button", { name: "Suspend organization" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset network allow-list" })).not.toBeInTheDocument();
    fireEvent.click(within(header).getByRole("button", { name: "More actions" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Reset network allow-list", "Suspend organization"]);
    // Suspend keeps its confirmation and its optional reason.
    fireEvent.click(screen.getByRole("menuitem", { name: "Suspend organization" }));
    const ask = screen.getByRole("dialog", { name: "Suspend organization?" });
    expect(within(ask).getByLabelText(/^Reason \(optional/)).toHaveValue("");
    expect(within(ask).getByText("Every member loses access to the organization's products until it is reactivated.")).toBeInTheDocument();
  });

  it("offers no header actions to view-only staff", async () => {
    mockApi(detailRoutes());
    await openDetail(VIEW);
    expect(screen.queryByRole("button", { name: "Assign plan" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More actions" })).not.toBeInTheDocument();
  });

  it("offers Reactivate, not Suspend, for a suspended organization", async () => {
    mockApi(detailRoutes({ [`GET ${CUSTOMER}`]: [ok({ ...DETAIL, organization: { ...DETAIL.organization, status: "suspended" } })] }));
    await openDetail(MANAGER);
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Reset network allow-list", "Reactivate organization"]);
  });

  it("names the partner once: in its own card for those who may see partners, a line otherwise", async () => {
    mockApi(detailRoutes({ [`GET ${CUSTOMER}`]: [ok({ ...DETAIL, partner: PARTNER })] }));
    const first = await openDetail(VIEW);
    expect(screen.getAllByRole("link", { name: "North Resellers" })).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Members and partner" })).toBeInTheDocument();
    first.unmount();

    mockApi(
      detailRoutes({
        [`GET ${CUSTOMER}`]: [ok({ ...DETAIL, partner: PARTNER })],
        [`GET ${CUSTOMER}/partner`]: [ok({ current: null, history: [], may_manage: false })],
      }),
    );
    await openDetail([...VIEW, "platform.staff_partner.view"]);
    expect(await screen.findByRole("heading", { name: "Partner" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Members" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "North Resellers" })).not.toBeInTheDocument();
    expect(screen.getByText("1 owner")).toBeInTheDocument();
  });
});

describe("a customer's members and history", () => {
  it("are not asked for until their tab is opened", async () => {
    const calls = mockApi(detailRoutes());
    await openDetail(VIEW);
    expect(calls.map((c) => c.key)).toEqual([`GET ${CUSTOMER}`]);
  });

  it("lists the members on the Members tab, and passes axe", async () => {
    mockApi(detailRoutes({ [`GET ${MEMBERS}`]: [ok({ results: [MEMBER, { ...MEMBER, membership_id: "m2", email: "sam@acme.example", name: "", status: "suspended", is_owner: false }] })] }));
    const { container } = await openDetail(VIEW);
    openTab("Members");
    // The tab was the asking: no Show button.
    expect(screen.queryByRole("button", { name: "Show members" })).not.toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Members of this customer" });
    const owner = within(table).getByRole("row", { name: /Asha Rao/ });
    expect(owner).toHaveTextContent("asha@acme.example");
    expect(owner).toHaveTextContent("Active");
    expect(owner).toHaveTextContent("Owner");
    expect(within(table).getAllByRole("row", { name: /sam@acme.example/ })[0]).toHaveTextContent("Suspended");
    await expectNoViolations(container);
    openTab("Overview");
    expect(screen.queryByRole("table", { name: "Members of this customer" })).not.toBeInTheDocument();
  });

  it("has an empty and an error state for members", async () => {
    mockApi(detailRoutes({ [`GET ${MEMBERS}`]: [ok({ results: [] })] }));
    const { unmount } = await openDetail(VIEW);
    openTab("Members");
    expect(await screen.findByText("No members")).toBeInTheDocument();
    unmount();

    mockApi(detailRoutes({ [`GET ${MEMBERS}`]: [{ status: 500, body: { detail: "Members are unavailable." } }] }));
    await openDetail(VIEW);
    openTab("Members");
    expect(await screen.findByRole("alert")).toHaveTextContent("Members are unavailable.");
  });

  it("lists the assignment history on the Billing tab", async () => {
    mockApi(detailRoutes({ [`GET ${HISTORY}`]: [ok({ results: [LINE, { ...LINE, item_id: "i2", plan_code: "crm_old", removed_at: "2026-10-01T10:00:00Z" }] })] }));
    await openDetail(VIEW);
    openTab("Billing");
    const table = await screen.findByRole("table", { name: "Assignment history" });
    const line = within(table).getAllByRole("row")[1];
    expect(line).toHaveTextContent("Easy CRM");
    expect(line).toHaveTextContent("CRM starter");
    expect(line).toHaveTextContent("3");
    expect(line).toHaveTextContent("₹1,500.00");
    expect(line).toHaveTextContent("Monthly (30 days)");
    expect(within(table).getByRole("row", { name: /CRM old/ })).toHaveTextContent("1 Oct 2026");
  });

  it("has an empty and an error state for history", async () => {
    mockApi(detailRoutes({ [`GET ${HISTORY}`]: [ok({ results: [] })] }));
    const { unmount } = await openDetail(VIEW);
    openTab("Billing");
    expect(await screen.findByText("No plan has been assigned")).toBeInTheDocument();
    unmount();

    mockApi(detailRoutes({ [`GET ${HISTORY}`]: [{ status: 403, body: {} }] }));
    await openDetail(VIEW);
    openTab("Billing");
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to this customer's assignment history.");
  });
});

describe("assigning a plan", () => {
  const openPopup = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Assign plan" }));
    const popup = screen.getByRole("dialog", { name: "Assign plan" });
    await within(popup).findByRole("combobox", { name: /^Plan/ });
    return popup;
  };
  const placed = (requires_payment: boolean) => ({ status: 201, body: { order_id: "o1", number: "ORD-7", status: "pending_payment", requires_payment } });

  it("loads the catalog only when opened and offers only what is on sale, at its version in force", async () => {
    const calls = mockApi(detailRoutes({ [`GET ${CATALOG}`]: [ok(OVERVIEW)] }));
    await openDetail(SUBSCRIPTIONS);
    expect(calls.some((c) => c.key === `GET ${CATALOG}`)).toBe(false);
    const popup = await openPopup();
    await expectNoViolations(popup);
    fireEvent.click(within(popup).getByRole("combobox", { name: /^Plan/ }));
    expect(within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Easy CRM: Starter", "Easy CRM: Free"]);
  });

  it("sends the billing currency only when one is chosen", async () => {
    const calls = mockApi(detailRoutes({ [`GET ${CATALOG}`]: [ok(OVERVIEW)], [`POST ${ASSIGN}`]: [placed(true)] }));
    await openDetail(SUBSCRIPTIONS);
    const popup = await openPopup();
    fireEvent.change(within(popup).getByLabelText("Billing currency"), { target: { value: "GBP" } });
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "Agreed on the renewal call." } });
    fireEvent.click(within(popup).getByRole("button", { name: "Assign plan" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ASSIGN}`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${ASSIGN}`)!.body).toMatchObject({ currency: "GBP" });
  });

  it("sends the exact body and a command id, and says the invoice waits for payment", async () => {
    const calls = mockApi(detailRoutes({ [`GET ${CATALOG}`]: [ok(OVERVIEW)], [`POST ${ASSIGN}`]: [placed(true)] }));
    await openDetail(SUBSCRIPTIONS);
    const popup = await openPopup();
    // Starter has direct prices for monthly and annual; the partner's quarterly price is not offered.
    fireEvent.click(within(popup).getByRole("combobox", { name: /^Interval/ }));
    expect(within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Monthly (30 days)", "Annual (360 days)"]);
    fireEvent.click(screen.getByRole("option", { name: "Annual (360 days)" }));
    fireEvent.change(within(popup).getByLabelText("Quantity"), { target: { value: "4" } });
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "Agreed on the renewal call." } });
    fireEvent.click(within(popup).getByRole("button", { name: "Assign plan" }));
    expect(await screen.findByText("Order ORD-7 placed. Its invoice now waits for payment.")).toBeInTheDocument();
    const call = calls.find((c) => c.key === `POST ${ASSIGN}`)!;
    expect(call.body).toEqual({ plan_version_id: "v2", billing_interval: "annual", quantity: 4, reason: "Agreed on the renewal call." });
    expect(call.headers["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/);
    // The customer's page reads again.
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${CUSTOMER}`).length).toBe(2));
  });

  it("says when nothing waits for payment, and refreshes an open history", async () => {
    const calls = mockApi(detailRoutes({ [`GET ${CATALOG}`]: [ok(OVERVIEW)], [`POST ${ASSIGN}`]: [placed(false)], [`GET ${HISTORY}`]: [ok({ results: [] })] }));
    await openDetail(SUBSCRIPTIONS);
    openTab("Billing");
    await screen.findByText("No plan has been assigned");
    const popup = await openPopup();
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "Agreed on the renewal call." } });
    fireEvent.click(within(popup).getByRole("button", { name: "Assign plan" }));
    expect(await screen.findByText("Order ORD-7 placed. Nothing waits for payment.")).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${HISTORY}`).length).toBe(2));
  });

  it("makes one command id per opening: a retry reuses it, opening again does not", async () => {
    const calls = mockApi(
      detailRoutes({
        [`GET ${CATALOG}`]: [ok(OVERVIEW)],
        [`POST ${ASSIGN}`]: [{ status: 503, body: { detail: "Try again." } }, placed(true)],
      }),
    );
    await openDetail(SUBSCRIPTIONS);
    const popup = await openPopup();
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "Agreed on the renewal call." } });
    fireEvent.click(within(popup).getByRole("button", { name: "Assign plan" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("Try again.");
    fireEvent.click(within(popup).getByRole("button", { name: "Assign plan" }));
    await screen.findByText("Order ORD-7 placed. Its invoice now waits for payment.");

    const again = await openPopup();
    fireEvent.change(within(again).getByLabelText("Reason"), { target: { value: "A second order, on purpose." } });
    fireEvent.click(within(again).getByRole("button", { name: "Assign plan" }));
    await waitFor(() => expect(calls.filter((c) => c.key === `POST ${ASSIGN}`).length).toBe(3));
    const [first, retry, second] = calls.filter((c) => c.key === `POST ${ASSIGN}`).map((c) => c.headers["idempotency-key"]);
    expect(retry).toBe(first);
    expect(second).not.toBe(first);
  });

  it("validates quantity and reason before sending", async () => {
    mockApi(detailRoutes({ [`GET ${CATALOG}`]: [ok(OVERVIEW)] }));
    await openDetail(SUBSCRIPTIONS);
    const popup = await openPopup();
    const assign = within(popup).getByRole("button", { name: "Assign plan" });
    expect(assign).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "Agreed on the renewal call." } });
    expect(assign).toBeEnabled();
    fireEvent.change(within(popup).getByLabelText("Quantity"), { target: { value: "0" } });
    expect(assign).toBeDisabled();
    expect(within(popup).getByText(/Enter a whole number from 1 to/)).toBeInTheDocument();
  });

  it("keeps a refusal in the popup, in the server's words", async () => {
    mockApi(detailRoutes({ [`GET ${CATALOG}`]: [ok(OVERVIEW)], [`POST ${ASSIGN}`]: [{ status: 409, body: { detail: "Set up billing (currency and billing email) before buying." } }] }));
    await openDetail(SUBSCRIPTIONS);
    const popup = await openPopup();
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "Agreed on the renewal call." } });
    fireEvent.click(within(popup).getByRole("button", { name: "Assign plan" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("Set up billing (currency and billing email) before buying.");
  });

  it("says when the catalog can't be read or nothing is on sale", async () => {
    mockApi(detailRoutes({ [`GET ${CATALOG}`]: [{ status: 403, body: {} }] }));
    const { unmount } = await openDetail(SUBSCRIPTIONS);
    fireEvent.click(screen.getByRole("button", { name: "Assign plan" }));
    const popup = screen.getByRole("dialog", { name: "Assign plan" });
    expect(await within(popup).findByRole("alert")).toHaveTextContent("You don't have access to the catalog.");
    expect(within(popup).getByRole("button", { name: "Assign plan" })).toBeDisabled();
    unmount();

    mockApi(detailRoutes({ [`GET ${CATALOG}`]: [ok({ ...OVERVIEW, plans: [] })] }));
    await openDetail(SUBSCRIPTIONS);
    fireEvent.click(screen.getByRole("button", { name: "Assign plan" }));
    expect(await screen.findByText("No plan is on sale")).toBeInTheDocument();
  });

  it("is hidden without the subscription grant", async () => {
    mockApi(detailRoutes());
    await openDetail(MANAGE);
    expect(screen.queryByRole("button", { name: "Assign plan" })).not.toBeInTheDocument();
  });
});
