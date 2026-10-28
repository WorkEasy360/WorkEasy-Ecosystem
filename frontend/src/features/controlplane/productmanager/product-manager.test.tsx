import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ProductManager } from "./ProductManager";
import { AddProductWizard, toRequest, validateStep, EMPTY_DRAFT } from "./AddProductWizard";
import { ProductDetail } from "./ProductDetail";
import { imageSize } from "./model";
import { AdminContextForTest, expectNoViolations, mockApi, ok } from "../testSupport";

/**
 * The Super Admin Product Manager (ADR-051): one central catalog. The list,
 * the four-step Add Product wizard and a product's detail page send exactly
 * the requests they should, refuse what they must before a round trip (the
 * server refuses it again), and show what the server returned - never a
 * computed access decision.
 */

let query = "";
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/admin/products",
}));

const ADMIN = "/api/v1/admin";
const MANAGE = ["platform.staff_catalog.manage", "platform.staff_integration.manage", "platform.staff_audit.view"];
const VIEW = ["platform.staff_catalog.view"];

function product(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    code: "easy_sample",
    name: "Easy Sample",
    slug: "easy-sample",
    tagline: "Sample things simply.",
    description: "A **sample** product.",
    category: "Sales",
    icon: "chart",
    status: "available",
    manager_status: "active",
    listing: "listed",
    availability: "available",
    licensing_mode: "per_seat",
    sort_order: 10,
    marketing_url: "https://www.workeasy360.com/sample",
    store_title: "",
    store_summary: "Sampling for teams.",
    application_url: "https://sample.workeasy360.com",
    documentation_url: "",
    support_url: "",
    customer_store_enabled: true,
    reseller_enabled: true,
    affiliate_enabled: true,
    white_label_enabled: true,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-20T10:00:00Z",
    activated_at: "2026-10-02T10:00:00Z",
    logo_media_id: "m-logo",
    media: [{ id: "m-logo", kind: "logo", width: 256, height: 256, content_type: "image/png", size_bytes: 900, caption: "", sort_order: 0 }],
    launch_url: "https://sample.workeasy360.com/",
    plans: [{ code: "sample_team", name: "Sample Team", status: "available", is_public: true, kind: "standard", billing_type: "paid", priced: true }],
    channels: {
      direct: { listed: true, available: true, public_plans: 1, priced_plans: 1, ready: true },
      reseller: { partners: 0 },
      affiliate: { product_rules: 0, programme_default: false },
      trial: { enabled: true, duration_days: 14, seat_limit: null, payment_method_required: false, eligible_plan_codes: ["sample_team"] },
    },
    integration: null,
    ...overrides,
  };
}

const DRAFT = product({
  id: "p2",
  code: "draft_tool",
  name: "Draft Tool",
  slug: "draft-tool",
  category: "Finance",
  status: "draft",
  manager_status: "draft",
  listing: "hidden",
  availability: "private",
  logo_media_id: null,
  media: [],
  launch_url: "",
  activated_at: null,
  plans: [],
  channels: { ...product().channels, trial: null, direct: { listed: false, available: false, public_plans: 0, priced_plans: 0, ready: false } },
});

const CATALOG = { products: [product(), DRAFT], can_see_integrations: true, categories: ["Sales", "Finance", "Other"] };

/** A real 2-pixel-wide PNG header of the size asked (enough for the size check). */
function pngFile(width: number, height: number, name = "logo.png", type = "image/png"): File {
  const bytes = new Uint8Array(33);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return new File([bytes], name, { type });
}

/** Uploads go through XMLHttpRequest (for progress): a fake that records them. */
const uploads: { url: string; type: string | null; progress: boolean }[] = [];
class FakeXHR {
  url = "";
  status = 0;
  responseText = "";
  headers: Record<string, string> = {};
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  open(_m: string, url: string) {
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k.toLowerCase()] = v;
  }
  getResponseHeader() {
    return "application/json";
  }
  send() {
    setTimeout(() => {
      this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
      uploads.push({ url: this.url, type: this.headers["content-type"] ?? null, progress: true });
      this.status = 201;
      this.responseText = JSON.stringify({ id: `m${uploads.length}`, kind: "logo", width: 256, height: 256, content_type: "image/png", size_bytes: 33, caption: "", sort_order: 0 });
      this.onload?.();
    }, 0);
  }
}

