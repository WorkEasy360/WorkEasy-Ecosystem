import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import { checkoutNavigation } from "@/features/console/BillingScreen";
import { StoreShell } from "./StoreShell";
import { StoreHomeScreen } from "./StoreHome";
import { ProductDetailsScreen } from "./ProductDetails";
import { CheckoutScreen } from "./Checkout";
import { RechargeScreen } from "./Recharge";
import { PolicyScreen } from "./Policy";
import { HelpScreen } from "./Help";
import { PartnersScreen } from "./Partners";

/**
 * The Digital Store's pages (ADR-065), as a buyer meets them: the four menus and
 * no cart; product cards that never sell what is Coming Soon; a product page with
 * its plans, renewal, setup and apps; a checkout that prices nothing itself and
 * pays part from the Platform Wallet only through the server; three separate
 * recharges; policies only as published.
 */

let search = new URLSearchParams();
const push = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push, refresh: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn() }),
  useSearchParams: () => search,
  usePathname: () => "/store",
}));

const PLAN_PRO = "11111111-1111-1111-1111-111111111111";
const PLAN_FREE = "22222222-2222-2222-2222-222222222222";
const INVOICE = "33333333-3333-3333-3333-333333333333";
const ORDER = "44444444-4444-4444-4444-444444444444";
const ATTEMPT = "55555555-5555-5555-5555-555555555555";

const plan = (id: string, code: string, name: string, amount: number, model = "per_seat") => ({
  plan_version_id: id,
  plan_code: code,
  name,
  caption: "",
  description: "",
  billing_type: "paid",
  kind: "standard",
  addon_for: null,
  included_seats: 1,
  max_seats: 50,
  trial_days: 0,
  trial_available: false,
  recommended: code === "crm_pro",
  renewal_rule: "auto_renew",
  included_usage: code === "crm_pro" ? { ai_tokens: 10000, whatsapp: 5000 } : {},
  prices: [{ interval: "annual", currency: "INR", model, amount_minor: amount, from_tiers: false }],
});

const CRM = {
  code: "easy_crm",
  slug: "easy-crm",
  name: "Easy CRM",
  title: "WorkEasy CRM",
  tagline: "Sales & Lead Management",
  summary: "Manage leads, deals and customer relationships.",
  description: "",
  marketing_url: "",
  availability: "available",
  status: "available",
  category: "Sales",
  icon: "",
  features: [],
  highlights: ["Lead Management", "Sales Pipeline", "Follow-ups & Tasks", "Reports"],
  plans: [plan(PLAN_PRO, "crm_pro", "Advanced", 2400000), plan(PLAN_FREE, "crm_free", "Free", 0, "flat")],
  starting_price: null,
  trial: { available: true, duration_days: 7, seat_limit: null, payment_method_required: false, plan_codes: ["crm_pro"] },
  ribbon: null,
  price: { amount_minor: 2400000, currency: "INR", interval: "annual", plan_name: "Advanced" },
  yearly_deal: null,
  badges: ["popular"],
  plan_types: [],
  trial_days: 7,
  logo_url: null,
  banner_url: null,
};

const SOON = {
  ...CRM,
  code: "easy_hr",
  slug: "easy-hr",
  title: "WorkEasy HR",
  name: "Easy HR",
  status: "coming_soon",
  availability: "coming_soon",
  category: "HR",
  plans: [],
  price: null,
  badges: [],
  trial: { available: false, duration_days: null, seat_limit: null, payment_method_required: false, plan_codes: [] },
};

const CATALOG = {
  currency: "INR",
  currencies: ["INR"],
  count: 2,
  total: 2,
  products: [CRM, SOON],
  facets: {
    categories: [
      { value: "Sales", label: "Sales", count: 1 },
      { value: "HR", label: "HR", count: 1 },
      { value: "Empty", label: "Empty", count: 0 },
    ],
    statuses: [
      { value: "available", label: "Available", count: 1 },
      { value: "coming_soon", label: "Coming soon", count: 1 },
    ],
    plan_types: [],
    best_for: [],
    price: { min_minor: 0, max_minor: 2400000 },
  },
  sorts: [{ value: "recommended", label: "Recommended" }],
};

