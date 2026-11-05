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

const nav = vi.hoisted(() => ({ push: vi.fn(), params: "" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/store",
  useSearchParams: () => new URLSearchParams(nav.params),
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
  nav.params = "";
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
    expect(crm.textContent).toContain("£19/month + tax");
    // A card is a shelf label: buying happens on the product's own page.
    expect(within(crm).queryByRole("button", { name: /Buy now|trial|cart/ })).toBeNull();
    expect(within(soon).getByText("Coming soon", { selector: "p" })).toBeInTheDocument();
    const filters = screen.getByRole("complementary", { name: "Store filters" });
    expect(within(filters).getByRole("button", { name: /Finance/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All Products" })).toHaveAttribute("aria-pressed", "true");
    // Log in and Get Started live in the marketplace header, not the page.
    expect(screen.queryByRole("link", { name: "Log in" })).toBeNull();
    // The header has the search; the page does not repeat it.
    expect(screen.queryByRole("search")).toBeNull();
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
    // A whole amount reads like a price tag, without ".00".
    expect(grid.textContent).toMatch(/20,000\/year \+ GST/);
  });

  // --- the marketplace (2026-11-04) ---------------------------------------------------

  function market(products = [card("harbor_crm", "Harbor CRM", { badges: ["best_seller"], product_type: "ai_tool" }), SOON]) {
    const base = page(products);
    return {
      ...base,
      facets: {
        ...base.facets,
        collections: [
          { value: "all", label: "All products", count: 2 },
          { value: "best_seller", label: "Best Seller", count: 1 },
        ],
        product_types: [
          { value: "all", label: "All Products", count: 2 },
          { value: "saas", label: "SaaS Products", count: 1 },
          { value: "ai_tool", label: "AI Tools", count: 1 },
          { value: "business_suite", label: "Business Suite", count: 0 },
        ],
        best_for: [
          { value: "startups", label: "Startups", count: 1 },
          { value: "agencies", label: "Agencies", count: 0 },
        ],
        integrations: [{ value: "WhatsApp", label: "WhatsApp", count: 1 }],
      },
    };
  }

  it("filters by product type, best for and integration, and tags a Best Seller", async () => {
    const calls = mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(market())] });
    render(<PublicStoreScreen />);
    const grid = await screen.findByRole("list", { name: "Products" });
    expect(within(grid.children[0] as HTMLElement).getByText("Best Seller")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Best Seller/ })).toBeInTheDocument();

    const types = screen.getByRole("group", { name: "Product type" });
    expect(within(types).getByLabelText(/All Products/)).toBeChecked();
    fireEvent.click(within(types).getByLabelText(/AI Tools/));
    await waitFor(() => expect(last(calls).get("product_type")).toBe("ai_tool"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Startups/ }));
    await waitFor(() => expect(last(calls).get("best_for")).toBe("startups"));
    fireEvent.click(screen.getByRole("checkbox", { name: /WhatsApp/ }));
    await waitFor(() => expect(last(calls).get("integration")).toBe("WhatsApp"));

    // Each one is a removable chip.
    fireEvent.click(screen.getByRole("button", { name: "Remove filter: For Startups" }));
    await waitFor(() => expect(last(calls).get("best_for")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Remove filter: AI Tools" }));
    await waitFor(() => expect(last(calls).get("product_type")).toBeNull());
  });

  it("opens filtered by its address - where the header's menus link", async () => {
    nav.params = "q=crm&category=Sales&type=business_suite&best_for=agencies,startups&collection=best_seller";
    const calls = mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(market())] });
    render(<PublicStoreScreen />);
    await screen.findByRole("list", { name: "Products" });
    const first = new URL(calls.find((c) => c.key === STORE)!.url, "http://x").searchParams;
    expect(Object.fromEntries(first)).toMatchObject({
      q: "crm",
      category: "Sales",
      product_type: "business_suite",
      best_for: "agencies,startups",
      collection: "best_seller",
    });
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
    expect(within(box).getByRole("region", { name: "Order Summary" }).textContent).toContain("Total Payable£19");
    // Not an INR price: no GST line, and no refund promise the admin did not set.
    expect(within(box).queryByText(/GST/)).toBeNull();
    expect(within(box).queryByText(/Refund Policy/)).toBeNull();
    for (const name of ["Proceed to Secure Payment", "Add to cart: Harbor CRM", "Start free trial: Harbor CRM"]) {
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
    expect(within(box).queryByText(/Special Offer/)).toBeNull();
    fireEvent.click(within(box).getByRole("radio", { name: /Harbor CRM Pro/ }));
    expect(within(box).getByText(/Special Offer – Save 40%!/)).toBeInTheDocument();
    fireEvent.click(within(box).getByRole("button", { name: "Increase quantity" }));
    const summary = within(box).getByRole("region", { name: "Order Summary" });
    expect(summary.textContent).toContain("Harbor CRM - Harbor CRM Pro (1 Month) × 2");
    expect(summary.textContent).toContain("Total Payable£60");
    expect(within(box).getByText("You save").textContent).toContain("£40");
  });

  it("a visitor who proceeds gets the signup form, with the plan and quantity remembered", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok(detail)], [SITE]: [ok(SITE_SETTINGS)] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    fireEvent.click(await screen.findByRole("button", { name: "Increase quantity" }));
    fireEvent.click(screen.getByRole("button", { name: "Proceed to Secure Payment" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Proceed to Secure Payment" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(`/o/acme/checkout?plan=${PV}&interval=monthly&quantity=3`));
    expect(localStorage.getItem(PURCHASE_INTENT)).toBeNull();
  });

  // ADR-058: the owner's mock-up - an INR plan with 40% off, GST 18% on top,
  // a coupon and the refund promise the Super Admin set.
  const inr = {
    ...detail,
    currency: "INR",
    currencies: ["INR"],
    tax: { label: "GST", rate_bp: 1800, description: "GST (18%)" },
    product: { ...detail.product, badges: ["new"], refund_days: 60 },
    plans: [
      {
        ...pro,
        name: "Starter",
        caption: "Up to 10,000 messages/month",
        prices: [{ interval: "annual", currency: "INR", model: "flat", amount_minor: 839900, list_amount_minor: 1399900, discount_bp: 4000, from_tiers: false }],
      },
    ],
  };
  const COUPON = "GET /api/v1/public/store/products/harbor-crm/coupon";

  it("an INR plan adds GST, takes a coupon and shows the refund promise", async () => {
    mockApi({
      [SESSION]: [SIGNED_IN],
      [PRODUCT]: [ok(inr)],
      [ORGS]: [ok(ONE_ORG)],
      [COUPON]: [ok({ valid: false, reason: "unknown", message: "That coupon code is not valid." }), ok({ valid: true, code: "SAVE10", label: "10% off", discount_minor: 83990 })],
    });
    const { container } = render(
      <main>
        <PublicStoreProductScreen slug="harbor-crm" />
      </main>,
    );
    const box = await screen.findByRole("complementary", { name: "Buy Harbor CRM" });
    expect(within(box).getByText(/Launch Offer – Save 40%!/)).toBeInTheDocument();
    const summary = within(box).getByRole("region", { name: "Order Summary" });
    expect(summary.textContent).toContain("Harbor CRM - Starter (1 Year)₹8,399");
    expect(summary.textContent).toContain("GST (18%)₹1,512");
    expect(summary.textContent).toContain("Total Payable₹9,911");
    expect(within(box).getByText("60-Day Refund Policy")).toBeInTheDocument();

    const field = within(box).getByRole("textbox", { name: "Coupon code" });
    fireEvent.change(field, { target: { value: "nope1" } });
    fireEvent.click(within(box).getByRole("button", { name: "Apply" }));
    expect(await within(box).findByRole("alert")).toHaveTextContent("That coupon code is not valid.");

    fireEvent.change(field, { target: { value: "save10" } });
    fireEvent.click(within(box).getByRole("button", { name: "Apply" }));
    expect(await within(box).findByText("SAVE10")).toBeInTheDocument();
    // ₹8,399 - ₹839.90 = ₹7,559.10; GST 18% = ₹1,360.64 -> ₹1,361; total ₹8,920.10.
    expect(summary.textContent).toContain("Coupon SAVE10−₹839.90");
    expect(summary.textContent).toContain("GST (18%)₹1,361");
    expect(summary.textContent).toContain("Total Payable₹8,920.10");
    await expectNoViolations(container);

    fireEvent.click(within(box).getByRole("button", { name: "Proceed to Secure Payment" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(`/o/acme/checkout?plan=${PV2}&interval=annual&coupon=SAVE10`));
  });

  it("a product with no plans on sale asks for a quote, once", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [PRODUCT]: [ok({ ...detail, plans: [], comparison: [], product: { ...detail.product, trial: { ...detail.product.trial, available: false, plan_codes: [] } } })] });
    render(<PublicStoreProductScreen slug="harbor-crm" />);
    const box = await screen.findByRole("complementary", { name: "Buy Harbor CRM" });
    expect(within(box).getByText("Pricing on request")).toBeInTheDocument();
    expect(within(box).getAllByText("Contact sales")).toHaveLength(1);
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
