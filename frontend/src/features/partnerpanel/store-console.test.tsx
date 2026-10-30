import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerContext } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import type { PortalPartner } from "@/lib/api/controlPlane";
import { StoreConsole } from "./StoreConsole";

/**
 * The Store Console (ADR-055): Explore shows every Store product open to
 * affiliates with this affiliate's status, an approved product is never offered
 * as a new request, Request Access sends only the form's answers, and My
 * Requests says what became of each request and why.
 */

let pathname = "/partner/affiliate/store-console/explore";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => pathname,
}));

const ID = "33333333-3333-3333-3333-333333333333";
const API = `/api/v1/partner/${ID}`;
const perm = (...keys: string[]) => keys.map((k) => `platform.partner_${k}`);
const AFFILIATE = perm("dashboard.view", "referral.view", "referral.create", "commission.view");

function partner(permissions: string[] = AFFILIATE): PortalPartner {
  return { partner_id: ID, code: "af", display_name: "Aff Co", status: "active", role: "owner", is_admin: true, capabilities: [], permissions };
}

const mount = (ui: React.ReactNode, permissions?: string[]) =>
  render(<PartnerContext.Provider value={{ partner: partner(permissions), base: "/partner/affiliate" }}>{ui}</PartnerContext.Provider>);

const product = (code: string, name: string, status: string, extra: Record<string, unknown> = {}) => ({
  code,
  name,
  slug: code,
  tagline: `${name} tagline`,
  summary: `${name} summary`,
  description: "",
  category: code === "books" ? "Finance" : "Sales",
  icon: "",
  logo_url: null,
  plans: [],
  starting_price: { amount_minor: 99900, currency: "INR", interval: "monthly" },
  commission: { rate_bp: 1000, recurring: false },
  status,
  request: null,
  ...extra,
});

