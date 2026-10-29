"use client";

import { useSyncExternalStore } from "react";

/**
 * The Store's cart (ADR-052): which plans the buyer has picked, one per
 * product, kept in this tab (`sessionStorage`) per organization.
 *
 * It holds **identifiers and labels only** - never a price. The checkout asks
 * the server to quote the cart and the server re-prices every line when the
 * order is placed, so nothing stored here can change what is charged.
 */

export interface CartItem {
  plan_version_id: string;
  product_code: string;
  product_name: string;
  plan_name: string;
}

export interface Cart {
  interval: string;
  items: CartItem[];
}

export const MAX_CART_ITEMS = 10;
const EMPTY: Cart = { interval: "monthly", items: [] };
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CHANGED = "w360-cart-changed";
const key = (slug: string) => `w360.cart.${slug}`;
const cache = new Map<string, { raw: string | null; cart: Cart }>();

function read(slug: string): Cart {
  let raw: string | null = null;
  try {
    raw = typeof window === "undefined" ? null : window.sessionStorage.getItem(key(slug));
  } catch {
    raw = null;
  }
  const hit = cache.get(slug);
  if (hit && hit.raw === raw) return hit.cart;
  let cart = EMPTY;
  try {
    const parsed = raw ? (JSON.parse(raw) as Partial<Cart>) : null;
    if (parsed && Array.isArray(parsed.items)) {
      cart = {
        interval: typeof parsed.interval === "string" ? parsed.interval : "monthly",
        items: parsed.items
          .filter((i): i is CartItem => !!i && typeof i === "object" && UUID_RE.test(String((i as CartItem).plan_version_id)))
          .slice(0, MAX_CART_ITEMS),
      };
    }
  } catch {
    cart = EMPTY;
  }
  cache.set(slug, { raw, cart });
  return cart;
}

function write(slug: string, cart: Cart) {
  try {
    if (cart.items.length === 0) window.sessionStorage.removeItem(key(slug));
    else window.sessionStorage.setItem(key(slug), JSON.stringify(cart));
  } catch {
    // Private mode or storage disabled: the cart simply does not persist.
  }
  window.dispatchEvent(new Event(CHANGED));
}

function subscribe(onChange: () => void) {
  window.addEventListener(CHANGED, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** The cart of one organization, with the ways to change it. Adding a plan of
 * a product already in the cart replaces that product's plan (one per product). */
export function useCart(slug: string) {
  const cart = useSyncExternalStore(subscribe, () => read(slug), () => EMPTY);
  return {
    cart,
    has: (planVersionId: string) => cart.items.some((i) => i.plan_version_id === planVersionId),
    add(item: CartItem, interval: string) {
      const current = read(slug);
      const others = current.items.filter((i) => i.product_code !== item.product_code);
      if (others.length >= MAX_CART_ITEMS) return;
      write(slug, { interval: current.items.length ? current.interval : interval, items: [...others, item] });
    },
    remove(planVersionId: string) {
      const current = read(slug);
      write(slug, { ...current, items: current.items.filter((i) => i.plan_version_id !== planVersionId) });
    },
    clear() {
      write(slug, EMPTY);
    },
  };
}

/** Where the cart is checked out: a single plan uses the ordinary checkout;
 * two or more go as one cart order. Identifiers only in the address. */
export function checkoutHref(slug: string, cart: Cart): string {
  const params = new URLSearchParams({ interval: cart.interval });
  if (cart.items.length === 1) params.set("plan", cart.items[0].plan_version_id);
  else params.set("cart", cart.items.map((i) => i.plan_version_id).join(","));
  return `/o/${slug}/checkout?${params.toString()}`;
}
