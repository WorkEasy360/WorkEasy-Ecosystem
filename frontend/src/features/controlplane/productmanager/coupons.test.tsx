import { afterEach, describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { storeAdminApi, type Coupon } from "@/lib/api/store";
import { Coupons } from "./Coupons";
import { expectNoViolations, mockApi, ok } from "../testSupport";

/**
 * Super Admin → Store → Coupons (ADR-058): "+ New coupon" opens the form in a
 * popup (2026-11-05, the owner: every form in a popup). The popup sends the
 * coupon exactly as typed, closes, reports it and the list is read again.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/admin/store/coupons",
}));

const COUPONS = "GET /api/v1/admin/store/coupons";
const PRODUCTS = "GET /api/v1/admin/catalog/products";

const CREATED: Coupon = {
  id: "c1",
  code: "LAUNCH40",
  description: "",
  kind: "percent",
  percent_bp: 4000,
  amount_minor: null,
  currency: null,
  label: "40% off",
  product_codes: [],
  starts_at: null,
  ends_at: null,
  max_redemptions: null,
  once_per_organization: true,
  status: "active",
  redemptions: 0,
  created_at: "2026-11-05T10:00:00Z",
};

afterEach(() => vi.restoreAllMocks());

describe("Super Admin coupons", () => {
  it("creates a coupon from the New coupon popup, closes it and reports it", async () => {
    const calls = mockApi({
      [COUPONS]: [ok({ results: [] }), ok({ results: [CREATED] })],
      [PRODUCTS]: [ok({ results: [{ id: "p1", code: "sample_app", name: "Sample App" }] })],
    });
    const create = vi.spyOn(storeAdminApi, "createCoupon").mockResolvedValue(CREATED);
    const { container } = render(<Coupons />);

    expect(await screen.findByText("No coupons yet.")).toBeInTheDocument();
    // No form sits open on the page.
    expect(screen.queryByLabelText(/^Code/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "New coupon" }));
    const popup = screen.getByRole("dialog", { name: "New coupon" });
    const submit = within(popup).getByRole("button", { name: "Create coupon" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText(/^Code/), { target: { value: "launch40" } });
    fireEvent.change(within(popup).getByLabelText(/^Percent off/), { target: { value: "40" } });
    expect(submit).toBeEnabled();
    await expectNoViolations(container.ownerDocument.body);
    fireEvent.click(submit);

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith({
      code: "LAUNCH40",
      kind: "percent",
      percent_bp: 4000,
      description: "",
      product_codes: [],
      starts_at: null,
      ends_at: null,
      max_redemptions: null,
      once_per_organization: true,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Coupon LAUNCH40 created.")).toBeInTheDocument();
    // The list is read again and shows the new code.
    expect(await screen.findByText("LAUNCH40")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === COUPONS)).toHaveLength(2);
  });

  const DISABLED: Coupon = { ...CREATED, id: "c2", code: "OLD10", label: "10% off", status: "disabled" };
  const listed = (extra: Record<string, { status: number; body?: unknown }[]> = {}) =>
    mockApi({
      [COUPONS]: [ok({ results: [CREATED, DISABLED] })],
      [PRODUCTS]: [ok({ results: [{ id: "p1", code: "sample_app", name: "Sample App" }] })],
      ...extra,
    });

  it("puts Enable and Disable in the one Actions column, last (admin-ux phase 3)", async () => {
    listed();
    const { container } = render(<Coupons />);
    const table = await screen.findByRole("table", { name: "Coupons" });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(headers).toEqual(["Code", "Discount", "Products", "Valid", "Used", "Status", "Actions"]);
    const active = within(table).getByRole("row", { name: /LAUNCH40/ });
    expect(within(active).getAllByRole("cell").at(-1)).toContainElement(within(active).getByRole("button", { name: "Disable coupon LAUNCH40" }));
    const off = within(table).getByRole("row", { name: /OLD10/ });
    expect(within(off).getAllByRole("cell").at(-1)).toContainElement(within(off).getByRole("button", { name: "Enable coupon OLD10" }));
    await expectNoViolations(container);
  });

  it("asks before disabling a coupon, says what happens, and sends nothing on Cancel", async () => {
    const calls = listed();
    const update = vi.spyOn(storeAdminApi, "updateCoupon");
    render(<Coupons />);
    fireEvent.click(await screen.findByRole("button", { name: "Disable coupon LAUNCH40" }));
    const confirm = screen.getByRole("dialog", { name: "Disable coupon LAUNCH40?" });
    expect(within(confirm).getByText(/can no longer use this code on a new order/)).toBeInTheDocument();
    fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(update).not.toHaveBeenCalled();

    update.mockResolvedValue({ ...CREATED, status: "disabled" });
    fireEvent.click(screen.getByRole("button", { name: "Disable coupon LAUNCH40" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Disable coupon LAUNCH40?" })).getByRole("button", { name: "Disable coupon" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("c1", { status: "disabled" }));
    expect(await screen.findByText("Coupon LAUNCH40 disabled.")).toBeInTheDocument();
    // The list is read again.
    await waitFor(() => expect(calls.filter((c) => c.key === COUPONS)).toHaveLength(2));
  });

  it("enables a disabled coupon straight away", async () => {
    listed();
    const update = vi.spyOn(storeAdminApi, "updateCoupon").mockResolvedValue({ ...DISABLED, status: "active" });
    render(<Coupons />);
    fireEvent.click(await screen.findByRole("button", { name: "Enable coupon OLD10" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("c2", { status: "active" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText("Coupon OLD10 enabled.")).toBeInTheDocument();
  });
});
