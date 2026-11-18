/**
 * The Admin console's map. This one list feeds the sidebar (modules only), the one tab
 * row inside a module, the command palette, the breadcrumbs and the permission check
 * on each link. Addresses do not follow the hierarchy on purpose: links in emails
 * already sent, and every record's own page, keep working.
 *
 * 2026-11-18 (admin-ux phase 1): two levels at most - a sidebar entry, then ONE row of
 * tabs. A tab is a section; its first page this staff member may open is the tab
 * itself, and its other pages open from buttons in that page's header ("KYC
 * requirements" on Applications), with a breadcrumb back. Settings draws its sections
 * as a menu down the left instead (`layout: "menu"`).
 *
 * Navigation convenience only: every request behind a link is authorized again by
 * the backend, whatever this list says.
 */

export interface ModulePage {
  href: string;
  label: string;
  /** The grant this page needs, or any one of several. Omit for every staff member. */
  needs?: string | string[];
  /** Only on this exact address (an overview whose address prefixes others). */
  exact?: boolean;
  /** Record pages that belong to this page although no prefix describes them. */
  alsoMatch?: RegExp;
  /** Other addresses that show this same page rather than a record (the list with
   * its Add dialog open): the tab row stays and the page is not a record's. */
  views?: string[];
  /** A count beside the tab, from the notifications the console already reads. */
  badge?: "failed_payments";
}

export interface ModuleSection {
  label: string;
  pages: ModulePage[];
}

/** A way into a page with a filter already set, for the command palette only. */
export interface ModuleShortcut {
  href: string;
  label: string;
  needs?: string | string[];
}

export interface AdminModule {
  key: string;
  label: string;
  icon: string;
  /** "tabs" (the default): one row of tabs above the page; "menu": a menu down the left. */
  layout?: "tabs" | "menu";
  sections: ModuleSection[];
  shortcuts?: ModuleShortcut[];
}

const CUSTOMER_VIEW = "platform.staff_customer.view";
const USER_VIEW = "platform.staff_user.view";
const PARTNER_VIEW = "platform.staff_partner.view";
const CATALOG_VIEW = "platform.staff_catalog.view";
const PAYMENT_VIEW = "platform.staff_payment.view";
const PAYOUT_MANAGE = "platform.staff_payout.manage";
const COMMISSION_VIEW = "platform.staff_commission.view";
const KYC_VIEW = "platform.staff_kyc.view";
const WALLET_VIEW = "platform.staff_wallet.view";
const SUPPORT_VIEW = "platform.staff_support.view";
const REPORT_VIEW = "platform.staff_report.view";

/** One partner's page and its Complete Your Profile belong to the Partners list. */
const PARTNER_PAGE = /^\/admin\/partners\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\/|$)/i;

/** Reports: one page, the report chosen on it (`?report=`). The groups are the
 * selector's, in the brief's order. */
export const REPORT_GROUPS: { label: string; reports: { kind: string; label: string }[] }[] = [
  {
    label: "Money",
    reports: [
      { kind: "sales", label: "Sales" },
      { kind: "payments", label: "Payments" },
      { kind: "renewals", label: "Subscription Renewals" },
      { kind: "wallet-movements", label: "Wallet Movements" },
    ],
  },
  {
    label: "Partners",
    reports: [
      { kind: "partner-conversions", label: "Partner Conversions" },
      { kind: "commissions", label: "Commissions" },
      { kind: "payouts", label: "Payouts" },
    ],
  },
  { label: "Usage", reports: [{ kind: "usage", label: "Usage by Product" }] },
];

