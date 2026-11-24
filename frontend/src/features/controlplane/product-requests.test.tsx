import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";
import { ProductRequestsConsole } from "./ProductRequests";
import { CENTER_ITEMS, centerItems } from "./partnerCenterNav";

/**
 * Partners → Product Requests (ADR-055): staff review an affiliate's request
 * with the partner, the product and the history beside it; approve; reject with
 * a reason; revoke. A staff member who may only look gets no decision buttons.
 */

let query = new URLSearchParams("request=req1");
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push, refresh: vi.fn() }),
  useSearchParams: () => query,
  usePathname: () => "/admin/partners/product-requests",
}));

const API = "/api/v1/admin/partner-product-requests";
const MANAGE = ["platform.staff_partner.view", "platform.staff_partner.manage"];

const REQUEST = {
  id: "req1",
  kind: "promote",
  partner_id: "p1",
  product_code: "crm",
  product_name: "Easy CRM",
  status: "pending",
  channel: "website",
  channel_label: "Website or blog",
  website: "https://reviews.example.com",
  audience: "Small agencies",
  strategy: "A comparison article.\nAnd a newsletter.",
  notes: "",
  requested_at: "2026-10-29T10:00:00Z",
  decided_at: null,
  decision_reason: "",
  revoked_at: null,
  revoke_reason: "",
  requested_by: "aff@example.com",
  decided_by: null,
  revoked_by: null,
  grant_id: null,
};

const QUEUE = {
  items: [{ ...REQUEST, partner_name: "Acme Media", partner_code: "ACME", partner_status: "active" }],
  counts: { pending: 1, approved: 4, rejected: 2, revoked: 0 },
  total: 1,
  limit: 50,
  offset: 0,
  channels: { website: "Website or blog" },
};

const DETAIL = {
  request: REQUEST,
  partner: {
    id: "p1",
    name: "Acme Media",
    code: "ACME",
    status: "active",
    website: "https://acme.example.com",
    referral_capability: true,
    selling_capability: false,
    channel: "affiliate",
    promotes: [],
    sells: [],
  },
  product: { code: "crm", name: "Easy CRM", status: "available", eligible: true },
  history: [REQUEST],
  grants: [],
};

const routes = (extra: Record<string, Reply[]> = {}) => ({
  [`GET ${API}`]: [ok(QUEUE), ok(QUEUE), ok(QUEUE)],
  [`GET ${API}/req1`]: [ok(DETAIL), ok(DETAIL), ok(DETAIL)],
  ...extra,
});

const mount = (permissions: string[] = MANAGE) =>
  render(
    <AdminContextForTest permissions={permissions}>
      <ProductRequestsConsole />
    </AdminContextForTest>,
  );

