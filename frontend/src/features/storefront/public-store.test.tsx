import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { expectNoViolations, mockApi, ok, SIGNED_IN } from "@/features/controlplane/testSupport";
import { PURCHASE_INTENT } from "@/features/site/SiteShell";
import { PublicStoreScreen } from "./PublicStore";
import { PublicStoreProductScreen } from "./PublicStoreProduct";

/**
 * The public Store (`/store`, 2026-11-02): the public catalog as a marketplace
 * page anyone can browse. Each filter goes to the server as chosen; buying
 * remembers the choice and signs in, so the organization's checkout opens it.
 */

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/store",
  useSearchParams: () => new URLSearchParams(),
}));

const STORE = "GET /api/v1/public/store";
const SESSION = "GET /api/v1/auth/session";
const SIGNED_OUT = { status: 200, body: { authenticated: false } };
const SITE = "GET /api/v1/public/site";
const SITE_SETTINGS = { signup_enabled: true, support_email: "help@example.com", sales_email: "sales@example.com", catalog_currency: "GBP" };
const ORGS = "GET /api/v1/organizations";
const ONE_ORG = { organizations: [{ slug: "acme", display_name: "Acme Ltd" }], next_cursor: null, has_more: false };
const PV = "0f6c1a52-58a4-4a3e-9d55-5c2f8d3c1a11";
const PV2 = "7d1e0b6a-2c44-4f0e-8a51-0b9c6f2e9d22";

function card(code: string, title: string, overrides: Record<string, unknown> = {}) {
  return {
    code,
    slug: code.replace(/_/g, "-"),
    name: title,
    title,
    tagline: `${title} for growing teams`,
    summary: `${title} for growing teams`,
    description: "",
    marketing_url: "",
    availability: "available",
    category: "Sales",
    icon: "",
    features: [],
    plans: [
      {
        plan_version_id: PV,
        plan_code: `${code}_starter`,
        name: `${title} Starter`,
        caption: "",
        description: "",
        billing_type: "paid",
        kind: "standard",
        addon_for: null,
        included_seats: 3,
        max_seats: null,
        trial_days: 0,
        trial_available: true,
        prices: [{ interval: "monthly", currency: "GBP", model: "flat", amount_minor: 1900, from_tiers: false }],
      },
    ],
    starting_price: { amount_minor: 1900, currency: "GBP", interval: "monthly" },
    trial: { available: true, duration_days: 14, seat_limit: null, payment_method_required: false, plan_codes: [`${code}_starter`] },
    status: "available",
    ribbon: { kind: "trial", label: "14-day free trial" },
    price: { amount_minor: 1900, currency: "GBP", interval: "monthly", plan_name: `${title} Starter` },
    yearly_deal: null,
    plan_types: ["monthly", "free_trial"],
    trial_days: 14,
    logo_url: null,
    banner_url: null,
    ...overrides,
  };
}

const SOON = card("ledger_lite", "Ledger Lite", {
  availability: "coming_soon",
  category: "Finance",
  plans: [],
  starting_price: null,
  trial: { available: false, duration_days: null, seat_limit: null, payment_method_required: false, plan_codes: [] },
  status: "coming_soon",
  ribbon: { kind: "coming_soon", label: "Coming soon" },
  price: null,
  plan_types: [],
  trial_days: null,
});

function page(products = [card("harbor_crm", "Harbor CRM"), SOON]) {
  return {
    currency: "GBP",
    currencies: ["GBP", "INR"],
    count: products.length,
    total: 2,
    products,
    facets: {
      categories: [
        { value: "Finance", label: "Finance", count: 1 },
        { value: "Sales", label: "Sales", count: 1 },
      ],
      statuses: [
        { value: "available", label: "Available now", count: 1 },
        { value: "coming_soon", label: "Coming soon", count: 1 },
      ],
      plan_types: [
        { value: "monthly", label: "Monthly", count: 1 },
        { value: "free_trial", label: "Free trial", count: 1 },
      ],
      price: { min_minor: 1900, max_minor: 1900 },
    },
    sorts: [
      { value: "recommended", label: "Recommended" },
      { value: "price_asc", label: "Price: low to high" },
    ],
  };
}

