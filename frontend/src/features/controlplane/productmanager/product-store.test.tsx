import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ProductStore } from "./ProductStore";
import { expectNoViolations, mockApi, ok } from "../testSupport";

/**
 * The Super Admin Store: a marketplace page over the central catalog. It sends
 * each filter to the server exactly as chosen and draws what comes back - the
 * cards, their ribbons and prices, the counts beside each filter - computing
 * none of it.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/admin/store",
}));

const STORE = "GET /api/v1/admin/products/store";

function card(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    code: "harbor_crm",
    slug: "harbor-crm",
    name: "Harbor CRM",
    title: "Harbor CRM",
    summary: "Pipelines, deals and follow-ups in one place.",
    category: "Sales",
    icon: "chart",
    logo_media_id: null,
    banner_media_id: "m-banner",
    manager_status: "active",
    listing: "listed",
    store_status: "live",
    ribbon: { kind: "new", label: "New!" },
    price: { amount_minor: 1499, currency: "GBP", interval: "monthly", plan_name: "Starter" },
    yearly_deal: { amount_minor: 14990, compare_at_minor: 17988, currency: "GBP", save_percent: 17 },
    trial_days: 14,
    plans_on_sale: 2,
    plan_types: ["monthly", "annual", "free_trial"],
    programs: ["customer_store", "reseller"],
    activated_at: "2026-10-20T10:00:00Z",
    created_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

const DRAFT = card({
  id: "p2",
  code: "ledger_lite",
  slug: "ledger-lite",
  name: "Ledger Lite",
  title: "Ledger Lite",
  summary: "",
  category: "Finance",
  banner_media_id: null,
  manager_status: "draft",
  listing: "hidden",
  store_status: "not_listed",
  ribbon: { kind: "draft", label: "Draft" },
  price: null,
  yearly_deal: null,
  trial_days: null,
  plans_on_sale: 0,
  plan_types: [],
});

function page(products = [card(), DRAFT], overrides: Record<string, unknown> = {}) {
  return {
    currency: "GBP",
    currencies: ["GBP"],
    count: products.length,
    total: 2,
    products,
    facets: {
      categories: [
        { value: "Finance", label: "Finance", count: 1 },
        { value: "Sales", label: "Sales", count: 1 },
      ],
      programs: [
        { value: "all", label: "All programs", count: 2 },
        { value: "customer_store", label: "Customer Store", count: 2 },
        { value: "reseller", label: "Resellers", count: 1 },
      ],
      statuses: [
        { value: "live", label: "In the store", count: 1 },
        { value: "coming_soon", label: "Coming soon", count: 0 },
        { value: "not_listed", label: "Not in the store", count: 1 },
      ],
      plan_types: [
        { value: "monthly", label: "Monthly", count: 1 },
        { value: "free_trial", label: "Free trial", count: 1 },
      ],
      price: { min_minor: 1499, max_minor: 1499 },
    },
    sorts: [
      { value: "recommended", label: "Recommended" },
      { value: "price_asc", label: "Price: low to high" },
    ],
    ...overrides,
  };
}

const last = (calls: { url: string }[]) => new URL(calls[calls.length - 1].url, "http://x").searchParams;

describe("Super Admin Store", () => {
  it("draws each product as a store card from the server's facts", async () => {
    mockApi({ [STORE]: [ok(page())] });
    const { container } = render(<ProductStore />);
    const grid = await screen.findByRole("list", { name: "Products" });
    const cards = Array.from(grid.children) as HTMLElement[];
    expect(cards).toHaveLength(2);

    const crm = cards[0];
    expect(within(crm).getByRole("link", { name: "Harbor CRM" })).toHaveAttribute("href", "/admin/products/p1");
    expect(within(crm).getByText("New!")).toBeInTheDocument();
    expect(within(crm).getByText("14-day trial")).toBeInTheDocument();
    expect(within(crm).getByRole("button", { name: "Show Sales products" })).toBeInTheDocument();
    expect(within(crm).getByRole("link", { name: "Manage Harbor CRM" })).toHaveAttribute("href", "/admin/products/p1");
    expect(within(within(crm).getByRole("list", { name: "Sold through" })).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Store",
      "Resellers",
      "14-day trial",
    ]);
    expect(crm.textContent).toContain("£14.99/month");
    expect(crm.textContent).toContain("£149.90/year");
    expect(crm.textContent).toContain("£179.88");
    expect(crm.textContent).toContain("Save 17%");
    expect(crm.querySelector("img")).toHaveAttribute("src", "/api/v1/admin/products/p1/media/m-banner");

    const draft = cards[1];
    expect(within(draft).getByText("Draft")).toBeInTheDocument();
    expect(within(draft).getByText("Not on sale")).toBeInTheDocument();
    expect(within(draft).getByText("No short description yet.")).toBeInTheDocument();

    expect(screen.getByRole("region", { name: "2 products" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("sends every filter to the server as chosen", async () => {
    const calls = mockApi({ [STORE]: [ok(page())] });
    render(<ProductStore />);
    await screen.findByRole("list", { name: "Products" });
    expect([...last(calls).keys()]).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: /^Finance/ }));
    await waitFor(() => expect(last(calls).get("category")).toBe("Finance"));
    expect(screen.getByRole("button", { name: /^Finance/ })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("radio", { name: /Resellers/ }));
    await waitFor(() => expect(last(calls).get("program")).toBe("reseller"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Not in the store/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /In the store/ }));
    await waitFor(() => expect(last(calls).get("status")).toBe("not_listed,live"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Free trial/ }));
    await waitFor(() => expect(last(calls).get("plan_type")).toBe("free_trial"));

    fireEvent.change(screen.getByLabelText("Min (GBP)"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Max (GBP)"), { target: { value: "49.99" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(last(calls).get("price_max")).toBe("49.99"));
    expect(last(calls).get("price_min")).toBe("10");

    fireEvent.change(screen.getByLabelText("Sort by:"), { target: { value: "price_asc" } });
    await waitFor(() => expect(last(calls).get("sort")).toBe("price_asc"));

    fireEvent.change(screen.getByLabelText("Search products"), { target: { value: " crm " } });
    await waitFor(() => expect(last(calls).get("q")).toBe("crm"));

    fireEvent.click(screen.getByRole("button", { name: "Remove filter: Finance" }));
    await waitFor(() => expect(last(calls).get("category")).toBeNull());

    fireEvent.click(screen.getAllByRole("button", { name: "Clear filters" })[0]);
    await waitFor(() => expect([...last(calls).keys()]).toEqual(["sort"]));
  });

  it("a category on a card filters by it", async () => {
    const calls = mockApi({ [STORE]: [ok(page())] });
    render(<ProductStore />);
    fireEvent.click(await screen.findByRole("button", { name: "Show Sales products" }));
    await waitFor(() => expect(last(calls).get("category")).toBe("Sales"));
  });

  it("shows the server's refusal and keeps the last page", async () => {
    mockApi({
      [STORE]: [ok(page()), { status: 400, body: { detail: "The lowest price must not be above the highest." } }],
    });
    render(<ProductStore />);
    await screen.findByRole("list", { name: "Products" });
    fireEvent.change(screen.getByLabelText("Min (GBP)"), { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("Max (GBP)"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The lowest price must not be above the highest.");
    expect(screen.getByRole("list", { name: "Products" })).toBeInTheDocument();
  });

  it("says when nothing matches, and when there is no access", async () => {
    mockApi({ [STORE]: [ok(page([], { count: 0 }))] });
    const { unmount } = render(<ProductStore />);
    expect(await screen.findByText("No product matches these filters.")).toBeInTheDocument();
    unmount();

    mockApi({ [STORE]: [{ status: 403, body: { detail: "No." } }] });
    render(<ProductStore />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to the store.");
  });
});