const SITE = { signup_enabled: true, support_email: "support@workeasy360.com", sales_email: "sales@workeasy360.com", catalog_currency: "INR" };
const SIGNED_OUT: Reply = { status: 200, body: { authenticated: false } };
const SIGNED_IN: Reply = { status: 200, body: { authenticated: true, user_id: "u1", email: "mk@acme.example", name: "Mohan Kumar" } };
const ONE_ORG = ok({ organizations: [{ organization_id: "o1", slug: "acme", display_name: "Acme Enterprises Pvt. Ltd.", status: "active", membership_id: "m1", is_owner: true }], next_cursor: null, has_more: false });
const OWNER = ok({
  organization: { organization_id: "o1", slug: "acme", display_name: "Acme Enterprises Pvt. Ltd.", status: "active", write_allowed: true },
  membership: { membership_id: "m1", is_owner: true, roles: ["owner"] },
  permissions: ["commercial.subscription.manage", "platform.billing.manage", "platform.billing.view"],
  console: "admin",
});
const PROFILE = ok({
  billing_account: {
    id: "b1",
    currency: "INR",
    collection_mode: "automatic",
    billing_email: "billing@acmeenterprise.com",
    billing_region: "",
    tax_reference: "07AAACA1234F1Z5",
    legal_name: "Acme Enterprises Pvt. Ltd.",
    address_line1: "",
    address_line2: "",
    city: "",
    state: "",
    postal_code: "",
    country: "",
    updated_at: null,
  },
});
const wallets = (platform: number) =>
  ok({
    currency: "INR",
    can_spend: true,
    platform: { exists: true, currency: "INR", balance_minor: platform, low_balance_threshold: null, is_low: false, recent: [], other_currencies: [] },
    ai_credits: { exists: true, balance: 250, low_balance_threshold: null, is_low: false, included_per_month: null, used_this_month: 0, purchased_total: 250, resets_at: null, recent: [] },
    consumption: { exists: true, currency: "INR", balance_minor: 0, low_balance_threshold: null, is_low: false, recent: [], this_month: [] },
    credit_packs: [{ code: "starter", name: "Starter", credits: 1000, price_minor: 49900, currency: "INR" }],
  });
const QUOTE = ok({
  kind: "plan",
  description: "Easy CRM Advanced",
  product_codes: ["easy_crm"],
  currency: "INR",
  billing_interval: "annual",
  quantity: 1,
  recurring_amount_minor: 2400000,
  amount_due_minor: 2832000,
  prorated: false,
  is_trial: false,
  trial_days: 0,
  requires_payment: true,
  setup_fee_minor: 0,
  subtotal_minor: 2400000,
  coupon: null,
  discount_minor: 0,
  tax: { label: "GST", rate_bp: 1800, amount_minor: 432000, description: "GST (18%)" },
  tax_minor: 432000,
  lines: [],
});
const RAZORPAY = ok({ available: true, provider: "razorpay", label: "Razorpay", methods: ["UPI", "Cards", "Net Banking", "Wallets"], test_mode: false });

function shellRoutes(signedIn: boolean): Record<string, Reply[]> {
  return {
    "GET /api/v1/public/store": [ok(CATALOG)],
    "GET /api/v1/public/site": [ok(SITE)],
    "GET /api/v1/auth/session": [signedIn ? SIGNED_IN : SIGNED_OUT],
    "GET /api/v1/organizations": [ONE_ORG],
    "GET /api/v1/orgs/acme/notifications": [ok({ items: [], count: 0 })],
  };
}

