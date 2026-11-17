import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import type { PublicStoreProductPage } from "@/lib/api/public";
import { expectNoViolations, mockApi, ok, type Call } from "@/features/controlplane/testSupport";
import { StoreProductPage } from "./ProductDetails";
import { storePaths } from "./links";

/**
 * The product page's redesign (2026-11-16): breadcrumb, hero, the gallery and its
 * full-screen view, the demo, features and screenshots sections, plan
 * highlights, only the configured ways to reach the product, and the Super
 * Admin's preview - which draws the same page with nothing to buy.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/store/product/easy-crm",
}));

const PRO = "11111111-1111-1111-1111-111111111111";
const FREE = "22222222-2222-2222-2222-222222222222";

const plan = (id: string, code: string, name: string, amount: number, extra: Record<string, unknown> = {}) => ({
  plan_version_id: id,
  plan_code: code,
  name,
  caption: "",
  description: "",
  billing_type: "paid" as const,
  kind: "standard" as const,
  addon_for: null,
  included_seats: 1,
  max_seats: 50,
  trial_days: 0,
  trial_available: false,
  recommended: code === "crm_pro",
  renewal_rule: "auto_renew" as const,
  included_usage: {},
  prices: [{ interval: "annual", currency: "INR", model: amount ? "per_seat" : "flat", amount_minor: amount, from_tiers: false }],
  ...extra,
});

const img = (name: string, caption: string) => ({ url: `/api/v1/public/products/easy-crm/media/${name}`, caption, width: 1600, height: 900 });

function page(over: { product?: Record<string, unknown>; [key: string]: unknown } = {}): PublicStoreProductPage {
  const { product, ...rest } = over;
  return {
    currency: "INR",
    currencies: ["INR"],
    tax: { label: "GST", rate_bp: 1800, description: "GST (18%)" },
    product: {
      code: "easy_crm",
      slug: "easy-crm",
      name: "Easy CRM",
      title: "WorkEasy CRM",
      tagline: "Sales & Lead Management",
      summary: "Manage leads, deals and customer relationships.",
      description: "## Why teams pick it\n\nEvery lead in **one place**.\n\n1. Capture\n2. Qualify",
      availability: "available",
      status: "available",
      category: "Sales",
      subcategory: "",
      icon: "",
      highlights: ["Lead Management", "Sales Pipeline", "Follow-ups & Tasks", "Reports", "Mobile access"],
      benefits: [],
      use_cases: [],
      setup_requirements: [],
      access: { web: false, apps: { ios: "https://apps.apple.com/app/id1", windows: "https://example.com/windows" } },
      tags: [],
      badges: [],
      documentation_url: "",
      support_url: "",
      logo: null,
      banner: img("banner", "Dashboard"),
      screenshots: [img("s1", "Pipeline board"), img("s2", "Lead list")],
      video: {
        provider: "youtube",
        id: "abc",
        embed_url: "https://www.youtube-nocookie.com/embed/abc",
        watch_url: "https://www.youtube.com/watch?v=abc",
        title: "See it in two minutes",
        description: "A short tour of the pipeline.",
        poster: "/api/v1/public/products/easy-crm/media/poster",
      },
      price: { amount_minor: 2400000, currency: "INR", interval: "annual", plan_name: "Advanced" },
      yearly_deal: null,
      trial: { available: true, duration_days: 7, seat_limit: null, payment_method_required: false, plan_codes: ["crm_pro"] },
      refund_days: null,
      ...product,
    },
    plans: [plan(PRO, "crm_pro", "Advanced", 2400000, { description: "- Unlimited pipelines\n- Priority support" }), plan(FREE, "crm_free", "Free", 0)],
    comparison: [],
    related: [{ code: "easy_helpdesk", slug: "easy-helpdesk", name: "Easy Help Desk", title: "WorkEasy Help Desk", summary: "Tickets.", icon: "", category: "Support", price: null }],
    ...rest,
  } as PublicStoreProductPage;
}

let calls: Call[] = [];
beforeEach(() => {
  calls = mockApi({ "GET /api/v1/public/store/recharge": [ok({ currency: "INR", rates: [], credit_packs: [], payment: { available: false, provider: null, label: null, methods: [] } })] });
});
afterEach(() => {
  vi.restoreAllMocks();
  document.body.style.overflow = "";
});

const stageButton = () => within(screen.getByRole("group", { name: "WorkEasy CRM pictures" })).getByRole("button", { name: /^View full screen/ });

describe("the product page", () => {
  it("has the breadcrumb Store > Products > the product, and the sections it holds", () => {
    render(<StoreProductPage data={page()} />);
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Store", "Products", "WorkEasy CRM"]);
    expect(within(crumbs).getByRole("link", { name: "Store" })).toHaveAttribute("href", storePaths.home);
    expect(within(crumbs).getByRole("link", { name: "Products" })).toHaveAttribute("href", storePaths.products);
    const tabs = screen.getByRole("navigation", { name: "On this page" });
    expect(within(tabs).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Overview", "#overview"],
      ["Features", "#features"],
      ["Screenshots", "#screenshots"],
      ["Demo", "#demo"],
      ["Plans & Pricing", "#plans"],
      ["App Availability", "#apps"],
    ]);
    // The hero: the category chip, and four of the highlights; Features: all of them.
    expect(within(screen.getByRole("list", { name: "About WorkEasy CRM" })).getByText("Sales")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Key highlights" })).getAllByRole("listitem")).toHaveLength(4);
    const features = screen.getByRole("region", { name: "Features" });
    expect(within(features).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Lead Management", "Sales Pipeline", "Follow-ups & Tasks", "Reports", "Mobile access"]);
    // The description's own headings sit under the Overview section's h2.
    expect(within(screen.getByRole("region", { name: "Overview" })).getByRole("heading", { level: 3, name: "Why teams pick it" })).toBeInTheDocument();
    expect(document.title).toBe("WorkEasy CRM - WorkEasy360 Store");
  });

  it("the gallery steps through the pictures with Previous / Next, the arrow keys and a swipe", () => {
    render(<StoreProductPage data={page()} />);
    const gallery = screen.getByRole("group", { name: "WorkEasy CRM pictures" });
    expect(stageButton()).toHaveAccessibleName("View full screen: Dashboard");
    fireEvent.click(within(gallery).getByRole("button", { name: "Next picture" }));
    expect(stageButton()).toHaveAccessibleName("View full screen: Pipeline board");
    expect(within(gallery).getByRole("button", { name: "Show picture 2: Pipeline board" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(stageButton(), { key: "ArrowRight" });
    expect(stageButton()).toHaveAccessibleName("View full screen: Lead list");
    fireEvent.keyDown(stageButton(), { key: "ArrowRight" }); // round to the first
    expect(stageButton()).toHaveAccessibleName("View full screen: Dashboard");
    fireEvent.keyDown(stageButton(), { key: "ArrowLeft" });
    expect(stageButton()).toHaveAccessibleName("View full screen: Lead list");
    fireEvent.click(within(gallery).getByRole("button", { name: "Previous picture" }));
    expect(stageButton()).toHaveAccessibleName("View full screen: Pipeline board");
    // A swipe to the left shows the next picture, and the click that ends it opens nothing.
    const stage = stageButton().parentElement!;
    fireEvent.pointerDown(stage, { clientX: 300, clientY: 100, pointerType: "touch", pointerId: 1 });
    fireEvent.pointerUp(stage, { clientX: 180, clientY: 110, pointerType: "touch", pointerId: 1 });
    fireEvent.click(stageButton());
    expect(stageButton()).toHaveAccessibleName("View full screen: Lead list");
    expect(screen.queryByRole("dialog")).toBeNull();
    // The thumbnails choose too.
    fireEvent.click(within(gallery).getByRole("button", { name: "Show picture 1: Dashboard" }));
    expect(stageButton()).toHaveAccessibleName("View full screen: Dashboard");
  });

  it("the picture opens full screen: the counter, Previous / Next, Escape closes and focus comes back", async () => {
    render(<StoreProductPage data={page()} />);
    const opener = stageButton();
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "WorkEasy CRM - pictures" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByText("1 / 3")).toBeInTheDocument();
    expect(within(dialog).getByRole("img", { name: "Dashboard" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.click(within(dialog).getByRole("button", { name: "Next picture" }));
    expect(within(dialog).getByText("2 / 3")).toBeInTheDocument();
    expect(within(dialog).getByText("Pipeline board", { selector: "figcaption" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(within(dialog).getByText("3 / 3")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(within(dialog).getByText("1 / 3")).toBeInTheDocument();
    await expectNoViolations(document.body);
    // Tab stays inside the dialog.
    const close = within(dialog).getByRole("button", { name: "Close" });
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(within(dialog).getByRole("button", { name: "Next picture" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(stageButton()).toHaveFocus();
  });

  it("the screenshots section opens the same full-screen view at that picture", () => {
    render(<StoreProductPage data={page()} />);
    const shots = screen.getByRole("region", { name: "Screenshots" });
    expect(within(shots).getAllByRole("button").map((b) => b.textContent)).toEqual(["View full screen: Pipeline board", "View full screen: Lead list"]);
    fireEvent.click(within(shots).getByRole("button", { name: "View full screen: Lead list" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("3 / 3")).toBeInTheDocument(); // after the banner and the first screenshot
    expect(within(dialog).getByRole("img", { name: "Lead list" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("the demo has its own section - its title, its words and a player that loads on Play only", () => {
    const { container } = render(<StoreProductPage data={page()} />);
    const demo = screen.getByRole("region", { name: "Product demo" });
    expect(within(demo).getByRole("heading", { level: 3, name: "See it in two minutes" })).toBeInTheDocument();
    expect(within(demo).getByText("A short tour of the pipeline.")).toBeInTheDocument();
    expect(container.querySelector("iframe")).toBeNull();
    // Its poster is the video's own (same-origin) picture.
    expect(demo.querySelector("img")).toHaveAttribute("src", "/api/v1/public/products/easy-crm/media/poster");
    fireEvent.click(within(demo).getByRole("button", { name: "Play video: See it in two minutes" }));
    const frame = container.querySelector("iframe")!;
    expect(frame.getAttribute("src")).toMatch(/^https:\/\/www\.youtube-nocookie\.com\/embed\/abc\?/);
    expect(frame).toHaveAttribute("sandbox");
    // The gallery holds the pictures only - the video is not shown twice.
    expect(within(screen.getByRole("group", { name: "WorkEasy CRM pictures" })).queryByRole("button", { name: /video/i })).toBeNull();
  });

  it("without a video there is no demo section, and without pictures the product's mark stands in", () => {
    const { container } = render(<StoreProductPage data={page({ product: { video: null, banner: null, screenshots: [], highlights: [] } })} />);
    expect(screen.queryByRole("region", { name: "Product demo" })).toBeNull();
    expect(container.querySelector("#demo")).toBeNull();
    const tabs = screen.getByRole("navigation", { name: "On this page" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Overview", "Plans & Pricing", "App Availability"]);
    expect(screen.queryByRole("group", { name: "WorkEasy CRM pictures" })).toBeNull();
    expect(container.querySelector("img")).toBeNull(); // never an invented picture
    expect(screen.queryByRole("region", { name: "Features" })).toBeNull();
    expect(screen.queryByRole("list", { name: "Key highlights" })).toBeNull();
  });

  it("a plan card shows the plan's own highlights, and Select Plan brings the plan box to it", () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      render(<StoreProductPage data={page()} />);
      const plans = screen.getByRole("region", { name: "Plans & Pricing" });
      const advanced = within(plans).getByRole("heading", { name: "Advanced" }).closest("li")!;
      expect(within(advanced).getByText("Recommended")).toBeInTheDocument();
      expect(within(advanced).getByText("Unlimited pipelines")).toBeInTheDocument();
      expect(within(advanced).getByText("Priority support")).toBeInTheDocument();
      const free = within(plans).getByRole("heading", { name: "Free" }).closest("li")!;
      fireEvent.click(within(free).getByRole("button", { name: "Select Plan" }));
      expect(scroll).toHaveBeenCalled();
      expect(screen.getByRole("radio", { name: /Free/ })).toBeChecked();
      expect(screen.getByRole("radio", { name: /Free/ })).toHaveFocus();
      expect(screen.getByRole("link", { name: "Start Free Plan" })).toHaveAttribute("href", `/store/checkout?plan=${FREE}&interval=annual`);
      expect(screen.queryByRole("link", { name: /Buy Now/ })).toBeNull();
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("App Availability shows only what was configured", () => {
    const first = render(<StoreProductPage data={page()} />);
    const apps = screen.getByRole("region", { name: "App Availability" });
    expect(within(apps).queryByText("Web application")).toBeNull();
    expect(within(apps).getByText("iOS (iPhone & iPad)")).toBeInTheDocument();
    expect(within(apps).getByText("Desktop (Windows)")).toBeInTheDocument();
    expect(within(apps).getAllByRole("link", { name: /Get the official app/ }).map((a) => a.getAttribute("href"))).toEqual(["https://apps.apple.com/app/id1", "https://example.com/windows"]);
    first.unmount();
    render(<StoreProductPage data={page({ product: { access: { web: false, apps: { android: "http://insecure.example" } } } })} />);
    const none = screen.getByRole("region", { name: "App Availability" });
    expect(within(none).queryByRole("listitem")).toBeNull();
    expect(within(none).getByText(/is listed here once it is available/)).toBeInTheDocument();
  });

  it("the buyer's page passes axe, with the estimate and tax from the server's rate", async () => {
    const { container } = render(<StoreProductPage data={page({ related: [] })} />);
    const box = screen.getByRole("complementary", { name: "Buy WorkEasy CRM" });
    expect(within(box).getByText("GST (18%)")).toBeInTheDocument();
    expect(within(box).getByText("Secure checkout")).toBeInTheDocument();
    expect(within(box).getByText(/The checkout confirms the final amount/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Buy Now/ })).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: /Start 7-day Free Trial/ })).toHaveLength(1);
    await expectNoViolations(container);
  });
});

describe("the Super Admin's preview", () => {
  it("draws the same page with nothing to buy, no related products, inert breadcrumbs and no request", async () => {
    document.title = "Product Publisher";
    const { container } = render(<StoreProductPage data={page()} preview />);
    expect(screen.queryAllByRole("link", { name: /Buy Now|Free Trial|Free Plan|Contact sales/ })).toHaveLength(0);
    expect(screen.getByRole("button", { name: /Buy Now/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Buy Now/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: /Start 7-day Free Trial/ })).toBeDisabled();
    expect(screen.getByText("Preview - purchasing opens once the product is published")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Related products" })).toBeNull();
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).queryAllByRole("link")).toHaveLength(0);
    expect(document.title).toBe("Product Publisher");
    // Everything else is the live page's.
    expect(screen.getByRole("region", { name: "Product demo" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Plans & Pricing" })).toBeInTheDocument();
    expect(calls.some((c) => c.key.endsWith("/store/recharge"))).toBe(false);
    await expectNoViolations(container);
  });

  it("is also asked for by the data's own preview flag", () => {
    render(<StoreProductPage data={{ ...page(), preview: true }} />);
    expect(screen.queryByRole("link", { name: /Buy Now/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Related products" })).toBeNull();
  });

  it("the live page lists related products and asks for the usage rates", async () => {
    render(<StoreProductPage data={page()} />);
    expect(screen.getByRole("heading", { name: "Related products" })).toBeInTheDocument();
    await vi.waitFor(() => expect(calls.some((c) => c.key === "GET /api/v1/public/store/recharge")).toBe(true));
  });
});