describe("Product Requests", () => {
  it("is a Partners section for anyone who may view partners", () => {
    expect(CENTER_ITEMS.map((i) => i.label)).toEqual(["Partner overview", "All partners", "New partner", "Product requests", "Upgrade fees", "Partner tickets", "Withdrawals"]);
    expect(centerItems(["platform.staff_partner.view"]).map((i) => i.label)).toContain("Product requests");
  });

  it("shows the queue with counts and the open request's partner, product, answers and history; passes axe", async () => {
    mockApi(routes());
    const { container } = mount();
    const table = await screen.findByRole("table", { name: "Product requests: pending" });
    expect(within(table).getByText("Acme Media")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Product requests" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pending 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Approved 4" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All 7" })).toBeInTheDocument();
    expect(await screen.findByText("Small agencies")).toBeInTheDocument();
    expect(screen.getByText("Yes - on the Store for affiliates")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Requests" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("approves through the server", async () => {
    const calls = mockApi(routes({ [`POST ${API}/req1/approve`]: [ok({ ...REQUEST, status: "approved", grant_id: "g1" })] }));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/req1/approve`)).toBe(true));
    expect(await screen.findByText(/Approved: Acme Media may promote Easy CRM/)).toBeInTheDocument();
  });

  it("rejects only with a reason, and sends it", async () => {
    const calls = mockApi(routes({ [`POST ${API}/req1/reject`]: [ok({ ...REQUEST, status: "rejected" })] }));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    const popup = screen.getByRole("dialog", { name: "Reject the request" });
    const reason = within(popup).getByLabelText(/Reason \(the affiliate reads it\)/);
    expect(reason).toBeRequired();
    const submit = within(popup).getByRole("button", { name: "Reject request" });
    expect(submit).toBeDisabled();
    fireEvent.change(reason, { target: { value: "Audience is not a fit." } });
    fireEvent.click(submit);
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/req1/reject`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${API}/req1/reject`)!.body).toEqual({ reason: "Audience is not a fit." });
    expect(await screen.findByText("Request rejected. The affiliate sees your reason.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers revocation for an approved request", async () => {
    const approved = { ...DETAIL, request: { ...REQUEST, status: "approved", grant_id: "g1" } };
    const calls = mockApi(
      routes({
        [`GET ${API}/req1`]: [ok(approved), ok(approved)],
        [`POST ${API}/req1/revoke`]: [ok({ ...REQUEST, status: "revoked" })],
      }),
    );
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke permission" }));
    const popup = screen.getByRole("dialog", { name: "Revoke the permission" });
    fireEvent.click(within(popup).getByRole("button", { name: "Revoke permission" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/req1/revoke`)).toBe(true));
    expect(await screen.findByText(/Permission revoked\. New links stop now/)).toBeInTheDocument();
  });

  it("gives a staff member who may only look no decision buttons", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view"]);
    expect(await screen.findByText("Small agencies")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("words a reseller's request for selling and filters the queue by type", async () => {
    const sellDetail = {
      ...DETAIL,
      request: { ...REQUEST, kind: "sell", channel: "existing_customers", channel_label: "My existing customers" },
      partner: { ...DETAIL.partner, referral_capability: false, selling_capability: true, channel: "reseller", sells: ["books"] },
    };
    const calls = mockApi(
      routes({
        [`GET ${API}/req1`]: [ok(sellDetail), ok(sellDetail)],
        [`POST ${API}/req1/approve`]: [ok({ ...REQUEST, kind: "sell", status: "approved", grant_id: "pg1" })],
      }),
    );
    mount();
    expect(await screen.findByText("What the reseller wrote")).toBeInTheDocument();
    expect(screen.getByText("Selling capability")).toBeInTheDocument();
    expect(screen.getByText("books")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/req1/approve`)).toBe(true));
    expect(await screen.findByText(/Approved: Acme Media may sell Easy CRM/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("combobox", { name: "Type" }));
    fireEvent.click(screen.getByRole("option", { name: "Reseller - sell" }));
    expect(push).toHaveBeenCalledWith("/admin/partners/product-requests?request=req1&kind=sell");
  });

  it("searches through the address", async () => {
    query = new URLSearchParams();
    mockApi(routes());
    mount();
    await screen.findByRole("table", { name: "Product requests: pending" });
    fireEvent.change(screen.getByLabelText("Search by partner or product"), { target: { value: "acme" } });
    // Searched as typed (after a short pause), with no Search button.
    expect(screen.queryByRole("button", { name: "Search" })).not.toBeInTheDocument();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/partners/product-requests?q=acme"));
  });
});

describe("Product Requests: table, filters and actions (admin-ux phase 3)", () => {
  const listUrl = (calls: Call[]) => new URL(calls.filter((c) => c.key === `GET ${API}`).at(-1)!.url, "http://x").searchParams;

  it("puts Review in the one Actions column, last, and it opens the request", async () => {
    query = new URLSearchParams();
    push.mockReset();
    mockApi(routes());
    mount();
    const table = await screen.findByRole("table", { name: "Product requests: pending" });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(headers.at(-1)).toBe("Actions");
    expect(headers).not.toContain("Action");
    const row = within(table).getByRole("row", { name: /Acme Media/ });
    const review = within(row).getByRole("button", { name: "Review the request from Acme Media for Easy CRM" });
    expect(within(row).getAllByRole("cell").at(-1)).toContainElement(review);
    fireEvent.click(review);
    expect(push).toHaveBeenCalledWith("/admin/partners/product-requests?request=req1");
  });

  it("lists the search and type from the address as tags; removing one, or Reset filters, takes it out of the address", async () => {
    query = new URLSearchParams("status=approved&q=acme&kind=sell");
    push.mockReset();
    const calls = mockApi(routes());
    mount();
    await screen.findByRole("table", { name: "Product requests: approved" });
    expect(Object.fromEntries(listUrl(calls))).toMatchObject({ status: "approved", q: "acme", kind: "sell" });
    const tags = screen.getByRole("list", { name: "Active filters" });
    expect(within(tags).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Search: “acme”", "Type: Reseller - sell"]);

    fireEvent.click(within(tags).getByRole("button", { name: "Remove filter Type: Reseller - sell" }));
    expect(push).toHaveBeenLastCalledWith("/admin/partners/product-requests?status=approved&q=acme");
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    // The status is the list, not a filter on it: it stays.
    expect(push).toHaveBeenLastCalledWith("/admin/partners/product-requests?status=approved");
  });

  it("leaves out a status, type or page the list does not offer, and says so", async () => {
    query = new URLSearchParams("status=bogus&kind=steal&offset=-4");
    const calls = mockApi(routes());
    mount();
    await screen.findByRole("table", { name: "Product requests: pending" });
    const sent = listUrl(calls);
    expect(sent.get("status")).toBe("pending");
    expect(sent.has("kind")).toBe(false);
    expect(sent.get("offset") ?? "0").toBe("0");
    expect(screen.getByText(/were not recognised and were left out/)).toBeInTheDocument();
  });

  it("offers Try again when the queue could not be loaded", async () => {
    query = new URLSearchParams();
    const calls = mockApi({ [`GET ${API}`]: [{ status: 500, body: { detail: "The queue is unavailable." } }, ok(QUEUE)] });
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("The queue is unavailable.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: "Product requests: pending" })).toBeInTheDocument();
    expect(calls.filter((c) => c.key === `GET ${API}`)).toHaveLength(2);
  });
});
