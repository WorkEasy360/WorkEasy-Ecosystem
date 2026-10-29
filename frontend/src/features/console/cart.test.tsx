import { describe, it, expect, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { checkoutHref, useCart, type CartItem } from "./cart";

/**
 * The Store's cart (ADR-052): one plan per product, at most ten, per
 * organization, identifiers and labels only - and a checkout address that
 * carries nothing but plan ids.
 */

const id = (n: number) => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const item = (n: number, product = `p${n}`): CartItem => ({
  plan_version_id: id(n),
  product_code: product,
  product_name: `Product ${n}`,
  plan_name: `Plan ${n}`,
});

beforeEach(() => window.sessionStorage.clear());

describe("the cart", () => {
  it("keeps one plan per product, per organization, and never a price", () => {
    const { result } = renderHook(() => useCart("acme"));
    act(() => result.current.add(item(1, "crm"), "annual"));
    act(() => result.current.add(item(2, "books"), "monthly"));
    act(() => result.current.add(item(3, "crm"), "monthly")); // replaces the CRM plan
    expect(result.current.cart.items.map((i) => i.plan_version_id)).toEqual([id(2), id(3)]);
    expect(result.current.cart.interval).toBe("annual"); // fixed by the first item
    expect(window.sessionStorage.getItem("w360.cart.acme")).not.toMatch(/amount|price/);
    const other = renderHook(() => useCart("other-org"));
    expect(other.result.current.cart.items).toEqual([]);
    act(() => result.current.remove(id(2)));
    expect(result.current.cart.items).toHaveLength(1);
    act(() => result.current.clear());
    expect(window.sessionStorage.getItem("w360.cart.acme")).toBeNull();
  });

  it("holds at most ten products and ignores a tampered entry", () => {
    const { result } = renderHook(() => useCart("acme"));
    for (let n = 1; n <= 11; n++) act(() => result.current.add(item(n), "monthly"));
    expect(result.current.cart.items).toHaveLength(10);
    window.sessionStorage.setItem("w360.cart.acme", JSON.stringify({ interval: "monthly", items: [{ plan_version_id: "../../x" }] }));
    const fresh = renderHook(() => useCart("acme"));
    expect(fresh.result.current.cart.items).toEqual([]);
  });

  it("checks out one plan directly and several as one cart order", () => {
    expect(checkoutHref("acme", { interval: "monthly", items: [item(1)] })).toBe(`/o/acme/checkout?interval=monthly&plan=${id(1)}`);
    expect(checkoutHref("acme", { interval: "annual", items: [item(1), item(2)] })).toBe(
      `/o/acme/checkout?interval=annual&cart=${encodeURIComponent(`${id(1)},${id(2)}`)}`,
    );
  });
});