export const ADMIN_MODULES: AdminModule[] = [
  {
    key: "dashboard",
    label: "Dashboard",
    icon: "dashboard",
    sections: [{ label: "Dashboard", pages: [{ href: "/admin", label: "Dashboard", exact: true }] }],
  },
  {
    key: "customers",
    label: "Customers",
    icon: "users",
    sections: [
      { label: "Organizations", pages: [{ href: "/admin/customers", label: "Organizations", needs: CUSTOMER_VIEW }] },
      { label: "Users", pages: [{ href: "/admin/users", label: "Users", needs: USER_VIEW }] },
      { label: "Product setup", pages: [{ href: "/admin/customers/setup", label: "Product setup", needs: CUSTOMER_VIEW }] },
      { label: "Product access", pages: [{ href: "/admin/entitlements", label: "Product access", needs: CUSTOMER_VIEW }] },
    ],
  },
  {
    key: "partners",
    label: "Partners",
    icon: "handshake",
    sections: [
      { label: "Overview", pages: [{ href: "/admin/partners", label: "Overview", needs: PARTNER_VIEW, exact: true }] },
      {
        label: "Partners",
        pages: [{ href: "/admin/partners/all", label: "Partners", needs: PARTNER_VIEW, alsoMatch: PARTNER_PAGE, views: ["/admin/partners/new"] }],
      },
      {
        label: "Applications",
        pages: [
          { href: "/admin/partners/applications", label: "Applications", needs: KYC_VIEW },
          { href: "/admin/partners/kyc-requirements", label: "KYC requirements", needs: KYC_VIEW },
        ],
      },
      {
        // Rules and the entries they produced; the entries keep Billing's grant.
        label: "Commissions",
        pages: [
          { href: "/admin/partners/commission-rules", label: "Commission rules", needs: COMMISSION_VIEW },
          { href: "/admin/commissions", label: "Commission entries", needs: COMMISSION_VIEW },
        ],
      },
      {
        // Moved from Billing with their own grants (Withdrawals: payout management).
        label: "Payouts",
        pages: [
          { href: "/admin/payouts", label: "Payouts", needs: COMMISSION_VIEW },
          { href: "/admin/partners/withdrawals", label: "Withdrawals", needs: PAYOUT_MANAGE },
        ],
      },
      {
        label: "Requests",
        pages: [
          { href: "/admin/partners/product-requests", label: "Product requests", needs: PARTNER_VIEW },
          { href: "/admin/partners/upgrade-fees", label: "Upgrade fees", needs: PARTNER_VIEW },
        ],
      },
    ],
    shortcuts: [
      { href: "/admin/partners/all?type=affiliate", label: "Affiliate partners", needs: PARTNER_VIEW },
      { href: "/admin/partners/all?type=reseller", label: "Reseller partners", needs: PARTNER_VIEW },
      { href: "/admin/partners/all?type=white_label", label: "White-label partners", needs: PARTNER_VIEW },
    ],
  },
  {
    key: "products",
    label: "Products & Pricing",
    icon: "box",
    sections: [
      { label: "Products", pages: [{ href: "/admin/products", label: "Products", needs: CATALOG_VIEW, views: ["/admin/products/new"] }] },
      {
        label: "Plans",
        pages: [
          { href: "/admin/plans", label: "Plans & add-ons", needs: CATALOG_VIEW },
          { href: "/admin/pricing", label: "Currency & pricing", needs: CATALOG_VIEW },
        ],
      },
      {
        label: "Usage pricing",
        pages: [
          { href: "/admin/usage-pricing", label: "Usage rates", needs: CATALOG_VIEW },
          { href: "/admin/usage-pricing/credit-packs", label: "Credit packs", needs: CATALOG_VIEW },
        ],
      },
      {
        label: "Store",
        pages: [
          { href: "/admin/store/dashboard", label: "Store overview", needs: CATALOG_VIEW },
          { href: "/admin/store", label: "Customer preview", needs: CATALOG_VIEW, exact: true },
          { href: "/admin/store/commercial-rules", label: "Commercial rules", needs: CATALOG_VIEW },
          { href: "/admin/store/coupons", label: "Coupons", needs: CATALOG_VIEW },
        ],
      },
    ],
  },
  {
    key: "billing",
    label: "Billing",
    icon: "card",
    sections: [
      { label: "Needs attention", pages: [{ href: "/admin/payments/failed", label: "Needs attention", needs: PAYMENT_VIEW, badge: "failed_payments" }] },
      { label: "Orders", pages: [{ href: "/admin/orders", label: "Orders", needs: PAYMENT_VIEW }] },
      { label: "Subscriptions", pages: [{ href: "/admin/subscriptions", label: "Subscriptions", needs: CUSTOMER_VIEW }] },
      { label: "Invoices", pages: [{ href: "/admin/invoices", label: "Invoices", needs: PAYMENT_VIEW }] },
      { label: "Payments", pages: [{ href: "/admin/payments", label: "Payments", needs: PAYMENT_VIEW }] },
      { label: "Refunds", pages: [{ href: "/admin/refunds", label: "Refunds", needs: PAYMENT_VIEW }] },
      { label: "Credit notes", pages: [{ href: "/admin/credit-notes", label: "Credit notes", needs: PAYMENT_VIEW }] },
    ],
  },
  {
    key: "wallets",
    label: "Wallets & Credits",
    icon: "wallet",
    sections: [
      { label: "Platform wallet", pages: [{ href: "/admin/wallets", label: "Platform wallet", needs: WALLET_VIEW, exact: true }] },
      { label: "Consumption wallet", pages: [{ href: "/admin/wallets/consumption", label: "Consumption wallet", needs: WALLET_VIEW }] },
      { label: "AI credits", pages: [{ href: "/admin/wallets/ai-credits", label: "AI credits", needs: WALLET_VIEW }] },
      { label: "Controls", pages: [{ href: "/admin/wallets/transactions", label: "Controls", needs: WALLET_VIEW }] },
    ],
  },
  {
    key: "support",
    label: "Support",
    icon: "help",
    sections: [
      // Partner tickets are here too (Who asked: Partners), so either grant opens it:
      // the screen reads whichever list the staff member's grant covers.
      { label: "Tickets", pages: [{ href: "/admin/tickets", label: "Tickets", needs: [SUPPORT_VIEW, PARTNER_VIEW] }] },
      { label: "Diagnostics", pages: [{ href: "/admin/diagnostics", label: "Diagnostics", needs: SUPPORT_VIEW }] },
      { label: "Help articles", pages: [{ href: "/admin/help-content", label: "Help articles", needs: SUPPORT_VIEW }] },
    ],
    shortcuts: [{ href: "/admin/tickets?audience=partner", label: "Partner tickets", needs: [SUPPORT_VIEW, PARTNER_VIEW] }],
  },
  {
    key: "reports",
    label: "Reports",
    icon: "chart",
    sections: [{ label: "Reports", pages: [{ href: "/admin/reports", label: "Reports", needs: REPORT_VIEW }] }],
    shortcuts: REPORT_GROUPS.flatMap((group) =>
      group.reports.map((r) => ({ href: `/admin/reports?report=${r.kind}`, label: `${r.label} report`, needs: REPORT_VIEW })),
    ),
  },
  {
    key: "settings",
    label: "Settings",
    icon: "settings",
    layout: "menu",
    sections: [
      { label: "Platform settings", pages: [{ href: "/admin/settings", label: "Platform settings", needs: "platform.staff_settings.view", exact: true }] },
      { label: "Email templates", pages: [{ href: "/admin/communication", label: "Email templates", needs: "platform.staff_communication.view" }] },
      { label: "AI platform", pages: [{ href: "/admin/ai", label: "AI platform", needs: "platform.staff_ai.view" }] },
      { label: "Domains & apps", pages: [{ href: "/admin/settings/domains", label: "Domains & apps", needs: "platform.staff_settings.view" }] },
      { label: "Team & roles", pages: [{ href: "/admin/security", label: "Team & roles", needs: "platform.staff_security.view", exact: true }] },
      {
        label: "Security",
        pages: [
          { href: "/admin/settings/login-clients", label: "Connected apps", needs: ["platform.staff_settings.view", "platform.staff_integration.view"] },
          { href: "/admin/security/sign-ins", label: "Sign-in audit", needs: "platform.staff_audit.view" },
          { href: "/admin/audit", label: "Audit log", needs: "platform.staff_audit.view" },
          // 2026-11-10: restricted KYC storage - who opened which document, across all applications.
          { href: "/admin/settings/kyc-access", label: "KYC document access", needs: "platform.staff_kyc.review" },
        ],
      },
    ],
  },
];

