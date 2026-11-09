import type { SidebarGroup, SidebarItem } from "@/components/sidebar/SidebarShell";
import { adminModules, locate } from "./adminModules";

/** The Admin's sidebar (2026-11-09): only the nine modules, nothing beneath them.
 * A module opens its first page this staff member may see; its other pages are
 * tabs inside it (ModuleNav). The module holding `pathname` is the current entry,
 * by the map in `adminModules.ts` rather than by the address's prefix. */
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
  "/admin/entitlements": "Entitlements",
  "/admin/customers": "Customers",
  "/admin/partners": "Partner overview",
  "/admin/partners/all": "All partners",
  "/admin/partners/new": "New partner",
  "/admin/partners/product-requests": "Product requests",
  "/admin/partners/upgrade-fees": "Upgrade fees",
  "/admin/partners/tickets": "Partner tickets",
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
  "/admin/security": "Staff access",
  "/admin/audit": "Audit log",
  "/admin/diagnostics": "Diagnostics",
  "/admin/settings": "Platform settings",
};
