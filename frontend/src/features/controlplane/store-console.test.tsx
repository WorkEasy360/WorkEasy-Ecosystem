import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { AdminContext } from "./ControlShell";
import { PlansScreen } from "./AdminPlans";
import { PricingScreen } from "./AdminPricing";
import { PaymentsScreen } from "./AdminPayments";
import { CommunicationScreen } from "./AdminCommunication";
import { toMajor, toMinor } from "./amounts";

/**
 * Phase 8 extension: the Super Admin console screens (plan builder, pricing,
 * payments, customer billing, users, communication). What the browser sends -
 * and never sends - plus the same non-visual axe pass per state as the other
 * consoles. Contrast, focus visibility and reflow remain manual.
 */

let pathname = "/admin";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => pathname,
}));

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

const writes = (calls: Call[]) => calls.filter((c) => !c.key.startsWith("GET "));
const bodyOf = (calls: Call[], key: string) => calls.find((c) => c.key === key)?.body as Record<string, unknown>;

function asStaff(permissions: string[], ui: React.ReactNode) {
  return render(
    <AdminContext.Provider value={{ permissions }}>
      <main>{ui}</main>
    </AdminContext.Provider>,
  );
}

const FAILED: Reply = { status: 500, body: { code: "unavailable", detail: "Try again shortly." } };
const T = "2026-09-01T10:00:00Z";
const ORG = "22222222-2222-2222-2222-222222222222";
const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;

beforeEach(() => {
  vi.restoreAllMocks();
  pathname = "/admin";
});

// --- navigation ------------------------------------------------------------------------

describe("typed amounts", () => {
  it("converts to integer minor units with the currency's exponent, without floats", () => {
    expect(toMinor("499.99", "INR")).toBe(49999);
    expect(toMinor("0.1", "USD")).toBe(10);
    expect(toMinor("1500", "JPY")).toBe(1500);
    expect(toMinor("12.5", "JPY")).toBeNull();
    for (const bad of ["", "1.234", "-1", "1e3", "abc", "1,000"]) expect(toMinor(bad, "INR")).toBeNull();
    expect(toMajor(49999, "INR")).toBe("499.99");
    expect(toMajor(5, "USD")).toBe("0.05");
    expect(toMajor(1500, "JPY")).toBe("1500");
  });
});

// --- plan builder --------------------------------------------------------------------------

const PRODUCTS = [
  { id: "prod-a", code: "alpha", name: "Alpha", status: "available", licensing_mode: "per_seat" },
  { id: "prod-b", code: "beta", name: "Beta", status: "available", licensing_mode: "per_seat" },
];

const BOOK = {
  id: "book-inr",
  code: "direct-inr",
  name: "Direct INR",
  channel: "direct",
  partner_id: null,
  region: "",
  currency: "INR",
  priority: 100,
  is_active: true,
  valid_from: T,
  valid_to: null,
};

const ALPHA_PRO = {
  id: "plan-alpha-pro",
  code: "alpha-pro",
  name: "Alpha Pro",
  caption: "For growing teams",
  description: "",
  status: "available",
  is_public: true,
  sort_order: 100,
  billing_type: "paid",
  kind: "standard",
  product: { id: "prod-a", code: "alpha", name: "Alpha" },
  addon_for: null,
  versions: [
    {
      id: "pv-alpha-pro-2",
      version: 2,
      published_at: T,
      effective_from: T,
      effective_to: null,
      included_seats: 5,
      max_seats: null,
      trial_days: 0,
      features: { "alpha.pipelines.max": 10 },
      prices: [
        {
          price_id: "price-1",
          plan_version_id: "pv-alpha-pro-2",
          bundle_version_id: null,
          price_book_id: "book-inr",
          price_book_code: "direct-inr",
          channel: "direct",
          partner_id: null,
          currency: "INR",
          interval: "monthly",
          model: "flat",
          amount_minor: 49999,
          valid_from: T,
        },
      ],
    },
  ],
};

const OVERVIEW = {
  products: PRODUCTS,
  plans: [ALPHA_PRO],
  bundles: [],
  price_books: [BOOK],
  currencies: ["INR", "USD", "JPY"],
  intervals: ["monthly", "quarterly", "semi_annual", "annual"],
  billing_types: ["paid", "free_trial"],
};

const FEATURES = {
  results: [
    { id: "f1", key: "alpha.pipelines.max", name: "Pipelines", kind: "quota", unit: "", default_value: 3, enum_ordering: null },
    { id: "f2", key: "alpha.export", name: "Export", kind: "boolean", unit: "", default_value: false, enum_ordering: null },
    { id: "f3", key: "alpha.support", name: "Support tier", kind: "enum", unit: "", default_value: "standard", enum_ordering: ["standard", "priority"] },
  ],
};