function allowed(page: { needs?: string | string[] }, permissions: string[]): boolean {
  if (page.needs === undefined) return true;
  const keys = Array.isArray(page.needs) ? page.needs : [page.needs];
  return keys.some((key) => permissions.includes(key));
}

/** The console as this staff member's grants open it: a section with none of its
 * pages left is not shown, and neither is a module with no sections left. */
export function adminModules(permissions: string[]): AdminModule[] {
  return ADMIN_MODULES.map((module) => ({
    ...module,
    sections: module.sections
      .map((section) => ({ ...section, pages: section.pages.filter((page) => allowed(page, permissions)) }))
      .filter((section) => section.pages.length > 0),
    shortcuts: module.shortcuts?.filter((shortcut) => allowed(shortcut, permissions)),
  })).filter((module) => module.sections.length > 0);
}

export interface Location {
  module: AdminModule;
  section: ModuleSection;
  page: ModulePage;
  /** A record's own page (a customer, a ticket) rather than the page itself: it
   * draws no tab row - its breadcrumb leads back. */
  detail: boolean;
}

/** Where an address sits. The longest matching address wins, so a page beneath
 * another is its own page, not part of the one above it. */
export function locate(pathname: string, modules: AdminModule[]): Location | null {
  const path = pathname.replace(/\/$/, "") || "/admin";
  let best: { location: Location; weight: number } | null = null;
  for (const mod of modules) {
    for (const section of mod.sections) {
      for (const page of section.pages) {
        const view = page.views?.includes(path) ?? false;
        const matches = page.exact
          ? path === page.href || view
          : path === page.href || view || path.startsWith(`${page.href}/`) || Boolean(page.alsoMatch?.test(path));
        // A view's own address is as long a match as the page's.
        const weight = view ? path.length : page.href.length;
        if (matches && (!best || weight > best.weight)) {
          best = { location: { module: mod, section, page, detail: path !== page.href && !view }, weight };
        }
      }
    }
  }
  return best?.location ?? null;
}

export interface Crumb {
  label: string;
  href?: string;
}

/** The breadcrumb to a page: module › tab, then the page when it is not the tab's
 * own (KYC requirements › under Applications). A record's page adds its own name. */
export function trailOf(location: Location): Crumb[] {
  const { module, section, page } = location;
  const crumbs: Crumb[] = [{ label: module.label, href: module.sections[0].pages[0].href }];
  if (module.sections.length === 1 && section.pages.length === 1) return crumbs;
  const menu = module.layout === "menu";
  const main = section.pages[0];
  // A menu's group of several pages ("Security") is a heading there, not a page.
  const grouped = menu && section.pages.length > 1;
  crumbs.push({ label: section.label, href: grouped ? undefined : main.href });
  if (page.href !== main.href || grouped) crumbs.push({ label: page.label, href: page.href });
  return crumbs;
}

/** The tab's other pages, opened from its main page's header. None on a record's
 * page, on the other pages themselves, or in a menu (it lists them all). */
export function relatedOf(location: Location): ModulePage[] {
  const { module, section, page, detail } = location;
  if (detail || module.layout === "menu" || section.pages[0].href !== page.href) return [];
  return section.pages.slice(1);
}
