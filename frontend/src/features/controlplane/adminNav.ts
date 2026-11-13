import type { SidebarGroup, SidebarItem } from "@/components/sidebar/SidebarShell";
import { adminModules, locate } from "./adminModules";
import { CATEGORIES, CATEGORY_MENU_LABEL, CATEGORY_PATH, type PartnerCategory } from "./partnerCategories";

/** Where this browser keeps the order the staff member gave the Partners menu. */
export const PARTNER_MENU_ORDER_KEY = "w360.admin.menu.partners";

const CATEGORY_ICON: Record<PartnerCategory, string> = {
  affiliate: "gift",
  reseller: "store",
  white_label: "palette",
};

/** Partners ▼ (ADR-062, the owner's brief of 2026-11-10): Affiliate, Reseller and
 * White Label - each the All Partners table with its type fixed - then All
 * Partners itself. The staff member may reorder them (drag, or Alt+Arrow keys);
 * the order is a preference of this browser and changes no partner's type. */
function partnerMenu(): SidebarItem[] {
  return [
    ...CATEGORIES.map((category) => ({
      href: CATEGORY_PATH[category],
      label: CATEGORY_MENU_LABEL[category],
      icon: CATEGORY_ICON[category],
      exact: true,
    })),
    { href: "/admin/partners/all", label: "All Partners", icon: "handshake", exact: true },
  ];
}

/** The Admin's sidebar (2026-11-09): the nine modules. A module opens its first
 * page this staff member may see; its other pages are tabs inside it (ModuleNav).
 * The module holding `pathname` is the current entry, by the map in
 * `adminModules.ts` rather than by the address's prefix.
 *
 * Partners alone opens beneath it (ADR-062) for a staff member who may see the
 * partner lists; its tabs are unchanged. */
export function adminNav(p: string[], pathname = ""): SidebarGroup[] {
  const modules = adminModules(p);
  const here = locate(pathname, modules);
  const items: SidebarItem[] = modules.map((module) => {
    const item: SidebarItem = {
      href: module.sections[0].pages[0].href,
      label: module.label,
      icon: module.icon,
      current: here?.module.key === module.key,
    };
    const lists = module.sections.some((section) => section.pages.some((page) => page.href === "/admin/partners/all"));
    if (module.key === "partners" && lists) {
      item.children = partnerMenu();
      item.reorderKey = PARTNER_MENU_ORDER_KEY;
    }
    return item;
  });
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
  "/admin/partners/affiliate": "Affiliate partners",
  "/admin/partners/reseller": "Reseller partners",
  "/admin/partners/white-label": "White Label partners",
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
  "/admin/customers/setup": "Setup",
  "/admin/partners/applications": "Applications",
  "/admin/partners/kyc-requirements": "KYC requirements",
  "/admin/partners/commission-rules": "Commission rules",
  "/admin/usage-pricing": "Usage rates",
  "/admin/usage-pricing/credit-packs": "Credit packs",
  "/admin/invoices": "Invoices",
  "/admin/payments/failed": "Failed & stuck",
  "/admin/refunds": "Refunds",
  "/admin/credit-notes": "Credit notes",
  "/admin/commissions": "Commission entries",
  "/admin/payouts": "Payouts",
  "/admin/wallets": "Platform Wallet",
  "/admin/wallets/ai-credits": "AI Credits",
  "/admin/wallets/consumption": "Consumption Wallet",
  "/admin/wallets/transactions": "Transaction Controls",
  "/admin/tickets": "Tickets",
  "/admin/help-content": "Help Content",
  "/admin/reports/sales": "Sales",
  "/admin/reports/payments": "Payments",
  "/admin/reports/renewals": "Subscription Renewals",
  "/admin/reports/wallet-movements": "Wallet Movements",
  "/admin/reports/usage": "Usage by Product",
  "/admin/reports/partner-conversions": "Partner Conversions",
  "/admin/reports/commissions": "Commissions",
  "/admin/reports/payouts": "Payouts",
  "/admin/settings/domains": "Domains & Apps",
  "/admin/settings/login-clients": "Login clients",
  "/admin/security/sign-ins": "Sign-in audit",
};
