import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "../../testSupport";
import { ProductManager } from "../ProductManager";
import { ProductPublisher } from "./ProductPublisher";
import { blankDraft, check, descriptionFromHighlights, fieldKey, highlightsFromDescription, toRequest, type PublisherDraft } from "./model";

/**
 * ADR-068: the one-page Product Publisher. One popup: Basic information,
 * Product details & media, Plans & pricing, Advanced settings (closed), and
 * Cancel | Save Draft | Preview | Publish to Store. One save writes the
 * product and its plans (the Plans section's own records); staged images
 * upload after it; Publish publishes through the server's checklist.
 */

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace, refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/admin/products",
}));

const ADMIN = "/api/v1/admin";
const MANAGE = ["platform.staff_catalog.manage", "platform.staff_pricing.manage", "platform.staff_integration.manage"];
const BOOK = { id: "b-inr", code: "direct_inr", name: "Direct INR", currency: "INR", region: "" };
const BOOT = {
  categories: ["Customer Support", "Sales / CRM"],
  books: [BOOK],
  intervals: ["monthly", "quarterly", "semi_annual", "annual"],
  max_plans: 12,
  can_manage: { catalog: true, pricing: true, integration: true },
};

function pngFile(width: number, height: number, name = "logo.png"): File {
  const bytes = new Uint8Array(33);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return new File([bytes], name, { type: "image/png" });
}

const uploads: string[] = [];
class FakeXHR {
  url = "";
  status = 0;
  responseText = "";
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  open(_m: string, url: string) {
    this.url = url;
  }
  setRequestHeader() {}
  getResponseHeader() {
    return "application/json";
  }
  send() {
    setTimeout(() => {
      this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
      uploads.push(this.url);
      this.status = 201;
      this.responseText = JSON.stringify({ id: `m${uploads.length}`, kind: "logo", width: 256, height: 256, content_type: "image/png", size_bytes: 33, caption: "", sort_order: 0 });
      this.onload?.();
    }, 0);
  }
}

function state(overrides: Record<string, unknown> = {}, plans: unknown[] = []) {
  return {
    ...BOOT,
    categories: BOOT.categories,
    can_see_integrations: true,
    can_see_activity: false,
    product: {
      id: "p1",
      code: "acme_desk",
      name: "Acme Desk",
      slug: "acme-desk",
      tagline: "Support tickets, sorted.",
      description: "",
      category: "Customer Support",
      icon: "",
      status: "draft",
      manager_status: "draft",
      listing: "hidden",
      availability: "draft",
      application_url: "https://desk.example.com",
      customer_store_enabled: true,
      reseller_enabled: false,
      affiliate_enabled: false,
      white_label_enabled: false,
      activated_at: null,
      media: [{ id: "logo1", kind: "logo", width: 256, height: 256, content_type: "image/png", size_bytes: 33, caption: "", sort_order: 0 }],
      launch_url: "",
      integration: null,
      channels: { trial: null },
      readiness: [],
      ...overrides,
    },
    listing: {
      description: "",
      video_url: "",
      video_title: "",
      video_description: "",
      highlights: ["Ticket management"],
      store_title: "",
      store_summary: "",
      application_url: "https://desk.example.com",
      documentation_url: "",
      support_url: "",
      marketing_url: "",
    },
    video: null,
    plans,
    add_on_count: 0,
  };
}

const STARTER_ROW = {
  id: "plan-1",
  code: "acme_desk_starter",
  name: "Starter",
  caption: "For small teams",
  description: "- 5 users\n- Email support",
  status: "draft",
  visible: true,
  recommended: false,
  billing_type: "paid",
  free: false,
  price_book_id: "b-inr",
  currency: "INR",
  prices: { monthly: 49900 },
  per_user: false,
  setup_fee_minor: 0,
  discount_bp: 0,
  included_seats: 5,
  max_seats: null,
  trial_days: 0,
  version: 1,
  published: true,
  features: {},
};

beforeEach(() => {
  uploads.length = 0;
  replace.mockReset();
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
});
afterEach(() => vi.unstubAllGlobals());