beforeEach(() => {
  query = "";
  push.mockReset();
  uploads.length = 0;
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
});
afterEach(() => vi.unstubAllGlobals());

function renderAs(permissions: string[], ui: React.ReactNode) {
  return render(<AdminContextForTest permissions={permissions}>{ui}</AdminContextForTest>);
}

// --- the list ---------------------------------------------------------------------------

describe("Product Manager list", () => {
  it("shows every product with its logo, status, visibility, trial, plans and launch URL, and passes axe", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [ok(CATALOG)] });
    const { container } = renderAs(MANAGE, <ProductManager />);
    expect(await screen.findByRole("heading", { level: 1, name: "Product Manager" })).toBeInTheDocument();
    expect(screen.getByText("Manage Workeasy360 products and their store availability.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "+ Add Product" })).toHaveAttribute("href", "/admin/products/new");
    const row = screen.getByRole("link", { name: "Easy Sample" }).closest("tr") as HTMLElement;
    expect(within(row).getByText("easy-sample")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(within(row).getByText("Published")).toBeInTheDocument();
    expect(within(row).getByText("14 days")).toBeInTheDocument();
    expect(within(row).getByText("https://sample.workeasy360.com/")).toBeInTheDocument();
    expect(row.querySelector("img")?.getAttribute("src")).toBe(`${ADMIN}/products/p1/media/m-logo`);
    const draft = screen.getByRole("link", { name: "Draft Tool" }).closest("tr") as HTMLElement;
    expect(within(draft).getByText("Draft")).toBeInTheDocument();
    expect(within(draft).getByText("Hidden")).toBeInTheDocument();
    expect(within(draft).getByText("Not set")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("searches and filters by status, visibility and category", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [ok(CATALOG)] });
    renderAs(MANAGE, <ProductManager />);
    await screen.findByRole("link", { name: "Easy Sample" });
    fireEvent.change(screen.getByLabelText("Search products"), { target: { value: "draft-t" } });
    expect(screen.queryByRole("link", { name: "Easy Sample" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Draft Tool" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search products"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "active" } });
    expect(screen.queryByRole("link", { name: "Draft Tool" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Store visibility"), { target: { value: "hidden" } });
    expect(screen.queryByRole("link", { name: "Easy Sample" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Store visibility"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "Other" } });
    expect(screen.getByText("No product matches these filters.")).toBeInTheDocument();
  });

  it("publishes, unpublishes and archives through the server, and previews a listing", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [ok(CATALOG)],
      [`POST ${ADMIN}/products/p2/publish`]: [ok({})],
      [`POST ${ADMIN}/products/p1/unpublish`]: [ok({})],
      [`POST ${ADMIN}/products/p2/archive`]: [ok({})],
    });
    renderAs(MANAGE, <ProductManager />);
    await screen.findByRole("link", { name: "Draft Tool" });
    const more = (name: string) => screen.getByLabelText(`More actions for ${name}`).closest("details") as HTMLElement;
    fireEvent.click(within(more("Draft Tool")).getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ADMIN}/products/p2/publish`)).toBe(true));
    fireEvent.click(within(more("Easy Sample")).getByRole("button", { name: "Unpublish" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ADMIN}/products/p1/unpublish`)).toBe(true));
    fireEvent.click(within(more("Draft Tool")).getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ADMIN}/products/p2/archive`)).toBe(true));

    fireEvent.click(within(more("Easy Sample")).getByRole("button", { name: "Preview listing" }));
    const dialog = screen.getByRole("dialog", { name: "Store listing preview" });
    expect(within(dialog).getAllByText("Easy Sample").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("Sampling for teams.")).toBeInTheDocument();
    expect(within(dialog).getByText("14-day free trial")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /buy|checkout|subscribe/i })).not.toBeInTheDocument();
  }, 20_000);

  it("a viewer sees the catalog but no way to add, edit, publish or archive", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [ok(CATALOG)] });
    renderAs(VIEW, <ProductManager />);
    await screen.findByRole("link", { name: "Easy Sample" });
    expect(screen.queryByRole("link", { name: "+ Add Product" })).not.toBeInTheDocument();
    const menu = screen.getByLabelText("More actions for Draft Tool").closest("details") as HTMLElement;
    expect(within(menu).queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("button", { name: "Archive" })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("link", { name: "Edit" })).not.toBeInTheDocument();
  });
});

// --- the wizard -------------------------------------------------------------------------

async function fillIdentity(name = "New Ledger") {
  fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: name } });
  fireEvent.change(screen.getByLabelText(/^Category/), { target: { value: "Finance" } });
  fireEvent.change(screen.getByLabelText(/^Short description/), { target: { value: "Books that balance." } });
  fireEvent.change(screen.getByLabelText("Product logo"), { target: { files: [pngFile(256, 256)] } });
  expect(await screen.findByText("logo.png")).toBeInTheDocument();
}

describe("Add Product wizard", () => {
  it("has four steps and checks step 1 before moving on", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [ok(CATALOG)] });
    const { container } = renderAs(MANAGE, <AddProductWizard />);
    const steps = await screen.findByRole("list", { name: "Steps" });
    expect(within(steps).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "1Step 1: Identity",
      "2Step 2: Store Listing",
      "3Step 3: Integration",
      "4Step 4: Availability",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    const alert = (await screen.findByText(/^Fix these/)).closest("[role=alert]") as HTMLElement;
    expect(alert).toHaveTextContent("Enter the product name.");
    expect(alert).toHaveTextContent("Choose a category.");
    expect(alert).toHaveTextContent("Add the product logo.");
    await expectNoViolations(container);
  });

  it("suggests the slug from the name, shows the permanent code and refuses a slug in use", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [ok(CATALOG)] });
    renderAs(MANAGE, <AddProductWizard />);
    fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: "Draft Tool" } });
    expect(screen.getByLabelText(/^Product slug/)).toHaveValue("draft-tool");
    expect(screen.getByText("draft_tool")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("This slug is already used by another product.", { selector: "li" })).toBeInTheDocument();
  });

  it("refuses an SVG or a too-small logo before uploading anything", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [ok(CATALOG)] });
    renderAs(MANAGE, <AddProductWizard />);
    const input = await screen.findByLabelText("Product logo");
    fireEvent.change(input, { target: { files: [new File(["<svg/>"], "x.svg", { type: "image/svg+xml" })] } });
    expect(await screen.findByText(/SVG and other formats are not accepted/)).toBeInTheDocument();
    fireEvent.change(input, { target: { files: [pngFile(16, 16, "tiny.png")] } });
    expect(await screen.findByText(/16×16 px; it must be from 64×64/)).toBeInTheDocument();
    expect(uploads).toEqual([]);
  });

  it("creates, uploads with progress and publishes - sending exactly the fields chosen", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [ok(CATALOG)],
      [`POST ${ADMIN}/products`]: [{ status: 201, body: { product: { ...DRAFT, id: "new1" } } }],
      [`POST ${ADMIN}/products/new1/publish`]: [ok({})],
    });
    renderAs(MANAGE, <AddProductWizard />);
    await fillIdentity();
    fireEvent.click(screen.getByLabelText("Icon: wallet"));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByRole("heading", { name: "2. Store Listing" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Short store description"), { target: { value: "Ledgers for small teams." } });
    fireEvent.change(screen.getByLabelText("Add screenshots"), { target: { files: [pngFile(1280, 720, "board.png")] } });
    fireEvent.change(await screen.findByLabelText("Caption for screenshot 1 (optional)"), { target: { value: "The board" } });
    fireEvent.change(screen.getByLabelText("Support URL"), { target: { value: "https://support.workeasy360.com/ledger" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByRole("heading", { name: "3. Integration" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Launch URL/), { target: { value: "javascript:alert(1)" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("The launch URL must be an https:// address.", { selector: "li" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Application URL/), { target: { value: "https://ledger.workeasy360.com" } });
    fireEvent.change(screen.getByLabelText(/^Launch URL/), { target: { value: "https://ledger.workeasy360.com/" } });
    fireEvent.change(screen.getByLabelText("Sign-in callback URL"), { target: { value: "https://ledger.workeasy360.com/api/auth/callback" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByRole("heading", { name: "4. Availability" })).toBeInTheDocument();
    expect(screen.getByText("Plans/Pricing required before final Store sale activation.")).toBeInTheDocument();
    const affiliate = screen.getByRole("group", { name: "Affiliate" });
    fireEvent.click(within(affiliate).getByLabelText("Not allowed"));
    fireEvent.click(within(screen.getByRole("group", { name: "Free trial" })).getByLabelText("Enabled"));
    fireEvent.change(screen.getByLabelText("Trial duration (days)"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish Product" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/products/new1?saved=published"));
    const create = calls.find((c) => c.key === `POST ${ADMIN}/products`)!;
    expect(create.body).toEqual({
      name: "New Ledger",
      slug: "new-ledger",
      category: "Finance",
      tagline: "Books that balance.",
      icon: "wallet",
      store_title: "",
      store_summary: "Ledgers for small teams.",
      description: "",
      marketing_url: "",
      documentation_url: "",
      support_url: "https://support.workeasy360.com/ledger",
      application_url: "https://ledger.workeasy360.com",
      customer_store_enabled: true,
      reseller_enabled: true,
      affiliate_enabled: false,
      white_label_enabled: true,
      integration: {
        launch_url: "https://ledger.workeasy360.com/",
        sso_enabled: true,
        redirect_uri: "https://ledger.workeasy360.com/api/auth/callback",
      },
      trial: { enabled: true, duration_days: 7 },
    });
    expect(JSON.stringify(create.body)).not.toMatch(/secret|code"/);
    expect(uploads.map((u) => u.url)).toEqual([
      `${ADMIN}/products/new1/media?kind=logo`,
      `${ADMIN}/products/new1/media?kind=screenshot&caption=The+board`,
    ]);
    expect(uploads.every((u) => u.type === "image/png" && u.progress)).toBe(true);
    expect(calls.some((c) => c.key === `POST ${ADMIN}/products/new1/publish`)).toBe(true);
  });

  it("Save Draft needs only a name and a slug, and never publishes", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [ok(CATALOG)],
      [`POST ${ADMIN}/products`]: [{ status: 201, body: { product: { ...DRAFT, id: "new2" } } }],
    });
    renderAs(MANAGE, <AddProductWizard />);
    fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: "Scratch Pad" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/products/new2?saved=draft"));
    expect(calls.find((c) => c.key === `POST ${ADMIN}/products`)!.body).toMatchObject({ name: "Scratch Pad", slug: "scratch-pad", integration: null, trial: null });
    expect(calls.some((c) => c.key.endsWith("/publish"))).toBe(false);
  });

  it("a server refusal is shown and nothing claims success", async () => {
    mockApi({
      [`GET ${ADMIN}/products/console`]: [ok(CATALOG)],
      [`POST ${ADMIN}/products`]: [{ status: 400, body: { code: "catalog_validation", detail: "support_url: the address must be publicly routable" } }],
    });
    renderAs(MANAGE, <AddProductWizard />);
    fireEvent.change(await screen.findByLabelText(/^Product name/), { target: { value: "Scratch Pad" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    expect(await screen.findByText(/publicly routable/, { selector: "p" })).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("is not offered without the catalog management permission", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [ok(CATALOG)] });
    renderAs(VIEW, <AddProductWizard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("needs the catalog management permission");
  });

  it("the step rules and the request are pure functions of the fields", () => {
    const taken = { takenSlugs: new Set(["easy-sample"]), takenCodes: new Set(["easy_sample"]) };
    expect(validateStep(0, { ...EMPTY_DRAFT, name: "A", slug: "a" }, { strict: false, hasLogo: false, ...taken })).toEqual({});
    expect(Object.keys(validateStep(0, { ...EMPTY_DRAFT, name: "A", slug: "a" }, { strict: true, hasLogo: false, ...taken }))).toEqual(["category", "tagline", "logo"]);
    expect(validateStep(3, { ...EMPTY_DRAFT, trial: "enabled", trialDays: "0" }, { strict: true, hasLogo: true, ...taken }).trialDays).toBeTruthy();
    expect(toRequest({ ...EMPTY_DRAFT, name: " X ", slug: "x", launchUrl: "https://x.workeasy360.com/", sso: "no" }).integration).toEqual({
      launch_url: "https://x.workeasy360.com/",
      sso_enabled: false,
      redirect_uri: undefined,
      health_url: undefined,
    });
  });

  it("reads an image's size from its bytes, as the server does", async () => {
    expect(await imageSize(pngFile(640, 400))).toEqual({ width: 640, height: 400 });
    expect(await imageSize(new File(["GIF89a"], "x.gif"))).toBeNull();
  });
});

// --- the detail page ----------------------------------------------------------------------

function managed(p = product(), extra: Record<string, unknown> = {}) {
  return {
    product: {
      ...p,
      readiness: [
        { key: "name", label: "Product name", ok: true },
        { key: "logo", label: "Logo", ok: !!p.logo_media_id },
        { key: "launch_url", label: "Launch URL", ok: !!p.launch_url },
      ],
    },
    categories: ["Sales", "Finance"],
    can_see_integrations: true,
    can_see_activity: true,
    ...extra,
  };
}

describe("Product detail", () => {
  it("shows the header, the seven tabs and the publish checklist, and passes axe", async () => {
    mockApi({ [`GET ${ADMIN}/products/p2`]: [ok(managed(DRAFT))] });
    const { container } = renderAs(MANAGE, <ProductDetail productId="p2" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Draft Tool" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Overview",
      "Store Listing",
      "Integration",
      "Availability",
      "Plans",
      "Pricing",
      "Activity",
    ]);
    expect(screen.getByText("Logo").closest("li")).toHaveTextContent("missing");
    expect(screen.getByText("Plans/Pricing required before final Store sale activation.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("Plans and Pricing open the shared modules filtered to this product", async () => {
    mockApi({ [`GET ${ADMIN}/products/p1`]: [ok(managed())] });
    renderAs(MANAGE, <ProductDetail productId="p1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Plans" }));
    expect(screen.getByRole("link", { name: "Manage Plans →" })).toHaveAttribute("href", "/admin/plans?product=easy_sample");
    expect(screen.getByText("Sample Team")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Pricing" }));
    expect(screen.getByRole("link", { name: "Manage Pricing →" })).toHaveAttribute("href", "/admin/pricing?product=easy_sample");
  });

  it("availability saves only the flags that changed, and archive is confirmed", async () => {
    query = "tab=availability";
    const calls = mockApi({
      [`GET ${ADMIN}/products/p1`]: [ok(managed())],
      [`PATCH ${ADMIN}/catalog/products/p1`]: [ok({})],
      [`POST ${ADMIN}/products/p1/archive`]: [ok(managed())],
    });
    renderAs(MANAGE, <ProductDetail productId="p1" />);
    const group = await screen.findByRole("group", { name: "White-label" });
    fireEvent.click(within(group).getByLabelText("Not allowed"));
    fireEvent.click(screen.getByRole("button", { name: "Save availability" }));
    await waitFor(() => expect(calls.find((c) => c.key === `PATCH ${ADMIN}/catalog/products/p1`)?.body).toEqual({ white_label_enabled: false }));
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm archive" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ADMIN}/products/p1/archive`)).toBe(true));
  });

  it("a trial may be switched on before the product has a plan", async () => {
    query = "tab=availability";
    const calls = mockApi({
      [`GET ${ADMIN}/products/p2`]: [ok(managed(DRAFT))],
      [`PUT ${ADMIN}/catalog/products/p2/trial`]: [ok({})],
    });
    renderAs(MANAGE, <ProductDetail productId="p2" />);
    const group = await screen.findByRole("group", { name: "Free trial" });
    fireEvent.click(within(group).getByLabelText("Enabled"));
    fireEvent.change(screen.getByLabelText("Trial duration (days)"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Save trial configuration" }));
    await waitFor(() =>
      expect(calls.find((c) => c.key === `PUT ${ADMIN}/catalog/products/p2/trial`)?.body).toEqual({
        enabled: true,
        duration_days: 7,
        seat_limit: null,
        payment_method_required: false,
        eligible_plan_codes: [],
      }),
    );
  });

  it("the store listing saves only what changed, and the slug is fixed once on sale", async () => {
    query = "tab=listing";
    const calls = mockApi({
      [`GET ${ADMIN}/products/p1`]: [ok(managed())],
      [`PATCH ${ADMIN}/catalog/products/p1`]: [ok({})],
    });
    renderAs(MANAGE, <ProductDetail productId="p1" />);
    expect(await screen.findByLabelText(/^Product slug/)).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Store title"), { target: { value: "Easy Sample Pro" } });
    fireEvent.click(screen.getByRole("button", { name: "Save listing" }));
    await waitFor(() => expect(calls.find((c) => c.key === `PATCH ${ADMIN}/catalog/products/p1`)?.body).toEqual({ store_title: "Easy Sample Pro" }));
  });

  it("uploads a new logo immediately, with progress, to this product only", async () => {
    query = "tab=listing";
    mockApi({ [`GET ${ADMIN}/products/p2`]: [ok(managed(DRAFT))] });
    renderAs(MANAGE, <ProductDetail productId="p2" />);
    fireEvent.change(await screen.findByLabelText("Product logo"), { target: { files: [pngFile(200, 200)] } });
    await waitFor(() => expect(uploads.map((u) => u.url)).toEqual([`${ADMIN}/products/p2/media?kind=logo`]));
  });

  it("the activity tab lists who did what", async () => {
    query = "tab=activity";
    mockApi({
      [`GET ${ADMIN}/products/p1`]: [ok(managed())],
      [`GET ${ADMIN}/products/p1/activity`]: [
        ok({
          results: [
            { id: "a1", occurred_at: "2026-10-27T10:00:00Z", action: "commercial.product.published", label: "Published", summary: "listing: listed, status: available", actor: "admin@workeasy360.com" },
            { id: "a2", occurred_at: "2026-10-26T10:00:00Z", action: "commercial.product.created", label: "Created", summary: "", actor: "admin@workeasy360.com" },
          ],
          next_cursor: null,
          has_more: false,
        }),
      ],
    });
    renderAs(MANAGE, <ProductDetail productId="p1" />);
    expect(await screen.findByText("Created")).toBeInTheDocument();
    expect(screen.getByText("Published", { selector: "span[class*=activityWhat]" })).toBeInTheDocument();
    expect(screen.getAllByText(/admin@workeasy360\.com/)).toHaveLength(2);
  });

  it("without the audit grant there is no Activity tab; a viewer cannot edit", async () => {
    mockApi({ [`GET ${ADMIN}/products/p1`]: [ok(managed(product(), { can_see_activity: false }))] });
    renderAs(VIEW, <ProductDetail productId="p1" />);
    await screen.findByRole("heading", { level: 1, name: "Easy Sample" });
    expect(screen.queryByRole("tab", { name: "Activity" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Unpublish" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Store Listing" }));
    expect(screen.getByText(/Editing the listing needs/)).toBeInTheDocument();
  });
});
