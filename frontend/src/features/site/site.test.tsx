import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { SiteShell, cleanIntent, PURCHASE_INTENT } from "./SiteShell";
import { ContactPage, LandingPage, PricingPage, ProductPage, ProductsPage } from "./SitePages";
import {
  ForgotPasswordScreen,
  ResetPasswordScreen,
  SignupScreen,
  VerifyEmailScreen,
} from "@/features/auth/SelfService";

/**
 * The public site and self-service accounts (Phase 9): the catalog drives
 * every product shown, a purchase is only ever an intent, and the account
 * screens send exactly what the API expects - tokens in the body, never in a
 * URL - and say the same thing whether or not an address is registered.
 */

const nav = vi.hoisted(() => ({
  router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() },
  search: new URLSearchParams(),
  pathname: "/",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => nav.search,
  usePathname: () => nav.pathname,
  useParams: () => ({ slug: "crm" }),
}));

configure({ asyncUtilTimeout: 4000 });

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

const PV = "11111111-1111-4111-8111-111111111111";
const SIGNED_OUT: Reply = { status: 200, body: { authenticated: false } };
const SIGNED_IN: Reply = { status: 200, body: { authenticated: true, user_id: "u1", email: "a@example.com", name: "Ada" } };
const SITE: Reply = {
  status: 200,
  body: { signup_enabled: true, support_email: "help@example.com", sales_email: "sales@example.com", catalog_currency: "INR" },
};

function product(code: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    code,
    slug: code.replace(/_/g, "-"),
    name,
    tagline: `${name} for growing teams`,
    description: `${name} does **useful** things.`,
    marketing_url: "",
    availability: "available",
    category: "Sales / CRM",
    icon: "customers",
    features: [{ name: "Pipelines", description: "Track every deal." }],
    plans: [
      {
        plan_version_id: PV,
        plan_code: `${code}_pro`,
        name: `${name} Pro`,
        caption: "",
        description: "",
        billing_type: "paid",
        kind: "standard",
        addon_for: null,
        included_seats: 5,
        max_seats: null,
        trial_days: 14,
        prices: [{ interval: "monthly", currency: "INR", model: "flat", amount_minor: 99900, from_tiers: false }],
        trial_available: false,
      },
    ],
    starting_price: { amount_minor: 99900, currency: "INR", interval: "monthly" },
    trial: { available: false, duration_days: null, seat_limit: null, payment_method_required: false, plan_codes: [] },
    ...extra,
  };
}

const CATALOG = {
  currency: "INR",
  currencies: ["INR", "USD"],
  products: [product("crm", "Sales Desk"), product("ledger", "Ledger", { availability: "coming_soon", plans: [], starting_price: null })],
  bundles: [],
};