const REJECTED = {
  id: "r2",
  product_code: "books",
  product_name: "Easy Books",
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

const STORE = {
  products: [
    product("crm", "Easy CRM", "approved"),
    product("books", "Easy Books", "rejected", { request: REJECTED }),
    product("desk", "Help Desk", "not_requested", { commission: null }),
    product("proj", "Easy Project", "pending", { request: { ...REJECTED, id: "r3", status: "pending", product_code: "proj", decided_at: null, decision_reason: "" } }),
  ],
  requests: [REJECTED],
  counts: { approved: 1, explore: 3, pending: 1 },
  can_request: true,
  cannot_request_reason: null,
  channels: { website: "Website or blog", social_media: "Social media" },
  prefill: { website: "https://aff.example.com" },
};

const routes = (extra: Record<string, Reply[]> = {}) => ({
  [`GET ${API}/affiliate/store`]: [ok(STORE), ok(STORE), ok(STORE)],
  ...extra,
});

describe("Store Console - Explore Products", () => {
  it("shows every product with its status, price and disclosed commission, and passes axe", async () => {
    pathname = "/partner/affiliate/store-console/explore";
    mockApi(routes());
    const { container } = mount(<StoreConsole tab="explore" />);
    const list = await screen.findByRole("list", { name: "Products" });
    const cards = within(list).getAllByRole("listitem");
    expect(cards).toHaveLength(4);
    const crm = cards.find((c) => within(c).queryByRole("heading", { name: "Easy CRM" }))!;
    expect(within(crm).getByText("Approved")).toBeInTheDocument();
    expect(within(crm).getByText(/Commission: 10.00% of each paid sale/)).toBeInTheDocument();
    expect(within(crm).getByText(/From .*99.*month/)).toBeInTheDocument();
    // An approved product is promoted, never requested again.
    expect(within(crm).queryByRole("button", { name: /Request/ })).not.toBeInTheDocument();
    expect(within(crm).getByRole("link", { name: "Promote Easy CRM in My Products" })).toHaveAttribute("href", "/partner/affiliate/store-console");
    const books = cards.find((c) => within(c).queryByRole("heading", { name: "Easy Books" }))!;
    expect(within(books).getByText("Reason: Not a fit for finance buyers.")).toBeInTheDocument();
    expect(within(books).getByRole("button", { name: "Request again to Easy Books" })).toBeInTheDocument();
    const desk = cards.find((c) => within(c).queryByRole("heading", { name: "Help Desk" }))!;
    expect(within(desk).queryByText(/Commission:/)).not.toBeInTheDocument();
    const proj = cards.find((c) => within(c).queryByRole("heading", { name: "Easy Project" }))!;
    expect(within(proj).getByText("Pending approval")).toBeInTheDocument();
    expect(within(proj).queryByRole("button")).not.toBeInTheDocument();
    // The tabs carry the server's counts.
    expect(screen.getByRole("link", { name: "My Products (1)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "My Requests (1 pending)" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("filters by search, category and status", async () => {
    mockApi(routes());
    mount(<StoreConsole tab="explore" />);
    await screen.findByRole("list", { name: "Products" });
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "books" } });
    expect(within(screen.getByRole("list", { name: "Products" })).getAllByRole("listitem")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "Sales" } });
    expect(within(screen.getByRole("list", { name: "Products" })).getAllByRole("listitem")).toHaveLength(3);
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "revoked" } });
    expect(screen.getByText("No product matches these filters.")).toBeInTheDocument();
  });

  it("requests access with the form's answers only and confirms it", async () => {
    const calls = mockApi(
      routes({ [`POST ${API}/affiliate/product-requests`]: [{ status: 201, body: { ...REJECTED, id: "r9", product_code: "desk", status: "pending" } }] }),
    );
    mount(<StoreConsole tab="explore" />);
    const list = await screen.findByRole("list", { name: "Products" });
    fireEvent.click(within(list).getByRole("button", { name: "Request access to Help Desk" }));
    const form = screen.getByRole("form", { name: "Request access to Help Desk" });
    expect(within(form).getByLabelText(/Website or social profile/)).toHaveValue("https://aff.example.com");
    fireEvent.change(within(form).getByLabelText("Promotion channel"), { target: { value: "social_media" } });
    fireEvent.change(within(form).getByLabelText("Target audience"), { target: { value: "Support leads" } });
    fireEvent.change(within(form).getByLabelText("How you will promote it"), { target: { value: "Weekly LinkedIn posts with a demo video." } });
    fireEvent.click(within(form).getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/affiliate/product-requests`)).toBe(true));
    const body = calls.find((c) => c.key === `POST ${API}/affiliate/product-requests`)!.body as Record<string, unknown>;
    expect(body).toEqual({
      product_code: "desk",
      channel: "social_media",
      website: "https://aff.example.com",
      audience: "Support leads",
      strategy: "Weekly LinkedIn posts with a demo video.",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(/Request sent for Help Desk/);
  });

  it("shows the server's refusal, and offers no request button when the member may not ask", async () => {
    mockApi(routes({ [`GET ${API}/affiliate/store`]: [ok({ ...STORE, can_request: false, cannot_request_reason: "Your role cannot request products. Ask the account owner." })] }));
    mount(<StoreConsole tab="explore" />, perm("dashboard.view", "referral.view"));
    expect(await screen.findByRole("note")).toHaveTextContent(/Your role cannot request products/);
    expect(screen.queryByRole("button", { name: /Request/ })).not.toBeInTheDocument();
  });

  it("explains an empty Store and a failed load", async () => {
    mockApi(routes({ [`GET ${API}/affiliate/store`]: [ok({ ...STORE, products: [], counts: { approved: 0, explore: 0, pending: 0 } })] }));
    const { unmount } = mount(<StoreConsole tab="explore" />);
    expect(await screen.findByText("No products are open to affiliates yet")).toBeInTheDocument();
    unmount();
    mockApi({ [`GET ${API}/affiliate/store`]: [{ status: 500, body: { code: "server_error", detail: "Boom" } }] });
    mount(<StoreConsole tab="explore" />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("Store Console - My Requests", () => {
  it("lists each request with its status, decision date and reason", async () => {
    pathname = "/partner/affiliate/store-console/requests";
    mockApi(routes());
    const { container } = mount(<StoreConsole tab="requests" />);
    const table = await screen.findByRole("table", { name: "Your product requests" });
    const row = within(table).getByRole("row", { name: /Easy Books/ });
    expect(within(row).getByText("Rejected")).toBeInTheDocument();
    expect(within(row).getByText("Not a fit for finance buyers.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /My Requests/ })).toHaveAttribute("aria-current", "page");
    await expectNoViolations(container);
  });
});