const CATALOG_MANAGER = [
  "platform.staff_catalog.view",
  "platform.staff_catalog.manage",
  "platform.staff_pricing.manage",
];

function catalogRoutes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    "GET /api/v1/admin/catalog/overview": [{ status: 200, body: OVERVIEW }],
    "GET /api/v1/admin/catalog/products/prod-a/features": [{ status: 200, body: FEATURES }],
    "GET /api/v1/admin/catalog/products/prod-b/features": [{ status: 200, body: { results: [] } }],
    "POST /api/v1/admin/catalog/plans": [{ status: 201, body: { id: "new-plan", code: "alpha-team" } }],
    "POST /api/v1/admin/catalog/plan-versions": [{ status: 201, body: { id: "new-version", version: 1, published: true } }],
    "POST /api/v1/admin/catalog/prices": [{ status: 201, body: { id: "new-price", amount_minor: 1 } }],
    "POST /api/v1/admin/catalog/plans/new-plan/status": [{ status: 200, body: { id: "new-plan", status: "available" } }],
    "POST /api/v1/admin/catalog/bundles": [{ status: 201, body: { id: "new-bundle", code: "suite" } }],
    "POST /api/v1/admin/catalog/bundle-versions": [{ status: 201, body: { id: "new-bundle-version", version: 1, published: true } }],
    "POST /api/v1/admin/catalog/bundles/new-bundle/status": [{ status: 200, body: { id: "new-bundle", status: "available" } }],
    ...extra,
  };
}

async function openPackageBuilder() {
  fireEvent.click(await screen.findByRole("tab", { name: /^Product packages/ }));
  fireEvent.click(screen.getByRole("button", { name: "New product package" }));
  return screen.findByRole("heading", { name: "New product package" });
}

async function fillPackage({ status = "Public", price = "499.99" }: { status?: string; price?: string } = {}) {
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Alpha Team" } });
  fireEvent.change(screen.getByLabelText("Code"), { target: { value: "alpha-team" } });
  fireEvent.change(screen.getByLabelText("Product"), { target: { value: "prod-a" } });
  await screen.findByLabelText("Pipelines");
  fireEvent.click(screen.getByRole("checkbox", { name: "Monthly (30 days)" }));
  if (price) fireEvent.change(screen.getByLabelText("Monthly (30 days) price (INR)"), { target: { value: price } });
  fireEvent.click(screen.getByRole("radio", { name: status }));
}

