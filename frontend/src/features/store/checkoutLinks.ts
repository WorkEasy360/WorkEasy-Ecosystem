/**
 * What a Store checkout is for (ADR-065): identifiers only, never a price - the
 * server quotes every order again. One product, one plan, one organization per
 * order; there is no cart.
 *
 * - a plan: `?plan=<plan version id>&interval=<period>[&quantity=<seats>][&coupon=<code>]`
 * - a product's own trial: `?trial=<product code>&trial_plan=<plan code>`
 * - plus, from a product's own site: `&return_to=<url>&from=<product code>` - an
 *   address the server must approve before the Store ever links to it.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CODE = /^[a-z][a-z0-9_]{0,63}$/;
const COUPON = /^[A-Z0-9][A-Z0-9_-]{2,31}$/;
export const INTERVALS = ["monthly", "quarterly", "semi_annual", "annual"] as const;
const MAX_QUANTITY = 100_000;

export type CheckoutTarget =
  | { kind: "plan"; plan: string; interval: string; quantity: number; coupon?: string }
  | { kind: "trial"; product: string; plan: string };

export interface CheckoutContext {
  target: CheckoutTarget;
  /** A product's page to return to - shown only once the server approved it. */
  returnTo?: { product: string; url: string };
}

export function checkoutHref(args: { plan: string; interval: string; quantity?: number; coupon?: string; returnTo?: { product: string; url: string } }): string {
  const params = new URLSearchParams({ plan: args.plan, interval: args.interval });
  if (args.quantity && args.quantity > 1) params.set("quantity", String(args.quantity));
  if (args.coupon) params.set("coupon", args.coupon);
  if (args.returnTo) {
    params.set("from", args.returnTo.product);
    params.set("return_to", args.returnTo.url);
  }
  return `/store/checkout?${params.toString()}`;
}

export function trialHref(product: string, plan: string): string {
  return `/store/checkout?${new URLSearchParams({ trial: product, trial_plan: plan }).toString()}`;
}

/** The checkout's own address, read back - each value validated before it goes
 * near an API path; anything else is "nothing was chosen" (`null`). */
export function checkoutFrom(params: URLSearchParams): CheckoutContext | null {
  let target: CheckoutTarget | null = null;
  const trial = params.get("trial");
  const trialPlan = params.get("trial_plan");
  if (trial && trialPlan && CODE.test(trial) && CODE.test(trialPlan)) {
    target = { kind: "trial", product: trial, plan: trialPlan };
  } else {
    const plan = params.get("plan");
    const interval = params.get("interval") ?? "";
    if (!plan || !UUID.test(plan) || !(INTERVALS as readonly string[]).includes(interval)) return null;
    const raw = Number(params.get("quantity") ?? "1");
    const quantity = Number.isInteger(raw) && raw >= 1 && raw <= MAX_QUANTITY ? raw : 1;
    const coupon = (params.get("coupon") ?? "").trim().toUpperCase();
    target = { kind: "plan", plan, interval, quantity, coupon: COUPON.test(coupon) ? coupon : undefined };
  }
  const from = params.get("from") ?? "";
  const url = params.get("return_to") ?? "";
  const returnTo = CODE.test(from) && /^https:\/\//.test(url) && url.length <= 2048 ? { product: from, url } : undefined;
  return { target, returnTo };
}
