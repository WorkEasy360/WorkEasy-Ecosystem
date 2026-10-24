/**
 * Where the Partners area's old addresses go now (ADR-048).
 *
 * The area is Dashboard, All Partners and Add Partner. The old list pages -
 * Resellers, White-label, Affiliates - are All Partners filtered on the label
 * (the capabilities a partner holds); Customers, Products & Pricing,
 * Commissions and Payouts belong to one partner and are reached from its page,
 * so their old addresses open All Partners. Bookmarks keep working; only the
 * filters the destination understands are carried, so an old filter value can
 * never turn into an error. The destination is always a fixed path.
 */

type Params = Record<string, string | string[] | undefined>;

const KEEP = ["q", "status"] as const;
const STATUSES = new Set(["draft", "active", "suspended", "terminated"]);

/** All Partners, optionally filtered on a partner type, keeping a search and a
 * valid status from the old address. */
export function allPartnersHref(params: Params, type?: "reseller" | "white_label" | "affiliate"): string {
  const out = new URLSearchParams();
  if (type) out.set("type", type);
  for (const key of KEEP) {
    const raw = params[key];
    const value = Array.isArray(raw) ? raw[0] : raw;
    if (!value) continue;
    if (key === "status" && !STATUSES.has(value)) continue;
    out.set(key, value.slice(0, 80));
  }
  const text = out.toString();
  return text ? `/admin/partners/all?${text}` : "/admin/partners/all";
}
