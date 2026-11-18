import type { SidebarGroup, SidebarItem } from "@/components/sidebar/SidebarShell";
import { adminModules, locate } from "./adminModules";

/** The Admin's sidebar (2026-11-18, admin-ux phase 1): the nine modules, with no
 * submenus. A module opens its first page this staff member may see; its other
 * pages are its tabs (ModuleNav). The module holding `pathname` is the current
 * entry, by the map in `adminModules.ts` rather than by the address's prefix.
 *
 * Partners no longer opens beneath it: Affiliate, Reseller and White Label are the
 * type chips on Partners › Partners (`/admin/partners/all?type=`). */
export function adminNav(p: string[], pathname = ""): SidebarGroup[] {
  const modules = adminModules(p);
  const here = locate(pathname, modules);
  const items: SidebarItem[] = modules.map((module) => ({
    href: module.sections[0].pages[0].href,
    label: module.label,
    icon: module.icon,
    current: here?.module.key === module.key,
  }));
  return [{ items }];
}

/** Each page's title (= its menu label), for the browser tab: "Users · WorkEasy360 Admin".
 * A record's own page (a user, a partner) sets its own from its name. */
export const ADMIN_PAGE_TITLES: Record<string, string> = {
  "/admin": "Dashboard",
  "/admin/notifications": "Notifications",
  "/admin/users": "Users",
  "/admin/subscriptions": "Subscriptions",
  "/admin/entitlements": "Product access",
  "/admin/customers": "Organizations",
  "/admin/partners": "Partner overview",
  "/admin/partners/all": "Partners",
  "/admin/partners/new": "New partner",
  "/admin/partners/product-requests": "Product requests",
  "/admin/partners/upgrade-fees": "Upgrade fees",
  "/admin/partners/withdrawals": "Withdrawals",
  "/admin/products": "Products",
  "/admin/products/new": "New product",
  "/admin/store": "Store",
  "/admin/store/dashboard": "Store",
  "/admin/store/commercial-rules": "Store",
  "/admin/store/coupons": "Store",
  "/admin/plans": "Plans & add-ons",
  "/admin/pricing": "Currency & pricing",
  "/admin/orders": "Orders",
  "/admin/payments": "Payments",
  "/admin/communication": "Email templates",
  "/admin/ai": "AI platform",
  "/admin/security": "Team & roles",
  "/admin/audit": "Audit log",
  "/admin/diagnostics": "Diagnostics",
  "/admin/settings": "Platform settings",
  "/admin/customers/setup": "Product setup",
  "/admin/partners/applications": "Applications",
  "/admin/partners/kyc-requirements": "KYC requirements",
  "/admin/partners/commission-rules": "Commission rules",
  "/admin/usage-pricing": "Usage rates",
  "/admin/usage-pricing/credit-packs": "Credit packs",
  "/admin/invoices": "Invoices",
  "/admin/payments/failed": "Needs attention",
  "/admin/refunds": "Refunds",
  "/admin/credit-notes": "Credit notes",
  "/admin/commissions": "Commission entries",
  "/admin/payouts": "Payouts",
  "/admin/wallets": "Platform wallet",
  "/admin/wallets/ai-credits": "AI credits",
  "/admin/wallets/consumption": "Consumption wallet",
  "/admin/wallets/transactions": "Controls",
  "/admin/tickets": "Tickets",
  "/admin/help-content": "Help articles",
  "/admin/reports": "Reports",
  "/admin/settings/domains": "Domains & apps",
  "/admin/settings/login-clients": "Connected apps",
  "/admin/settings/kyc-access": "KYC document access",
  "/admin/security/sign-ins": "Sign-in audit",
};