const last = (calls: { key: string; url: string }[]) =>
  new URL(calls.filter((c) => c.key === STORE).at(-1)!.url, "http://x").searchParams;

const stored = () => JSON.parse(localStorage.getItem(PURCHASE_INTENT) ?? "{}");

beforeEach(() => {
  nav.push.mockReset();
  localStorage.clear();
});

describe("the public Store", () => {
  it("shows every product as a store card, with categories, to a visitor", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(page())] });
    const { container } = render(
      <main>
        <PublicStoreScreen />
      </main>,
    );
    const grid = await screen.findByRole("list", { name: "Products" });
    const [crm, soon] = Array.from(grid.children) as HTMLElement[];

    expect(within(crm).getByRole("link", { name: "Harbor CRM" })).toHaveAttribute("href", "/store/product/harbor-crm");
    expect(within(crm).getByRole("link", { name: "View details: Harbor CRM" })).toHaveAttribute("href", "/store/product/harbor-crm");
    expect(within(crm).getByText("14-day free trial")).toBeInTheDocument();
    expect(crm.textContent).toContain("£19.00/month");
    for (const name of ["Buy now: Harbor CRM", "Add to cart: Harbor CRM", "Start free trial: Harbor CRM"]) {
      expect(within(crm).getByRole("button", { name })).toBeInTheDocument();
    }
    expect(within(soon).queryByRole("button", { name: /Buy now|trial|cart/ })).toBeNull();
    const filters = screen.getByRole("complementary", { name: "Store filters" });
    expect(within(filters).getByRole("button", { name: /Finance/ })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Log in" })).toHaveAttribute("href", "/login?mode=user");
    await expectNoViolations(container);
  });

  it("sends each filter to the server", async () => {
    const calls = mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(page())] });
    render(<PublicStoreScreen />);
    await screen.findByRole("list", { name: "Products" });
    fireEvent.click(screen.getByRole("button", { name: "Show Sales products" }));
    await waitFor(() => expect(last(calls).get("category")).toBe("Sales"));
    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "INR" } });
    await waitFor(() => expect(last(calls).get("currency")).toBe("INR"));
  });

  it("a visitor who buys gets the signup form, with the plan remembered", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(page())], [SITE]: [ok(SITE_SETTINGS)] });
    render(<PublicStoreScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Buy now: Harbor CRM" }));
    const dialog = await screen.findByRole("dialog", { name: "Create your account to continue" });
    expect(within(dialog).getByRole("button", { name: "Create account" })).toBeInTheDocument();
    expect(within(dialog).getAllByRole("link", { name: "Sign in" })[0]).toHaveAttribute("href", "/login?mode=user");
    expect(nav.push).not.toHaveBeenCalled();
    expect(stored()).toMatchObject({ plan: PV, interval: "monthly", trial: false });
  });

  it("a signed-in buyer with one organization goes straight to its checkout", async () => {
    mockApi({ [SESSION]: [SIGNED_IN], [STORE]: [ok(page())], [ORGS]: [ok(ONE_ORG)] });
    render(<PublicStoreScreen />);
    expect(await screen.findByRole("link", { name: "Go to my dashboard" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Buy now: Harbor CRM" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(`/o/acme/checkout?plan=${PV}&interval=monthly`));
    expect(localStorage.getItem(PURCHASE_INTENT)).toBeNull();
  });

  it("a signed-in trial opens the organization, which starts it", async () => {
    mockApi({ [SESSION]: [SIGNED_IN], [STORE]: [ok(page())], [ORGS]: [ok(ONE_ORG)] });
    render(<PublicStoreScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Start free trial: Harbor CRM" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/o/acme"));
    expect(stored()).toMatchObject({ productTrial: { product: "harbor_crm", plan: "harbor_crm_starter" } });
  });

  it("collects products in a cart and buys all of them in one checkout", async () => {
    const books = card("books", "Books", { plans: [{ ...card("books", "Books").plans[0], plan_version_id: PV2 }] });
    mockApi({ [SESSION]: [SIGNED_IN], [STORE]: [ok(page([card("harbor_crm", "Harbor CRM"), books]))], [ORGS]: [ok(ONE_ORG)] });
    render(<PublicStoreScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Add to cart: Harbor CRM" }));
    fireEvent.click(screen.getByRole("button", { name: "Add to cart: Books" }));
    const bar = await screen.findByRole("region", { name: "Your cart (2 products)" });
    expect(screen.getByRole("button", { name: "In cart: Books" })).toBeDisabled();
    fireEvent.click(within(bar).getByRole("button", { name: "Buy all selected (2)" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(`/o/acme/checkout?cart=${PV}%2C${PV2}&interval=monthly`));
    // The cart is now that checkout's order.
    await waitFor(() => expect(screen.queryByRole("region", { name: /Your cart/ })).toBeNull());
  });

  it("a visitor's cart is remembered for after signup", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(page())], [SITE]: [ok(SITE_SETTINGS)] });
    render(<PublicStoreScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Add to cart: Harbor CRM" }));
    fireEvent.click(await screen.findByRole("button", { name: "Buy all selected (1)" }));
    expect(await screen.findByRole("dialog", { name: "Create your account to continue" })).toBeInTheDocument();
    expect(stored()).toMatchObject({ cart: [PV], interval: "monthly" });
  });
});

