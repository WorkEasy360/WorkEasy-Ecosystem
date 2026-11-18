/**
 * Affiliate, Reseller, White Label (ADR-062): the Admin's three partner
 * categories. The server decides which partners a category lists (each partner
 * under exactly one - the type its row shows); this file only names them.
 *
 * 2026-11-18 (admin-ux phase 1): they are the type chips on Partners › Partners,
 * `/admin/partners/all?type=<category>`. Their old pages (`CATEGORY_PATH`) open
 * that list with the chip chosen - and stay the addresses a guest session may
 * return to, since the server accepts a path with no query there.
 */

import { isDay } from "./kit/DateRangeFilter";

export type PartnerCategory = "affiliate" | "reseller" | "white_label";

export const CATEGORIES: readonly PartnerCategory[] = ["affiliate", "reseller", "white_label"];

/** The old type pages (ADR-062), now redirects to the list with the chip chosen. */
export const CATEGORY_PATH: Record<PartnerCategory, string> = {
  affiliate: "/admin/partners/affiliate",
  reseller: "/admin/partners/reseller",
  white_label: "/admin/partners/white-label",
};

/** The owner's words for them (2026-11-10), on the chips and the guest banner. */
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
const KEEP = ["q", "status", "profile", "kyc", "from", "to"] as const;
const VALUES: Partial<Record<(typeof KEEP)[number], Set<string>>> = {
  status: new Set(["draft", "active", "suspended", "terminated"]),
  profile: new Set(["complete", "incomplete"]),
  kyc: new Set(["ready", "approved", "not_approved", "legacy"]),
};
/** The created-date range (admin-ux phase 3): whole days, `YYYY-MM-DD`. */
const DAYS = new Set<string>(["from", "to"]);

// --- the list this tab last showed ---------------------------------------------------
//
// So "Back to Partners" on a partner's page, and a guest session's return, open the
// list the Admin came from with its type, search and filters. Per tab
// (sessionStorage), an address only - never a partner's data - and read back only
// when it is the list (or, saved before 2026-11-18, one of the old type pages).

const LIST_KEY = "w360.admin.partners.list";
const LIST_PATH = "/admin/partners/all";
const OLD_LIST_RE = /^\/admin\/partners\/(all|affiliate|reseller|white-label)$/;

const categoryOfPath = (path: string): PartnerCategory | null =>
  (Object.entries(CATEGORY_PATH).find(([, p]) => p === path)?.[0] as PartnerCategory | undefined) ?? null;

/** Remember the list on screen (`pathname` + `search`). */
export function rememberPartnerList(pathname: string, search: string): void {
  if (pathname !== LIST_PATH) return;
  try {
    window.sessionStorage.setItem(LIST_KEY, JSON.stringify({ path: pathname, search: search.startsWith("?") || !search ? search : `?${search}` }));
  } catch {
    /* private mode: Back to Partners opens the partner's category */
  }
}

/** The list this tab last showed, as an address (`/admin/partners/all?type=reseller&q=acme`),
 * or null. With `path`, only when it was that list. */
export function lastPartnerList(path?: string): string | null {
  try {
    const raw = JSON.parse(window.sessionStorage.getItem(LIST_KEY) ?? "null") as { path?: unknown; search?: unknown } | null;
    if (!raw || typeof raw.path !== "string" || !OLD_LIST_RE.test(raw.path)) return null;
    if (path && path !== LIST_PATH && raw.path !== path) return null;
    const search = typeof raw.search === "string" && /^(\?[^#\s]{0,400})?$/.test(raw.search) ? raw.search : "";
    // Only the filters a list understands, so nothing else rides along.
    const params = Object.fromEntries(new URLSearchParams(search).entries());
    const type = categoryOfPath(raw.path) ?? (isCategory(params.type) ? params.type : null);
    return categoryHref(type, params);
  } catch {
    return null;
  }
}

/** The Partners list with a category's chip chosen (or All for `null`), keeping the
 * filters of `params` it understands. Always a fixed path. */
export function categoryHref(category: PartnerCategory | null, params: Params = {}): string {
  const out = new URLSearchParams();
  if (category) out.set("type", category);
  for (const key of KEEP) {
    const raw = params[key];
    const value = Array.isArray(raw) ? raw[0] : raw;
    if (!value) continue;
    const allowed = VALUES[key];
    if (allowed && !allowed.has(value)) continue;
    if (DAYS.has(key) && !isDay(value)) continue;
    out.set(key, value.slice(0, 80));
  }
  const text = out.toString();
  return text ? `${LIST_PATH}?${text}` : LIST_PATH;
}

/** Where an old type page sends its visitor: the list with that chip chosen and its
 * filters - and, back from a guest session, how it ended (`?guest=`), so the list
 * restores its search and filters or says why the guest view did not open. */
export function typePageHref(category: PartnerCategory, params: Params = {}): string {
  const target = categoryHref(category, params);
  const raw = Array.isArray(params.guest) ? params.guest[0] : params.guest;
  if (raw !== "ended" && raw !== "expired" && raw !== "denied") return target;
  return `${target}&guest=${raw}`;
}
