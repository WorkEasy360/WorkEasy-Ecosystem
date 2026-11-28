import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import type { ProductAccess } from "@/lib/api/productAccess";
import { StoreShell } from "./StoreShell";
import { ProductCard } from "./ProductCard";
import { AccessActions } from "./productAccess";
import { CheckoutScreen } from "./Checkout";

/**
 * Automatic free trials on the Store (2026-11-28): each card's one primary
 * action comes from the server - Start Free Trial, Open Product, Setting Up,
 * Retry Setup, Upgrade Plan, Buy Plan - and Start Free Trial needs nothing
 * first: no organization, no seat, no checkout.
 */

let search = new URLSearchParams();
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push, refresh: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn() }),
  useSearchParams: () => search,
  usePathname: () => "/store",
}));

const PLAN_PRO = "11111111-1111-1111-1111-111111111111";
const TRIAL_ID = "66666666-6666-6666-6666-666666666666";

const CRM = {
  code: "easy_crm",
  slug: "easy-crm",
  name: "Easy CRM",
  title: "WorkEasy CRM",
  tagline: "Sales & Lead Management",
  summary: "Manage leads and deals.",
  description: "",
  marketing_url: "",
  availability: "available",
  status: "available",
  category: "Sales",
  icon: "",
  features: [],
  highlights: [],
  plans: [
    {
      plan_version_id: PLAN_PRO,
      plan_code: "crm_pro",
      name: "Advanced",
      caption: "",
      description: "",
      billing_type: "paid",
      kind: "standard",
      addon_for: null,
      included_seats: 1,
      max_seats: 50,
      trial_days: 0,
      trial_available: true,
      recommended: true,
      renewal_rule: "auto_renew",
      included_usage: {},
      prices: [{ interval: "annual", currency: "INR", model: "per_seat", amount_minor: 2400000, from_tiers: false }],
    },
  ],
  starting_price: null,
  trial: { available: true, duration_days: 7, seat_limit: null, payment_method_required: false, plan_codes: ["crm_pro"] },
  ribbon: null,
  price: { amount_minor: 2400000, currency: "INR", interval: "annual", plan_name: "Advanced" },
  yearly_deal: null,
  badges: [],
  plan_types: [],
  trial_days: 7,
  logo_url: null,
  banner_url: null,
};

const CATALOG = {
  currency: "INR",
  currencies: ["INR"],
  count: 1,
  total: 1,
  products: [CRM],
  facets: { categories: [], statuses: [], plan_types: [], best_for: [], price: { min_minor: 0, max_minor: 0 } },
  sorts: [],
};

const SITE = { signup_enabled: true, support_email: "s@example.com", sales_email: "s@example.com", catalog_currency: "INR" };
const SIGNED_IN: Reply = ok({ authenticated: true, user_id: "u1", email: "asha@example.com", name: "Asha Rao" });
const ONE_ORG = ok({
  organizations: [{ organization_id: "o1", slug: "acme", display_name: "Acme", status: "active", membership_id: "m1", is_owner: true }],
  next_cursor: null,
  has_more: false,
});
const NO_ORG = ok({ organizations: [], next_cursor: null, has_more: false });
const NEW_ORG = ok({
  organizations: [{ organization_id: "o2", slug: "new-ws", display_name: "Asha's Workspace", status: "active", membership_id: "m2", is_owner: true }],
  next_cursor: null,
  has_more: false,
});

function access(action: ProductAccess["action"], extra: Partial<ProductAccess> = {}): ProductAccess {
  return {
    action,
    reason: null,
    holding: null,
    launch_state: null,
    upgrade_available: false,
    trial_ends_at: null,
    trial_days_left: null,
    ...extra,
  };
}

const OPEN = access("open", { holding: "trial", launch_state: "active", trial_days_left: 7, upgrade_available: true });

function routes(extra: Record<string, Reply[]>, { orgs = ONE_ORG }: { orgs?: Reply } = {}): Record<string, Reply[]> {
  return {
    "GET /api/v1/public/store": [ok(CATALOG)],
    "GET /api/v1/public/site": [ok(SITE)],
    "GET /api/v1/auth/session": [SIGNED_IN],
    "GET /api/v1/organizations": [orgs],
    "GET /api/v1/orgs/acme/notifications": [ok({ items: [], count: 0 })],
    "GET /api/v1/orgs/new-ws/notifications": [ok({ items: [], count: 0 })],
    ...extra,
  };
}

