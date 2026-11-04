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
    expect(await screen.findByRole("heading", { level: 1, name: "Explore Workeasy360 Products" })).toBeInTheDocument();
    const grid = await screen.findByRole("list", { name: "Products" });
    const [crm, soon] = Array.from(grid.children) as HTMLElement[];

    expect(within(crm).getByRole("link", { name: "Harbor CRM" })).toHaveAttribute("href", "/store/product/harbor-crm");
    expect(within(crm).getByRole("link", { name: "View details: Harbor CRM" })).toHaveAttribute("href", "/store/product/harbor-crm");
    expect(within(crm).getByText("14-day free trial")).toBeInTheDocument();
    expect(crm.textContent).toContain("£19.00/month + tax");
    // A card is a shelf label: buying happens on the product's own page.
    expect(within(crm).queryByRole("button", { name: /Buy now|trial|cart/ })).toBeNull();
    expect(within(soon).getByText("Coming soon", { selector: "p" })).toBeInTheDocument();
    const filters = screen.getByRole("complementary", { name: "Store filters" });
    expect(within(filters).getByRole("button", { name: /Finance/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All Products" })).toHaveAttribute("aria-pressed", "true");
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

  it("rupee prices say + GST", async () => {
    const inr = card("harbor_crm", "Harbor CRM", { price: { amount_minor: 2000000, currency: "INR", interval: "annual", plan_name: "Starter" } });
    mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(page([inr]))] });
    render(<PublicStoreScreen />);
    const grid = await screen.findByRole("list", { name: "Products" });
    expect(grid.textContent).toMatch(/20,000\.00\/year \+ GST/);
  });
});