beforeEach(() => {
  search = new URLSearchParams();
  push.mockReset();
  replace.mockReset();
  window.sessionStorage.clear();
  window.localStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

describe("the Store's frame", () => {
  it("has the four menus, Sign In and Get Started - and no cart", async () => {
    mockApi(shellRoutes(false));
    const { container } = render(
      <StoreShell>
        <p>page</p>
      </StoreShell>,
    );
    const nav = screen.getByRole("navigation", { name: "Store" });
    expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(["Products", "Recharge", "Partners", "Help"]);
    expect(await screen.findByRole("link", { name: "Sign In" })).toHaveAttribute("href", "/login?next=%2Fstore");
    expect(screen.getByRole("link", { name: "Get Started" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /cart/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /checkout/i })).toBeNull();
    fireEvent.click(within(nav).getByRole("button", { name: "Products" }));
    // Only departments with products are offered.
    expect(await screen.findByRole("link", { name: /Sales/ })).toHaveAttribute("href", "/store/products?category=Sales");
    expect(screen.queryByRole("link", { name: /Empty/ })).toBeNull();
    // The footer's policies.
    const legal = screen.getByRole("navigation", { name: "Legal" });
    expect(within(legal).getAllByRole("link")).toHaveLength(8);
    await expectNoViolations(container);
  });

  it("signed in: My Account, the organization, and Sign out", async () => {
    mockApi(shellRoutes(true));
    render(
      <StoreShell>
        <p>page</p>
      </StoreShell>,
    );
    const header = screen.getByRole("banner");
    expect(await within(header).findByRole("button", { name: "My Account" })).toBeInTheDocument();
    fireEvent.click(within(header).getByRole("button", { name: /Account menu for Mohan Kumar/ }));
    expect(screen.getByText("Acme Enterprises Pvt. Ltd.")).toBeInTheDocument();
    for (const label of ["My Products", "Billing & Subscriptions", "Wallets & Credits", "Organization & Team", "Support", "Account & Security", "Sign out"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });
});

describe("the marketplace", () => {
  it("shows each product's price, free plan and trial - and never sells Coming Soon", async () => {
    mockApi(shellRoutes(false));
    render(
      <StoreShell>
        <StoreHomeScreen />
      </StoreShell>,
    );
    expect(await screen.findByRole("link", { name: "Buy now: WorkEasy CRM" })).toHaveAttribute("href", "/store/product/easy-crm#plans");
    expect(screen.getAllByText("/user/year").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Free plan").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7-day free trial").length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: "Buy now: WorkEasy HR" })).toBeNull();
    expect(screen.getByRole("link", { name: "View details: WorkEasy HR" })).toBeInTheDocument();
    // A department tab filters; an empty department is not a tab.
    expect(screen.queryByRole("button", { name: "Empty" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "HR" }));
    expect(screen.queryByRole("link", { name: "View details: WorkEasy CRM" })).toBeNull();
    // A benefit is named only when the catalog bears it out.
    expect(screen.getByText("Start Free")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "How It Works" })).toBeInTheDocument();
  });
});

const PAGE = {
  currency: "INR",
  currencies: ["INR"],
  tax: { label: "GST", rate_bp: 1800, description: "GST (18%)" },
  product: {
    ...CRM,
    subcategory: "",
    tags: [],
    benefits: [],
    use_cases: [],
    setup_requirements: ["A WhatsApp Business number"],
    access: { web: true, apps: { android: "https://play.google.com/store/apps/details?id=x" } },
    documentation_url: "",
    support_url: "",
    logo: null,
    banner: null,
    screenshots: [],
    video: null,
    refund_days: 30,
  },
  plans: CRM.plans,
  comparison: [{ key: "pipelines", label: "Sales pipelines", kind: "boolean", unit: "", values: { [PLAN_PRO]: true } }],
  related: [],
};

