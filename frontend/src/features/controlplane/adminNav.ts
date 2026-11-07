import type { SidebarGroup, SidebarItem } from "@/components/sidebar/SidebarShell";
import { centerItems } from "./partnerCenterNav";

const can = (permissions: string[], key: string) => permissions.includes(key);

/** The Super Admin's navigation (2026-11-07 design system): Dashboard, then
 * one collapsible section per area, named in the plural - Users, Partners,
 * Store, Communication, Staff, Settings. Only the current page's section is
 * open. A page's menu label is its title, its breadcrumb and its browser-tab
 * title. Each page appears only when the staff member holds the grant it
 * needs, and a section with none of its pages left is not shown. Navigation
 * convenience only: every request is authorized again by the backend. */
export function adminNav(p: string[]): SidebarGroup[] {
  const section = (key: string, label: string, icon: string, children: SidebarItem[]): SidebarItem => ({
    href: `#${key}`,
    label,
    icon,
    children,
  });
  return [
    {
      items: [
        { href: "/admin", label: "Dashboard", icon: "dashboard", exact: true },
        section("users", "Users", "users", [
          { href: "/admin/users", label: "Users", when: can(p, "platform.staff_user.view") },
          { href: "/admin/subscriptions", label: "Subscriptions", when: can(p, "platform.staff_customer.view") },
          { href: "/admin/entitlements", label: "Entitlements", when: can(p, "platform.staff_customer.view") },
        ]),
        // The Partners area's own pages (ADR-048). "New partner" is not a menu
        // entry: it is the All partners page's main action and in the New menu.
        section(
          "partners",
          "Partners",
          "handshake",
          centerItems(p)
            .filter((item) => item.href !== "/admin/partners/new")
            .map((item) => ({
              href: item.href,
              label: item.label,
              exact: item.exact,
              alsoMatchPattern: item.alsoMatches,
              alsoMatch: item.href === "/admin/partners/all" ? ["/admin/partners/new"] : undefined,
            })),
        ),
        section("store", "Store", "store", [
          { href: "/admin/products", label: "Products", when: can(p, "platform.staff_catalog.view") },
          { href: "/admin/store", label: "Store", when: can(p, "platform.staff_catalog.view") },
          { href: "/admin/plans", label: "Plans & add-ons", when: can(p, "platform.staff_catalog.view") },
          { href: "/admin/pricing", label: "Currency & pricing", when: can(p, "platform.staff_catalog.view") },
          { href: "/admin/orders", label: "Orders", when: can(p, "platform.staff_payment.view") },
          { href: "/admin/payments", label: "Payments", when: can(p, "platform.staff_payment.view") },
        ]),
        section("communication", "Communication", "mail", [
          {
            href: "/admin/communication",
            label: "Email templates",
            when: can(p, "platform.staff_communication.view"),
          },
          { href: "/admin/ai", label: "AI platform", when: can(p, "platform.staff_ai.view") },
        ]),
        section("staff", "Staff", "shield", [
          { href: "/admin/security", label: "Staff access", when: can(p, "platform.staff_security.view") },
          { href: "/admin/audit", label: "Audit log", when: can(p, "platform.staff_audit.view") },
        ]),
        section("settings", "Settings", "settings", [
          { href: "/admin/settings", label: "Platform settings", when: can(p, "platform.staff_settings.view") },
          { href: "/admin/diagnostics", label: "Diagnostics", when: can(p, "platform.staff_support.view") },
        ]),
      ],
    },
  ];
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