function renderPublisher(props: Partial<React.ComponentProps<typeof ProductPublisher>> = {}) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  const view = render(
    <AdminContextForTest permissions={MANAGE}>
      <ProductPublisher productId={null} takenSlugs={new Set(["taken-name"])} onClose={onClose} onSaved={onSaved} {...props} />
    </AdminContextForTest>,
  );
  return { ...view, onClose, onSaved };
}

async function fillBasics(name = "Acme Desk") {
  fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: name } });
  fireEvent.change(screen.getByLabelText(/^Product category/), { target: { value: "Customer Support" } });
  fireEvent.change(screen.getByLabelText(/^Short description/), { target: { value: "Support tickets, sorted." } });
  fireEvent.change(screen.getByLabelText(/^Application URL/), { target: { value: "https://desk.example.com" } });
  fireEvent.change(screen.getByLabelText("Product logo"), { target: { files: [pngFile(256, 256)] } });
  expect(await screen.findByText("logo.png")).toBeInTheDocument();
}

function planCards(): HTMLElement[] {
  return within(screen.getByRole("list", { name: "Plans" })).getAllByRole("listitem");
}

describe("Product Publisher", () => {
  it("opens from Products > Add Product as one popup with the four sections, the action bar and no wizard steps; passes axe", async () => {
    mockApi({
      [`GET ${ADMIN}/products/console`]: [ok({ products: [], can_see_integrations: true, categories: BOOT.categories })],
      [`GET ${ADMIN}/products/publisher`]: [ok(BOOT)],
    });
    render(
      <AdminContextForTest permissions={MANAGE}>
        <ProductManager />
      </AdminContextForTest>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Add Product" }));
    const dialog = await screen.findByRole("dialog", { name: "Add Product" });
    expect(within(dialog).getByText("Create your product, configure pricing, and publish it to the Digital Store.")).toBeInTheDocument();
    for (const name of ["Basic information", "Product details & media", "Plans & pricing", "Advanced settings"]) {
      expect(within(dialog).getByRole("heading", { name: new RegExp(name.replace("&", "&")) })).toBeInTheDocument();
    }
    for (const action of ["Cancel", "Save Draft", "Preview", "Publish to Store"]) {
      expect(within(dialog).getByRole("button", { name: action })).toBeInTheDocument();
    }
    expect(within(dialog).queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    // Advanced settings are closed until asked for.
    expect(within(dialog).getByRole("heading", { name: "Advanced settings" }).closest("details")).not.toHaveAttribute("open");
    // One plan card to start with, named Starter.
    expect(planCards()).toHaveLength(1);
    expect(within(planCards()[0]).getByLabelText("Plan name")).toHaveValue("Starter");
    await expectNoViolations(dialog);
  });

  it("Publish checks the page first and asks the server nothing while fields are missing", async () => {
    const calls = mockApi({ [`GET ${ADMIN}/products/publisher`]: [ok(BOOT)] });
    renderPublisher();
    fireEvent.click(await screen.findByRole("button", { name: "Publish to Store" }));
    expect(await screen.findByText(/^To publish, fix: Product name, Short description, .* and \d+ more\.$/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Product name/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText(/^Short description/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Add the product logo.")).toBeInTheDocument();
    expect(screen.getByText("Enter the price.")).toBeInTheDocument();
    expect(calls.filter((c) => c.key.startsWith("POST") || c.key.startsWith("PUT"))).toHaveLength(0);
  });

  it("creates the product and several plans in one request, uploads the logo, then publishes", async () => {
    const created = state({}, [STARTER_ROW, { ...STARTER_ROW, id: "plan-2", code: "acme_desk_pro", name: "Pro", recommended: true, prices: { monthly: 99900, annual: 999000 } }]);
    const live = state({ manager_status: "active", status: "available", listing: "listed" }, created.plans);
    const calls = mockApi({
      [`GET ${ADMIN}/products/publisher`]: [ok(BOOT)],
      [`POST ${ADMIN}/products/publisher`]: [ok(created)],
      [`POST ${ADMIN}/products/p1/publisher/publish`]: [ok(live)],
    });
    const { onSaved } = renderPublisher();
    await fillBasics();
    const [starter] = planCards();
    fireEvent.change(within(starter).getByLabelText(/^Monthly price/), { target: { value: "499" } });
    fireEvent.change(within(starter).getByLabelText("Features / highlights"), { target: { value: "5 users\nEmail support" } });

    fireEvent.click(screen.getByRole("button", { name: /Add Another Plan/ }));
    const pro = planCards()[1];
    fireEvent.change(within(pro).getByLabelText("Plan name"), { target: { value: "Pro" } });
    fireEvent.click(within(pro).getByLabelText("Yearly"));
    fireEvent.change(within(pro).getByLabelText(/^Monthly price/), { target: { value: "999" } });
    fireEvent.change(within(pro).getByLabelText(/^Yearly price/), { target: { value: "9990" } });
    fireEvent.click(within(pro).getByLabelText("Recommended"));

    fireEvent.click(screen.getByRole("button", { name: "Publish to Store" }));
    expect(await screen.findByText(/Published\. The product and its plans are now in the Digital Store/)).toBeInTheDocument();

    const create = calls.find((c) => c.key === `POST ${ADMIN}/products/publisher`)!;
    const body = create.body as { product: Record<string, unknown>; plans: Record<string, unknown>[]; integration?: unknown };
    expect(body.product).toMatchObject({ name: "Acme Desk", slug: "acme-desk", category: "Customer Support", tagline: "Support tickets, sorted.", application_url: "https://desk.example.com", customer_store_enabled: true, reseller_enabled: false });
    expect(body.integration).toBeUndefined();
    expect(body.plans).toHaveLength(2);
    expect(body.plans[0]).toMatchObject({ id: null, name: "Starter", price_book_id: "b-inr", prices: { monthly: 49900 }, description: "- 5 users\n- Email support", recommended: false });
    expect(body.plans[1]).toMatchObject({ id: null, name: "Pro", prices: { monthly: 99900, annual: 999000 }, recommended: true });
    // The staged logo is uploaded against the new product, then it is published.
    expect(uploads).toEqual([`${ADMIN}/products/p1/media?kind=logo`]);
    const order = calls.map((c) => c.key);
    expect(order.indexOf(`POST ${ADMIN}/products/p1/publisher/publish`)).toBeGreaterThan(order.indexOf(`POST ${ADMIN}/products/publisher`));
    expect(onSaved).toHaveBeenCalledWith("p1", true);
    // Live now: the action bar says so.
    expect(screen.getByRole("button", { name: "Update Store" })).toBeInTheDocument();
  });

  it("Save Draft saves without publishing, and a second save updates the same product (no duplicate)", async () => {
    const saved = state({}, [STARTER_ROW]);
    const calls = mockApi({
      [`GET ${ADMIN}/products/publisher`]: [ok(BOOT)],
      [`POST ${ADMIN}/products/publisher`]: [ok(saved)],
      [`GET ${ADMIN}/products/p1/publisher`]: [ok(saved)],
      [`PUT ${ADMIN}/products/p1/publisher`]: [ok(saved)],
    });
    renderPublisher();
    fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: "Acme Desk" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    expect(await screen.findByText("Draft saved. It stays private until you publish it.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${ADMIN}/products/p1/publisher`)).toBe(true));
    expect(calls.filter((c) => c.key === `POST ${ADMIN}/products/publisher`)).toHaveLength(1);
    expect(calls.some((c) => c.key.endsWith("/publish"))).toBe(false);
    const put = calls.find((c) => c.key === `PUT ${ADMIN}/products/p1/publisher`)!;
    expect((put.body as { plans: { id: string | null }[] }).plans[0].id).toBe("plan-1");
  });

  it("a problem in a closed part says which field, opens that part and shows on the product name", async () => {
    const calls = mockApi({ [`GET ${ADMIN}/products/publisher`]: [ok(BOOT)] });
    renderPublisher();
    // "Taken Name" makes the store address taken-name, which another product holds.
    fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: "Taken Name" } });
    fireEvent.change(within(planCards()[0]).getByLabelText(/^Monthly price/), { target: { value: "499" } });
    fireEvent.change(within(planCards()[0]).getByLabelText("Seat limit"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    expect(await screen.findByText("To save, fix: Store address, Starter seat limit.")).toBeInTheDocument();
    // The address is the name's: the name says so, and Advanced settings opened.
    expect(screen.getByLabelText(/^Product name/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/Change the name, or the store address under Advanced settings\./)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Advanced settings" }).closest("details")).toHaveAttribute("open");
    expect(within(planCards()[0]).getByText("Advanced plan settings").closest("details")).toHaveAttribute("open");
    expect(calls.filter((c) => c.key.startsWith("POST"))).toHaveLength(0);
  });

  it("a server refusal lands on its field, inside the right plan card", async () => {
    mockApi({
      [`GET ${ADMIN}/products/publisher`]: [ok(BOOT)],
      [`POST ${ADMIN}/products/publisher`]: [{ status: 400, body: { code: "catalog_validation_error", detail: "That price is refused.", field: "plans.0.prices.monthly" } } as Reply],
    });
    renderPublisher();
    fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: "Acme Desk" } });
    fireEvent.change(within(planCards()[0]).getByLabelText(/^Monthly price/), { target: { value: "499" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    await waitFor(() => expect(within(planCards()[0]).getByLabelText(/^Monthly price/)).toHaveAttribute("aria-invalid", "true"));
    expect(screen.queryByText(/Draft saved/)).not.toBeInTheDocument();
  });

  it("edits a saved product: its plans are the Plans section's records, and only an unsaved plan can be removed", async () => {
    mockApi({ [`GET ${ADMIN}/products/p1/publisher`]: [ok(state({}, [STARTER_ROW]))] });
    renderPublisher({ productId: "p1" });
    expect(await screen.findByRole("dialog", { name: "Edit Acme Desk" })).toBeInTheDocument();
    expect(screen.getByLabelText(/^Product name/)).toHaveValue("Acme Desk");
    const [starter] = planCards();
    expect(within(starter).getByLabelText("Plan name")).toHaveValue("Starter");
    expect(within(starter).getByLabelText(/^Monthly price/)).toHaveValue("499.00");
    expect(within(starter).getByLabelText("Features / highlights")).toHaveValue("5 users\nEmail support");
    expect(within(starter).queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Add Another Plan/ }));
    expect(within(planCards()[1]).getByRole("button", { name: /^Remove/ })).toBeInTheDocument();
  });

  it("Preview saves a private draft and shows the real Store page with nothing to buy", async () => {
    const saved = state({}, [STARTER_ROW]);
    const page = {
      preview: true,
      currency: "INR",
      currencies: ["INR"],
      tax: { label: "GST", rate_bp: 1800, description: "GST (18%)" },
      product: {
        code: "acme_desk",
        slug: "acme-desk",
        name: "Acme Desk",
        tagline: "Support tickets, sorted.",
        availability: "available",
        trial: { available: false, duration_days: null, seat_limit: null, payment_method_required: false, plan_codes: [] },
        category: "Customer Support",
        icon: "",
        title: "Acme Desk",
        summary: "Support tickets, sorted.",
        description: "## Why teams pick it\n- Fast",
        highlights: ["Ticket management"],
        benefits: [],
        use_cases: [],
        setup_requirements: [],
        access: { web: true, apps: {} },
        video: null,
        documentation_url: "",
        support_url: "",
        status: "available",
        logo: null,
        banner: null,
        screenshots: [],
        price: { amount_minor: 49900, currency: "INR", interval: "monthly" },
        yearly_deal: null,
        held_plan_code: null,
        renews_at: null,
      },
      plans: [
        {
          plan_version_id: "v1",
          plan_code: "acme_desk_starter",
          name: "Starter",
          caption: "For small teams",
          description: "- 5 users",
          billing_type: "paid",
          kind: "standard",
          addon_for: null,
          included_seats: 5,
          max_seats: null,
          trial_days: 0,
          recommended: false,
          sort_order: 10,
          renewal_rule: "auto_renew",
          included_usage: {},
          trial_available: false,
          prices: [{ interval: "monthly", currency: "INR", model: "flat", amount_minor: 49900, list_amount_minor: 49900, discount_bp: 0, setup_fee_minor: 0, tax_behaviour: "exclusive", from_tiers: false }],
        },
      ],
      comparison: [{ key: "seats", label: "Users included", kind: "seats", values: { v1: { included: 5, max: null } } }],
      related: [],
    };
    mockApi({
      [`GET ${ADMIN}/products/publisher`]: [ok(BOOT)],
      [`POST ${ADMIN}/products/publisher`]: [ok(saved)],
      [`GET ${ADMIN}/products/p1/publisher`]: [ok(saved)],
      [`GET ${ADMIN}/products/p1/store-preview`]: [ok(page)],
    });
    renderPublisher();
    fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: "Acme Desk" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    const preview = await screen.findByRole("dialog", { name: "Store preview" });
    expect(within(preview).getByRole("heading", { level: 1, name: "Acme Desk" })).toBeInTheDocument();
    expect(within(preview).queryByRole("link", { name: /Buy Now/ })).not.toBeInTheDocument();
    fireEvent.click(within(preview).getByRole("button", { name: "Back to editing" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Store preview" })).not.toBeInTheDocument());
  });

  it("Cancel with unsaved changes asks first; Keep editing keeps them", async () => {
    mockApi({ [`GET ${ADMIN}/products/publisher`]: [ok(BOOT)] });
    const { onClose } = renderPublisher();
    fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: "Acme Desk" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    const ask = await screen.findByRole("dialog", { name: "Leave without saving?" });
    fireEvent.click(within(ask).getByRole("button", { name: "Keep editing" }));
    expect(screen.getByLabelText(/^Product name/)).toHaveValue("Acme Desk");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Leave without saving?" })).getByRole("button", { name: "Discard changes" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});

describe("Product Publisher rules", () => {
  const books = [BOOK];
  const filled = (patch: Partial<PublisherDraft> = {}): PublisherDraft => {
    const d = blankDraft(BOOK.id);
    return {
      ...d,
      name: "Acme Desk",
      category: "Customer Support",
      tagline: "Support tickets, sorted.",
      applicationUrl: "https://desk.example.com",
      logo: { ...d.logo, mediaId: "logo1" },
      plans: [{ ...d.plans[0], prices: { ...d.plans[0].prices, monthly: "499" } }],
      ...patch,
    };
  };

  it("checks what publishing needs, and a slug another product uses", () => {
    expect(check(filled(), books, { publish: true, takenSlugs: new Set() })).toEqual({});
    expect(check(filled({ name: "Taken Name" }), books, { publish: false, takenSlugs: new Set(["taken-name"]) }).slug).toMatch(/Another product/);
    const two = filled();
    two.plans = [{ ...two.plans[0], recommended: true }, { ...two.plans[0], key: "x", recommended: true }];
    expect(check(two, books, { publish: false, takenSlugs: new Set() }).plans).toMatch(/Only one plan/);
    const zero = filled();
    zero.plans = [{ ...zero.plans[0], prices: { ...zero.plans[0].prices, monthly: "0" } }];
    expect(check(zero, books, { publish: false, takenSlugs: new Set() })["plans.0.prices.monthly"]).toMatch(/mark it Free/);
  });

  it("money becomes minor units by text, a free plan is one zero price, and highlights a bullet list", () => {
    const free = filled();
    free.plans = [{ ...free.plans[0], free: true }];
    const body = toRequest(free, books) as { plans: { prices: Record<string, number>; free: boolean }[] };
    expect(body.plans[0]).toMatchObject({ free: true, prices: { monthly: 0 } });
    const paid = toRequest(filled(), books) as { plans: { prices: Record<string, number> }[] };
    expect(paid.plans[0].prices).toEqual({ monthly: 49900 });
    expect(descriptionFromHighlights(" 5 users \n\nAutomation")).toBe("- 5 users\n- Automation");
    expect(highlightsFromDescription("- 5 users\n- Automation")).toBe("5 users\nAutomation");
  });

  it("maps a server field to the page's", () => {
    expect(fieldKey("plans.2.price_book_id")).toBe("plans.2.bookId");
    expect(fieldKey("plans.1.prices.annual")).toBe("plans.1.prices.annual");
    expect(fieldKey("video_url")).toBe("videoUrl");
    expect(fieldKey("plans")).toBe("plans");
  });
});