function card() {
  return render(
    <StoreShell>
      <ul>
        <ProductCard card={CRM as never} />
      </ul>
    </StoreShell>,
  );
}

beforeEach(() => {
  search = new URLSearchParams();
  push.mockReset();
  window.sessionStorage.clear();
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("a product card's access action", () => {
  it("Start Free Trial: one click starts it, and then Open Product opens it", async () => {
    const calls = mockApi(
      routes({
        "GET /api/v1/orgs/acme/product-access": [ok({ products: { easy_crm: access("start_trial", { trial_plan_code: "crm_pro", trial_days: 7 }) } })],
        "POST /api/v1/orgs/acme/product-access/easy_crm/trial": [ok({ trial: { id: TRIAL_ID, product_code: "easy_crm", status: "active" }, access: OPEN })],
        "POST /api/v1/orgs/acme/apps/easy_crm/launch": [ok({ redirect_url: "https://crm.example.com/launch?organization=o1", state: "active", sign_in: "workeasy360" })],
      }),
    );
    const { container } = card();
    fireEvent.click(await screen.findByRole("button", { name: "Start 7-day Free Trial: WorkEasy CRM" }));
    const openButton = await screen.findByRole("button", { name: "Open Product: WorkEasy CRM" });
    const started = calls.find((c) => c.key === "POST /api/v1/orgs/acme/product-access/easy_crm/trial");
    expect(started?.body).toEqual({ plan_code: "crm_pro" });
    expect(started?.headers["idempotency-key"]).toBeTruthy();
    // No checkout, no seat screen, no organization step.
    expect(calls.some((c) => c.key.includes("/orders") || c.key.includes("/seats") || c.key.includes("/workspace"))).toBe(false);
    expect(screen.getByRole("link", { name: "Upgrade Plan: WorkEasy CRM" })).toHaveAttribute("href", "/store/product/easy-crm#plans");
    await expectNoViolations(container);

    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    fireEvent.click(openButton);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://crm.example.com/launch?organization=o1"));
  });

  it("a customer with no organization gets a workspace from the same click", async () => {
    const calls = mockApi(
      routes(
        {
          // Read again after the workspace exists: now the person's one organization.
          "GET /api/v1/organizations": [NO_ORG, NEW_ORG],
          "POST /api/v1/organizations/workspace": [{ status: 201, body: { organization_id: "o2", slug: "new-ws", display_name: "Asha's Workspace", created: true } }],
          "POST /api/v1/orgs/new-ws/product-access/easy_crm/trial": [ok({ trial: { id: TRIAL_ID, product_code: "easy_crm", status: "active" }, access: OPEN })],
          "GET /api/v1/orgs/new-ws/product-access": [ok({ products: { easy_crm: OPEN } })],
        },
        { orgs: NO_ORG },
      ),
    );
    card();
    fireEvent.click(await screen.findByRole("button", { name: "Start 7-day Free Trial: WorkEasy CRM" }));
    expect(await screen.findByRole("button", { name: "Open Product: WorkEasy CRM" })).toBeInTheDocument();
    const order = calls.map((c) => c.key).filter((k) => k.startsWith("POST"));
    expect(order).toEqual(["POST /api/v1/organizations/workspace", "POST /api/v1/orgs/new-ws/product-access/easy_crm/trial"]);
  });

  it("Setting Up is never a button to press, and Retry Setup finishes the setup", async () => {
    mockApi(
      routes({
        "GET /api/v1/orgs/acme/product-access": [ok({ products: { easy_crm: access("retry_setup", { reason: "provisioning_failed", holding: "trial" }) } })],
        "POST /api/v1/orgs/acme/product-access/easy_crm/repair": [ok({ access: OPEN })],
      }),
    );
    card();
    expect(await screen.findByText(/Setup did not finish/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Open Product/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry Setup: WorkEasy CRM" }));
    expect(await screen.findByRole("button", { name: "Open Product: WorkEasy CRM" })).toBeInTheDocument();
  });

  it("Setting Up is shown disabled while provisioning is still running", async () => {
    mockApi(routes({ "GET /api/v1/orgs/acme/product-access": [ok({ products: { easy_crm: access("setting_up", { holding: "trial" }) } })] }));
    card();
    const button = await screen.findByRole("button", { name: "Setting Up…" });
    expect(button).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Open Product/ })).toBeNull();
  });

  it("an ended trial says Upgrade Plan", async () => {
    mockApi(routes({ "GET /api/v1/orgs/acme/product-access": [ok({ products: { easy_crm: access("upgrade", { reason: "trial_ended" }) } })] }));
    card();
    expect(await screen.findByRole("link", { name: "Upgrade Plan: WorkEasy CRM" })).toHaveAttribute("href", "/store/product/easy-crm#plans");
    expect(screen.queryByRole("button", { name: /Free Trial/ })).toBeNull();
  });

  it("a member who may not start trials is told who can", async () => {
    mockApi(routes({ "GET /api/v1/orgs/acme/product-access": [ok({ products: { easy_crm: access("ask_owner", { reason: "owner_starts_trials" }) } })] }));
    card();
    expect(await screen.findByText(/Only your organization's owner can start a trial/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Free Trial/ })).toBeNull();
  });

  it("a refused trial says why, in words", async () => {
    mockApi(
      routes({
        "GET /api/v1/orgs/acme/product-access": [ok({ products: { easy_crm: access("start_trial", { trial_plan_code: "crm_pro", trial_days: 7 }) } })],
        "POST /api/v1/orgs/acme/product-access/easy_crm/trial": [{ status: 409, body: { code: "trial_not_allowed", reason: "already_used" } }],
      }),
    );
    card();
    fireEvent.click(await screen.findByRole("button", { name: "Start 7-day Free Trial: WorkEasy CRM" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already used its free trial");
  });
});

describe("a person in several organizations", () => {
  it("the product page's actions name the organization they are for", async () => {
    window.sessionStorage.setItem("w360.store-org", "beta");
    mockApi(
      routes(
        {
          "GET /api/v1/orgs/beta/product-access": [ok({ products: { easy_crm: OPEN } })],
          "GET /api/v1/orgs/beta/notifications": [ok({ items: [], count: 0 })],
        },
        {
          orgs: ok({
            organizations: [
              { organization_id: "o1", slug: "acme", display_name: "Acme", status: "active", membership_id: "m1", is_owner: true },
              { organization_id: "o3", slug: "beta", display_name: "Beta Works", status: "active", membership_id: "m3", is_owner: true },
            ],
            next_cursor: null,
            has_more: false,
          }),
        },
      ),
    );
    render(
      <StoreShell>
        <AccessActions code="easy_crm" title="WorkEasy CRM" productSlug="easy-crm" showBuy={false} />
      </StoreShell>,
    );
    expect(await screen.findByRole("button", { name: "Open Product: WorkEasy CRM" })).toBeInTheDocument();
    expect(screen.getByText("For Beta Works")).toBeInTheDocument();
  });
});

describe("the checkout's trial", () => {
  it("starts without an organization: the workspace is created, then the trial", async () => {
    search = new URLSearchParams({ trial: "easy_crm", trial_plan: "crm_pro" });
    const calls = mockApi(
      routes(
        {
          "POST /api/v1/organizations/workspace": [{ status: 201, body: { organization_id: "o2", slug: "new-ws", display_name: "Asha's Workspace", created: true } }],
          "POST /api/v1/orgs/new-ws/product-access/easy_crm/trial": [ok({ trial: { id: TRIAL_ID, product_code: "easy_crm", status: "active" }, access: OPEN })],
          "GET /api/v1/orgs/new-ws/product-access": [ok({ products: { easy_crm: OPEN } })],
          "GET /api/v1/orgs/new-ws/access": [ok({ permissions: ["commercial.subscription.manage"] })],
          "GET /api/v1/public/store/payment-option": [ok({ available: false, provider: null, label: null, methods: [] })],
        },
        { orgs: NO_ORG },
      ),
    );
    render(
      <StoreShell>
        <CheckoutScreen />
      </StoreShell>,
    );
    expect(await screen.findByText(/Your workspace is created automatically/)).toBeInTheDocument();
    expect(screen.queryByText(/Create one in User Admin/)).toBeNull();
    const start = await screen.findByRole("button", { name: /Start Free Trial/ });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/store/checkout/complete?trial=${TRIAL_ID}&org=new-ws`));
    expect(calls.map((c) => c.key).filter((k) => k.startsWith("POST"))).toEqual([
      "POST /api/v1/organizations/workspace",
      "POST /api/v1/orgs/new-ws/product-access/easy_crm/trial",
    ]);
  });
});
