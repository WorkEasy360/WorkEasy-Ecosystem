/**
 * Where the Partners area's old addresses go now (ADR-048, ADR-062).
 *
 * The old list pages - Resellers, White-label, Affiliates - are now the
 * Affiliate, Reseller and White Label pages (Partners → …, ADR-062); Customers,
 * Products & Pricing, Commissions and Payouts belong to one partner and are
 * reached from its page, so their old addresses open All Partners. Bookmarks
 * keep working; only the filters the destination understands are carried, so
 * an old filter value can never turn into an error. The destination is always
 * a fixed path.
 */

import { categoryHref } from "./partnerCategories";

type Params = Record<string, string | string[] | undefined>;

const KEEP = ["q", "status"] as const;

/** All Partners - or, given a partner type, that type's own page - keeping a
 * search and a valid status from the old address. */
export function allPartnersHref(params: Params, type?: "reseller" | "white_label" | "affiliate"): string {
  const kept: Params = {};
  for (const key of KEEP) kept[key] = params[key];
  return categoryHref(type ?? null, kept);
}