describe("a product's page", () => {
  it("sells the chosen plan through the checkout, by identifiers only", async () => {
    mockApi({
      ...shellRoutes(false),
      "GET /api/v1/public/store/products/easy-crm": [ok(PAGE)],
      "GET /api/v1/public/store/recharge": [
        ok({
          currency: "INR",
          rates: [{ meter: "whatsapp", label: "WhatsApp messages", wallet: "consumption", currency: "INR", unit_amount: 80, per_units: 1, included_units: 0, effective_from: "" }],
          credit_packs: [],
          payment: { available: true, provider: "razorpay", label: "Razorpay", methods: [] },
        }),
      ],
    });
    render(
      <StoreShell>
        <ProductDetailsScreen slug="easy-crm" />
      </StoreShell>,
    );
    expect(await screen.findByRole("link", { name: /Buy Now/ })).toHaveAttribute("href", `/store/checkout?plan=${PLAN_PRO}&interval=annual`);
    expect(screen.getByRole("link", { name: /Start 7-day Free Trial/ })).toHaveAttribute("href", "/store/checkout?trial=easy_crm&trial_plan=crm_pro");
    // Plans & Pricing: renewal, included usage, the usage rates beyond it.
    expect(screen.getByText(/Renews every year at the plan's price, currently/)).toBeInTheDocument();
    expect(screen.getByText("10,000 AI tokens")).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "Additional usage rates" })).toBeInTheDocument();
    // Overview: setup requirements; App Availability: the official link only.
    expect(screen.getByText("A WhatsApp Business number")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Get the official app/ })).toHaveAttribute("href", "https://play.google.com/store/apps/details?id=x");
    // More seats: the estimate follows, GST added on top.
    fireEvent.click(screen.getByRole("button", { name: "More seats" }));
    expect(screen.getByRole("link", { name: /Buy Now/ })).toHaveAttribute("href", `/store/checkout?plan=${PLAN_PRO}&interval=annual&quantity=2`);
  });

  it("a Coming Soon product shows everything and sells nothing", async () => {
    mockApi({
      ...shellRoutes(false),
      "GET /api/v1/public/store/products/easy-hr": [ok({ ...PAGE, product: { ...PAGE.product, ...SOON, setup_requirements: [], access: { web: false, apps: {} } }, plans: [] })],
    });
    render(
      <StoreShell>
        <ProductDetailsScreen slug="easy-hr" />
      </StoreShell>,
    );
    expect(await screen.findByText("Not on sale yet")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Buy Now/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Free Trial/ })).toBeNull();
  });
});

describe("the checkout", () => {
  it("signed out, asks for an account first - sign in or create one", async () => {
    search = new URLSearchParams(`plan=${PLAN_PRO}&interval=annual`);
    mockApi(shellRoutes(false));
    render(
      <StoreShell>
        <CheckoutScreen />
      </StoreShell>,
    );
    expect(await screen.findByRole("radio", { name: /Existing Account/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /Create New Account/ })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Sign in" })).toBeInTheDocument();
    expect(await screen.findByText("Advanced")).toBeInTheDocument(); // the order summary
    expect(screen.getByText("Sign in to see the final amount and pay.")).toBeInTheDocument();
  });

  it("pays part from the Platform Wallet and the rest online - the server's amounts", async () => {
    search = new URLSearchParams(`plan=${PLAN_PRO}&interval=annual`);
    const assign = vi.spyOn(checkoutNavigation, "assign").mockImplementation(() => undefined);
    const calls = mockApi({
      ...shellRoutes(true),
      "GET /api/v1/orgs/acme/access": [OWNER],
      "GET /api/v1/orgs/acme/billing-account": [PROFILE],
      "GET /api/v1/orgs/acme/wallets": [wallets(1_000_000)],
      "POST /api/v1/orgs/acme/orders/quote": [QUOTE],
      "GET /api/v1/public/store/payment-option": [RAZORPAY],
      "POST /api/v1/referrals/convert": [ok({ converted: false })],
      "POST /api/v1/orgs/acme/orders": [{ status: 201, body: { order_id: ORDER, number: "ORD-000123", requires_payment: true, invoice_id: INVOICE, kind: "plan", status: "pending_payment" } }],
      [`POST /api/v1/orgs/acme/billing/invoices/${INVOICE}/pay`]: [
        {
          status: 202,
          body: { attempt_id: ATTEMPT, invoice_id: INVOICE, status: "awaiting_payment", amount_minor: 1832000, currency: "INR", provider: "razorpay", checkout_url: "https://rzp.io/l/abc", failure_code: null, expires_at: "", wallet_amount_minor: 1000000, wallet_status: "held" },
        },
      ],
    });
    const { container } = render(
      <StoreShell>
        <CheckoutScreen />
      </StoreShell>,
    );
    // One organization: chosen by itself.
    expect(await screen.findByText("Current Organization")).toBeInTheDocument();
    expect(await screen.findByText("GST (18%)")).toBeInTheDocument();
    expect(screen.getAllByText("₹28,320").length).toBeGreaterThan(0);
    expect(screen.getByRole("radio", { name: /Razorpay/ })).toBeChecked();
    fireEvent.click(screen.getByRole("switch", { name: "Use Platform Wallet" }));
    expect(screen.getByLabelText("Wallet Amount to Use")).toHaveValue("10000");
    expect(screen.getByText("₹18,320")).toBeInTheDocument(); // Amount Payable Now
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: /Pay ₹18,320 & Subscribe/ }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://rzp.io/l/abc"));
    const order = calls.find((c) => c.key === "POST /api/v1/orgs/acme/orders");
    expect(order?.body).toEqual({ plan_version_id: PLAN_PRO, billing_interval: "annual" }); // identifiers, never a price
    const pay = calls.find((c) => c.key.endsWith("/pay"));
    expect(pay?.body).toEqual({ wallet_amount_minor: 1000000 });
    expect(pay?.headers["idempotency-key"]).toBeTruthy();
  });

  it("without online payment, only the wallet may pay the whole amount", async () => {
    search = new URLSearchParams(`plan=${PLAN_PRO}&interval=annual`);
    mockApi({
      ...shellRoutes(true),
      "GET /api/v1/orgs/acme/access": [OWNER],
      "GET /api/v1/orgs/acme/billing-account": [PROFILE],
      "GET /api/v1/orgs/acme/wallets": [wallets(100)],
      "POST /api/v1/orgs/acme/orders/quote": [QUOTE],
      "GET /api/v1/public/store/payment-option": [ok({ available: false, provider: null, label: null, methods: [] })],
    });
    render(
      <StoreShell>
        <CheckoutScreen />
      </StoreShell>,
    );
    expect(await screen.findByText(/Online payment in INR is not available yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pay ₹28,320 & Subscribe/ })).toBeDisabled();
  });

  it("a plan that cannot join the organization's billing period is explained, not a catalog error", async () => {
    const { quoteProblem } = await import("./Checkout");
    const { ApiError } = await import("@/lib/api/client");
    const err = new ApiError(409, { code: "price_not_available", interval: "monthly", detail: "This is a catalog defect" });
    expect(quoteProblem(err)).toBe(
      "Your organization's subscription renews monthly, so a new product joins it monthly - and this plan has no monthly price. Choose a plan sold monthly, or contact us.",
    );
  });

  it("nothing chosen is said plainly", async () => {
    mockApi(shellRoutes(false));
    render(
      <StoreShell>
        <CheckoutScreen />
      </StoreShell>,
    );
    expect(screen.getByText("Nothing was chosen to buy.")).toBeInTheDocument();
  });
});