describe("the plan builder", () => {
  it("lists offers by tab with type, status and the latest published prices", async () => {
    mockApi(catalogRoutes());
    const { container } = asStaff(CATALOG_MANAGER, <PlansScreen />);
    expect(await screen.findByRole("heading", { level: 1, name: "Plans & add-ons" })).toBeInTheDocument();
    expect(await screen.findByText("No plans yet.")).toBeInTheDocument();
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("tab", { name: /^Product packages/ }));
    const table = await screen.findByRole("table", { name: "Product packages" });
    const row = within(table).getByRole("row", { name: /Alpha Pro/ });
    expect(row).toHaveTextContent("Public");
    expect(row).toHaveTextContent("Paid");
    expect(row).toHaveTextContent("Monthly (30 days)");
    expect(row).toHaveTextContent("v2");
    expect(within(row).getByRole("button", { name: "Edit details of Alpha Pro" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("offers no builder to staff who can only view the catalog", async () => {
    mockApi(catalogRoutes());
    asStaff(["platform.staff_catalog.view"], <PlansScreen />);
    await screen.findByRole("tab", { name: /^Plans/ });
    expect(screen.queryByRole("button", { name: /^New / })).not.toBeInTheDocument();
  });

  it("validates before any call is made", async () => {
    const calls = mockApi(catalogRoutes());
    const { container } = asStaff(CATALOG_MANAGER, <PlansScreen />);
    await openPackageBuilder();
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "Bad Code!" } });
    fireEvent.click(screen.getByRole("radio", { name: "Public" }));
    fireEvent.click(screen.getByRole("button", { name: "Create product package" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Enter a title.");
    expect(alert).toHaveTextContent(/Enter a code/);
    expect(alert).toHaveTextContent("Choose the product this package belongs to.");
    expect(alert).toHaveTextContent(/at least one billing cycle/);
    expect(writes(calls)).toEqual([]);
    await expectNoViolations(container);

    // A price that isn't a plain amount is refused too.
    await fillPackage({ price: "4.999" });
    fireEvent.click(screen.getByRole("button", { name: "Create product package" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Monthly \(30 days\): enter a price/));
    expect(writes(calls)).toEqual([]);
  });

  it("creates, versions, prices and publishes in order, sending integer minor units", async () => {
    const calls = mockApi(catalogRoutes());
    asStaff(CATALOG_MANAGER, <PlansScreen />);
    await openPackageBuilder();
    await fillPackage();
    fireEvent.click(screen.getByRole("checkbox", { name: /Export/ }));
    // The builder is a popup over the list; saving closes it.
    const popup = screen.getByRole("dialog", { name: "New product package" });
    fireEvent.click(within(popup).getByRole("button", { name: "Create product package" }));
    expect(await screen.findByText("Product package “Alpha Team” created.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    expect(writes(calls).map((c) => c.key)).toEqual([
      "POST /api/v1/admin/catalog/plans",
      "POST /api/v1/admin/catalog/plan-versions",
      "POST /api/v1/admin/catalog/prices",
      "POST /api/v1/admin/catalog/plans/new-plan/status",
    ]);
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/plans")).toMatchObject({
      product_id: "prod-a",
      code: "alpha-team",
      name: "Alpha Team",
      is_public: true,
      billing_type: "paid",
      kind: "standard",
    });
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/plan-versions")).toEqual({
      plan_id: "new-plan",
      included_seats: 1,
      max_seats: null,
      trial_days: 0,
      features: { "alpha.pipelines.max": 3, "alpha.export": true, "alpha.support": "standard" },
      publish: true,
    });
    const price = bodyOf(calls, "POST /api/v1/admin/catalog/prices");
    expect(price).toEqual({
      price_model: "flat",
      price_book_id: "book-inr",
      plan_version_id: "new-version",
      interval: "monthly",
      amount_minor: 49999,
    });
    expect(Number.isInteger(price.amount_minor)).toBe(true);
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/plans/new-plan/status")).toEqual({ status: "available" });
  });

  it("a Draft publishes no version and no prices, and stays a draft", async () => {
    const calls = mockApi(catalogRoutes());
    asStaff(CATALOG_MANAGER, <PlansScreen />);
    await openPackageBuilder();
    await fillPackage({ status: "Draft" });
    fireEvent.click(screen.getByRole("button", { name: "Create product package" }));
    await screen.findByText("Product package “Alpha Team” created as a draft.");
    expect(writes(calls).map((c) => c.key)).toEqual([
      "POST /api/v1/admin/catalog/plans",
      "POST /api/v1/admin/catalog/plan-versions",
    ]);
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/plans")).toMatchObject({ is_public: false });
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/plan-versions")).toMatchObject({ publish: false });
  });

  it("a Free Trial plan needs trial days, includes a package and publishes zero prices", async () => {
    const calls = mockApi(catalogRoutes());
    const { container } = asStaff(CATALOG_MANAGER, <PlansScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "New plan" }));
    await screen.findByRole("heading", { name: "New plan" });
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Starter suite" } });
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "starter-suite" } });
    fireEvent.click(screen.getByRole("radio", { name: "Free Trial" }));
    fireEvent.click(screen.getByRole("radio", { name: "Private" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Monthly (30 days)" }));
    // No price input for a free trial: it is published at zero.
    expect(screen.queryByLabelText(/price \(INR\)/)).not.toBeInTheDocument();

    // Beta has no package priced monthly; Alpha's published package qualifies.
    fireEvent.click(screen.getByRole("tab", { name: "Beta" }));
    expect(screen.getByText("No package available for the selected billing cycles.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Alpha" }));
    fireEvent.change(screen.getByLabelText("Package for Alpha"), { target: { value: "pv-alpha-pro-2" } });
    expect(screen.getByRole("tab", { name: "Alpha (included)" })).toBeInTheDocument();
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Create plan" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("A free trial needs 1 to 365 trial days."));
    expect(writes(calls)).toEqual([]);

    fireEvent.change(screen.getByLabelText(/Trial days/), { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "Create plan" }));
    await screen.findByText("Plan “Starter suite” created.");
    expect(writes(calls).map((c) => c.key)).toEqual([
      "POST /api/v1/admin/catalog/bundles",
      "POST /api/v1/admin/catalog/bundle-versions",
      "POST /api/v1/admin/catalog/prices",
      "POST /api/v1/admin/catalog/bundles/new-bundle/status",
    ]);
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/bundles")).toMatchObject({
      code: "starter-suite",
      billing_type: "free_trial",
      is_public: false,
    });
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/bundle-versions")).toEqual({
      bundle_id: "new-bundle",
      items: [{ plan_version_id: "pv-alpha-pro-2", quantity: 1 }],
      trial_days: 14,
      publish: true,
    });
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/prices")).toMatchObject({
      bundle_version_id: "new-bundle-version",
      interval: "monthly",
      amount_minor: 0,
    });
  });

  it("resumes after a failed step without creating the plan twice", async () => {
    const calls = mockApi(
      catalogRoutes({
        "POST /api/v1/admin/catalog/plan-versions": [
          FAILED,
          { status: 201, body: { id: "new-version", version: 1, published: true } },
        ],
      }),
    );
    asStaff(CATALOG_MANAGER, <PlansScreen />);
    await openPackageBuilder();
    await fillPackage();
    fireEvent.click(screen.getByRole("button", { name: "Create product package" }));

    expect((await screen.findAllByText("Try again shortly.")).length).toBeGreaterThan(0);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    // The plan was made before the failure: saving again resumes, it does not start over.
    fireEvent.click(screen.getByRole("button", { name: "Resume saving" }));
    await screen.findByText("Product package “Alpha Team” created.");

    const keys = writes(calls).map((c) => c.key);
    expect(keys).toEqual([
      "POST /api/v1/admin/catalog/plans",
      "POST /api/v1/admin/catalog/plan-versions",
      "POST /api/v1/admin/catalog/plan-versions",
      "POST /api/v1/admin/catalog/prices",
      "POST /api/v1/admin/catalog/plans/new-plan/status",
    ]);
    expect(keys.filter((k) => k === "POST /api/v1/admin/catalog/plans")).toHaveLength(1);
  });

  it("refuses to publish prices for staff without the pricing grant", async () => {
    const calls = mockApi(catalogRoutes());
    asStaff(["platform.staff_catalog.view", "platform.staff_catalog.manage"], <PlansScreen />);
    await openPackageBuilder();
    await fillPackage();
    fireEvent.click(screen.getByRole("button", { name: "Create product package" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/platform\.staff_pricing\.manage/));
    expect(writes(calls)).toEqual([]);
  });

  it("publishes a new version of an existing package, pre-filled from the current one", async () => {
    const calls = mockApi(
      catalogRoutes({
        "POST /api/v1/admin/catalog/plan-versions": [{ status: 201, body: { id: "pv-alpha-pro-3", version: 3, published: true } }],
      }),
    );
    asStaff(CATALOG_MANAGER, <PlansScreen />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Product packages/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Publish new version of Alpha Pro" }));
    await screen.findByRole("heading", { name: "Publish a new version of “Alpha Pro”" });
    expect(screen.getByLabelText("Included seats")).toHaveValue("5");
    expect(screen.getByLabelText("Monthly (30 days) price (INR)")).toHaveValue("499.99");
    expect(await screen.findByLabelText("Pipelines")).toHaveValue("10");
    fireEvent.change(screen.getByLabelText("Monthly (30 days) price (INR)"), { target: { value: "549" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish version" }));
    await screen.findByText("A new version of “Alpha Pro” is published.");
    expect(writes(calls).map((c) => c.key)).toEqual([
      "POST /api/v1/admin/catalog/plan-versions",
      "POST /api/v1/admin/catalog/prices",
    ]);
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/plan-versions")).toMatchObject({
      plan_id: "plan-alpha-pro",
      included_seats: 5,
      publish: true,
    });
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/prices")).toMatchObject({
      plan_version_id: "pv-alpha-pro-3",
      amount_minor: 54900,
    });
  });

  it("edits presentation and visibility without touching terms", async () => {
    const calls = mockApi(
      catalogRoutes({
        "PATCH /api/v1/admin/catalog/plans/plan-alpha-pro": [{ status: 200, body: {} }],
      }),
    );
    asStaff(CATALOG_MANAGER, <PlansScreen />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Product packages/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit details of Alpha Pro" }));
    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Alpha Professional" } });
    fireEvent.click(screen.getByRole("radio", { name: "Private" }));
    expect(screen.getByRole("radio", { name: "Draft" })).toBeDisabled();
    expect(screen.getByRole("dialog", { name: "Edit details of “Alpha Pro”" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    await screen.findByText("“Alpha Professional” updated.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(writes(calls).map((c) => c.key)).toEqual(["PATCH /api/v1/admin/catalog/plans/plan-alpha-pro"]);
    expect(bodyOf(calls, "PATCH /api/v1/admin/catalog/plans/plan-alpha-pro")).toEqual({
      name: "Alpha Professional",
      caption: "For growing teams",
      description: "",
      is_public: false,
    });
  });

  it("the description preview renders hostile text as text and only https links", async () => {
    mockApi(catalogRoutes());
    const { container } = asStaff(CATALOG_MANAGER, <PlansScreen />);
    await openPackageBuilder();
    const description = screen.getByLabelText("Description");
    fireEvent.change(description, {
      target: { value: `${EVIL}\n\n**bold** and [bad](javascript:alert(1)) and [good](https://example.com/x)\n- one` },
    });
    const preview = screen.getByRole("group", { name: "Preview" });
    expect(preview.querySelector("img")).toBeNull();
    expect(preview.querySelector("script")).toBeNull();
    expect(preview.textContent).toContain("<img");
    expect(within(preview).getByText("bold").tagName).toBe("STRONG");
    expect(within(preview).queryByRole("link", { name: /bad/ })).not.toBeInTheDocument();
    expect(within(preview).getByRole("link", { name: /good/ })).toHaveAttribute("href", "https://example.com/x");
    expect(preview.querySelector("li")?.textContent).toBe("one");
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    await expectNoViolations(container);
  });

  it("the toolbar inserts Markdown at the cursor", async () => {
    mockApi(catalogRoutes());
    asStaff(CATALOG_MANAGER, <PlansScreen />);
    await openPackageBuilder();
    const description = screen.getByLabelText("Description") as HTMLTextAreaElement;
    fireEvent.change(description, { target: { value: "Fast sync" } });
    description.setSelectionRange(0, 4);
    fireEvent.click(screen.getByRole("button", { name: "Bold" }));
    expect(description.value).toBe("**Fast** sync");
  });

  it("the builder opens in a popup that Cancel and Escape close without a call", async () => {
    const calls = mockApi(catalogRoutes());
    asStaff(CATALOG_MANAGER, <PlansScreen />);
    await openPackageBuilder();
    const popup = screen.getByRole("dialog", { name: "New product package" });
    expect(within(popup).getByLabelText("Title")).toHaveFocus();
    fireEvent.click(within(popup).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openPackageBuilder();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
  });
});

// --- pricing -------------------------------------------------------------------------------

describe("currency and pricing", () => {
  it("lists books and current prices, and creates a book with only its segment", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/catalog/overview": [{ status: 200, body: OVERVIEW }],
      "POST /api/v1/admin/catalog/price-books": [{ status: 201, body: { id: "book-usd", code: "direct-usd" } }],
    });
    const { container } = asStaff(["platform.staff_catalog.view", "platform.staff_pricing.manage"], <PricingScreen />);
    const books = await screen.findByRole("table", { name: "Price books" });
    expect(within(books).getByText("direct-inr")).toBeInTheDocument();
    const prices = screen.getByRole("table", { name: "Alpha: current prices" });
    expect(within(prices).getByText("Alpha Pro")).toBeInTheDocument();
    await expectNoViolations(container);

    // The new book's form is a popup over the list.
    expect(screen.queryByLabelText("Code")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New price book" }));
    const popup = screen.getByRole("dialog", { name: "New price book" });
    fireEvent.change(within(popup).getByLabelText("Code"), { target: { value: "direct-usd" } });
    fireEvent.change(within(popup).getByLabelText("Name"), { target: { value: "Direct USD" } });
    fireEvent.change(within(popup).getByLabelText("Currency"), { target: { value: "USD" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Create price book" }));
    await screen.findByText("Price book “Direct USD” created.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(bodyOf(calls, "POST /api/v1/admin/catalog/price-books")).toEqual({
      code: "direct-usd",
      name: "Direct USD",
      currency: "USD",
      priority: 100,
    });
  });

  it("checks a new book in its popup before any call, and keeps it open", async () => {
    const calls = mockApi({ "GET /api/v1/admin/catalog/overview": [{ status: 200, body: OVERVIEW }] });
    asStaff(["platform.staff_catalog.view", "platform.staff_pricing.manage"], <PricingScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "New price book" }));
    const popup = screen.getByRole("dialog", { name: "New price book" });
    fireEvent.change(within(popup).getByLabelText("Code"), { target: { value: "Bad Code!" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Create price book" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent(/Enter a code.*Enter a name\./);
    expect(writes(calls)).toEqual([]);
    expect(screen.getByRole("dialog", { name: "New price book" })).toBeInTheDocument();
  });
});

// --- payments ------------------------------------------------------------------------------

describe("payment providers", () => {
  const provider = {
    id: "pp1",
    key: "stripe-eu",
    adapter: "stripe",
    display_name: "Stripe EU",
    mode: "test",
    status: "disabled",
    currencies: ["EUR", "GBP"],
    priority: 100,
    version: 4,
    credential_ref: "STRIPE_EU",
    webhook_secret_ref: "STRIPE_EU",
    credential_configured: true,
    webhook_secret_configured: false,
    credential_variable: "PAYMENTS_API_KEY_STRIPE_EU",
    webhook_secret_variable: "PAYMENTS_WEBHOOK_SECRET_STRIPE_EU",
    webhook_path: "/api/v1/public/payments/webhooks/stripe-eu",
    // Never sent by the server; if it ever were, it must not reach the page.
    api_key: "sk_live_THIS_MUST_NOT_RENDER",
  };

  it("shows where secrets go and never a secret value", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/payments/providers": [{ status: 200, body: { results: [provider] } }],
      "GET /api/v1/admin/payments/events": [
        {
          status: 200,
          body: {
            results: [
              {
                id: "e1",
                provider: "stripe-eu",
                event_id: "evt_1",
                kind: "payment_succeeded",
                outcome: "applied",
                organization_id: ORG,
                attempt_id: "a1",
                amount_minor: 1050,
                currency: "EUR",
                received_at: T,
              },
            ],
          },
        },
      ],
      "PATCH /api/v1/admin/payments/providers/stripe-eu": [{ status: 200, body: provider }],
    });
    const { container } = asStaff(["platform.staff_payment.view", "platform.staff_payment.manage"], <PaymentsScreen />);
    await screen.findByRole("list", { name: "Payment providers" });
    expect(screen.getByRole("heading", { name: "Stripe EU" })).toBeInTheDocument();
    // One secret missing: the provider needs setup.
    expect(screen.getByText("Needs setup")).toBeInTheDocument();
    expect(screen.getByText(/PAYMENTS_API_KEY_STRIPE_EU/)).toBeInTheDocument();
    expect(screen.getByText(/PAYMENTS_WEBHOOK_SECRET_STRIPE_EU/)).toBeInTheDocument();
    expect(screen.getByText("/api/v1/public/payments/webhooks/stripe-eu")).toBeInTheDocument();
    expect(container.textContent).not.toContain("sk_live");
    expect(container.querySelector("input[type=password]")).toBeNull();
    // Enabling needs both secrets present.
    expect(screen.getByRole("button", { name: "Enable Stripe EU" })).toBeDisabled();
    expect(screen.getByRole("table", { name: "Payment event log" })).toHaveTextContent("Payment succeeded");
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Switch Stripe EU to live mode" }));
    await screen.findByText("Stripe EU is now in live mode.");
    expect(bodyOf(calls, "PATCH /api/v1/admin/payments/providers/stripe-eu")).toEqual({ mode: "live", version: 4 });
  });

  it("adds a provider by reference, with no field for a secret", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/payments/providers": [{ status: 200, body: { results: [] } }],
      "GET /api/v1/admin/payments/events": [{ status: 200, body: { results: [] } }],
      "POST /api/v1/admin/payments/providers": [{ status: 201, body: provider }],
    });
    asStaff(["platform.staff_payment.view", "platform.staff_payment.manage"], <PaymentsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    const popup = screen.getByRole("dialog", { name: "Add provider" });
    expect(popup.querySelector("input[type=password]")).toBeNull();
    fireEvent.change(within(popup).getByLabelText("Key"), { target: { value: "razor-in" } });
    fireEvent.change(within(popup).getByLabelText("Adapter"), { target: { value: "razorpay" } });
    fireEvent.change(within(popup).getByLabelText("API key reference"), { target: { value: "razor_in" } });
    fireEvent.change(within(popup).getByLabelText("Webhook secret reference"), { target: { value: "razor_in" } });
    fireEvent.click(within(popup).getByRole("checkbox", { name: "INR" }));
    fireEvent.click(within(popup).getByRole("button", { name: "Add provider" }));
    await screen.findByText(/Provider added/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(bodyOf(calls, "POST /api/v1/admin/payments/providers")).toEqual({
      key: "razor-in",
      adapter: "razorpay",
      display_name: "razor-in",
      mode: "test",
      credential_ref: "RAZOR_IN",
      webhook_secret_ref: "RAZOR_IN",
      currencies: ["INR"],
      priority: 100,
    });
  });

  it("refuses a provider without references in the popup, before any call", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/payments/providers": [{ status: 200, body: { results: [] } }],
      "GET /api/v1/admin/payments/events": [{ status: 200, body: { results: [] } }],
    });
    asStaff(["platform.staff_payment.view", "platform.staff_payment.manage"], <PaymentsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    const popup = screen.getByRole("dialog", { name: "Add provider" });
    fireEvent.click(within(popup).getByRole("button", { name: "Add provider" }));
    const alert = await within(popup).findByRole("alert");
    expect(alert).toHaveTextContent(/API key reference: 1–48/);
    expect(alert).toHaveTextContent("Choose at least one currency.");
    expect(writes(calls)).toEqual([]);
  });

  it("edits a provider's currencies in a popup from its row", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/payments/providers": [{ status: 200, body: { results: [provider] } }],
      "GET /api/v1/admin/payments/events": [{ status: 200, body: { results: [] } }],
      "PATCH /api/v1/admin/payments/providers/stripe-eu": [{ status: 200, body: provider }],
    });
    asStaff(["platform.staff_payment.view", "platform.staff_payment.manage"], <PaymentsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit currencies of Stripe EU" }));
    const popup = screen.getByRole("dialog", { name: "Edit currencies of Stripe EU" });
    fireEvent.click(within(popup).getByRole("checkbox", { name: "GBP" }));
    fireEvent.click(within(popup).getByRole("checkbox", { name: "EUR" }));
    expect(within(popup).getByRole("button", { name: "Save currencies" })).toBeDisabled();
    fireEvent.click(within(popup).getByRole("checkbox", { name: "INR" }));
    fireEvent.click(within(popup).getByRole("button", { name: "Save currencies" }));
    await screen.findByText("Stripe EU: currencies updated.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(bodyOf(calls, "PATCH /api/v1/admin/payments/providers/stripe-eu")).toEqual({ currencies: ["INR"], version: 4 });
  });
});