beforeEach(() => {
  nav.router.push.mockReset();
  nav.router.replace.mockReset();
  nav.search = new URLSearchParams();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the public site", () => {
  it("lists every product the catalog returns - nothing hard-coded - and passes axe", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "GET /api/v1/public/catalog": [{ status: 200, body: CATALOG }],
    });
    const { container } = render(
      <SiteShell>
        <LandingPage />
      </SiteShell>,
    );
    expect(await screen.findByRole("heading", { name: "Sales Desk" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ledger" })).toBeInTheDocument();
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Learn more about Sales Desk" })).toHaveAttribute("href", "/products/crm");
    for (const label of ["Sign in", "Sign up", "Get started"]) {
      expect(within(screen.getByRole("banner")).getByRole("link", { name: label })).toBeInTheDocument();
    }
    await expectNoViolations(container);
  });

  it("the Products menu is a keyboard disclosure built from the catalog", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/public/catalog": [{ status: 200, body: CATALOG }],
    });
    render(
      <SiteShell>
        <p>Page</p>
      </SiteShell>,
    );
    const button = screen.getByRole("button", { name: /Products/ });
    expect(button).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByRole("link", { name: /Sales Desk/ })).toHaveAttribute("href", "/products/crm");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    // Signed in: the header offers the dashboard instead of sign-up.
    expect(await screen.findByRole("link", { name: "Go to dashboard" })).toHaveAttribute("href", "/dashboard");
  });

  it("buying from a product page remembers only an intent and sends a visitor to sign up", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "GET /api/v1/public/catalog/products/crm": [
        { status: 200, body: { currency: "INR", currencies: ["INR"], product: CATALOG.products[0], bundles: [] } },
      ],
    });
    const { container } = render(<ProductPage slug="crm" />);
    fireEvent.click(await screen.findByRole("button", { name: "Start free trial: Sales Desk Pro" }));
    expect(nav.router.push).toHaveBeenCalledWith("/signup");
    const stored = JSON.parse(localStorage.getItem(PURCHASE_INTENT) ?? "{}");
    expect(stored).toMatchObject({ plan: PV, interval: "monthly", trial: true });
    expect(typeof stored.savedAt).toBe("number"); // Phase 11: a TTL, so a stale intent expires
    expect(screen.getByRole("heading", { name: "Features" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("a store card's Start free trial remembers the product's own trial, not a checkout (Phase 12)", async () => {
    const crm = product("crm", "Sales Desk", {
      trial: { available: true, duration_days: 14, seat_limit: 3, payment_method_required: false, plan_codes: ["crm_pro"] },
    });
    crm.plans[0].trial_available = true;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "GET /api/v1/public/catalog": [{ status: 200, body: { ...CATALOG, products: [crm] } }],
    });
    render(<ProductsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Start free trial: Sales Desk" }));
    expect(nav.router.push).toHaveBeenCalledWith("/signup");
    const stored = JSON.parse(localStorage.getItem(PURCHASE_INTENT) ?? "{}");
    expect(stored).toMatchObject({ productTrial: { product: "crm", plan: "crm_pro" } });
    expect(stored.plan).toBeUndefined();
    expect(stored.trial).toBeUndefined();
  });

  it("an intent is only ever identifiers it can validate", () => {
    expect(cleanIntent({ plan: "../../admin" })).toBeNull();
    expect(cleanIntent({ plan: PV, interval: "forever" })).toEqual({ plan: PV, bundle: undefined, interval: undefined, trial: false });
    expect(cleanIntent(null)).toBeNull();
  });

  it("a product-trial intent is only ever a product code and a plan code (Phase 11)", () => {
    expect(cleanIntent({ productTrial: { product: "crm", plan: "crm_starter" } })).toEqual({
      productTrial: { product: "crm", plan: "crm_starter" },
    });
    expect(cleanIntent({ productTrial: { product: "../../admin", plan: "x" } })).toBeNull();
    expect(cleanIntent({ productTrial: { product: "CRM", plan: "x" } })).toBeNull();
    expect(cleanIntent({ productTrial: { product: "crm", plan: "" } })).toBeNull();
    expect(cleanIntent({ productTrial: { product: "", plan: "" } } as never)).toBeNull();
  });

  it("pricing switches currency on request", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "GET /api/v1/public/catalog": [{ status: 200, body: CATALOG }],
    });
    render(<PricingPage />);
    const currency = await screen.findByLabelText("Currency");
    fireEvent.change(currency, { target: { value: "USD" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("currency=USD"))).toBe(true));
  });

  it("contact shows the addresses the platform settings publish", async () => {
    mockApi({ "GET /api/v1/public/site": [SITE] });
    render(<ContactPage />);
    expect(await screen.findByRole("link", { name: "sales@example.com" })).toHaveAttribute("href", "mailto:sales@example.com");
    expect(screen.getByRole("link", { name: "help@example.com" })).toBeInTheDocument();
  });
});

describe("self-service accounts", () => {
  it("signup sends only the form's fields and then says the same thing for every address", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "GET /api/v1/public/site": [SITE],
      "POST /api/v1/public/signup": [{ status: 202, body: { status: "accepted" } }],
    });
    const { container } = render(<SignupScreen />);
    await expectNoViolations(container);
    fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Ada" } });
    fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Lovelace" } });
    fireEvent.change(screen.getByLabelText("Work email"), { target: { value: " ada@example.com " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long enough passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/public/signup")?.body).toEqual({
      first_name: "Ada",
      last_name: "Lovelace",
      email: "ada@example.com",
      password: "a long enough passphrase",
    });
  });

  it("signup lists every password problem at once", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "GET /api/v1/public/site": [SITE],
      "POST /api/v1/public/signup": [
        {
          status: 400,
          body: { code: "password_policy_violation", problems: ["Too short.", "Too common."] },
        },
      ],
    });
    render(<SignupScreen />);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Too short.", "Too common."]);
  });

  it("verification posts the fragment's token in the body with the password, then opens the dashboard", async () => {
    window.history.replaceState(null, "", "/verify-email#token=abcdefghijklmnopqrstuvwxyz0123");
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/public/signup/verify": [{ status: 200, body: { organization_slug: "ada-co", organization_id: "o1" } }],
    });
    render(<VerifyEmailScreen />);
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "a long enough passphrase" } });
    // The token left the address bar as soon as it was read.
    expect(window.location.hash).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Confirm and sign in" }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
    const verify = calls.find((c) => c.key === "POST /api/v1/public/signup/verify");
    expect(verify?.body).toEqual({ token: "abcdefghijklmnopqrstuvwxyz0123", password: "a long enough passphrase" });
    expect(verify?.url).not.toContain("token");
  });

  it("forgot password answers the same whatever the address", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/public/password/forgot": [{ status: 202, body: { status: "accepted" } }],
    });
    render(<ForgotPasswordScreen />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "who@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Email me a reset link" }));
    expect(await screen.findByText(/If an account uses/)).toBeInTheDocument();
  });

  it("reset refuses two different passwords before sending anything", async () => {
    window.history.replaceState(null, "", "/reset-password#token=zyxwvutsrqponmlkjihgfedcba9876");
    const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    render(<ResetPasswordScreen />);
    fireEvent.change(await screen.findByLabelText("New password"), { target: { value: "first long passphrase" } });
    fireEvent.change(screen.getByLabelText("Repeat the new password"), { target: { value: "second long passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("don't match");
    expect(calls.filter((c) => c.key.startsWith("POST"))).toEqual([]);
  });
});