describe("Recharge", () => {
  it("keeps the three balances apart and pays a top-up online only", async () => {
    vi.spyOn(checkoutNavigation, "assign").mockImplementation(() => undefined);
    const calls = mockApi({
      ...shellRoutes(true),
      "GET /api/v1/orgs/acme/wallets": [wallets(500000)],
      "GET /api/v1/public/store/recharge": [ok({ currency: "INR", rates: [], credit_packs: [], payment: { available: true, provider: "razorpay", label: "Razorpay", methods: ["UPI"] } })],
      "POST /api/v1/orgs/acme/wallets/top-up": [{ status: 201, body: { order_id: ORDER, number: "ORD-000124", invoice_id: INVOICE, amount_due_minor: 200000, currency: "INR", requires_payment: true, kind: "wallet", status: "pending_payment" } }],
      [`POST /api/v1/orgs/acme/billing/invoices/${INVOICE}/pay`]: [
        { status: 202, body: { attempt_id: ATTEMPT, invoice_id: INVOICE, status: "awaiting_payment", amount_minor: 200000, currency: "INR", provider: "razorpay", checkout_url: "https://rzp.io/l/top", failure_code: null, expires_at: "" } },
      ],
    });
    render(
      <StoreShell>
        <RechargeScreen />
      </StoreShell>,
    );
    expect(await screen.findByText("₹5,000")).toBeInTheDocument(); // Platform Wallet
    expect(screen.getByText("250")).toBeInTheDocument(); // AI credits, never money
    const platform = screen.getByRole("region", { name: "Platform Wallet" });
    fireEvent.change(within(platform).getByLabelText("Recharge amount"), { target: { value: "2000" } });
    fireEvent.click(within(platform).getByRole("button", { name: /Recharge Platform Wallet/ }));
    expect(screen.getByText(/₹2,000 into your Platform Wallet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Confirm & Pay/ }));
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/pay"))).toBe(true));
    expect(calls.find((c) => c.key.endsWith("/top-up"))?.body).toEqual({ wallet_kind: "platform", amount_minor: 200000, currency: "INR" });
    expect(calls.find((c) => c.key.endsWith("/pay"))?.body).toEqual({ wallet_amount_minor: 0 });
  });

  it("signed out, shows the terms and asks to sign in", async () => {
    mockApi({
      ...shellRoutes(false),
      "GET /api/v1/public/store/recharge": [ok({ currency: "INR", rates: [], credit_packs: [{ code: "starter", name: "Starter", credits: 1000, price_minor: 49900, currency: "INR" }], payment: { available: true, provider: "razorpay", label: "Razorpay", methods: [] } })],
    });
    render(
      <StoreShell>
        <RechargeScreen />
      </StoreShell>,
    );
    expect(await screen.findByRole("button", { name: "Buy Credits" })).toBeDisabled();
    expect(screen.getAllByRole("link", { name: "Sign in" }).some((a) => a.getAttribute("href") === "/login?next=%2Fstore%2Frecharge")).toBe(true);
    expect(screen.getByText(/No usage rates are set in INR yet/)).toBeInTheDocument();
  });
});

