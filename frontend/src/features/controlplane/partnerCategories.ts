/**
 * Partners → Affiliate, Reseller, White Label (ADR-062): the Admin's three
 * partner categories, each the All Partners table with its type fixed. The
 * server decides which partners a category lists (each partner under exactly
 * one - the type its row shows); this file only names the pages.
 */

export type PartnerCategory = "affiliate" | "reseller" | "white_label";

export const CATEGORIES: readonly PartnerCategory[] = ["affiliate", "reseller", "white_label"];

export const CATEGORY_PATH: Record<PartnerCategory, string> = {
  affiliate: "/admin/partners/affiliate",
  reseller: "/admin/partners/reseller",
  white_label: "/admin/partners/white-label",
};

/** The sidebar's words for them (the owner's, 2026-11-10). */
export const CATEGORY_MENU_LABEL: Record<PartnerCategory, string> = {
  affiliate: "Affiliate",
  reseller: "Reseller",
  white_label: "White Label",
};

export const isCategory = (value: unknown): value is PartnerCategory =>
  typeof value === "string" && (CATEGORIES as readonly string[]).includes(value);

type Params = Record<string, string | string[] | undefined>;

/** The filters a category page understands, carried from another address; an
 * unknown status never turns into an error. */
const KEEP = ["q", "status", "profile", "kyc"] as const;
const VALUES: Partial<Record<(typeof KEEP)[number], Set<string>>> = {
  status: new Set(["draft", "active", "suspended", "terminated"]),
  profile: new Set(["complete", "incomplete"]),
  kyc: new Set(["ready", "approved", "not_approved", "legacy"]),
};

/** A category's page (or All partners for `null`), keeping the filters of
 * `params` it understands. Always a fixed path. */
export function categoryHref(category: PartnerCategory | null, params: Params = {}): string {
  const out = new URLSearchParams();
  for (const key of KEEP) {
    const raw = params[key];
    const value = Array.isArray(raw) ? raw[0] : raw;
    if (!value) continue;
    const allowed = VALUES[key];
    if (allowed && !allowed.has(value)) continue;
    out.set(key, value.slice(0, 80));
  }
  const base = category ? CATEGORY_PATH[category] : "/admin/partners/all";
  const text = out.toString();
  return text ? `${base}?${text}` : base;
}
