import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { ConsoleAccess } from "@/lib/api/console";
import type { OrgWallets } from "@/lib/api/orgAccount";
import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";
import { OrgAccessContext } from "@/features/console/OrgAccess";
import { BillingProfileScreen } from "@/features/console/BillingProfileScreen";
import { NotificationPrefsScreen } from "@/features/console/NotificationPrefsScreen";
import { WalletLedgerScreen, WalletUsageScreen, WalletsOverviewScreen, entryAmount } from "./WalletScreens";
import { toMinor } from "./WalletSpend";

let search = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => search,
  usePathname: () => "/o/acme/wallets",
  useParams: () => ({ orgSlug: "acme" }),
}));

const API = "/api/v1/orgs/acme";
const VIEW = "platform.billing.view";
const SPEND = "platform.billing.manage";

function access(permissions: string[]): ConsoleAccess {
  return {
    organization: { organization_id: "o1", slug: "acme", display_name: "Acme Ltd", status: "active", write_allowed: true },
    membership: { membership_id: "m1", is_owner: false, roles: [] },
    permissions,
    console: "admin",
  };
}

function renderOrg(ui: React.ReactNode, permissions: string[]) {
  return render(
    <OrgAccessContext.Provider value={{ slug: "acme", access: access(permissions), reload: vi.fn(), memberName: "Manoj Kumar" }}>
      <main>{ui}</main>
    </OrgAccessContext.Provider>,
  );
}

const entry = (id: string, amount: number, type = "top_up", label = "Top-up") => ({
  id,
  entry_type: type,
  entry_type_label: label,
  amount,
  balance_after: 1_840_000_00,
  reason: label,
  source_type: "order",
  source_id: "ORD-1",
  created_at: "2026-11-10T10:00:00Z",
});

function wallets(overrides: Partial<OrgWallets> = {}): OrgWallets {
  return {
    currency: "INR",
    can_spend: true,
    platform: {
      exists: true,
      currency: "INR",
      balance_minor: 1_840_000_00,
      low_balance_threshold: null,
      is_low: false,
      recent: [entry("e1", 10_000_00), entry("e2", -2_500_00, "subscription", "Subscription deduction")],
      other_currencies: [],
    },
    ai_credits: {
      exists: true,
      balance: 482_000,
      low_balance_threshold: 10_000,
      is_low: false,
      included_per_month: 50_000,
      used_this_month: 12_500,
      purchased_total: 500_000,
      resets_at: "2026-12-01T00:00:00Z",
      recent: [],
    },
    consumption: {
      exists: true,
      currency: "INR",
      balance_minor: 6_285_00,
      low_balance_threshold: 10_000_00,
      is_low: true,
      recent: [],
      this_month: [
        { meter: "whatsapp", label: "WhatsApp messages", quantity: 1200, included_units: 1000, charged_units: 200, amount_minor: 16_000 },
        { meter: "sms", label: "SMS messages", quantity: 0, included_units: null, charged_units: 0, amount_minor: 0 },
      ],
    },
    credit_packs: [{ code: "starter", name: "Starter", credits: 10_000, price_minor: 999_00, currency: "INR" }],
    ...overrides,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  search = new URLSearchParams();
});

