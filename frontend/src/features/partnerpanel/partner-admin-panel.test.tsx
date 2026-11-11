import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerContext } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import { money } from "@/features/controlplane/ui";
import type { PortalPartner } from "@/lib/api/controlPlane";
import { BillingRecords, CustomerSubscriptions } from "./BillingPages";
import { BusinessKyc } from "./BusinessKyc";
import { PaymentCommissions } from "./Catalog";
import { PlanActivation } from "./CustomerPages";
import { PlatformWalletPage, WalletTransactions } from "./PlatformWallet";
import { EligibleProducts, HelpContent, SalesResources } from "./ProductsReferrals";
import { UnifiedDashboard } from "./UnifiedDashboard";

/**
 * The Partner Admin Panel's new pages (ADR-063): the Platform Wallet, Plan
 * Activation paid from it, Customer Subscriptions, Billing Records, Eligible
 * Products, Sales Resources, Help Content, Business & KYC and the onboarding
 * dashboard. The API is mocked: every amount is the server's, and these tests
 * check what is drawn and exactly what is sent.
 */

let search = "";
const nav = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav,
  useSearchParams: () => new URLSearchParams(search),
  usePathname: () => "/partner/reseller/dashboard",
}));

const ID = "66666666-6666-6666-6666-666666666666";
const ORG = "77777777-7777-7777-7777-777777777777";
const API = `/api/v1/partner/${ID}`;
const BASE = "/partner/reseller";
const perm = (...keys: string[]) => keys.map((k) => `platform.partner_${k}`);

const SELLER = perm(
  "dashboard.view",
  "profile.view",
  "product.view",
  "customer.view",
  "customer.provision",
  "pricing.view",
  "subscription.sell",
  "subscription.manage",
  "commission.view",
  "payout.view",
  "wallet.view",
  "wallet.manage",
  "referral.view",
  "referral.create",
);

function partner(over: Partial<PortalPartner> = {}): PortalPartner {
  return {
    partner_id: ID,
    code: "rs",
    display_name: "Resell Co",
    status: "active",
    role: "owner",
    is_admin: true,
    capabilities: [],
    level: "reseller",
    permissions: SELLER,
    ...over,
  };
}

function mount(ui: React.ReactNode, routes: Record<string, Reply[]>, who: PortalPartner = partner()) {
  const calls = mockApi(routes);
  const view = render(<PartnerContext.Provider value={{ partner: who, base: BASE }}>{ui}</PartnerContext.Provider>);
  return { calls, ...view };
}

const WALLET = {
  currency: "INR",
  wallets: [{ wallet_id: "w1", currency: "INR", balance_minor: 500000, low_balance_threshold: null, is_low: false }],
  top_up: { allowed: true, online: { INR: true }, min_minor: 100, max_minor: 100000000, offline_note: "For a bank transfer, raise a support ticket." },
  pays_for_customers: true,
  top_ups: [
    { id: "t1", status: "succeeded", amount_minor: 500000, currency: "INR", checkout_url: null, test_mode: true, completed_at: "2026-11-01T10:00:00Z", failure_code: "", created_at: "2026-11-01T09:59:00Z" },
  ],
  recent: [
    {
      id: "e2",
      currency: "INR",
      entry_type: "subscription",
      entry_type_label: "Subscription deduction",
      amount_minor: -399900,
      balance_after_minor: 100100,
      reason: "Plan activation ORD-000123",
      source_type: "order",
      source_id: "o1",
      reverses_id: null,
      created_at: "2026-11-02T10:00:00Z",
      order: { id: "o1", number: "ORD-000123" },
      customer: { organization_id: ORG, name: "Customer X" },
    },
  ],
};

beforeEach(() => {
  search = "";
  vi.restoreAllMocks();
});