// --- a customer's billing --------------------------------------------------------------------

const TEMPLATES = {
  results: [
    {
      key: "invitation",
      description: "Invites a person to an organization.",
      variables: ["organization", "inviter", "link"],
      required: ["link"],
      default_subject: "Join {organization}",
      default_body_text: "Hi,\n\n{inviter} invited you.",
      override: null,
    },
    {
      key: "password_reset",
      description: "A password reset link.",
      variables: ["link"],
      required: ["link"],
      default_subject: "Reset your password",
      default_body_text: "Reset: {link}",
      override: { subject: "Reset it", body_text: "Here: {link}", version: 3, updated_at: T },
    },
  ],
};

const SENDER = { sender_name: "Workeasy360", reply_to: "", from_address: "no-reply@workeasy360.test", version: 2 };

function communicationRoutes(): Record<string, Reply[]> {
  return {
    "GET /api/v1/admin/communication/templates": [{ status: 200, body: TEMPLATES }],
    "GET /api/v1/admin/communication/sender": [{ status: 200, body: SENDER }],
    "POST /api/v1/admin/communication/templates/invitation/preview": [
      { status: 200, body: { subject: "Join Acme Ltd", body_text: "Hi,\n\nPat invited you.\nhttps://example.test/i/abc" } },
    ],
    "PUT /api/v1/admin/communication/templates/invitation": [{ status: 200, body: TEMPLATES.results[0] }],
    "PUT /api/v1/admin/communication/templates/password_reset": [{ status: 200, body: TEMPLATES.results[1] }],
    "DELETE /api/v1/admin/communication/templates/password_reset": [{ status: 200, body: TEMPLATES.results[1] }],
    "POST /api/v1/admin/communication/templates/invitation/test": [{ status: 202, body: { queued: true, to: "s@example.com" } }],
    "PUT /api/v1/admin/communication/sender": [{ status: 200, body: { ...SENDER, version: 3 } }],
  };
}

