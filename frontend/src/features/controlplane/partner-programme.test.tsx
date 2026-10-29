import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { mockApi, ok, type Call } from "./testSupport";
import { PartnerProgramme } from "./PartnerProgramme";

/**
 * Super Admin → Currency & pricing → partner margins and set-up fees (ADR-053).
 * The browser sends a scope and whole numbers; the server versions every change.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/pricing",
}));

const RULE = {
  id: "r1",
  code: "md-reseller",
  version: 1,
  experience: "reseller",
  partner_id: null,
  product_code: "",
  discount_bp: 2000,
  recurring: true,
  effective_from: "2026-10-28T10:00:00Z",
  retired_at: null,
  reason: "Launch terms",
};

const routes = (over: Record<string, unknown> = {}) => ({
  "GET /api/v1/admin/margins": [ok({ live: [RULE], history: [RULE] })],
  "GET /api/v1/admin/partners/resellers": [ok({ results: [{ id: "p1", code: "res-one", display_name: "Res One" }], next_cursor: null, has_more: false })],
  "GET /api/v1/admin/partner-programme/fees": [ok({ fees: { reseller: { amount_minor: 2000000, currency: "INR" }, white_label: null } })],
  ...over,
});

const PRODUCTS = [{ code: "easy_crm", name: "Easy CRM" }];

describe("partner margins", () => {
  it("lists the rules in force and saves a new one as whole basis points", async () => {
    const calls = mockApi(
      routes({
        "POST /api/v1/admin/margins": [{ status: 201, body: { ...RULE, id: "r2", discount_bp: 1250 } }],
      }),
    );
    render(<PartnerProgramme manage manageFees products={PRODUCTS} />);
    const table = await screen.findByRole("table", { name: "Margin rules in force" });
    expect(within(table).getByText("Default for Reseller partners")).toBeInTheDocument();
    expect(within(table).getByText("20.00%")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Discount off list price (%)"), { target: { value: "12.5" } });
    fireEvent.change(screen.getByLabelText("Product"), { target: { value: "easy_crm" } });
    fireEvent.click(screen.getByRole("button", { name: "Save margin" }));
    expect(await screen.findByText(/Margin saved/)).toBeInTheDocument();
    expect((calls.find((c) => c.key === "POST /api/v1/admin/margins") as Call).body).toEqual({
      discount_bp: 1250,
      recurring: true,
      product_code: "easy_crm",
      experience: "reseller",
    });
  });

  it("refuses a percentage that is not one, before asking the server", async () => {
    mockApi(routes());
    render(<PartnerProgramme manage manageFees products={PRODUCTS} />);
    await screen.findByRole("table", { name: "Margin rules in force" });
    fireEvent.change(screen.getByLabelText("Discount off list price (%)"), { target: { value: "150" } });
    expect(screen.getByRole("button", { name: "Save margin" })).toBeDisabled();
    expect(screen.getByText(/from 0 to 100/)).toBeInTheDocument();
  });

  it("shows no controls to staff who may only view", async () => {
    mockApi(routes());
    render(<PartnerProgramme manage={false} manageFees={false} products={PRODUCTS} />);
    await screen.findByRole("table", { name: "Margin rules in force" });
    expect(screen.queryByRole("button", { name: "Save margin" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save fee" })).not.toBeInTheDocument();
  });
});

describe("set-up fees", () => {
  it("shows each fee and saves one in minor units", async () => {
    const calls = mockApi(
      routes({
        "PUT /api/v1/admin/partner-programme/fees": [ok({ fees: { reseller: null, white_label: { amount_minor: 10000000, currency: "INR" } } })],
      }),
    );
    render(<PartnerProgramme manage manageFees products={PRODUCTS} />);
    const table = await screen.findByRole("table", { name: "Set-up fees" });
    expect(within(table).getByText(/20,000\.00/)).toBeInTheDocument();
    expect(within(table).getByText("No fee")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Becoming"), { target: { value: "white_label" } });
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "100000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save fee" }));
    expect(await screen.findByText("Set-up fee saved.")).toBeInTheDocument();
    expect((calls.find((c) => c.key === "PUT /api/v1/admin/partner-programme/fees") as Call).body).toEqual({
      experience: "white_label",
      amount: "100000",
      currency: "INR",
    });
  });
});