describe("policies, help and partners", () => {
  it("a policy shows only what was published", async () => {
    mockApi({ ...shellRoutes(false), "GET /api/v1/public/help": [ok({ results: [] })] });
    const first = render(
      <StoreShell>
        <PolicyScreen policyKey="refund-cancellation" />
      </StoreShell>,
    );
    expect(await screen.findByText(/has not been published yet/)).toBeInTheDocument();
    first.unmount();
    mockApi({
      ...shellRoutes(false),
      "GET /api/v1/public/help": [ok({ results: [{ slug: "refund-cancellation", kind: "policy", title: "Refunds", body: "Approved text.", product_code: "" }] })],
    });
    render(
      <StoreShell>
        <PolicyScreen policyKey="refund-cancellation" />
      </StoreShell>,
    );
    expect(await screen.findByText("Approved text.")).toBeInTheDocument();
  });

  it("help answers by topic and names the support address", async () => {
    mockApi({ ...shellRoutes(false), "GET /api/v1/public/help": [ok({ results: [] })] });
    render(
      <StoreShell>
        <HelpScreen />
      </StoreShell>,
    );
    for (const topic of ["Buying a product", "Subscriptions", "Payments & GST", "Refunds", "After you buy", "Renewals", "Recharge"]) {
      expect(screen.getByRole("heading", { name: topic })).toBeInTheDocument();
    }
    expect((await screen.findAllByRole("link", { name: "support@workeasy360.com" })).length).toBeGreaterThan(0);
  });

  it("partners apply on the Partner portal, to the two public programmes", async () => {
    mockApi({
      ...shellRoutes(false),
      "GET /api/v1/public/store/partner-programme": [
        ok({
          applications_open: true,
          programmes: [
            { key: "affiliate", label: "Affiliate", description: "Promote Workeasy360 products and earn commission from referrals." },
            { key: "reseller", label: "Reseller", description: "Sell Workeasy360 products to customers." },
          ],
          kyc_requirements: [{ label: "PAN card", help_text: "", is_required: true }],
          kyc_required: true,
          stages: [{ key: "draft", label: "Draft" }, { key: "approved", label: "Approved" }],
        }),
      ],
    });
    render(
      <StoreShell>
        <PartnersScreen />
      </StoreShell>,
    );
    expect(await screen.findByRole("link", { name: "Apply as Affiliate" })).toHaveAttribute("href", "/partner/apply?type=affiliate");
    expect(screen.getByRole("link", { name: "Apply as Reseller" })).toHaveAttribute("href", "/partner/apply?type=reseller");
    expect(screen.queryByText(/White[- ]label/i)).toBeNull();
    expect(screen.getByText("PAN card")).toBeInTheDocument();
  });
});