describe("a product's page in the public Store", () => {
  const PRODUCT = "GET /api/v1/public/store/products/harbor-crm";
  const BOOKS = "GET /api/v1/public/store/products/books";
  const crm = card("harbor_crm", "Harbor CRM");
  const pro = {
    ...crm.plans[0],
    plan_version_id: PV2,
    plan_code: "harbor_crm_pro",
    name: "Harbor CRM Pro",
    caption: "Up to 50 users",
    recommended: false,
    prices: [{ interval: "monthly", currency: "GBP", model: "flat", amount_minor: 3000, list_amount_minor: 5000, discount_bp: 4000, from_tiers: false }],
  };
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
    plans: [...crm.plans, pro],
    comparison: [{ key: "seats", label: "Users included", kind: "seats", unit: "", values: { [PV]: { included: 3, max: null } } }],
    related: [{ code: "books", slug: "books", name: "Books", title: "Books", summary: "Ledgers", icon: "", category: "Sales", price: null }],
  };
  const PV3 = "3b0a4c9e-1f2d-4e5a-9b6c-7d8e9f0a1b33";
  const books = {
    ...detail,
    product: { ...detail.product, ...card("books", "Books"), description: "", highlights: [], benefits: [], use_cases: [], tags: [], badges: [], screenshots: [] },
    plans: [{ ...card("books", "Books").plans[0], plan_version_id: PV3 }],
    related: [],
  };

  it("shows the product beside a checkout box", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(detail)] });
    const { container } = render(
      <main>
        <PublicStoreProductScreen slug="harbor-crm" />
      </main>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Harbor CRM" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Breadcrumb" }).textContent).toContain("Sales");
    expect(screen.getByText("Harbor CRM keeps every deal in one place.")).toBeInTheDocument();
    for (const text of ["Pipelines", "Close deals faster", "Small sales teams"]) expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "The pipeline" })).toBeInTheDocument();

    const box = screen.getByRole("complementary", { name: "Buy Harbor CRM" });
    expect(within(box).getByRole("radio", { name: /Harbor CRM Starter/ })).toBeChecked();
    expect(within(box).getByRole("spinbutton", { name: "Quantity" })).toHaveValue(1);
    expect(within(box).getByRole("region", { name: "Order summary" }).textContent).toContain("Estimated total£19");
    for (const name of ["Proceed to Secure Checkout", "Add to cart: Harbor CRM", "Start free trial: Harbor CRM"]) {
      expect(within(box).getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Buy now: Harbor CRM Starter" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Books" })).toHaveAttribute("href", "/store/product/books");
    await expectNoViolations(container);
  });

  it("a discounted plan shows the offer, and the summary follows the plan and quantity", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(detail)] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    const box = await screen.findByRole("complementary", { name: "Buy Harbor CRM" });
    expect(within(box).queryByText(/Special offer/)).toBeNull();
    fireEvent.click(within(box).getByRole("radio", { name: /Harbor CRM Pro/ }));
    expect(within(box).getByText(/Special offer - save 40%/)).toBeInTheDocument();
    fireEvent.click(within(box).getByRole("button", { name: "Increase quantity" }));
    const summary = within(box).getByRole("region", { name: "Order summary" });
    expect(summary.textContent).toContain("Harbor CRM - Harbor CRM Pro (1 month) × 2");
    expect(summary.textContent).toContain("Estimated total£60");
    expect(within(box).getByText("You save").textContent).toContain("£40");
  });

  it("a visitor who proceeds gets the signup form, with the plan and quantity remembered", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(detail)], [SITE]: [ok(SITE_SETTINGS)] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    fireEvent.click(await screen.findByRole("button", { name: "Increase quantity" }));
    fireEvent.click(screen.getByRole("button", { name: "Proceed to Secure Checkout" }));
    const dialog = await screen.findByRole("dialog", { name: "Create your account to continue" });
    expect(within(dialog).getByRole("button", { name: "Create account" })).toBeInTheDocument();
    expect(within(dialog).getAllByRole("link", { name: "Sign in" })[0]).toHaveAttribute("href", "/login?mode=user");
    expect(nav.push).not.toHaveBeenCalled();
    expect(stored()).toMatchObject({ plan: PV, interval: "monthly", trial: false, quantity: 2 });
  });

  it("a signed-in buyer with one organization goes straight to its checkout", async () => {
    mockApi({ [SESSION]: [SIGNED_IN], [PRODUCT]: [ok(detail)], [ORGS]: [ok(ONE_ORG)] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    fireEvent.change(await screen.findByRole("spinbutton", { name: "Quantity" }), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Proceed to Secure Checkout" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(`/o/acme/checkout?plan=${PV}&interval=monthly&quantity=3`));
    expect(localStorage.getItem(PURCHASE_INTENT)).toBeNull();
  });

  it("a signed-in trial opens the organization, which starts it", async () => {
    mockApi({ [SESSION]: [SIGNED_IN], [PRODUCT]: [ok(detail)], [ORGS]: [ok(ONE_ORG)] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    fireEvent.click(await screen.findByRole("button", { name: "Start free trial: Harbor CRM" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/o/acme"));
    expect(stored()).toMatchObject({ productTrial: { product: "harbor_crm", plan: "harbor_crm_starter" } });
  });

  it("plays the product's video first, and loads the player only when asked", async () => {
    const withVideo = {
      ...detail,
      product: {
        ...detail.product,
        video: {
          provider: "youtube",
          id: "dQw4w9WgXcQ",
          embed_url: "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
          watch_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        },
      },
    };
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(withVideo)] });
    const { container } = render(
      <main>
        <PublicStoreProductScreen slug="harbor-crm" />
      </main>,
    );
    const play = await screen.findByRole("button", { name: "Play video: Harbor CRM" });
    expect(container.querySelector("iframe")).toBeNull();
    const thumbs = screen.getByRole("list", { name: "Video and pictures" });
    expect(within(thumbs).getByRole("button", { name: "Show the video" })).toHaveAttribute("aria-pressed", "true");
    expect(within(thumbs).getByRole("button", { name: "Show picture 1: The pipeline" })).toBeInTheDocument();
    await expectNoViolations(container);

    fireEvent.click(play);
    const player = screen.getByTitle("Harbor CRM - video");
    expect(player.getAttribute("src")).toMatch(/^https:\/\/www\.youtube-nocookie\.com\/embed\/dQw4w9WgXcQ\?autoplay=1/);

    fireEvent.click(within(thumbs).getByRole("button", { name: "Show picture 1: The pipeline" }));
    expect(container.querySelector("iframe")).toBeNull();
    expect(screen.getByRole("img", { name: "The pipeline" })).toBeInTheDocument();
  });

  it("plays an uploaded video in the browser's own player", async () => {
    const uploaded = {
      ...detail,
      product: { ...detail.product, video: { provider: "upload", id: "v1", url: "/api/v1/public/products/harbor-crm/videos/v1.mp4", content_type: "video/mp4" } },
    };
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(uploaded)] });
    const { container } = render(
      <main>
        <PublicStoreProductScreen slug="harbor-crm" />
      </main>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Play video: Harbor CRM" }));
    const player = container.querySelector("video");
    expect(player?.getAttribute("src")).toBe("/api/v1/public/products/harbor-crm/videos/v1.mp4");
    expect(player).toHaveAttribute("controls");
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("a product with one plan lists what it includes instead of a comparison table", async () => {
    const single = {
      ...books,
      comparison: [
        { key: "seats", label: "Users included", kind: "seats", unit: "", values: { [PV3]: { included: 5, max: null } } },
        { key: "books.ledger", label: "General ledger", kind: "boolean", unit: "", values: { [PV3]: true } },
        { key: "books.payroll", label: "Payroll", kind: "boolean", unit: "", values: { [PV3]: false } },
      ],
    };
    mockApi({ [SESSION]: [SIGNED_OUT], [BOOKS]: [ok(single)] });
    const { container } = render(
      <main>
        <PublicStoreProductScreen slug="books" />
      </main>,
    );
    const heading = await screen.findByRole("heading", { name: "What's included" });
    const list = heading.parentElement!;
    expect(within(list).getByText("Users included: 5")).toBeInTheDocument();
    expect(within(list).getByText("General ledger")).toBeInTheDocument();
    expect(within(list).queryByText(/Payroll/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Compare plans" })).toBeNull();
    await expectNoViolations(container);
  });

  it("buying from the plan comparison asks a visitor to create an account", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(detail)], [SITE]: [ok(SITE_SETTINGS)] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    fireEvent.click(await screen.findByRole("button", { name: "Buy now: Harbor CRM Starter" }));
    expect(await screen.findByRole("dialog", { name: "Create your account to continue" })).toBeInTheDocument();
    expect(stored()).toMatchObject({ plan: PV, interval: "monthly" });
  });

  it("collects products in a cart and buys all of them in one checkout", async () => {
    mockApi({ [SESSION]: [SIGNED_IN], [PRODUCT]: [ok(detail)], [BOOKS]: [ok(books)], [ORGS]: [ok(ONE_ORG)] });
    const first = render(<PublicStoreProductScreen slug="harbor-crm" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add to cart: Harbor CRM" }));
    expect(await screen.findByRole("region", { name: "Your cart (1 product)" })).toBeInTheDocument();
    first.unmount();

    render(<PublicStoreProductScreen slug="books" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add to cart: Books" }));
    const bar = await screen.findByRole("region", { name: "Your cart (2 products)" });
    expect(screen.getByRole("button", { name: "In cart: Books" })).toBeDisabled();
    fireEvent.click(within(bar).getByRole("button", { name: "Buy all selected (2)" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(`/o/acme/checkout?cart=${PV}%2C${PV3}&interval=monthly`));
    // The cart is now that checkout's order.
    await waitFor(() => expect(screen.queryByRole("region", { name: /Your cart/ })).toBeNull());
  });

  it("a visitor's cart is remembered for after signup", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(detail)], [SITE]: [ok(SITE_SETTINGS)] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add to cart: Harbor CRM" }));
    fireEvent.click(await screen.findByRole("button", { name: "Buy all selected (1)" }));
    expect(await screen.findByRole("dialog", { name: "Create your account to continue" })).toBeInTheDocument();
    expect(stored()).toMatchObject({ cart: [PV], interval: "monthly" });
  });
});
