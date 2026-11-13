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

// --- the list this tab last showed ---------------------------------------------------
//
// So "Back to Partners" on a partner's page, and a guest session's return, open the
// list the Admin came from with its search and filters. Per tab (sessionStorage),
// an address only - never a partner's data - and read back only when it is one of
// the four list pages.

const LIST_KEY = "w360.admin.partners.list";
const LIST_RE = /^\/admin\/partners\/(all|affiliate|reseller|white-label)$/;

/** Remember the list on screen (`pathname` + `search`). */
export function rememberPartnerList(pathname: string, search: string): void {
  if (!LIST_RE.test(pathname)) return;
  try {
    window.sessionStorage.setItem(LIST_KEY, JSON.stringify({ path: pathname, search: search.startsWith("?") || !search ? search : `?${search}` }));
  } catch {
    /* private mode: Back to Partners opens the partner's category list */
  }
}

/** The list this tab last showed, as an address (`/admin/partners/reseller?q=acme`),
 * or null. With `path`, only when it was that list. */
export function lastPartnerList(path?: string): string | null {
  try {
    const raw = JSON.parse(window.sessionStorage.getItem(LIST_KEY) ?? "null") as { path?: unknown; search?: unknown } | null;
    if (!raw || typeof raw.path !== "string" || !LIST_RE.test(raw.path)) return null;
    if (path && raw.path !== path) return null;
    const search = typeof raw.search === "string" && /^(\?[^#\s]{0,400})?$/.test(raw.search) ? raw.search : "";
    // Only the filters a list understands, so nothing else rides along.
    const params = Object.fromEntries(new URLSearchParams(search).entries());
    const base = categoryHref(null, params).replace(/^\/admin\/partners\/all/, raw.path);
    return base;
  } catch {
    return null;
  }
}

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