describe("Platform Wallet", () => {
  it("shows the balance apart from earnings, the top-ups and the movements; passes axe", async () => {
    const { container } = mount(<PlatformWalletPage />, { [`GET ${API}/platform-wallet`]: [ok(WALLET)] });
    expect((await screen.findAllByText(money(500000, "INR"))).length).toBeGreaterThan(0);
    expect(screen.getByRole("list", { name: "Balance" })).toHaveTextContent(money(500000, "INR"));
    expect(screen.getByText(/Your earnings are kept apart/)).toBeInTheDocument();
    const recent = screen.getByRole("table", { name: "Your latest wallet movements" });
    expect(recent).toHaveTextContent("ORD-000123");
    expect(recent).toHaveTextContent("Customer X");
    expect(screen.getByRole("table", { name: "Your recent top-ups" })).toHaveTextContent("Paid");
    await expectNoViolations(container);
  });

  it("Add Money sends what was typed and goes to the provider's page", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const { calls } = mount(<PlatformWalletPage />, {
      [`GET ${API}/platform-wallet`]: [ok(WALLET)],
      [`POST ${API}/platform-wallet/top-up`]: [
        { status: 201, body: { id: "t2", status: "awaiting_payment", amount_minor: 250000, currency: "INR", checkout_url: "https://pay.example.com/c/1", test_mode: true, completed_at: null, failure_code: "" } },
      ],
    });
    fireEvent.click(await screen.findByRole("button", { name: "Add Money" }));
    const dialog = await screen.findByRole("dialog", { name: "Add Money" });
    const submit = within(dialog).getByRole("button", { name: "Continue to payment" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Amount (INR)"), { target: { value: "2500.00" } });
    fireEvent.click(submit);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://pay.example.com/c/1"));
    // The amount is the person's text: the server turns it into minor units.
    expect(calls.find((c) => c.key === `POST ${API}/platform-wallet/top-up`)!.body).toEqual({ amount: "2500.00", currency: "INR" });
    vi.unstubAllGlobals();
  });

  it("explains, and offers no Add Money, when the role may not add money", async () => {
    mount(<PlatformWalletPage />, { [`GET ${API}/platform-wallet`]: [ok({ ...WALLET, top_up: { ...WALLET.top_up, allowed: false } })] });
    expect(await screen.findByText(/Adding money needs an active, approved reseller account/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add Money" })).toBeNull();
  });

  it("Transactions filters by type on the server", async () => {
    const { calls } = mount(<WalletTransactions />, {
      [`GET ${API}/platform-wallet/transactions`]: [ok({ items: WALLET.recent, total: 1, limit: 25, offset: 0 })],
    });
    expect(await screen.findByRole("table", { name: "Platform Wallet transactions" })).toHaveTextContent("Plan activation ORD-000123");
    fireEvent.click(screen.getByRole("button", { name: /Top-ups/ }));
    await waitFor(() => expect(calls.at(-1)!.url).toContain("type=top_up"));
  });
});

const PLANS = {
  currency: "INR",
  can_create_plan: false,
  not_implemented: {},
  plans: [
    {
      plan_version_id: "pv1",
      plan_code: "crm_pro",
      plan_name: "CRM Professional",
      product_code: "crm",
      product_name: "Easy CRM",
      included_seats: 1,
      trial_days: 0,
      currency: "INR",
      enabled_for_sale: true,
      prices: [{ interval: "monthly", partner_price_minor: null, list_price_minor: 499900, customer_price_minor: 499900, partner_cost_minor: 399900, difference_minor: null, source: "list" }],
    },
  ],
};

const CUSTOMERS = {
  results: [{ organization_id: ORG, name: "Customer X", organization_status: "active", link_status: "active", started_at: "2026-10-01T00:00:00Z", ended_at: null, subscription_status: "", products: [], owner_activation: { activation: "activated", expires_at: null, send_count: 1 } }],
  next_cursor: null,
  has_more: false,
};

const QUOTE = {
  customer: { organization_id: ORG },
  plan: { plan_version_id: "pv1", product_code: "crm", plan_code: "crm_pro", name: "CRM Professional" },
  payer: { usable: true, reason: null, message: null, adopted: false },
  charge: { partner_cost_minor: 399900, due_now_minor: 399900, setup_fee_minor: 0, currency: "INR", billing_interval: "monthly", quantity: 1, channel: "reseller", period: { prorated: false } },
  charge_minor: 399900,
  wallet: { currency: "INR", balance_minor: 500000, charge_minor: 399900, balance_after_minor: 100100, sufficient: true },
};

async function chooseActivation() {
  fireEvent.change(await screen.findByLabelText("Assigned customer"), { target: { value: ORG } });
  fireEvent.change(await screen.findByLabelText("Eligible product"), { target: { value: "crm" } });
  fireEvent.change(screen.getByLabelText("Available plan"), { target: { value: "pv1" } });
  fireEvent.click(screen.getByRole("button", { name: "Review price" }));
}

describe("Plan Activation", () => {
  const routes = (over: Record<string, Reply[]> = {}) => ({
    [`GET ${API}/customers`]: [ok(CUSTOMERS)],
    [`GET ${API}/plans`]: [ok(PLANS)],
    [`POST ${API}/customers/${ORG}/activation/quote`]: [ok(QUOTE)],
    ...over,
  });

  it("walks customer, product, plan, the purchase price and the wallet deduction, then confirms with that amount once", async () => {
    const { calls, container } = mount(
      <PlanActivation />,
      routes({
        [`POST ${API}/customers/${ORG}/activation`]: [
          { status: 201, body: { order: { order_id: "o1", number: "ORD-000124", status: "fulfilled", description: "x", paid_by: "partner_wallet" }, wallet: { currency: "INR", balance_minor: 100100 } } },
        ],
      }),
    );
    await chooseActivation();
    const price = await screen.findByRole("heading", { name: /4\. Partner purchase price and wallet deduction/ });
    const section = price.closest("section")!;
    expect(section).toHaveTextContent(money(399900, "INR"));
    expect(section).toHaveTextContent(money(100100, "INR"));
    await expectNoViolations(container);
    fireEvent.click(within(section).getByRole("button", { name: `Confirm and pay ${money(399900, "INR")}` }));
    expect(await screen.findByText(/Order ORD-000124 is recorded/)).toBeInTheDocument();
    const sent = calls.find((c) => c.key === `POST ${API}/customers/${ORG}/activation`)!;
    expect(sent.body).toEqual({ plan_version_id: "pv1", billing_interval: "monthly", quantity: 1, expected_charge_minor: 399900 });
    expect(sent.headers["idempotency-key"]).toBeTruthy();
  });

  it("a retry of the same review reuses its key, so it can never charge twice", async () => {
    const { calls } = mount(
      <PlanActivation />,
      routes({ [`POST ${API}/customers/${ORG}/activation`]: [{ status: 503, body: { code: "unavailable", detail: "Try again." } }] }),
    );
    await chooseActivation();
    const confirm = await screen.findByRole("button", { name: /Confirm and pay/ });
    fireEvent.click(confirm);
    await screen.findByText("Try again.");
    fireEvent.click(screen.getByRole("button", { name: /Confirm and pay/ }));
    await waitFor(() => expect(calls.filter((c) => c.key.endsWith("/activation")).length).toBe(2));
    const keys = calls.filter((c) => c.key.endsWith("/activation")).map((c) => c.headers["idempotency-key"]);
    expect(keys[0]).toBe(keys[1]);
  });

  it("an insufficient balance offers Add Money instead of confirming", async () => {
    mount(<PlanActivation />, routes({ [`POST ${API}/customers/${ORG}/activation/quote`]: [ok({ ...QUOTE, wallet: { ...QUOTE.wallet, sufficient: false, balance_minor: 1000 } })] }));
    await chooseActivation();
    expect(await screen.findByText(/does not hold enough/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add Money" })).toHaveAttribute("href", `${BASE}/platform-wallet`);
    expect(screen.queryByRole("button", { name: /Confirm and pay/ })).toBeNull();
  });

  it("says when the customer pays Workeasy360 directly", async () => {
    mount(
      <PlanActivation />,
      routes({
        [`POST ${API}/customers/${ORG}/activation/quote`]: [
          ok({ ...QUOTE, payer: { usable: false, reason: "pays_directly", message: "This customer already holds a subscription it pays for itself." }, charge: null, charge_minor: null, wallet: null }),
        ],
      }),
    );
    await chooseActivation();
    expect(await screen.findByRole("alert")).toHaveTextContent("pays for itself");
  });
});

const SUBS = {
  filter: "all",
  truncated: false,
  counts: { all: 2, active: 2, pending_setup: 1, expiring: 1, expired: 0 },
  items: [
    {
      subscription_id: "s1",
      organization_id: ORG,
      customer: "Customer X",
      status: "active",
      currency: "INR",
      billing_interval: "monthly",
      current_period_start: "2026-10-15T00:00:00Z",
      current_period_end: "2026-11-15T00:00:00Z",
      renewal_date: null,
      cancel_at: null,
      version: 3,
      paid_by: "partner_wallet",
      renewal_prepaid: false,
      state: { expired: false, expiring: true, pending_setup: true, active: true, renews: false, ends_at: "2026-11-15T00:00:00Z" },
      lines: [
        { subscription_item_id: "i1", product_code: "crm", product_name: "Easy CRM", plan_code: "crm_pro", plan_name: "CRM Professional", plan_version_id: "pv1", quantity: 1, added_at: "2026-10-15T00:00:00Z", removed_at: null, order: { order_id: "o1", number: "ORD-000123", status: "fulfilled", paid: true } },
      ],
    },
  ],
};

describe("Customer Subscriptions", () => {
  it("filters by status on the server, with each filter's count; keeps the states apart", async () => {
    const { calls, container } = mount(<CustomerSubscriptions />, { [`GET ${API}/subscriptions`]: [ok(SUBS)] });
    const table = await screen.findByRole("table", { name: "Customer subscriptions" });
    const row = within(table).getByRole("row", { name: /Customer X/ });
    expect(row).toHaveTextContent("Your Platform Wallet");
    expect(row).toHaveTextContent("Pending setup");
    expect(row).toHaveTextContent("ORD-000123");
    expect(screen.getByRole("button", { name: /Expiring/ })).toHaveTextContent("1");
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: /Pending Setup/ }));
    await waitFor(() => expect(calls.at(-1)!.url).toContain("status=pending_setup"));
  });

  it("renews a wallet-paid subscription with the amount it showed", async () => {
    const RENEWAL = {
      subscription_id: "s1",
      period_end: "2026-11-15T00:00:00Z",
      already_paid: false,
      blocked: null,
      message: null,
      charge_minor: 399900,
      lines: [{ product_code: "crm", plan_code: "crm_pro", quantity: 1, partner_cost_minor: 399900 }],
      wallet: { currency: "INR", balance_minor: 500000, charge_minor: 399900, balance_after_minor: 100100, sufficient: true },
    };
    const { calls } = mount(<CustomerSubscriptions />, {
      [`GET ${API}/subscriptions`]: [ok(SUBS)],
      [`GET ${API}/customers/${ORG}/subscriptions/s1/renewal`]: [ok(RENEWAL)],
      [`POST ${API}/customers/${ORG}/subscriptions/s1/renewal`]: [ok({ ...RENEWAL, already_paid: true, charge_minor: 0 })],
    });
    fireEvent.click(await screen.findByRole("button", { name: "Renew" }));
    const dialog = await screen.findByRole("dialog", { name: "Renew from your Platform Wallet" });
    fireEvent.click(await within(dialog).findByRole("button", { name: `Pay ${money(399900, "INR")}` }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/customers/${ORG}/subscriptions/s1/renewal`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${API}/customers/${ORG}/subscriptions/s1/renewal`)!.body).toEqual({ expected_charge_minor: 399900 });
  });
});

describe("Earnings & Payouts → Commissions (2026-11-11 brief, section 12)", () => {
  it("shows pending, available, reversed and paid-out earnings apart, then the ledger in words", async () => {
    const { container } = mount(<PaymentCommissions />, {
      [`GET ${API}/balance`]: [
        ok({
          balances: [{ currency: "INR", balance_minor: 30000, pending_minor: 10000, available_minor: 15000, in_payouts_minor: 0, paid_minor: 5000, entries: 3 }],
          entries: [],
          not_implemented: {},
        }),
      ],
      [`GET ${API}/commissions`]: [
        ok({
          results: [{ id: "a1", kind: "reversal", amount_minor: -2000, currency: "INR", event_at: "2026-11-02T00:00:00Z", clears_at: "2026-11-02T00:00:00Z" }],
          next_cursor: null,
          has_more: false,
          balances: [{ currency: "INR", earned_minor: 32000, adjustments_minor: -2000, balance_minor: 30000 }],
        }),
      ],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Commissions" })).toBeInTheDocument();
    const cards = await screen.findByRole("group", { name: "Earnings" });
    await waitFor(() => expect(within(cards).getByText("Reversals & adjustments")).toBeInTheDocument());
    const card = (label: string) => within(cards).getByText(label).parentElement!;
    expect(card("Pending earnings")).toHaveTextContent(money(10000, "INR"));
    expect(card("Available earnings")).toHaveTextContent(money(15000, "INR"));
    expect(card("Reversals & adjustments")).toHaveTextContent(money(-2000, "INR"));
    expect(card("Total paid out")).toHaveTextContent(money(5000, "INR"));
    const ledger = await screen.findByRole("table", { name: "Commission ledger entries" });
    expect(ledger).toHaveTextContent("Reversed");
    expect(ledger).not.toHaveTextContent("reversal");
    await expectNoViolations(container);
  });
});

describe("Billing Records", () => {
  it("lists the partner's own payments and its wallet's payments for customer plans", async () => {
    mount(<BillingRecords />, {
      [`GET ${API}/billing-records`]: [
        ok({
          partner_payments: [{ id: "f1", purpose: "partner_wallet_top_up", label: "Platform Wallet top-up", amount_minor: 500000, currency: "INR", status: "succeeded", created_at: "2026-11-01T00:00:00Z", completed_at: null, refund_status: "none" }],
          wallet_charges: WALLET.recent,
          orders_visible: true,
        }),
      ],
    });
    expect(await screen.findByRole("table", { name: "Your payments to Workeasy360" })).toHaveTextContent("Platform Wallet top-up");
    expect(screen.getByRole("table", { name: "Plans paid from your Platform Wallet" })).toHaveTextContent("ORD-000123");
    expect(screen.getByRole("link", { name: "Customer orders and invoice references" })).toHaveAttribute("href", `${BASE}/payments`);
  });

  it("shows a partner without a Platform Wallet (White Label alone) only its own payments", async () => {
    const whiteLabel = partner({ level: "white_label", permissions: perm("dashboard.view", "branding.view", "branding.manage", "domain.view", "domain.manage") });
    mount(
      <BillingRecords />,
      { [`GET ${API}/billing-records`]: [ok({ partner_payments: [], wallet_charges: [], orders_visible: false })] },
      whiteLabel,
    );
    expect(await screen.findByText("No payments yet.")).toBeInTheDocument();
    expect(screen.getByText("Your payments to Workeasy360, with their status.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Wallet payments for customer plans" })).toBeNull();
    expect(screen.queryByRole("table", { name: "Plans paid from your Platform Wallet" })).toBeNull();
    expect(screen.queryByText(/Platform Wallet has paid/)).toBeNull();
  });
});

describe("Products & Referrals", () => {
  const OVERVIEW_WITH_LINK = {
    links: [{ id: "l1", code: "ABCD1234", label: "General", status: "active", created_at: "", campaign: "", campaign_id: null, campaign_name: null, product_code: "", product_name: null, plan_code: "", url: "https://app.example.com/r/ABCD1234" }],
  };

  it("offers the Store referral link, and the purchase price of each plan it may sell", async () => {
    mount(<EligibleProducts />, {
      [`GET ${API}/affiliate/overview`]: [ok(OVERVIEW_WITH_LINK)],
      [`GET ${API}/affiliate/store`]: [ok({ kind: "promote", products: [], requests: [], counts: { approved: 0, explore: 0, pending: 0 }, can_request: true, cannot_request_reason: null, channels: {}, prefill: { website: "" } })],
      [`GET ${API}/plans`]: [ok(PLANS)],
    });
    expect(await screen.findByRole("textbox", { name: "Store referral link" })).toHaveValue("https://app.example.com/r/ABCD1234?to=store");
    expect(screen.getByRole("button", { name: "Copy Store referral link" })).toBeInTheDocument();
    const plans = await screen.findByRole("table", { name: "Easy CRM plans and your purchase price" });
    expect(plans).toHaveTextContent(money(399900, "INR"));
    expect(screen.getByRole("link", { name: "Activate a plan for a customer" })).toHaveAttribute("href", `${BASE}/customers/activate`);
  });

  it("Sales Resources and Help Content show published material only, and say when there is none", async () => {
    mount(<SalesResources />, { [`GET ${API}/sales-resources`]: [ok({ products: [], guides: [] })] });
    expect(await screen.findByText("No sales resources yet")).toBeInTheDocument();
  });

  it("Help Content lists FAQs and guides and filters by kind", async () => {
    const { calls } = mount(<HelpContent />, {
      [`GET ${API}/help-articles`]: [ok({ items: [{ slug: "kyc", kind: "faq", title: "Which KYC documents do I need?", body: "A PAN and an address proof.", product_code: "" }] })],
    });
    expect(await screen.findByText("Which KYC documents do I need?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contact Support" })).toHaveAttribute("href", `${BASE}/support-manager?new=1`);
    fireEvent.click(screen.getByRole("button", { name: "Setup instructions" }));
    await waitFor(() => expect(calls.at(-1)!.url).toContain("kind=setup_guide"));
  });
});

describe("before a partner may trade", () => {
  const draft = partner({
    status: "draft",
    permissions: perm("dashboard.view", "profile.view"),
    onboarding: { kyc_status: "correction_required", account_status: "draft", cleared: false, reason: "kyc_pending", legacy: false, kyc_required: true },
  });

  it("the dashboard is the onboarding status and the next step, nothing commercial", async () => {
    const { container } = mount(
      <UnifiedDashboard level="affiliate" />,
      {
        [`GET ${API}/dashboard`]: [ok({})],
        [`GET ${API}/upgrade`]: [ok({})],
        [`GET ${API}/notifications`]: [ok({ items: [], counts: { all: 0, unread: 0 } })],
        [`GET ${API}/products`]: [ok({ products: [] })],
      },
      draft,
    );
    expect(await screen.findByRole("heading", { name: "Onboarding status" })).toBeInTheDocument();
    expect(screen.getByText(/asked for corrections/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Business & KYC" })).toHaveAttribute("href", `${BASE}/business-kyc`);
    expect(screen.queryByRole("link", { name: "Add Money" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Quick actions" })).toBeNull();
    await expectNoViolations(container);
  });

  it("Business & KYC shows the KYC and account states side by side", async () => {
    mount(
      <BusinessKyc />,
      {
        [`GET ${API}/profile`]: [{ status: 403, body: { code: "permission_denied" } }],
        [`GET ${API}/kyc`]: [
          ok({ status: "correction_required", version: 2, details: {}, requested_experience: "affiliate", submitted_at: null, decided_at: null, decision_reason: "", correction: { reason: "The PAN is unreadable.", flagged_requirements: [], decided_at: "2026-11-02T00:00:00Z" }, requirements: [], documents: [], editable: true, may_edit: true, history: [{ decision: "request_correction", from_status: "under_review", to_status: "correction_required", reason: "The PAN is unreadable.", decided_at: "2026-11-02T00:00:00Z" }] }),
        ],
      },
      draft,
    );
    // The six onboarding steps (2026-11-11 brief, section 9): KYC Documents is
    // where the correction is asked for, marked as the current step.
    const progress = (await screen.findByRole("heading", { name: "Onboarding progress" })).closest("section")!;
    const steps = within(progress).getAllByRole("listitem");
    expect(steps.map((li) => li.textContent)).toEqual([
      "Account VerificationDone",
      "Business ProfileDone",
      "!KYC DocumentsCorrection required",
      "4Application SubmissionNot started",
      "5Admin ReviewNot started",
      "6Approval / ActivationNot started",
    ]);
    expect(steps[2]).toHaveAttribute("aria-current", "step");
    expect(progress).toHaveTextContent("Workeasy360 asked for corrections.");
    expect(screen.getByRole("heading", { level: 1, name: "Business & KYC" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 2, name: "KYC application" })).toBeInTheDocument();
    expect(await screen.findByText(/KYC history \(1\)/)).toBeInTheDocument();
  });
});
