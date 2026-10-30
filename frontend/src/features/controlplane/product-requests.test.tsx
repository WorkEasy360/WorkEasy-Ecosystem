import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "./testSupport";
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
  partner: { id: "p1", name: "Acme Media", code: "ACME", status: "active", website: "https://acme.example.com", referral_capability: true, promotes: [] },
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
  it("is a Partner Control Center section for anyone who may view partners", () => {
    expect(CENTER_ITEMS.map((i) => i.label)).toEqual(["Dashboard", "All Partners", "Add Partner", "Product Requests"]);
    expect(centerItems(["platform.staff_partner.view"]).map((i) => i.label)).toContain("Product Requests");
  });

  it("shows the queue with counts and the open request's partner, product, answers and history; passes axe", async () => {
    mockApi(routes());
    const { container } = mount();
    const table = await screen.findByRole("table", { name: "Product requests: pending" });
    expect(within(table).getByText("Acme Media")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Approved (4)" })).toBeInTheDocument();
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
    const reason = screen.getByLabelText(/Reason \(the affiliate reads it\)/);
    expect(reason).toBeRequired();
    fireEvent.change(reason, { target: { value: "Audience is not a fit." } });
    fireEvent.click(screen.getByRole("button", { name: "Reject request" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/req1/reject`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${API}/req1/reject`)!.body).toEqual({ reason: "Audience is not a fit." });
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
    fireEvent.click(screen.getByRole("button", { name: "Revoke permission" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/req1/revoke`)).toBe(true));
  });

  it("gives a staff member who may only look no decision buttons", async () => {
    mockApi(routes());
    mount(["platform.staff_partner.view"]);
    expect(await screen.findByText("Small agencies")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("searches through the address", async () => {
    query = new URLSearchParams();
    mockApi(routes());
    mount();
    await screen.findByRole("table", { name: "Product requests: pending" });
    fireEvent.change(screen.getByLabelText("Search by partner or product"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(push).toHaveBeenCalledWith("/admin/partners/product-requests?q=acme");
  });
});
