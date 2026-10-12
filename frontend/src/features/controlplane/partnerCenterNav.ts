/**
 * The Partner Control Center's sections, and which grant each page needs.
 *
 * Shared by the Super Admin sidebar (one "Partners" entry, shown when any
 * section is available) and the Control Center's own navigation. Navigation
 * convenience only: every page's data comes from an endpoint that re-checks the
 * permission on every request.
 */

export interface CenterItem {
  href: string;
  label: string;
  icon: string;
  /** Active only on an exact match (the dashboard, whose URL prefixes the others). */
  exact?: boolean;
  /** Other URL prefixes that belong to this section (old addresses). */
  also?: string[];
  permission: string;
}

const PARTNER_VIEW = "platform.staff_partner.view";
const COMMISSION_VIEW = "platform.staff_commission.view";

export const CENTER_ITEMS: CenterItem[] = [
  { href: "/admin/partners", label: "Dashboard", icon: "dashboard", exact: true, permission: PARTNER_VIEW },
  { href: "/admin/partners/all", label: "All Partners", icon: "handshake", permission: PARTNER_VIEW },
  { href: "/admin/partners/resellers", label: "Resellers", icon: "store", permission: PARTNER_VIEW },
  { href: "/admin/partners/white-label", label: "White-label", icon: "palette", permission: PARTNER_VIEW },
  { href: "/admin/partners/affiliates", label: "Affiliates", icon: "gift", permission: PARTNER_VIEW },
  { href: "/admin/partners/customers", label: "Customers", icon: "building", permission: PARTNER_VIEW },
  { href: "/admin/partners/products", label: "Products & Pricing", icon: "tag", permission: PARTNER_VIEW },
  { href: "/admin/partners/commissions", label: "Commissions", icon: "currency", also: ["/admin/commissions"], permission: COMMISSION_VIEW },
  { href: "/admin/partners/payouts", label: "Payouts", icon: "receipt", also: ["/admin/payouts"], permission: COMMISSION_VIEW },
];

const under = (pathname: string, prefix: string) => pathname === prefix || pathname.startsWith(`${prefix}/`);

export function isCurrent(pathname: string, item: CenterItem): boolean {
  if (item.exact) return pathname === item.href;
  return [item.href, ...(item.also ?? [])].some((prefix) => under(pathname, prefix));
}

/** The sections this staff member's grants open, in order. */
export function centerItems(permissions: string[]): CenterItem[] {
  return CENTER_ITEMS.filter((item) => permissions.includes(item.permission));
}
