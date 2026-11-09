import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import WalletsPage from "@/app/admin/wallets/page";
import TransactionsPage from "@/app/admin/wallets/transactions/page";
import { AiCreditsScreen, ConsumptionWalletScreen, PlatformWalletScreen, TransactionControlsScreen } from "./AdminWallets";
import { CustomerBilling } from "./AdminBilling";
import { ToastProvider } from "./kit";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call } from "./testSupport";

/**
 * Wallets & Credits (2026-11-09): exactly three balances, each movement with its own
 * command id, the server's refusal shown where it was asked for, and nothing a partner
 * earns ever shown as a balance.
 */

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/admin/wallets",
}));

const VIEW = ["platform.staff_wallet.view"];
const MANAGE = [...VIEW, "platform.staff_wallet.manage"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const PLATFORM = "/api/v1/admin/wallets";
const ENTRIES = "/api/v1/admin/wallets/entries";
const EVENTS = "/api/v1/admin/wallets/events";
const RECONCILE = "/api/v1/admin/wallets/reconcile";

const wallet = (over: Record<string, unknown> = {}) => ({
  id: "w1",
  kind: "platform",
  kind_label: "Platform Wallet",
  owner_type: "organization",
  owner_id: "o1",
  owner_name: "Acme Logistics",
  currency: "INR",
  balance: 125050,
  low_balance_threshold: 200000,
  is_low: true,
  updated_at: "2026-11-09T01:00:00Z",
  ...over,
});

const partnerWallet = wallet({
  id: "w2",
  owner_type: "partner",
  owner_id: "p1",
  owner_name: "BrightMart Partners",
  currency: "USD",
  balance: 5000,
  low_balance_threshold: null,
  is_low: false,
});

const aiWallet = wallet({
  id: "w3",
  kind: "ai_credits",
  kind_label: "AI Credits",
  currency: "",
  balance: 4200,
  low_balance_threshold: null,
  is_low: false,
});

const entry = (over: Record<string, unknown> = {}) => ({
  id: "e1111111-0000-0000-0000-000000000000",
  wallet_id: "w1",
  type: "top_up",
  type_label: "Top-up",
  amount: 50000,
  balance_after: 125050,
  reason: "Bank transfer received",
  source_type: "offline_payment",
  source_id: "NEFT-1",
  reverses: null,
  actor_user_id: "u1",
  created_at: "2026-11-09T01:00:00Z",
  wallet_kind: "platform",
  owner_type: "organization",
  owner_id: "o1",
  owner_name: "Acme Logistics",
  currency: "INR",
  ...over,
});

const reversal = entry({
  id: "e2222222-0000-0000-0000-000000000000",
  type: "reversal",
  type_label: "Reversal",
  amount: -20000,
  balance_after: 105050,
  reason: "Entered twice by mistake",
  source_type: "manual",
  source_id: "",
  reverses: "e1111111-0000-0000-0000-000000000000",
});

const event = (over: Record<string, unknown> = {}) => ({
  id: "ev1",
  organization_id: "o1",
  organization_name: "Acme Logistics",
  product_code: "easy_crm",
  meter: "sms",
  meter_label: "SMS messages",
  quantity: 12,
  occurred_at: "2026-11-09T00:59:00Z",
  received_at: "2026-11-09T01:00:00Z",
  outcome: "failed",
  failure_code: "insufficient_balance",
  charged_units: 0,
  amount: 0,
  currency: "INR",
  entry_id: null,
  duplicate_count: 2,
  last_duplicate_at: "2026-11-09T01:01:00Z",
  reviewed_at: null,
  review_note: "",
  ...over,
});

const page = (results: unknown[], next: string | null = null) => ok({ results, next_cursor: next, has_more: next !== null });

const asStaff = (permissions: string[], ui: React.ReactElement) =>
  render(
    <AdminContextForTest permissions={permissions}>
      <ToastProvider>{ui}</ToastProvider>
    </AdminContextForTest>,
  );

const requests = (calls: Call[], key: string) => calls.filter((c) => c.key === key);

const choose = (name: RegExp | string, option: string) => {
  fireEvent.click(screen.getByRole("combobox", { name }));
  fireEvent.click(screen.getByRole("option", { name: option }));
};

const typeInto = (name: RegExp | string, value: string) => fireEvent.change(screen.getByRole("textbox", { name }), { target: { value } });

const submit = (name: string) => fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name }));

/** Opens a row's history and actions, returning once the ledger has loaded. */
async function openWallet(owner = "Acme Logistics") {
  fireEvent.click(await screen.findByRole("button", { name: `History and actions for ${owner}'s wallet` }));
  await screen.findByRole("dialog");
}

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
});