const COMMS = ["platform.staff_communication.view", "platform.staff_communication.manage"];

describe("communication", () => {
  it("inserts placeholders at the cursor, previews the unsaved text and saves with no version for a first override", async () => {
    const calls = mockApi(communicationRoutes());
    const { container } = asStaff(COMMS, <CommunicationScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Invitation" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const body = screen.getByLabelText("Body (plain text)") as HTMLTextAreaElement;
    fireEvent.focus(body);
    body.setSelectionRange(body.value.length, body.value.length);
    fireEvent.click(screen.getByRole("button", { name: "{link} (required)" }));
    expect(body.value).toBe("Hi,\n\n{inviter} invited you.{link}");

    const subject = screen.getByLabelText("Subject") as HTMLInputElement;
    fireEvent.focus(subject);
    subject.setSelectionRange(0, 0);
    fireEvent.click(screen.getByRole("button", { name: "{inviter}" }));
    expect(subject.value).toBe("{inviter}Join {organization}");

    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("Join Acme Ltd")).toBeInTheDocument();
    expect(bodyOf(calls, "POST /api/v1/admin/communication/templates/invitation/preview")).toEqual({
      subject: "{inviter}Join {organization}",
      body_text: "Hi,\n\n{inviter} invited you.{link}",
    });
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Save template" }));
    await screen.findByText(/Template saved/);
    expect(bodyOf(calls, "PUT /api/v1/admin/communication/templates/invitation")).toEqual({
      subject: "{inviter}Join {organization}",
      body_text: "Hi,\n\n{inviter} invited you.{link}",
      version: null,
    });

    fireEvent.click(screen.getByRole("button", { name: "Send me a test" }));
    expect(await screen.findByText(/The test went to s@example.com/)).toBeInTheDocument();
  });

  it("groups the templates by category, in words, with the key as small text and the status", async () => {
    mockApi(communicationRoutes());
    const { container } = asStaff(COMMS, <CommunicationScreen />);
    const security = await screen.findByRole("table", { name: "Email templates: Account & security" });
    const row = within(security).getByRole("row", { name: /Password reset/ });
    expect(row).toHaveTextContent("password_reset");
    expect(row).toHaveTextContent("Customized");
    expect(within(screen.getByRole("table", { name: "Email templates: Other" })).getByRole("row", { name: /Invitation/ })).toHaveTextContent("Default");
    expect(screen.getByRole("heading", { level: 1, name: "Email templates" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("saves and resets an existing override against its version", async () => {
    const calls = mockApi(communicationRoutes());
    asStaff(COMMS, <CommunicationScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Password reset" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    expect(screen.getByLabelText("Subject")).toHaveValue("Reset it");
    fireEvent.click(screen.getByRole("button", { name: "Save template" }));
    await screen.findByText(/Template saved/);
    expect(bodyOf(calls, "PUT /api/v1/admin/communication/templates/password_reset")).toMatchObject({ version: 3 });
    fireEvent.click(screen.getByRole("button", { name: "Reset to default" }));
    await screen.findByText(/reset to the default wording/);
    expect(bodyOf(calls, "DELETE /api/v1/admin/communication/templates/password_reset")).toEqual({ version: 3 });
    expect(screen.getByLabelText("Subject")).toHaveValue("Reset your password");
  });

  it("the sender form shows the fixed address and saves with its version", async () => {
    const calls = mockApi(communicationRoutes());
    asStaff(COMMS, <CommunicationScreen />);
    expect(await screen.findByText("no-reply@workeasy360.test")).toBeInTheDocument();
    // The values in force are shown as text; they are changed in a popup.
    expect(screen.getByText("Reply-to address").nextElementSibling).toHaveTextContent("Not set");
    expect(screen.queryByLabelText("Reply-to address (optional)")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit sender" }));
    const popup = screen.getByRole("dialog", { name: "Sender" });
    expect(within(popup).getByLabelText("Sender name")).toHaveValue("Workeasy360");
    fireEvent.change(within(popup).getByLabelText("Reply-to address (optional)"), { target: { value: "help@acme.test" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Save sender" }));
    await screen.findByText("Sender settings saved.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(bodyOf(calls, "PUT /api/v1/admin/communication/sender")).toEqual({
      sender_name: "Workeasy360",
      reply_to: "help@acme.test",
      version: 2,
    });
  });

  it("view-only staff can preview but not save", async () => {
    mockApi(communicationRoutes());
    asStaff(["platform.staff_communication.view"], <CommunicationScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Invitation" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    expect(screen.getByRole("button", { name: "Preview" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save template" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save sender" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit sender" })).not.toBeInTheDocument();
  });

  it("edits a template in a popup that Close dismisses", async () => {
    mockApi(communicationRoutes());
    asStaff(COMMS, <CommunicationScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Invitation" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const popup = screen.getByRole("dialog", { name: "Edit: Invitation" });
    expect(within(popup).getByLabelText("Subject")).toHaveValue("Join {organization}");
    // The title bar's × and the Close at the bottom both dismiss it.
    const closers = within(popup).getAllByRole("button", { name: "Close" });
    expect(closers).toHaveLength(2);
    fireEvent.click(closers[1]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});


// --- Phase 9: segments, orders, settings, roles ------------------------------------------
