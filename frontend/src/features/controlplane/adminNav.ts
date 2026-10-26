import type { SidebarGroup, SidebarItem } from "@/components/sidebar/SidebarShell";
import { centerItems } from "./partnerCenterNav";

const can = (permissions: string[], key: string) => permissions.includes(key);

/** The Super Admin's navigation (2026-10-26 redesign): Dashboard, then one
 * collapsible section per area - User, Partner, Product Store, Communication,
 * Staff, Settings. A section opens by itself on any of its pages. Each page
 * appears only when the staff member holds the grant it needs, and a section
 * with none of its pages left is not shown. Navigation convenience only: every
 * request is authorized again by the backend. */
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
        section("user", "User", "users", [
          { href: "/admin/users", label: "Users", when: can(p, "platform.staff_user.view") },
          { href: "/admin/subscriptions", label: "Subscriptions", when: can(p, "platform.staff_customer.view") },
          { href: "/admin/entitlements", label: "Entitlements", when: can(p, "platform.staff_customer.view") },
        ]),
        // The Partners area's own sections (ADR-048): its dashboard, All Partners
        // (a partner's own pages belong to it) and Add Partner.
        section(
          "partner",
          "Partner",
          "handshake",
          centerItems(p).map((item) => ({
            href: item.href,
            label: item.href === "/admin/partners" ? "Partner dashboard" : item.label,
            exact: item.exact,
            alsoMatchPattern: item.alsoMatches,
          })),
        ),
        section("store", "Product Store", "store", [
          { href: "/admin/products", label: "Product manager", when: can(p, "platform.staff_catalog.view") },
          { href: "/admin/plans", label: "Plans & add-ons", when: can(p, "platform.staff_catalog.view") },
          { href: "/admin/pricing", label: "Currency & pricing", when: can(p, "platform.staff_catalog.view") },
          { href: "/admin/orders", label: "Orders", when: can(p, "platform.staff_payment.view") },
          { href: "/admin/payments", label: "Payments", when: can(p, "platform.staff_payment.view") },
        ]),
        section("communication", "Communication", "mail", [
          {
            href: "/admin/communication",
            label: "Email & templates",
            when: can(p, "platform.staff_communication.view"),
          },
          { href: "/admin/ai", label: "AI", when: can(p, "platform.staff_ai.view") },
        ]),
        section("staff", "Staff", "shield", [
          { href: "/admin/security", label: "Staff access", when: can(p, "platform.staff_security.view") },
          { href: "/admin/audit", label: "Audit", when: can(p, "platform.staff_audit.view") },
          { href: "/admin/diagnostics", label: "Diagnostics", when: can(p, "platform.staff_support.view") },
        ]),
        section("settings", "Settings", "settings", [
          { href: "/admin/settings", label: "Platform settings", when: can(p, "platform.staff_settings.view") },
        ]),
      ],
    },
  ];
}