describe("Wallets & Credits overview", () => {
  it("shows three separate balances, never a combined one, with each wallet's spend action; passes axe", async () => {
    mockApi({ [`GET ${API}/wallets`]: [ok(wallets())] });
    const { container } = renderOrg(<WalletsOverviewScreen />, [VIEW, SPEND]);
    const platform = await screen.findByRole("region", { name: "Platform Wallet" });
    const ai = screen.getByRole("region", { name: "AI Credits" });
    const consumption = screen.getByRole("region", { name: "Consumption Wallet" });
    expect(platform).toHaveTextContent("₹18,40,000.00");
    expect(ai).toHaveTextContent("4,82,000 credits");
    expect(consumption).toHaveTextContent("₹6,285.00");
    // The low Consumption Wallet says so; the others do not.
    expect(within(consumption).getByText("Low balance")).toBeInTheDocument();
    expect(within(platform).queryByText("Low balance")).not.toBeInTheDocument();
    // No figure anywhere is the three added up.
    const page = container.textContent ?? "";
    expect(page).not.toMatch(/18,46,285|18,46,285\.00|total balance/i);
    expect(within(platform).getByRole("button", { name: "Add Money" })).toBeEnabled();
    expect(within(ai).getByRole("button", { name: "Buy Credits" })).toBeEnabled();
    expect(within(consumption).getByRole("button", { name: "Recharge" })).toBeEnabled();
    expect(within(consumption).getByRole("table", { name: "Consumption this month" })).toHaveTextContent("WhatsApp messages");
    expect(within(ai).getByText("50,000 AI tokens")).toBeInTheDocument();
    expect(screen.queryByText(/Contact Organization Admin/)).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("a member who may see but not spend gets Contact Organization Admin instead of spend buttons", async () => {
    mockApi({ [`GET ${API}/wallets`]: [ok(wallets({ can_spend: false }))] });
    renderOrg(<WalletsOverviewScreen />, [VIEW]);
    await screen.findByRole("region", { name: "Platform Wallet" });
    expect(screen.queryByRole("button", { name: "Add Money" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Buy Credits" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recharge" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Contact Organization Admin")).toHaveLength(3);
  });

  it("a member without billing access is told who can, and no wallet is requested", async () => {
    const calls = mockApi({});
    renderOrg(<WalletsOverviewScreen />, []);
    expect(screen.getByText("Contact Organization Admin")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Platform Wallet" })).not.toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("before a billing account exists, points to the Billing Profile and offers no top-up", async () => {
    mockApi({ [`GET ${API}/wallets`]: [ok(wallets({ currency: null }))] });
    renderOrg(<WalletsOverviewScreen />, [VIEW, SPEND]);
    expect(await screen.findByRole("link", { name: "Billing Profile" })).toHaveAttribute("href", "/o/acme/settings/billing/profile");
    expect(screen.getByRole("button", { name: "Add Money" })).toBeDisabled();
  });

  it("Add Money places an order in the billing currency, then pays its invoice", async () => {
    const calls = mockApi({
      [`GET ${API}/wallets`]: [ok(wallets())],
      [`POST ${API}/wallets/top-up`]: [
        {
          status: 201,
          body: { order_id: "o9", number: "ORD-9", invoice_id: "i9", invoice_number: "INV-9", status: "pending_payment" },
        },
      ],
      [`POST ${API}/billing/invoices/i9/pay`]: [ok({ attempt_id: "a9", status: "succeeded", checkout_url: null })],
    });
    renderOrg(<WalletsOverviewScreen />, [VIEW, SPEND]);
    fireEvent.click(await screen.findByRole("button", { name: "Add Money" }));
    const dialog = screen.getByRole("dialog", { name: "Add money to the Platform Wallet" });
    const submit = within(dialog).getByRole("button", { name: /Continue to pay/ });
    expect(submit).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Amount (INR)"), { target: { value: "1,500.50" } });
    expect(within(dialog).getByRole("button", { name: "Continue to pay ₹1,500.50" })).toBeEnabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Continue to pay ₹1,500.50" }));
    expect(await screen.findByText("This invoice has been paid.")).toBeInTheDocument();
    const placed = calls.find((c) => c.key === `POST ${API}/wallets/top-up`);
    expect(placed?.body).toEqual({ wallet_kind: "platform", amount_minor: 150050, currency: "INR" });
    expect(placed?.headers["idempotency-key"]).toBeTruthy();
    expect(calls.some((c) => c.key === `POST ${API}/billing/invoices/i9/pay`)).toBe(true);
  });

  it("a refused order keeps the dialog open with the server's words", async () => {
    mockApi({
      [`GET ${API}/wallets`]: [ok(wallets())],
      [`POST ${API}/wallets/top-up`]: [
        {
          status: 409,
          body: { code: "payment_not_allowed", reason: "order_pending", detail: "Another order is waiting for payment. Pay or cancel it in Orders & Payments first." },
        },
      ],
    });
    renderOrg(<WalletsOverviewScreen />, [VIEW, SPEND]);
    fireEvent.click(await screen.findByRole("button", { name: "Recharge" }));
    const dialog = screen.getByRole("dialog", { name: "Recharge the Consumption Wallet" });
    fireEvent.change(within(dialog).getByLabelText("Amount (INR)"), { target: { value: "200" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /Continue to pay/ }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Another order is waiting for payment");
  });
});

describe("one wallet's page", () => {
  const page = {
    items: [{ ...entry("e1", 10_000_00), kind: "platform", currency: "INR" }],
    total: 1,
    limit: 25,
    offset: 0,
    types: [
      { value: "top_up", label: "Top-up" },
      { value: "subscription", label: "Subscription deduction" },
    ],
  };

  it("lists transactions with reference and status, and filters by type", async () => {
    const calls = mockApi({
      [`GET ${API}/wallets`]: [ok(wallets())],
      [`GET ${API}/wallets/transactions`]: [ok(page)],
      [`GET ${API}/orders`]: [ok({ results: [], next_cursor: null })],
    });
    renderOrg(<WalletLedgerScreen kind="platform" />, [VIEW, SPEND]);
    const table = await screen.findByRole("table", { name: "Platform Wallet transactions" });
    expect(await within(table).findByText("Order ORD-1")).toBeInTheDocument();
    expect(within(table).getByText("+₹10,000.00")).toBeInTheDocument();
    expect(within(table).getByText("Completed")).toBeInTheDocument();
    const first = calls.find((c) => c.key === `GET ${API}/wallets/transactions`);
    expect(first?.url).toContain("kind=platform");
    expect(first?.url).not.toContain("organization");
  });

  it("opens Buy Credits on arrival with ?action=buy (the dashboard's quick action)", async () => {
    search = new URLSearchParams("action=buy");
    mockApi({
      [`GET ${API}/wallets`]: [ok(wallets())],
      [`GET ${API}/wallets/transactions`]: [ok({ ...page, items: [] })],
      [`GET ${API}/orders`]: [ok({ results: [], next_cursor: null })],
    });
    renderOrg(<WalletLedgerScreen kind="ai_credits" />, [VIEW, SPEND]);
    const dialog = await screen.findByRole("dialog", { name: "Buy AI credits" });
    expect(within(dialog).getByRole("radio", { name: /Starter/ })).toBeChecked();
    expect(within(dialog).getByRole("button", { name: "Continue to pay ₹999.00" })).toBeEnabled();
  });

  it("never opens a spend dialog on arrival for a member who may not spend", async () => {
    search = new URLSearchParams("action=top-up");
    mockApi({
      [`GET ${API}/wallets`]: [ok(wallets({ can_spend: false }))],
      [`GET ${API}/wallets/transactions`]: [ok(page)],
      [`GET ${API}/orders`]: [ok({ results: [], next_cursor: null })],
    });
    renderOrg(<WalletLedgerScreen kind="consumption" />, [VIEW]);
    await screen.findByRole("table", { name: "Consumption Wallet transactions" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Contact Organization Admin")).toBeInTheDocument();
  });

  it("says a payment is waiting for a top-up not yet paid", async () => {
    mockApi({
      [`GET ${API}/wallets`]: [ok(wallets())],
      [`GET ${API}/wallets/transactions`]: [ok(page)],
      [`GET ${API}/orders`]: [
        ok({
          results: [
            { order_id: "o1", number: "ORD-5", kind: "wallet", status: "pending_payment", description: "Platform Wallet top-up", amount_due_minor: 500_00, currency: "INR" },
            { order_id: "o2", number: "ORD-6", kind: "wallet", status: "pending_payment", description: "Consumption Wallet top-up", amount_due_minor: 100_00, currency: "INR" },
          ],
          next_cursor: null,
        }),
      ],
    });
    renderOrg(<WalletLedgerScreen kind="platform" />, [VIEW, SPEND]);
    const waiting = await screen.findByText(/A payment is waiting/);
    expect(waiting.closest("[role=alert]")).toHaveTextContent("ORD-5");
    expect(waiting.closest("[role=alert]")).not.toHaveTextContent("ORD-6");
  });
});

describe("usage", () => {
  it("filters by product and service and exports what matches", async () => {
    search = new URLSearchParams("meter=ai_tokens");
    const usage = {
      items: [
        {
          id: "u1",
          product_code: "crm",
          meter: "ai_tokens",
          meter_label: "AI tokens",
          quantity: 2500,
          outcome: "charged",
          outcome_label: "Charged",
          charged_units: 2500,
          amount: 3,
          currency: "",
          occurred_at: "2026-11-10T10:00:00Z",
        },
      ],
      total: 1,
      limit: 25,
      offset: 0,
      products: ["crm"],
      meters: [{ value: "ai_tokens", label: "AI tokens" }],
    };
    const calls = mockApi({
      [`GET ${API}/wallets`]: [ok(wallets())],
      [`GET ${API}/wallets/usage`]: [ok(usage)],
    });
    renderOrg(<WalletUsageScreen />, [VIEW]);
    expect(await screen.findByText("3 credits")).toBeInTheDocument();
    expect(calls.find((c) => c.key === `GET ${API}/wallets/usage`)?.url).toContain("meter=ai_tokens");
    expect(screen.getByRole("table", { name: "Included allowance this month" })).toHaveTextContent("WhatsApp messages");
    const createObjectURL = vi.fn(() => "blob:x");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
  });
});

describe("amounts", () => {
  it("reads typed amounts as minor units, refusing nonsense", () => {
    expect(toMinor("1500")).toBe(150000);
    expect(toMinor("1,500.5")).toBe(150050);
    expect(toMinor("0")).toBeNull();
    expect(toMinor("-5")).toBeNull();
    expect(toMinor("12.345")).toBeNull();
    expect(toMinor("abc")).toBeNull();
  });

  it("writes a movement as its own wallet counts it", () => {
    expect(entryAmount({ amount: -250 }, "ai_credits", "")).toBe("−250 credits");
    expect(entryAmount({ amount: 10_000 }, "platform", "INR")).toBe("+₹100.00");
  });
});

describe("billing profile", () => {
  const profile = {
    id: "b1",
    currency: "INR",
    collection_mode: "automatic",
    billing_email: "accounts@acme.example",
    billing_region: "",
    tax_reference: "09AAACB1234F1Z5", // pragma: allowlist secret
    legal_name: "Acme Enterprises Pvt. Ltd.",
    address_line1: "4th Floor, Tower B",
    address_line2: "",
    city: "Noida",
    state: "Uttar Pradesh",
    postal_code: "201309",
    country: "IN",
    updated_at: "2026-11-10T10:00:00Z",
  };

  it("shows the profile and saves only what changed", async () => {
    const calls = mockApi({
      [`GET ${API}/billing-account`]: [ok({ billing_account: profile })],
      [`PATCH ${API}/billing-account`]: [ok({ billing_account: { ...profile, city: "Greater Noida" } })],
    });
    const { container } = renderOrg(<BillingProfileScreen />, [VIEW, SPEND]);
    expect(await screen.findByText("Acme Enterprises Pvt. Ltd.")).toBeInTheDocument();
    expect(screen.getByText("Paid online")).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Edit billing information" }));
    const dialog = screen.getByRole("dialog", { name: "Edit billing information" });
    expect(within(dialog).getByLabelText("Business name")).toHaveValue("Acme Enterprises Pvt. Ltd.");
    fireEvent.change(within(dialog).getByLabelText("City"), { target: { value: "Greater Noida" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save billing information" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PATCH ${API}/billing-account`)).toBe(true));
    expect(calls.find((c) => c.key === `PATCH ${API}/billing-account`)?.body).toEqual({ city: "Greater Noida" });
  });

  it("is read-only, with Contact Organization Admin, for a member who may not manage billing", async () => {
    mockApi({ [`GET ${API}/billing-account`]: [ok({ billing_account: profile })] });
    renderOrg(<BillingProfileScreen />, [VIEW]);
    expect(await screen.findByText("Acme Enterprises Pvt. Ltd.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit billing information" })).not.toBeInTheDocument();
    expect(screen.getByText("Contact Organization Admin")).toBeInTheDocument();
  });
});

describe("notification preferences", () => {
  it("turns a category off for this member", async () => {
    const prefs = {
      categories: [
        { key: "payment", label: "Payment updates", description: "An order or invoice is waiting for payment.", in_app: true },
        { key: "ticket", label: "Support ticket updates", description: "Support replied.", in_app: true },
      ],
    };
    const calls = mockApi({
      [`GET ${API}/notification-preferences`]: [ok(prefs)],
      [`PUT ${API}/notification-preferences`]: [ok({ categories: [{ ...prefs.categories[0], in_app: false }, prefs.categories[1]] })],
    });
    renderOrg(<NotificationPrefsScreen />, []);
    const payment = await screen.findByRole("switch", { name: "Payment updates" });
    const save = screen.getByRole("button", { name: "Save preferences" });
    expect(save).toBeDisabled();
    fireEvent.click(payment);
    fireEvent.click(save);
    expect(await screen.findByText("Notification preferences saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === `PUT ${API}/notification-preferences`)?.body).toEqual({
      categories: [
        { key: "payment", in_app: false },
        { key: "ticket", in_app: true },
      ],
    });
  });
});