describe("a product's page in the public Store", () => {
  const PRODUCT = "GET /api/v1/public/store/products/harbor-crm";
  const crm = card("harbor_crm", "Harbor CRM");
  const detail = {
    currency: "GBP",
    currencies: ["GBP"],
    product: {
      ...crm,
      subcategory: "Pipeline",
      tags: ["crm"],
      badges: ["featured"],
      description: "Harbor CRM keeps every deal in one place.",
      highlights: ["Pipelines", "Forecasts"],
      benefits: ["Close deals faster"],
      use_cases: ["Small sales teams"],
      documentation_url: "",
      support_url: "",
      logo: null,
      banner: null,
      screenshots: [{ url: "/api/v1/public/products/harbor-crm/media/s1", caption: "The pipeline", width: 1200, height: 800 }],
    },
    plans: crm.plans,
    comparison: [{ key: "seats", label: "Users included", kind: "seats", unit: "", values: { [PV]: { included: 3, max: null } } }],
    related: [{ code: "books", slug: "books", name: "Books", title: "Books", summary: "Ledgers", icon: "", category: "Sales", price: null }],
  };

  it("shows the full details and every way to buy", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(detail)] });
    const { container } = render(
      <main>
        <PublicStoreProductScreen slug="harbor-crm" />
      </main>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Harbor CRM" })).toBeInTheDocument();
    expect(screen.getByText("Harbor CRM keeps every deal in one place.")).toBeInTheDocument();
    for (const text of ["Pipelines", "Close deals faster", "Small sales teams"]) expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "The pipeline" })).toBeInTheDocument();
    const box = screen.getByRole("complementary", { name: "Buy Harbor CRM" });
    for (const name of ["Buy now: Harbor CRM", "Add to cart: Harbor CRM", "Start free trial: Harbor CRM"]) {
      expect(within(box).getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Buy now: Harbor CRM Starter" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Books" })).toHaveAttribute("href", "/store/product/books");
    await expectNoViolations(container);
  });

  it("buying from the page asks a visitor to create an account", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(detail)], [SITE]: [ok(SITE_SETTINGS)] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    fireEvent.click(await screen.findByRole("button", { name: "Buy now: Harbor CRM Starter" }));
    expect(await screen.findByRole("dialog", { name: "Create your account to continue" })).toBeInTheDocument();
    expect(stored()).toMatchObject({ plan: PV, interval: "monthly" });
  });
});
