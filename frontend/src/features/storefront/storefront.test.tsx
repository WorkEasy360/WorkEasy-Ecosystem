import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { expectNoViolations, mockApi, ok, SIGNED_IN } from "@/features/controlplane/testSupport";
import { StorefrontScreen } from "./Storefront";

/**
 * A white-label partner's branded Store (ADR-053): the partner's brand over the
 * central catalog. The tests are about what a visitor sees and where Buy goes -
 * the platform's own checkout with the store named - never about styling.
 */

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push, refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/store/acme-partners",
}));

const SIGNED_OUT = { status: 200, body: { authenticated: false } };

const STORE = {
  slug: "acme-partners",
  brand: {
    brand_name: "Acme Cloud",
    logo_url: "/api/v1/public/storefront/acme-partners/logo",
    accent_color: "#0f766e",
    theme: {},
    support_email: "help@acme.example",
    support_url: "",
    portal: {
      short_name: "Acme",
      support_name: "",
      support_phone: "+91 98765 43210",
      website_url: "https://acme.example",
      terms_url: "https://acme.example/terms",
      privacy_url: "javascript:alert(1)",
      welcome_message: "Business apps from Acme.",
      footer_text: "Acme Cloud, Pune",
    },
  },
  products: [
    {
      code: "easy_crm",
      name: "Easy CRM",
      slug: "easy-crm",
      tagline: "Sell more",
      category: "crm",
      icon: "",
      summary: "Pipelines and follow-ups.",
      logo_url: null,
      plans: [
        {
          plan_version_id: "11111111-1111-1111-1111-111111111111",
          plan_code: "crm_basic",
          plan_name: "Basic",
          trial_days: 7,
          prices: [{ currency: "INR", interval: "monthly", amount_minor: 99900 }],
        },
      ],
    },
  ],
};

beforeEach(() => {
  push.mockReset();
});

describe("the branded store", () => {
  it("shows the partner's brand, only its products and the server's prices", async () => {
    mockApi({
      "GET /api/v1/public/storefront/acme-partners": [ok(STORE)],
      "GET /api/v1/auth/session": [SIGNED_OUT],
    });
    const { container } = render(<StorefrontScreen slug="acme-partners" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Acme Cloud Store" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Acme Cloud logo" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Easy CRM" })).toBeInTheDocument();
    expect(screen.getByText(/999\.00/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Terms" })).toHaveAttribute("href", "https://acme.example/terms");
    // Rendered as an anchor, but React refuses a javascript: URL; the server
    // stores only https addresses in the first place.
    expect(screen.getByText(/Checkout, payment and your account are provided by Workeasy360/)).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("sends a signed-out buyer to sign in and back to the store", async () => {
    mockApi({
      "GET /api/v1/public/storefront/acme-partners": [ok(STORE)],
      "GET /api/v1/auth/session": [SIGNED_OUT],
    });
    render(<StorefrontScreen slug="acme-partners" />);
    fireEvent.click(await screen.findByRole("button", { name: /^Buy/ }));
    expect(push).toHaveBeenCalledWith(`/login?mode=user&next=${encodeURIComponent("/store/acme-partners")}`);
  });

  it("takes a signed-in buyer to the platform's checkout with the store named", async () => {
    mockApi({
      "GET /api/v1/public/storefront/acme-partners": [ok(STORE)],
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/organizations": [
        ok({ organizations: [{ organization_id: "o1", slug: "buyer-co", display_name: "Buyer Co", status: "active", membership_id: "m1", is_owner: true }], has_more: false }),
      ],
    });
    render(<StorefrontScreen slug="acme-partners" />);
    const buy = await screen.findByRole("button", { name: /^Buy/ });
    await vi.waitFor(() => expect(buy).not.toBeDisabled());
    fireEvent.click(buy);
    await vi.waitFor(() => expect(push).toHaveBeenCalled());
    const target = push.mock.calls[0][0] as string;
    expect(target.startsWith("/o/buyer-co/checkout?")).toBe(true);
    const query = new URLSearchParams(target.split("?")[1]);
    expect(query.get("plan")).toBe("11111111-1111-1111-1111-111111111111");
    expect(query.get("storefront")).toBe("acme-partners");
    expect(query.get("interval")).toBe("monthly");
  });

  it("says plainly when there is no such store", async () => {
    mockApi({
      "GET /api/v1/public/storefront/nobody": [{ status: 404, body: { code: "not_found" } }],
      "GET /api/v1/auth/session": [SIGNED_OUT],
    });
    render(<StorefrontScreen slug="nobody" />);
    expect(await screen.findByText("This store is not available.")).toBeInTheDocument();
  });
});
