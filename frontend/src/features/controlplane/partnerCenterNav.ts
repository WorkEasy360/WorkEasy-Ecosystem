/**
 * The Partners area's navigation (ADR-048): Dashboard, All Partners, Add
 * Partner and - since ADR-055 - Product Requests, the affiliates' requests to
 * promote a Store product (a queue across partners, so it has no one partner's
 * page to live on). Resellers, White-label and Affiliates are filters on
 * All Partners (the capabilities a partner holds); products, pricing,
 * commission, payouts, customers, branding and domains belong to one partner and
 * live on its page.
 *
 * Shared by the Super Admin sidebar (one "Partners" entry, shown when any
 * section is available) and the Partners area's own navigation. Navigation
 * convenience only: every page's data comes from an endpoint that re-checks the
 * permission on every request.
 */

export interface CenterItem {
  href: string;
  label: string;
  icon: string;
  /** Active only on an exact match (the dashboard, whose URL prefixes the others). */
  exact?: boolean;
  /** Other addresses that belong to this section (a partner's own pages). */
  alsoMatches?: RegExp;
  permission: string;
}

const PARTNER_VIEW = "platform.staff_partner.view";
const PARTNER_MANAGE = "platform.staff_partner.manage";

/** One partner's page and its Complete Your Profile belong to All Partners. */
const PARTNER_PAGE = /^\/admin\/partners\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\/|$)/i;

export const CENTER_ITEMS: CenterItem[] = [
  { href: "/admin/partners", label: "Dashboard", icon: "dashboard", exact: true, permission: PARTNER_VIEW },
  { href: "/admin/partners/all", label: "All Partners", icon: "handshake", alsoMatches: PARTNER_PAGE, permission: PARTNER_VIEW },
  { href: "/admin/partners/new", label: "Add Partner", icon: "plus", permission: PARTNER_MANAGE },
  { href: "/admin/partners/product-requests", label: "Product Requests", icon: "store", permission: PARTNER_VIEW },
];

const under = (pathname: string, prefix: string) => pathname === prefix || pathname.startsWith(`${prefix}/`);

export function isCurrent(pathname: string, item: CenterItem): boolean {
  if (item.exact) return pathname === item.href;
  return under(pathname, item.href) || Boolean(item.alsoMatches?.test(pathname));
}

/** The sections this staff member's grants open, in order. */
export function centerItems(permissions: string[]): CenterItem[] {
  return CENTER_ITEMS.filter((item) => permissions.includes(item.permission));
}
