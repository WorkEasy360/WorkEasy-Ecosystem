import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerContext } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";
import type { PortalPartner } from "@/lib/api/controlPlane";
import { PartnerStore } from "./PartnerStore";

/**
 * A partner's Store: the Store Console's Explore Products as a marketplace
 * page. Each card shows this partner's access and what a sale earns; the one
 * action is the Store Console's - ask for access, or open an approved product.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/partner/affiliate/store",
}));

const ID = "33333333-3333-3333-3333-333333333333";
const API = `/api/v1/partner/${ID}`;
const perm = (...keys: string[]) => keys.map((k) => `platform.partner_${k}`);

function partner(): PortalPartner {
  return {
    partner_id: ID,
    code: "af",
    display_name: "Aff Co",
    status: "active",
    role: "owner",
    is_admin: true,
    capabilities: [],
    permissions: perm("dashboard.view", "referral.view", "referral.create", "commission.view", "product.view"),
  };
}

const mount = (ui: React.ReactNode, base = "/partner/affiliate") =>
  render(<PartnerContext.Provider value={{ partner: partner(), base }}>{ui}</PartnerContext.Provider>);

const REQUEST = {
  id: "r2",
  kind: "promote",
  product_code: "ledger_lite",
  product_name: "Ledger Lite",
  status: "rejected",
  channel: "website",
  channel_label: "Website or blog",
  website: "",
  audience: "Accountants",
  strategy: "Reviews on my blog.",
  notes: "",
  requested_at: "2026-10-20T10:00:00Z",
  decided_at: "2026-10-21T10:00:00Z",
  decision_reason: "Not a fit for finance buyers.",
  revoked_at: null,
  revoke_reason: "",
};

const card = (code: string, title: string, status: string, extra: Record<string, unknown> = {}) => ({
  code,
  name: title,
  title,
  slug: code,
  tagline: `${title} tagline`,
  summary: `${title} summary`,
  description: "",
  category: code === "ledger_lite" ? "Finance" : "Sales",
  icon: "",
  logo_url: null,
  banner_url: null,
  plans: [],
  starting_price: { amount_minor: 1900, currency: "GBP", interval: "monthly" },
  commission: { rate_bp: 1000, recurring: false },
  margin: null,
  status,
  request: null,
  ribbon: null,
  price: { amount_minor: 1900, currency: "GBP", interval: "monthly", plan_name: "Starter" },
  yearly_deal: null,
  plan_types: ["monthly"],
  trial_days: null,
  ...extra,
});

function page(kind = "promote", overrides: Record<string, unknown> = {}) {
  return {
    kind,
    currency: "GBP",
    currencies: ["GBP"],
    count: 4,
    total: 4,
    products: [
      card("harbor_crm", "Harbor CRM", "approved", { ribbon: { kind: "new", label: "New!" } }),
      card("ledger_lite", "Ledger Lite", "rejected", { request: REQUEST }),
      card("deskly", "Deskly", "not_requested", { commission: null }),
      card("taskboard", "Taskboard", "pending", { request: { ...REQUEST, id: "r3", status: "pending", decision_reason: "" } }),
    ],
    facets: {
      categories: [
        { value: "Finance", label: "Finance", count: 1 },
        { value: "Sales", label: "Sales", count: 3 },
      ],
      statuses: [
        { value: "approved", label: "Approved", count: 1 },
        { value: "pending", label: "Pending approval", count: 1 },
        { value: "not_requested", label: "Not requested", count: 1 },
        { value: "rejected", label: "Rejected", count: 1 },
        { value: "revoked", label: "Revoked", count: 0 },
      ],
      plan_types: [{ value: "monthly", label: "Monthly", count: 4 }],
      price: { min_minor: 1900, max_minor: 1900 },
    },
    sorts: [{ value: "recommended", label: "Recommended" }],
    counts: { approved: 1, explore: 3, pending: 1 },
    can_request: true,
    cannot_request_reason: null,
    channels: { website: "Website or blog", social_media: "Social media" },
    prefill: { website: "https://aff.example.com" },
    ...overrides,
  };
}

const AFFILIATE_STORE = `GET ${API}/affiliate/marketplace`;
const MINE = { path: "store-console", label: "My Products" };

describe("a partner's Store", () => {
  it("shows each product with this partner's access, what it earns and the one next step", async () => {
    mockApi({ [AFFILIATE_STORE]: [ok(page())] });
    const { container } = mount(<PartnerStore kind="promote" mine={MINE} />);
    const grid = await screen.findByRole("list", { name: "Products" });
    const [crm, ledger, desk, tasks] = Array.from(grid.children) as HTMLElement[];

    expect(within(crm).getByText("Approved")).toBeInTheDocument();
    expect(within(crm).getByText("New!")).toBeInTheDocument();
    expect(within(crm).getByText(/Commission: 10/)).toBeInTheDocument();
    expect(within(crm).getByRole("link", { name: "Open Harbor CRM in My Products" })).toHaveAttribute("href", "/partner/affiliate/store-console");
    expect(within(ledger).getByText("Reason: Not a fit for finance buyers.")).toBeInTheDocument();
    expect(within(ledger).getByRole("button", { name: "Request again to Ledger Lite" })).toBeInTheDocument();
    expect(within(desk).queryByText(/Commission/)).toBeNull();
    expect(within(desk).getByRole("button", { name: "Request access to Deskly" })).toBeInTheDocument();
    expect(within(tasks).getByText("Pending approval")).toBeInTheDocument();
    expect(within(tasks).queryByRole("button")?.textContent ?? "").not.toMatch(/Request/);
    expect(screen.getByRole("group", { name: "Access" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("asks for access with the Store Console's form, then reads the Store again", async () => {
    const calls = mockApi({
      [AFFILIATE_STORE]: [ok(page()), ok(page())],
      [`POST ${API}/affiliate/product-requests`]: [{ status: 201, body: { ...REQUEST, id: "r9", product_code: "deskly", status: "pending" } }],
    });
    mount(<PartnerStore kind="promote" mine={MINE} />);
    fireEvent.click(await screen.findByRole("button", { name: "Request access to Deskly" }));
    const form = screen.getByRole("dialog", { name: "Request access to Deskly" });
    fireEvent.change(within(form).getByLabelText("Promotion channel"), { target: { value: "social_media" } });
    fireEvent.change(within(form).getByLabelText("Target audience"), { target: { value: "Support leads" } });
    fireEvent.change(within(form).getByLabelText("How you will promote it"), { target: { value: "Weekly posts with a demo video." } });
    fireEvent.click(within(form).getByRole("button", { name: "Send request" }));
    expect(await screen.findByText(/Request sent for Deskly/)).toBeInTheDocument();
    expect(calls.find((c) => c.key === `POST ${API}/affiliate/product-requests`)!.body).toMatchObject({ product_code: "deskly", channel: "social_media" });
    await waitFor(() => expect(calls.filter((c) => c.key === AFFILIATE_STORE)).toHaveLength(2));
  });

  it("sends the access filter to the server", async () => {
    const calls = mockApi({ [AFFILIATE_STORE]: [ok(page())] });
    mount(<PartnerStore kind="promote" mine={MINE} />);
    await screen.findByRole("list", { name: "Products" });
    fireEvent.click(screen.getByRole("checkbox", { name: /Pending approval/ }));
    await waitFor(() => expect(new URL(calls.at(-1)!.url, "http://x").searchParams.get("status")).toBe("pending"));
  });

  it("a white-label partner reads the sellers' Store and opens approved products in Products & Plans", async () => {
    mockApi({ [`GET ${API}/reseller/marketplace`]: [ok(page("sell"))] });
    mount(<PartnerStore kind="sell" mine={{ path: "partner-products", label: "Products & Plans" }} />, "/partner/white-label");
    expect(await screen.findByRole("link", { name: "Open Harbor CRM in Products & Plans" })).toHaveAttribute(
      "href",
      "/partner/white-label/partner-products",
    );
  });

  it("offers no request when the member may not ask, and says why", async () => {
    mockApi({ [AFFILIATE_STORE]: [ok(page("promote", { can_request: false, cannot_request_reason: "Ask the account owner." }))] });
    mount(<PartnerStore kind="promote" mine={MINE} />);
    expect(await screen.findByRole("note")).toHaveTextContent("Ask the account owner.");
    expect(screen.queryAllByRole("button", { name: /^Request/ })).toEqual([]);
  });
});
