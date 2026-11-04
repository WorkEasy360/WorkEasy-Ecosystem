import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { expectNoViolations, mockApi, ok, SIGNED_IN } from "@/features/controlplane/testSupport";
import { PUBLIC_CART } from "@/features/console/cart";
import { StoreHeader } from "./StoreHeader";

/**
 * The marketplace's header (2026-11-04, the owner's mock-up): an
 * announcement a visitor may dismiss, the Store's search, menus built from the
 * public Store's own answer, what is new, the cart, and Log in / Get Started.
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

function product(code: string, title: string, extra: Record<string, unknown> = {}) {
  return { code, slug: code.replace(/_/g, "-"), name: title, title, summary: `${title} in one place`, badges: [], ribbon: null, product_type: "saas", ...extra };
}

const PAGE = {
  currency: "GBP",
  currencies: ["GBP"],
  count: 3,
  total: 3,
  products: [
    product("easy_crm", "Easy CRM", { badges: ["new"] }),
    product("sailo_ai", "Sailo AI", { product_type: "ai_tool" }),
    product("easy_books", "Easy Books"),
  ],
  facets: {
    categories: [
      { value: "Sales / CRM", label: "Sales / CRM", count: 1 },
      { value: "Finance", label: "Finance", count: 2 },
    ],
    statuses: [],
    plan_types: [],
    best_for: [
      { value: "startups", label: "Startups", count: 2 },
      { value: "agencies", label: "Agencies", count: 0 },
    ],
    price: { min_minor: null, max_minor: null },
  },
  sorts: [],
};

beforeEach(() => {
  nav.push.mockReset();
  localStorage.clear();
  sessionStorage.clear();
});

describe("the marketplace header", () => {
  it("builds its menus from the public Store and passes axe", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(PAGE)] });
    const { container } = render(<StoreHeader />);
    const nav = screen.getByRole("navigation", { name: "Store" });

    fireEvent.click(within(nav).getByRole("button", { name: "All Products" }));
    expect(await screen.findByRole("link", { name: /Finance/ })).toHaveAttribute("href", "/store?category=Finance");
    expect(screen.getByRole("link", { name: /Sales \/ CRM/ })).toHaveAttribute("href", "/store?category=Sales%20%2F%20CRM");

    fireEvent.click(within(nav).getByRole("button", { name: "AI Tools" }));
    expect(screen.getByRole("link", { name: /Sailo AI/ })).toHaveAttribute("href", "/store/product/sailo-ai");
    expect(screen.getByRole("link", { name: "See every AI tool" })).toHaveAttribute("href", "/store?type=ai_tool");
    fireEvent.keyDown(document, { key: "Escape" });

    fireEvent.click(within(nav).getByRole("button", { name: "Business Suite" }));
    expect(screen.getByText("Business suites are coming soon.")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });

    fireEvent.click(within(nav).getByRole("button", { name: "Industry Solutions" }));
    expect(screen.getByRole("link", { name: /For startups/ })).toHaveAttribute("href", "/store?best_for=startups");
    expect(within(nav).getByRole("link", { name: "Partner Offers" })).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Log in" })).toHaveAttribute("href", "/login?mode=user");
    expect(screen.getByRole("link", { name: "Get Started" })).toHaveAttribute("href", "/signup");
    await expectNoViolations(container);
  });

  it("searches the Store", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(PAGE)] });
    render(<StoreHeader />);
    const box = await screen.findByRole("searchbox", { name: "Search products" });
    fireEvent.change(box, { target: { value: " invoices & GST " } });
    fireEvent.submit(box.closest("form")!);
    expect(nav.push).toHaveBeenCalledWith("/store?q=invoices%20%26%20GST");
  });

  it("counts what is new and what is in the cart", async () => {
    localStorage.setItem(
      `w360.cart.${PUBLIC_CART}`,
      JSON.stringify({ interval: "monthly", items: [{ plan_version_id: "0f6c1a52-58a4-4a3e-9d55-5c2f8d3c1a11", product_code: "easy_crm", product_name: "Easy CRM", plan_name: "Starter" }] }),
    );
    mockApi({ [SESSION]: [SIGNED_OUT], [STORE]: [ok(PAGE)] });
    render(<StoreHeader />);
    const bell = await screen.findByRole("button", { name: "What's new: 1 new product" });
    fireEvent.click(bell);
    expect(screen.getByRole("link", { name: /Easy CRM/ })).toHaveAttribute("href", "/store/product/easy-crm");
    await waitFor(() => expect(screen.getByRole("link", { name: /^Cart/ })).toHaveAttribute("href", "/store#cart"));
  });

  it("a visitor dismisses the announcement, and it stays dismissed", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT, SIGNED_OUT], [STORE]: [ok(PAGE), ok(PAGE)] });
    const first = render(<StoreHeader />);
    expect(screen.getByText("One platform. Every possibility.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss announcement" }));
    expect(screen.queryByText("One platform. Every possibility.")).toBeNull();
    first.unmount();
    render(<StoreHeader />);
    expect(screen.queryByText("One platform. Every possibility.")).toBeNull();
  });

  it("someone signed in goes to their dashboard", async () => {
    mockApi({ [SESSION]: [SIGNED_IN], [STORE]: [ok(PAGE)] });
    render(<StoreHeader />);
    expect(await screen.findByRole("link", { name: "My dashboard" })).toHaveAttribute("href", "/dashboard?mode=user");
    expect(screen.queryByRole("link", { name: "Log in" })).toBeNull();
  });
});
