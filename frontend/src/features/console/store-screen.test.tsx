import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { ConsoleAccess } from "@/lib/api/console";
import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";
import { OrgAccessContext } from "./OrgAccess";
import { StoreScreen } from "./StoreScreen";

/**
 * The organization's Store: Explore's products as a marketplace page. Each
 * filter goes to the server as chosen; each card draws the server's facts and
 * offers Explore's own buttons - a trial, buying, the cart - to the owner only.
 */

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/o/acme/store",
  useSearchParams: () => new URLSearchParams(),
}));

const ORG = "/api/v1/orgs/acme";
const STORE = `GET ${ORG}/store`;
const OWNER = ["platform.organization.view", "commercial.subscription.manage"];

function access(permissions: string[]): ConsoleAccess {
  return {
    organization: { organization_id: "o1", slug: "acme", display_name: "Acme Ltd", status: "active", write_allowed: true },
    membership: { membership_id: "m-me", is_owner: true, roles: ["owner"] },
    permissions,
    console: "admin",
  };
}

const mount = (permissions = OWNER) =>
  render(
    <OrgAccessContext.Provider value={{ slug: "acme", access: access(permissions), reload: vi.fn() }}>
      <main>
        <StoreScreen />
      </main>
    </OrgAccessContext.Provider>,
  );

function card(code: string, title: string, overrides: Record<string, unknown> = {}) {
  return {
    code,
    slug: code.replace(/_/g, "-"),
    name: title,
    title,
    tagline: `${title} for growing teams`,
    summary: `${title} for growing teams`,
    description: "",
    availability: "available",
    category: "Sales",
    icon: "",
    features: [],
    plans: [
      {
        plan_version_id: `${code}-pv`,
        plan_code: `${code}_starter`,
        name: `${title} Starter`,
        caption: "",
        description: "",
        billing_type: "paid",
        kind: "standard",
        addon_for: null,
        product_code: code,
        product_name: title,
        included_seats: 3,
        max_seats: null,
        trial_days: 0,
        prices: [{ interval: "monthly", currency: "GBP", model: "flat", amount_minor: 1900, from_tiers: false }],
      },
    ],
    starting_price: { amount_minor: 1900, currency: "GBP", interval: "monthly" },
    trial: { available: true, duration_days: 14, seat_limit: null, payment_method_required: false, plan_codes: [`${code}_starter`] },
    holding: null,
    trial_state: "available",
    trial_ends_at: null,
    trial_days_left: null,
    trial_outcome: null,
    status: "available",
    ribbon: { kind: "trial", label: "14-day free trial" },
    price: { amount_minor: 1900, currency: "GBP", interval: "monthly", plan_name: `${title} Starter` },
    yearly_deal: { amount_minor: 19000, compare_at_minor: 22800, currency: "GBP", save_percent: 17 },
    plan_types: ["monthly", "free_trial"],
    trial_days: 14,
    logo_url: null,
    banner_url: "/api/v1/public/products/harbor-crm/media/b1",
    ...overrides,
  };
}

const SOON = card("ledger_lite", "Ledger Lite", {
  availability: "coming_soon",
  category: "Finance",
  plans: [],
  starting_price: null,
  trial: { available: false, duration_days: null, seat_limit: null, payment_method_required: false, plan_codes: [] },
  trial_state: "unavailable",
  status: "coming_soon",
  ribbon: { kind: "coming_soon", label: "Coming soon" },
  price: null,
  yearly_deal: null,
  plan_types: [],
  trial_days: null,
  banner_url: null,
});

function page(products = [card("harbor_crm", "Harbor CRM"), SOON]) {
  return {
    currency: "GBP",
    currencies: ["GBP"],
    count: products.length,
    total: 2,
    products,
    billing_account: true,
    facets: {
      categories: [
        { value: "Finance", label: "Finance", count: 1 },
        { value: "Sales", label: "Sales", count: 1 },
      ],
      statuses: [
        { value: "available", label: "Available", count: 1 },
        { value: "trial", label: "On a free trial", count: 0 },
        { value: "owned", label: "Your products", count: 0 },
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

describe("the organization's Store", () => {
  it("draws each product as a store card with Explore's buttons", async () => {
    mockApi({ [STORE]: [ok(page())] });
    const { container } = mount();
    const grid = await screen.findByRole("list", { name: "Products" });
    const [crm, soon] = Array.from(grid.children) as HTMLElement[];

    expect(within(crm).getByRole("link", { name: "Harbor CRM" })).toHaveAttribute("href", "/o/acme/store/harbor_crm");
    expect(within(crm).getByText("14-day free trial")).toBeInTheDocument();
    expect(within(within(crm).getByRole("list", { name: "Plans" })).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Monthly",
      "Free trial",
    ]);
    expect(crm.textContent).toContain("£19/month");
    expect(crm.textContent).toContain("Save 17%");
    expect(crm.querySelector("img")).toHaveAttribute("src", "/api/v1/public/products/harbor-crm/media/b1");
    for (const name of ["Start 14-day free trial: Harbor CRM", "Buy now: Harbor CRM", "Add to cart: Harbor CRM"]) {
      expect(within(crm).getByRole("button", { name })).toBeInTheDocument();
    }
    expect(within(crm).getByRole("link", { name: "Learn more: Harbor CRM" })).toHaveAttribute("href", "/products/harbor-crm");

    expect(within(soon).getAllByText("Coming soon").length).toBeGreaterThan(0);
    expect(within(soon).queryByRole("button", { name: /Buy now|Start/ })).toBeNull();
    expect(screen.getByRole("region", { name: "2 products" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("sends each filter to the server", async () => {
    const calls = mockApi({ [STORE]: [ok(page())] });
    mount();
    await screen.findByRole("list", { name: "Products" });
    fireEvent.click(screen.getByRole("checkbox", { name: /Your products/ }));
    await waitFor(() => expect(last(calls).get("status")).toBe("owned"));
    fireEvent.click(screen.getByRole("button", { name: "Show Sales products" }));
    await waitFor(() => expect(last(calls).get("category")).toBe("Sales"));
    // Only the Super Admin's Store has a program filter.
    expect(screen.queryByRole("radio")).toBeNull();
  });

  it("starts a trial through Explore's call, then reads the Store again", async () => {
    const calls = mockApi({
      [STORE]: [ok(page()), ok(page())],
      [`POST ${ORG}/trials`]: [{ status: 201, body: { id: "t1", product_code: "harbor_crm", plan_code: "harbor_crm_starter", status: "active" } }],
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Start 14-day free trial: Harbor CRM" }));
    expect(await screen.findByText("Trial started: Harbor CRM.")).toBeInTheDocument();
    const started = calls.filter((c) => c.key === `POST ${ORG}/trials`);
    expect(started.map((c) => c.body)).toEqual([{ product_code: "harbor_crm", plan_code: "harbor_crm_starter" }]);
    await waitFor(() => expect(calls.filter((c) => c.key === STORE)).toHaveLength(2));
  });

  it("buying opens the checkout for the card's plan", async () => {
    mockApi({ [STORE]: [ok(page())] });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Buy now: Harbor CRM" }));
    expect(nav.push).toHaveBeenCalledWith("/o/acme/checkout?plan=harbor_crm-pv&interval=monthly");
  });

  it("a member who may not buy can browse, without buttons", async () => {
    mockApi({ [STORE]: [ok(page())] });
    mount(["platform.organization.view"]);
    await screen.findByRole("list", { name: "Products" });
    expect(screen.getByText("You can browse. Only the organization owner can buy or start a trial.")).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: /^(Buy now|Add to cart|Start)/ })).toEqual([]);
  });
});