describe("the wallet lists", () => {
  it("shows real rows with links to their owners, money, the Low badge and the threshold; passes axe", async () => {
    const calls = mockApi({ [`GET ${PLATFORM}`]: [page([wallet(), partnerWallet])] });
    const { container } = asStaff(MANAGE, <PlatformWalletScreen />);

    expect(await screen.findByRole("link", { name: "Acme Logistics" })).toHaveAttribute("href", "/admin/customers/o1");
    expect(screen.getByRole("link", { name: "BrightMart Partners" })).toHaveAttribute("href", "/admin/partners/p1");
    expect(screen.getByRole("heading", { level: 1, name: "Platform Wallet" })).toBeInTheDocument();
    expect(screen.getByText("₹1,250.50")).toBeInTheDocument();
    expect(screen.getByText("₹2,000.00")).toBeInTheDocument();
    expect(screen.getByText("Customer")).toBeInTheDocument();
    expect(screen.getByText("Partner")).toBeInTheDocument();
    expect(screen.getAllByText("Low")).toHaveLength(1);
    expect(new URL(calls[0].url, "http://x").searchParams.get("kind")).toBe("platform");
    await expectNoViolations(container);
  });

  it("reads low=1 from the address, and the toggle turns it off", async () => {
    query = "low=1";
    const calls = mockApi({ [`GET ${PLATFORM}`]: [page([wallet()])] });
    asStaff(VIEW, <WalletsPage />);

    await screen.findByRole("link", { name: "Acme Logistics" });
    expect(new URL(calls[0].url, "http://x").searchParams.get("low")).toBe("1");
    const toggle = screen.getByRole("checkbox", { name: "Low balance only" });
    expect(toggle).toBeChecked();

    fireEvent.click(toggle);
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(new URL(calls[1].url, "http://x").searchParams.get("low")).toBeNull();
  });

  it("filters by owner type and searches by owner name on the server", async () => {
    const calls = mockApi({ [`GET ${PLATFORM}`]: [page([wallet()])] });
    asStaff(VIEW, <PlatformWalletScreen />);
    await screen.findByRole("link", { name: "Acme Logistics" });

    choose("Owner type", "Partners");
    await waitFor(() => expect(new URL(calls.at(-1)!.url, "http://x").searchParams.get("owner_type")).toBe("partner"));

    const search = screen.getByRole("searchbox", { name: "Search by owner name" });
    fireEvent.change(search, { target: { value: "Acme" } });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(new URL(calls.at(-1)!.url, "http://x").searchParams.get("q")).toBe("Acme"));
  });

  it("pages with the server's cursor", async () => {
    const calls = mockApi({ [`GET ${PLATFORM}`]: [page([wallet()], "next-1"), page([partnerWallet])] });
    asStaff(VIEW, <PlatformWalletScreen />);
    await screen.findByRole("link", { name: "Acme Logistics" });

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("link", { name: "BrightMart Partners" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Acme Logistics" })).toBeInTheDocument();
    expect(new URL(calls[1].url, "http://x").searchParams.get("cursor")).toBe("next-1");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("says when there are no wallets, and offers to open one only to a manager", async () => {
    mockApi({ [`GET ${PLATFORM}`]: [page([])] });
    const first = asStaff(MANAGE, <PlatformWalletScreen />);
    expect(await screen.findByText("No Platform Wallet wallets yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open the first wallet" })).toBeInTheDocument();
    first.unmount();

    asStaff(VIEW, <PlatformWalletScreen />);
    expect(await screen.findByText("No Platform Wallet wallets yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open the first wallet" })).not.toBeInTheDocument();
  });

  it("says when the list could not be loaded, or is not allowed", async () => {
    mockApi({ [`GET ${PLATFORM}`]: [{ status: 500, body: {} }] });
    const first = asStaff(VIEW, <PlatformWalletScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load these wallets.");
    first.unmount();

    mockApi({ [`GET ${PLATFORM}`]: [{ status: 403, body: { detail: "No." } }] });
    asStaff(VIEW, <PlatformWalletScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to these wallets.");
  });

  it("writes AI Credits as whole credits with no currency, and links to pack purchases and usage events", async () => {
    const calls = mockApi({
      [`GET ${PLATFORM}`]: [page([aiWallet])],
      "GET /api/v1/admin/wallets/w3": [ok(aiWallet)],
      [`GET ${ENTRIES}`]: [page([entry({ wallet_id: "w3", wallet_kind: "ai_credits", currency: "", amount: 500, balance_after: 4200, type_label: "Credit pack purchase" })])],
    });
    const { container } = asStaff(MANAGE, <AiCreditsScreen />);

    expect(await screen.findByText("4,200 credits")).toBeInTheDocument();
    expect(new URL(calls[0].url, "http://x").searchParams.get("kind")).toBe("ai_credits");
    expect(screen.queryByRole("columnheader", { name: /Currency/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pack purchases" })).toHaveAttribute("href", "/admin/wallets/transactions?kind=ai_credits&type=credit_purchase");
    expect(screen.getByRole("link", { name: "Usage events" })).toHaveAttribute("href", "/admin/wallets/transactions?tab=events&meter=ai_tokens");
    await expectNoViolations(container);

    await openWallet();
    const box = screen.getByRole("dialog");
    expect(await within(box).findByText("+500 credits")).toBeInTheDocument();
    // Never an offline top-up for credits.
    expect(within(box).queryByRole("button", { name: "Record offline top-up" })).not.toBeInTheDocument();
    expect(within(box).getByRole("button", { name: "Manual adjustment" })).toBeInTheDocument();
    expect(within(box).getByText(/come from credit packs/)).toBeInTheDocument();
  });

  it("lists the Consumption Wallet by its own kind", async () => {
    const calls = mockApi({ [`GET ${PLATFORM}`]: [page([wallet({ kind: "consumption", kind_label: "Consumption Wallet" })])] });
    asStaff(VIEW, <ConsumptionWalletScreen />);
    await screen.findByRole("link", { name: "Acme Logistics" });
    expect(screen.getByRole("heading", { level: 1, name: "Consumption Wallet" })).toBeInTheDocument();
    expect(new URL(calls[0].url, "http://x").searchParams.get("kind")).toBe("consumption");
    // Only the Platform Wallet is held by partners.
    expect(screen.queryByRole("combobox", { name: "Owner type" })).not.toBeInTheDocument();
  });

  it("hides every action from a staff member who may only look", async () => {
    mockApi({
      [`GET ${PLATFORM}`]: [page([wallet()])],
      "GET /api/v1/admin/wallets/w1": [ok(wallet())],
      [`GET ${ENTRIES}`]: [page([entry()])],
    });
    asStaff(VIEW, <PlatformWalletScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "History for Acme Logistics's wallet" }));
    const box = await screen.findByRole("dialog");
    expect(await within(box).findByText("Bank transfer received")).toBeInTheDocument();
    for (const name of ["Record offline top-up", "Manual adjustment", "Set low-balance threshold"]) {
      expect(within(box).queryByRole("button", { name })).not.toBeInTheDocument();
    }
    fireEvent.click(within(box).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("button", { name: "Open wallet" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New top-up order" })).not.toBeInTheDocument();
  });
});

describe("commands on one wallet", () => {
  const walletApi = (extra: Record<string, { status: number; body?: unknown }[]> = {}) =>
    mockApi({
      [`GET ${PLATFORM}`]: [page([wallet()])],
      "GET /api/v1/admin/wallets/w1": [ok(wallet())],
      [`GET ${ENTRIES}`]: [page([entry()])],
      ...extra,
    });

  it("shows the wallet's own history, asked for by wallet id", async () => {
    const calls = walletApi();
    asStaff(MANAGE, <PlatformWalletScreen />);
    await openWallet();
    expect(await within(screen.getByRole("dialog")).findByText("+₹500.00")).toBeInTheDocument();
    expect(new URL(requests(calls, `GET ${ENTRIES}`)[0].url, "http://x").searchParams.get("wallet")).toBe("w1");
  });

  it("records an offline top-up with the exact body and a command id; a retry after a refusal keeps it", async () => {
    const calls = walletApi({
      "POST /api/v1/admin/wallets/w1/top-up": [{ status: 409, body: { detail: "The server is busy with this wallet." } }, { status: 201, body: entry() }],
    });
    asStaff(MANAGE, <PlatformWalletScreen />);
    await openWallet();
    fireEvent.click(screen.getByRole("button", { name: "Record offline top-up" }));

    const save = () => within(screen.getByRole("dialog")).getByRole("button", { name: "Record top-up" });
    expect(save()).toBeDisabled();
    typeInto(/Amount received \(INR\)/, "1250.50");
    typeInto(/Payment reference/, "NEFT-778");
    typeInto(/Reason/, "short");
    expect(save()).toBeDisabled();
    typeInto(/Reason/, "Bank transfer from the customer");
    fireEvent.click(save());

    expect(await screen.findByRole("alert")).toHaveTextContent("The server is busy with this wallet.");
    fireEvent.click(save());
    await waitFor(() => expect(requests(calls, "POST /api/v1/admin/wallets/w1/top-up")).toHaveLength(2));

    const [refused, accepted] = requests(calls, "POST /api/v1/admin/wallets/w1/top-up");
    expect(accepted.body).toEqual({ amount: 125050, reference: "NEFT-778", reason: "Bank transfer from the customer" });
    expect(accepted.headers["idempotency-key"]).toMatch(UUID);
    expect(accepted.headers["idempotency-key"]).toBe(refused.headers["idempotency-key"]);

    expect(await screen.findByText("Top-up of ₹1,250.50 recorded.")).toBeInTheDocument();
    // It goes back to the wallet, whose ledger and list are read again.
    await screen.findByRole("button", { name: "Manual adjustment" });
    await waitFor(() => expect(requests(calls, `GET ${ENTRIES}`).length).toBe(2));
    expect(requests(calls, `GET ${PLATFORM}`).length).toBe(2);

    // A new dialog is a new command.
    fireEvent.click(screen.getByRole("button", { name: "Record offline top-up" }));
    typeInto(/Amount received \(INR\)/, "10");
    typeInto(/Payment reference/, "NEFT-779");
    typeInto(/Reason/, "A second bank transfer arrived");
    fireEvent.click(save());
    await waitFor(() => expect(requests(calls, "POST /api/v1/admin/wallets/w1/top-up")).toHaveLength(3));
    expect(requests(calls, "POST /api/v1/admin/wallets/w1/top-up")[2].headers["idempotency-key"]).not.toBe(accepted.headers["idempotency-key"]);
  });

  it("sends an increase straight away, signed and with a command id", async () => {
    const calls = walletApi({ "POST /api/v1/admin/wallets/w1/adjust": [{ status: 201, body: entry() }] });
    asStaff(MANAGE, <PlatformWalletScreen />);
    await openWallet();
    fireEvent.click(screen.getByRole("button", { name: "Manual adjustment" }));

    typeInto(/^Amount \(INR\)/, "250.50");
    typeInto(/Reason/, "Correcting a duplicate charge");
    submit("Apply adjustment");

    await waitFor(() => expect(requests(calls, "POST /api/v1/admin/wallets/w1/adjust")).toHaveLength(1));
    const [sent] = requests(calls, "POST /api/v1/admin/wallets/w1/adjust");
    expect(sent.body).toEqual({ amount: 25050, reason: "Correcting a duplicate charge" });
    expect(sent.headers["idempotency-key"]).toMatch(UUID);
    expect(await screen.findByText("Balance increased by ₹250.50.")).toBeInTheDocument();
  });

  it("asks before a decrease, keeps what was typed on Cancel, and shows the server's refusal", async () => {
    const calls = walletApi({
      "POST /api/v1/admin/wallets/w1/adjust": [{ status: 409, body: { detail: "The wallet does not hold enough for this." } }, { status: 201, body: entry() }],
    });
    asStaff(MANAGE, <PlatformWalletScreen />);
    await openWallet();
    fireEvent.click(screen.getByRole("button", { name: "Manual adjustment" }));

    typeInto(/^Amount \(INR\)/, "-99999");
    typeInto(/Reason/, "Removing a wrongly added amount");
    submit("Apply adjustment");

    // Nothing is sent until it is confirmed.
    expect(await screen.findByRole("heading", { name: "Decrease this balance by ₹99,999.00?" })).toBeInTheDocument();
    expect(requests(calls, "POST /api/v1/admin/wallets/w1/adjust")).toHaveLength(0);

    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("textbox", { name: /^Amount \(INR\)/ })).toHaveValue("-99999");
    expect(requests(calls, "POST /api/v1/admin/wallets/w1/adjust")).toHaveLength(0);

    submit("Apply adjustment");
    fireEvent.click(await screen.findByRole("button", { name: "Decrease balance" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The wallet does not hold enough for this.");

    fireEvent.click(screen.getByRole("button", { name: "Decrease balance" }));
    await waitFor(() => expect(requests(calls, "POST /api/v1/admin/wallets/w1/adjust")).toHaveLength(2));
    const [refused, accepted] = requests(calls, "POST /api/v1/admin/wallets/w1/adjust");
    expect(accepted.body).toEqual({ amount: -9999900, reason: "Removing a wrongly added amount" });
    expect(accepted.headers["idempotency-key"]).toBe(refused.headers["idempotency-key"]);
    expect(await screen.findByText("Balance decreased by ₹99,999.00.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Manual adjustment" })).toBeInTheDocument();
  });

  it("takes whole credits for an AI Credits adjustment, never decimals", async () => {
    const calls = mockApi({
      [`GET ${PLATFORM}`]: [page([aiWallet])],
      "GET /api/v1/admin/wallets/w3": [ok(aiWallet)],
      [`GET ${ENTRIES}`]: [page([])],
      "POST /api/v1/admin/wallets/w3/adjust": [{ status: 201, body: entry() }],
    });
    asStaff(MANAGE, <AiCreditsScreen />);
    await openWallet();
    fireEvent.click(screen.getByRole("button", { name: "Manual adjustment" }));

    typeInto(/^Amount \(credits\)/, "12.5");
    typeInto(/Reason/, "Goodwill credits after an outage");
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Apply adjustment" })).toBeDisabled();
    typeInto(/^Amount \(credits\)/, "50");
    submit("Apply adjustment");

    await waitFor(() => expect(requests(calls, "POST /api/v1/admin/wallets/w3/adjust")).toHaveLength(1));
    expect(requests(calls, "POST /api/v1/admin/wallets/w3/adjust")[0].body).toEqual({ amount: 50, reason: "Goodwill credits after an outage" });
  });

  it("sets and removes a low-balance threshold in minor units", async () => {
    const calls = walletApi({ "PUT /api/v1/admin/wallets/w1": [ok(wallet())] });
    asStaff(MANAGE, <PlatformWalletScreen />);
    await openWallet();

    fireEvent.click(screen.getByRole("button", { name: "Set low-balance threshold" }));
    expect(screen.getByRole("textbox", { name: /Low-balance threshold/ })).toHaveValue("2000.00");
    typeInto(/Low-balance threshold/, "750.25");
    submit("Save threshold");
    await waitFor(() => expect(requests(calls, "PUT /api/v1/admin/wallets/w1")).toHaveLength(1));
    expect(requests(calls, "PUT /api/v1/admin/wallets/w1")[0].body).toEqual({ low_balance_threshold: 75025 });

    fireEvent.click(await screen.findByRole("button", { name: "Set low-balance threshold" }));
    typeInto(/Low-balance threshold/, "");
    submit("Save threshold");
    await waitFor(() => expect(requests(calls, "PUT /api/v1/admin/wallets/w1")).toHaveLength(2));
    expect(requests(calls, "PUT /api/v1/admin/wallets/w1")[1].body).toEqual({ low_balance_threshold: null });
  });
});

describe("page actions", () => {
  const CUSTOMERS = { results: [{ organization_id: "o1", display_name: "Acme Logistics", owner_email: "a@acme.test" }], next_cursor: null };
  const PARTNERS = { results: [{ id: "p1", display_name: "BrightMart Partners", contact_email: "b@bright.test", code: "bm" }], next_cursor: null };

  it("opens a wallet for a customer chosen by searching on the server", async () => {
    const calls = mockApi({
      [`GET ${PLATFORM}`]: [page([])],
      "GET /api/v1/admin/customers": [ok(CUSTOMERS)],
      [`POST ${PLATFORM}`]: [{ status: 201, body: wallet() }],
    });
    asStaff(MANAGE, <PlatformWalletScreen />);
    await screen.findByText("No Platform Wallet wallets yet");
    fireEvent.click(screen.getByRole("button", { name: "Open wallet" }));

    const box = screen.getByRole("dialog");
    expect(within(box).getByRole("button", { name: "Open wallet" })).toBeDisabled();
    typeInto("Search customers by name", "Acme");
    fireEvent.click(await within(box).findByRole("button", { name: /Acme Logistics/ }));
    expect(new URL(requests(calls, "GET /api/v1/admin/customers")[0].url, "http://x").searchParams.get("q")).toBe("Acme");

    choose(/^Currency/, "USD");
    fireEvent.click(within(box).getByRole("button", { name: "Open wallet" }));
    await waitFor(() => expect(requests(calls, `POST ${PLATFORM}`)).toHaveLength(1));
    expect(requests(calls, `POST ${PLATFORM}`)[0].body).toEqual({ owner_type: "organization", owner_id: "o1", kind: "platform", currency: "USD" });
    expect(await screen.findByText("Platform Wallet opened for Acme Logistics.")).toBeInTheDocument();
  });

  it("allows a partner only a Platform Wallet, and says why", async () => {
    const calls = mockApi({
      [`GET ${PLATFORM}`]: [page([])],
      "GET /api/v1/admin/partners": [ok(PARTNERS)],
      [`POST ${PLATFORM}`]: [{ status: 400, body: { detail: "A partner holds a Platform Wallet only: commissions are earnings, not a balance." } }],
    });
    asStaff(MANAGE, <AiCreditsScreen />);
    await screen.findByText("No AI Credits wallets yet");
    fireEvent.click(screen.getByRole("button", { name: "Open wallet" }));

    const box = screen.getByRole("dialog");
    expect(within(box).getByRole("combobox", { name: /^Wallet/ })).toHaveTextContent("AI Credits");
    choose(/^Owner type/, "Partner");
    const kind = within(box).getByRole("combobox", { name: /^Wallet/ });
    expect(kind).toBeDisabled();
    expect(kind).toHaveTextContent("Platform Wallet");
    expect(within(box).getByText(/A partner holds a Platform Wallet only, because commissions are earnings, not a balance\./)).toBeInTheDocument();

    typeInto("Search partners by name", "Bright");
    fireEvent.click(await within(box).findByRole("button", { name: /BrightMart Partners/ }));
    fireEvent.click(within(box).getByRole("button", { name: "Open wallet" }));
    await waitFor(() => expect(requests(calls, `POST ${PLATFORM}`)).toHaveLength(1));
    expect(requests(calls, `POST ${PLATFORM}`)[0].body).toEqual({ owner_type: "partner", owner_id: "p1", kind: "platform", currency: "INR" });
    expect(await within(box).findByRole("alert")).toHaveTextContent("commissions are earnings");
  });

  it("places a top-up order with a command id, and links to the customer's billing page", async () => {
    const calls = mockApi({
      [`GET ${PLATFORM}`]: [page([])],
      "GET /api/v1/admin/customers": [ok(CUSTOMERS)],
      "POST /api/v1/admin/customers/o1/wallet-orders": [
        { status: 409, body: { detail: "This customer has another order awaiting payment. Settle or cancel it first." } },
        { status: 201, body: { order_id: "ord1", number: "ORD-9", status: "pending_payment", amount_due_minor: 150000, currency: "INR" } },
      ],
    });
    asStaff(MANAGE, <ConsumptionWalletScreen />);
    await screen.findByText("No Consumption Wallet wallets yet");
    fireEvent.click(screen.getByRole("button", { name: "New top-up order" }));

    const box = screen.getByRole("dialog");
    expect(within(box).getByText(/paid, from the customer's billing page/)).toBeInTheDocument();
    typeInto("Search customers by name", "Acme");
    fireEvent.click(await within(box).findByRole("button", { name: /Acme Logistics/ }));
    expect(within(box).getByRole("combobox", { name: /^Wallet/ })).toHaveTextContent("Consumption Wallet");
    typeInto(/^Amount \(INR\)/, "1500");
    fireEvent.click(within(box).getByRole("button", { name: "Place order" }));

    expect(await within(box).findByRole("alert")).toHaveTextContent("another order awaiting payment");
    fireEvent.click(within(box).getByRole("button", { name: "Place order" }));
    await waitFor(() => expect(requests(calls, "POST /api/v1/admin/customers/o1/wallet-orders")).toHaveLength(2));

    const [refused, accepted] = requests(calls, "POST /api/v1/admin/customers/o1/wallet-orders");
    expect(accepted.body).toEqual({ wallet_kind: "consumption", amount_minor: 150000, currency: "INR" });
    expect(accepted.headers["idempotency-key"]).toMatch(UUID);
    expect(accepted.headers["idempotency-key"]).toBe(refused.headers["idempotency-key"]);

    expect(await screen.findByText("Order ORD-9 placed for Acme Logistics")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open billing page" })).toHaveAttribute("href", "/admin/customers/o1");
  });

  it("sells only an active credit pack", async () => {
    const calls = mockApi({
      [`GET ${PLATFORM}`]: [page([])],
      "GET /api/v1/admin/customers": [ok(CUSTOMERS)],
      "GET /api/v1/admin/credit-packs": [
        ok({
          results: [
            { id: "c1", code: "starter", name: "Starter", credits: 1000, price_minor: 49900, currency: "INR", status: "active" },
            { id: "c2", code: "old", name: "Old pack", credits: 10, price_minor: 100, currency: "INR", status: "archived" },
          ],
        }),
      ],
      "POST /api/v1/admin/customers/o1/wallet-orders": [
        { status: 201, body: { order_id: "ord2", number: "ORD-10", status: "pending_payment", amount_due_minor: 49900, currency: "INR" } },
      ],
    });
    asStaff(MANAGE, <AiCreditsScreen />);
    await screen.findByText("No AI Credits wallets yet");
    fireEvent.click(screen.getByRole("button", { name: "Sell credit pack" }));

    const box = screen.getByRole("dialog");
    typeInto("Search customers by name", "Acme");
    fireEvent.click(await within(box).findByRole("button", { name: /Acme Logistics/ }));
    fireEvent.click(await within(box).findByRole("combobox", { name: /^Credit pack/ }));
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Starter: 1,000 credits for ₹499.00"]);
    fireEvent.click(screen.getByRole("option", { name: "Starter: 1,000 credits for ₹499.00" }));
    fireEvent.click(within(box).getByRole("button", { name: "Place order" }));

    await waitFor(() => expect(requests(calls, "POST /api/v1/admin/customers/o1/wallet-orders")).toHaveLength(1));
    const [sent] = requests(calls, "POST /api/v1/admin/customers/o1/wallet-orders");
    expect(sent.body).toEqual({ pack_code: "starter" });
    expect(sent.headers["idempotency-key"]).toMatch(UUID);
    expect(await screen.findByText("Order ORD-10 placed for Acme Logistics")).toBeInTheDocument();
  });

  it("says so when no credit pack is on sale, and links to where one is added", async () => {
    mockApi({ [`GET ${PLATFORM}`]: [page([])], "GET /api/v1/admin/credit-packs": [ok({ results: [] })] });
    asStaff(MANAGE, <AiCreditsScreen />);
    await screen.findByText("No AI Credits wallets yet");
    fireEvent.click(screen.getByRole("button", { name: "Sell credit pack" }));
    expect(await screen.findByRole("link", { name: "Add one under Credit packs" })).toHaveAttribute("href", "/admin/usage-pricing/credit-packs");
  });
});

describe("Transaction Controls: transactions", () => {
  const controls = (rows: unknown[] = [entry(), reversal], extra: Record<string, { status: number; body?: unknown }[]> = {}) =>
    mockApi({
      [`GET ${RECONCILE}`]: [ok({ balanced: true, mismatches: [] })],
      [`GET ${ENTRIES}`]: [page(rows)],
      ...extra,
    });

  it("shows signed amounts, the owner, the source and which entry a reversal names; passes axe", async () => {
    controls();
    const { container } = asStaff(MANAGE, <TransactionControlsScreen />);

    expect(await screen.findByRole("heading", { level: 1, name: "Transaction Controls" })).toBeInTheDocument();
    expect(await screen.findByText("+₹500.00")).toBeInTheDocument();
    expect(screen.getByText("−₹200.00")).toBeInTheDocument();
    expect(screen.getByText("₹1,050.50")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Acme Logistics" })[0]).toHaveAttribute("href", "/admin/customers/o1");
    expect(screen.getByRole("link", { name: "Reverses entry e1111111" })).toHaveAttribute("href", "#entry-e1111111-0000-0000-0000-000000000000");
    expect(screen.getByText("Offline payment")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("says when the entry a reversal names is not in the list", async () => {
    controls([reversal]);
    asStaff(VIEW, <TransactionControlsScreen />);
    expect(await screen.findByText("Reverses entry e1111111 (not in this list)")).toBeInTheDocument();
  });

  it("filters on the server by wallet and type, and starts from the address", async () => {
    query = "kind=ai_credits&type=credit_purchase";
    const calls = controls();
    asStaff(VIEW, <TransactionsPage />);
    await screen.findByText("+₹500.00");
    const first = new URL(requests(calls, `GET ${ENTRIES}`)[0].url, "http://x").searchParams;
    expect(first.get("kind")).toBe("ai_credits");
    expect(first.get("type")).toBe("credit_purchase");

    choose("Wallet", "Consumption Wallet");
    await waitFor(() => expect(new URL(requests(calls, `GET ${ENTRIES}`).at(-1)!.url, "http://x").searchParams.get("kind")).toBe("consumption"));
    choose("Type", "Manual adjustment");
    await waitFor(() => expect(new URL(requests(calls, `GET ${ENTRIES}`).at(-1)!.url, "http://x").searchParams.get("type")).toBe("adjustment"));
  });

  it("says when there are none, and when they could not be loaded", async () => {
    controls([]);
    const first = asStaff(VIEW, <TransactionControlsScreen />);
    expect(await screen.findByText("No transactions yet")).toBeInTheDocument();
    first.unmount();

    mockApi({ [`GET ${RECONCILE}`]: [ok({ balanced: true, mismatches: [] })], [`GET ${ENTRIES}`]: [{ status: 500, body: {} }] });
    asStaff(VIEW, <TransactionControlsScreen />);
    expect(await screen.findByText(/Couldn't load transactions\./)).toBeInTheDocument();
  });

  it("offers Reverse only to a manager and never on an entry that already reverses something", async () => {
    controls();
    const first = asStaff(MANAGE, <TransactionControlsScreen />);
    expect(await screen.findByRole("button", { name: "Reverse entry e1111111" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reverse entry e2222222" })).not.toBeInTheDocument();
    first.unmount();

    controls();
    asStaff(VIEW, <TransactionControlsScreen />);
    await screen.findByText("+₹500.00");
    expect(screen.queryByRole("button", { name: /^Reverse entry/ })).not.toBeInTheDocument();
  });

  it("reverses in full with no amount, and in part with one, each its own command", async () => {
    const path = "POST /api/v1/admin/wallets/entries/e1111111-0000-0000-0000-000000000000/reverse";
    const calls = controls(undefined, { [path]: [{ status: 201, body: reversal }] });
    asStaff(MANAGE, <TransactionControlsScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Reverse entry e1111111" }));
    expect(screen.getByRole("heading", { name: "Reverse this ₹500.00 entry" })).toBeInTheDocument();
    typeInto(/Reason/, "Customer asked for it back");
    submit("Reverse");
    await waitFor(() => expect(requests(calls, path)).toHaveLength(1));
    expect(requests(calls, path)[0].body).toEqual({ reason: "Customer asked for it back" });
    expect(requests(calls, path)[0].headers["idempotency-key"]).toMatch(UUID);
    expect(await screen.findByText("Entry reversed.")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Reverse entry e1111111" }));
    typeInto(/Amount to reverse \(INR\)/, "100.50");
    typeInto(/Reason/, "Refunding only part of it");
    submit("Reverse");
    await waitFor(() => expect(requests(calls, path)).toHaveLength(2));
    expect(requests(calls, path)[1].body).toEqual({ amount: 10050, reason: "Refunding only part of it" });
    expect(requests(calls, path)[1].headers["idempotency-key"]).not.toBe(requests(calls, path)[0].headers["idempotency-key"]);
  });

  it("shows the server's refusal of a reversal and needs a reason first", async () => {
    const path = "POST /api/v1/admin/wallets/entries/e1111111-0000-0000-0000-000000000000/reverse";
    controls(undefined, { [path]: [{ status: 409, body: { detail: "At most 100 of this transaction can still be reversed." } }] });
    asStaff(MANAGE, <TransactionControlsScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Reverse entry e1111111" }));
    const box = screen.getByRole("dialog");
    expect(within(box).getByRole("button", { name: "Reverse" })).toBeDisabled();
    typeInto(/Reason/, "Customer asked for it back");
    submit("Reverse");
    expect(await within(box).findByRole("alert")).toHaveTextContent("At most 100 of this transaction can still be reversed.");
  });
});

describe("Transaction Controls: usage events", () => {
  const eventsApi = (rows: unknown[] = [event(), event({ id: "ev2", outcome: "charged", failure_code: "", amount: 600, duplicate_count: 0, reviewed_at: "2026-11-09T02:00:00Z", review_note: "Looks right" })], extra: Record<string, { status: number; body?: unknown }[]> = {}) =>
    mockApi({
      [`GET ${RECONCILE}`]: [ok({ balanced: true, mismatches: [] })],
      [`GET ${ENTRIES}`]: [page([])],
      [`GET ${EVENTS}`]: [page(rows)],
      ...extra,
    });

  const openEvents = async () => {
    fireEvent.click(await screen.findByRole("tab", { name: "Usage events" }));
    return screen.findByRole("table", { name: "Usage events" });
  };

  it("shows the outcome, failure, amount and duplicates; passes axe", async () => {
    eventsApi();
    const { container } = asStaff(MANAGE, <TransactionControlsScreen />);
    const table = await openEvents();

    expect(within(table).getAllByRole("link", { name: "Acme Logistics" })[0]).toHaveAttribute("href", "/admin/customers/o1");
    expect(within(table).getAllByText("SMS messages")).toHaveLength(2);
    expect(within(table).getByText("Failed")).toBeInTheDocument();
    expect(within(table).getByText("Charged")).toBeInTheDocument();
    expect(within(table).getByText("Insufficient balance")).toBeInTheDocument();
    expect(within(table).getByText("₹6.00")).toBeInTheDocument();
    expect(within(table).getAllByText("Easy CRM")).toHaveLength(2);
    await expectNoViolations(container);
  });

  it("opens on the tab and meter named in the address", async () => {
    query = "tab=events&meter=ai_tokens";
    const calls = eventsApi();
    asStaff(VIEW, <TransactionsPage />);
    await screen.findByRole("table", { name: "Usage events" });
    expect(new URL(requests(calls, `GET ${EVENTS}`)[0].url, "http://x").searchParams.get("meter")).toBe("ai_tokens");
    expect(requests(calls, `GET ${ENTRIES}`)).toHaveLength(0);
  });

  it("filters on the server by outcome, meter, duplicates and review", async () => {
    const calls = eventsApi();
    asStaff(VIEW, <TransactionControlsScreen />);
    await openEvents();
    const last = () => new URL(requests(calls, `GET ${EVENTS}`).at(-1)!.url, "http://x").searchParams;

    choose("Outcome", "No rate set");
    await waitFor(() => expect(last().get("outcome")).toBe("not_rated"));
    choose("Meter", "Webhook deliveries");
    await waitFor(() => expect(last().get("meter")).toBe("webhook"));
    fireEvent.click(screen.getByRole("checkbox", { name: "Duplicates" }));
    await waitFor(() => expect(last().get("duplicates")).toBe("1"));
    fireEvent.click(screen.getByRole("checkbox", { name: "Not yet reviewed" }));
    await waitFor(() => expect(last().get("unreviewed")).toBe("1"));
  });

  it("offers Retry only on a failed event, and shows the server's refusal", async () => {
    const calls = eventsApi(undefined, {
      "POST /api/v1/admin/wallets/events/ev1/retry": [{ status: 409, body: { detail: "The wallet does not hold enough for this." } }, ok(event({ outcome: "charged" }))],
    });
    asStaff(MANAGE, <TransactionControlsScreen />);
    const table = await openEvents();

    const retries = within(table).getAllByRole("button", { name: /^Retry/ });
    expect(retries).toHaveLength(1);
    fireEvent.click(retries[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("The wallet does not hold enough for this.");

    fireEvent.click(within(table).getByRole("button", { name: /^Retry/ }));
    expect(await screen.findByText("Event retried. Its new outcome is in the list.")).toBeInTheDocument();
    expect(requests(calls, "POST /api/v1/admin/wallets/events/ev1/retry")[1].body).toEqual({});
    await waitFor(() => expect(requests(calls, `GET ${EVENTS}`).length).toBe(2));
  });

  it("marks an unreviewed event reviewed with a note, and only that one", async () => {
    const calls = eventsApi(undefined, {
      "POST /api/v1/admin/wallets/events/ev1/review": [{ status: 400, body: { detail: "Say what you found." } }, ok(event({ reviewed_at: "2026-11-09T03:00:00Z", review_note: "Checked with support" }))],
    });
    asStaff(MANAGE, <TransactionControlsScreen />);
    const table = await openEvents();

    expect(within(table).getByText("Looks right")).toBeInTheDocument();
    const buttons = within(table).getAllByRole("button", { name: /^Mark reviewed/ });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);

    const box = screen.getByRole("dialog");
    expect(within(box).getByRole("button", { name: "Mark reviewed" })).toBeDisabled();
    typeInto(/Note/, "Checked with support");
    fireEvent.click(within(box).getByRole("button", { name: "Mark reviewed" }));
    expect(await within(box).findByRole("alert")).toHaveTextContent("Say what you found.");
    fireEvent.click(within(box).getByRole("button", { name: "Mark reviewed" }));

    await waitFor(() => expect(requests(calls, "POST /api/v1/admin/wallets/events/ev1/review")).toHaveLength(2));
    expect(requests(calls, "POST /api/v1/admin/wallets/events/ev1/review")[1].body).toEqual({ note: "Checked with support" });
    expect(await screen.findByText("Event marked reviewed.")).toBeInTheDocument();
  });

  it("hides Retry and Mark reviewed without the grant", async () => {
    eventsApi();
    asStaff(VIEW, <TransactionControlsScreen />);
    const table = await openEvents();
    expect(within(table).queryByRole("button", { name: /^Retry/ })).not.toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: /^Mark reviewed/ })).not.toBeInTheDocument();
  });

  it("says when there are no events", async () => {
    eventsApi([]);
    asStaff(VIEW, <TransactionControlsScreen />);
    fireEvent.click(await screen.findByRole("tab", { name: "Usage events" }));
    expect(await screen.findByText("No usage events yet")).toBeInTheDocument();
  });
});

describe("reconciliation", () => {
  it("says plainly when every balance equals its ledger", async () => {
    mockApi({ [`GET ${RECONCILE}`]: [ok({ balanced: true, mismatches: [] })], [`GET ${ENTRIES}`]: [page([])] });
    asStaff(VIEW, <TransactionControlsScreen />);
    expect(await screen.findByText("Every balance equals its ledger.")).toBeInTheDocument();
  });

  it("lists the wallets that do not match", async () => {
    mockApi({
      [`GET ${RECONCILE}`]: [ok({ balanced: false, mismatches: [{ wallet_id: "abcdef12-0000-0000-0000-000000000000", balance: 500, ledger_total: 450 }] })],
      [`GET ${ENTRIES}`]: [page([])],
    });
    const { container } = asStaff(VIEW, <TransactionControlsScreen />);
    expect(await screen.findByText("1 wallet does not match the ledger")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Wallets whose balance differs from their ledger" });
    expect(within(table).getByText("abcdef12…")).toBeInTheDocument();
    expect(within(table).getByText("500")).toBeInTheDocument();
    expect(within(table).getByText("450")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("says when the check could not be run", async () => {
    mockApi({ [`GET ${RECONCILE}`]: [{ status: 500, body: {} }], [`GET ${ENTRIES}`]: [page([])] });
    asStaff(VIEW, <TransactionControlsScreen />);
    expect(await screen.findByText("Something went wrong. Please try again.")).toBeInTheDocument();
  });
});

describe("exactly three balances", () => {
  const noCommissionBalance = () => {
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/commission[^.]{0,20}(balance|wallet)/i);
    expect(screen.queryAllByRole("columnheader", { name: /commission/i })).toHaveLength(0);
    expect(screen.queryAllByRole("option", { name: /commission/i })).toHaveLength(0);
  };

  it.each([
    ["Platform Wallet", <PlatformWalletScreen key="p" />],
    ["AI Credits", <AiCreditsScreen key="a" />],
    ["Consumption Wallet", <ConsumptionWalletScreen key="c" />],
  ])("%s never shows a commission as a balance", async (_name, ui) => {
    mockApi({ [`GET ${PLATFORM}`]: [page([wallet()])] });
    asStaff(MANAGE, ui);
    await screen.findByRole("link", { name: "Acme Logistics" });
    noCommissionBalance();
  });

  it("offers only the three wallets, in the filters of Transaction Controls and in Open wallet", async () => {
    mockApi({ [`GET ${RECONCILE}`]: [ok({ balanced: true, mismatches: [] })], [`GET ${ENTRIES}`]: [page([entry()])], [`GET ${PLATFORM}`]: [page([])] });
    const first = asStaff(MANAGE, <TransactionControlsScreen />);
    await screen.findByText("+₹500.00");
    fireEvent.click(screen.getByRole("combobox", { name: "Wallet" }));
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["All wallets", "Platform Wallet", "AI Credits", "Consumption Wallet"]);
    noCommissionBalance();
    first.unmount();

    asStaff(MANAGE, <PlatformWalletScreen />);
    await screen.findByText("No Platform Wallet wallets yet");
    fireEvent.click(screen.getByRole("button", { name: "Open wallet" }));
    fireEvent.click(screen.getByRole("combobox", { name: /^Wallet/ }));
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Platform Wallet", "AI Credits", "Consumption Wallet"]);
    noCommissionBalance();
  });
});

describe("pay an invoice from the wallet", () => {
  const INVOICE = {
    invoice_id: "inv1",
    number: "INV-7",
    status: "open",
    currency: "INR",
    total_minor: 99900,
    amount_paid_minor: 0,
    amount_refunded_minor: 0,
    amount_due_minor: 99900,
    billing_interval: "monthly",
    period_start: "2026-11-01T00:00:00Z",
    period_end: "2026-12-01T00:00:00Z",
    issued_at: "2026-11-01T00:00:00Z",
    due_at: "2026-11-08T00:00:00Z",
    paid_at: null,
    dunning_stage: null,
    version: 4,
    attempts: [],
  };
  const BILLING = "GET /api/v1/admin/customers/o1/billing";
  const PAY = "POST /api/v1/admin/customers/o1/invoices/inv1/pay-from-wallet";

  it("pays with the reason and the version it was decided on, then reads the invoice again; passes axe", async () => {
    const calls = mockApi({ [BILLING]: [ok({ invoices: [INVOICE] })], [PAY]: [ok(INVOICE)] });
    const { container } = asStaff(["platform.staff_payment.view", "platform.staff_wallet.manage"], <CustomerBilling organizationId="o1" />);

    const opener = await screen.findByRole("button", { name: "Pay INV-7 from wallet" });
    await expectNoViolations(container);
    fireEvent.click(opener);
    const box = screen.getByRole("dialog");
    expect(within(box).getByRole("button", { name: "Pay from wallet" })).toBeDisabled();
    typeInto(/Reason/, "Customer asked to use the wallet");
    fireEvent.click(within(box).getByRole("button", { name: "Pay from wallet" }));

    await waitFor(() => expect(requests(calls, PAY)).toHaveLength(1));
    expect(requests(calls, PAY)[0].body).toEqual({ reason: "Customer asked to use the wallet", expected_version: 4 });
    expect(await screen.findByText("Invoice INV-7 is paid from the Platform Wallet.")).toBeInTheDocument();
    await waitFor(() => expect(requests(calls, BILLING)).toHaveLength(2));
  });

  it("shows the server's refusal when the wallet holds too little", async () => {
    mockApi({
      [BILLING]: [ok({ invoices: [INVOICE] })],
      [PAY]: [{ status: 409, body: { detail: "The Platform Wallet does not hold enough to pay this invoice." } }],
    });
    asStaff(["platform.staff_payment.view", "platform.staff_wallet.manage"], <CustomerBilling organizationId="o1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Pay INV-7 from wallet" }));
    const box = screen.getByRole("dialog");
    typeInto(/Reason/, "Customer asked to use the wallet");
    fireEvent.click(within(box).getByRole("button", { name: "Pay from wallet" }));
    expect(await within(box).findByRole("alert")).toHaveTextContent("The Platform Wallet does not hold enough to pay this invoice.");
  });

  it("is hidden without the wallet grant, and on an invoice with nothing to pay", async () => {
    mockApi({ [BILLING]: [ok({ invoices: [INVOICE] })] });
    const first = asStaff(["platform.staff_payment.view", "platform.staff_payment.manage"], <CustomerBilling organizationId="o1" />);
    await screen.findByRole("button", { name: "Void INV-7" });
    expect(screen.queryByRole("button", { name: "Pay INV-7 from wallet" })).not.toBeInTheDocument();
    first.unmount();

    mockApi({ [BILLING]: [ok({ invoices: [{ ...INVOICE, status: "paid", amount_due_minor: 0 }] })] });
    asStaff(["platform.staff_payment.view", "platform.staff_wallet.manage"], <CustomerBilling organizationId="o1" />);
    await screen.findByText("INV-7");
    expect(screen.queryByRole("button", { name: "Pay INV-7 from wallet" })).not.toBeInTheDocument();
  });
});
