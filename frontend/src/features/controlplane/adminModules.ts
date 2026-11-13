/**
 * The Admin console's map (2026-11-09): nine modules, each made of sections, each
 * section of pages. This one list feeds the sidebar (modules only), the tabs
 * inside a module (sections and pages), the command palette and the permission
 * check on each link. Addresses do not follow the hierarchy on purpose: links in
 * emails already sent, and every record's own page, keep working.
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
  /** Addresses that belong to this page although no prefix describes them. */
  alsoMatch?: RegExp;
}

export interface ModuleSection {
  label: string;
  pages: ModulePage[];
}

export interface AdminModule {
  key: string;
  label: string;
  icon: string;
  sections: ModuleSection[];
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

/** One partner's page and its Complete Your Profile belong to All partners - and so
 * do the Affiliate, Reseller and White Label pages (ADR-062), which are All partners
 * with its type fixed: the module's tabs stay exactly as they are. */
const PARTNER_PAGE =
  /^\/admin\/partners\/((affiliate|reseller|white-label)$|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\/|$))/i;

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
      {
        label: "Organizations & Users",
        pages: [
          { href: "/admin/customers", label: "Organizations", needs: CUSTOMER_VIEW },
          { href: "/admin/users", label: "Users", needs: USER_VIEW },
        ],
      },
      {
        label: "Products & Setup",
        pages: [
          { href: "/admin/customers/setup", label: "Setup", needs: CUSTOMER_VIEW },
          { href: "/admin/entitlements", label: "Entitlements", needs: CUSTOMER_VIEW },
        ],
      },
    ],
  },
  {
    key: "partners",
    label: "Partners",
    icon: "handshake",
    sections: [
      {
        label: "Applications & KYC",
        pages: [
          { href: "/admin/partners/applications", label: "Applications", needs: KYC_VIEW },
          { href: "/admin/partners/kyc-requirements", label: "KYC requirements", needs: KYC_VIEW },
        ],
      },
      {
        label: "Approved Partners",
        pages: [
          { href: "/admin/partners/all", label: "All partners", needs: PARTNER_VIEW, alsoMatch: PARTNER_PAGE },
          { href: "/admin/partners", label: "Partner overview", needs: PARTNER_VIEW, exact: true },
          { href: "/admin/partners/product-requests", label: "Product requests", needs: PARTNER_VIEW },
          { href: "/admin/partners/upgrade-fees", label: "Upgrade fees", needs: PARTNER_VIEW },
        ],
      },
      {
        label: "Commission Rules",
        pages: [{ href: "/admin/partners/commission-rules", label: "Commission rules", needs: COMMISSION_VIEW }],
      },
    ],
  },
  {
    key: "products",
    label: "Products & Pricing",
    icon: "box",
    sections: [
      {
        label: "Product Catalog",
        pages: [
          { href: "/admin/products", label: "Products", needs: CATALOG_VIEW },
          { href: "/admin/store", label: "Store", needs: CATALOG_VIEW },
        ],
      },
      {
        label: "Plans",
        pages: [
          { href: "/admin/plans", label: "Plans & add-ons", needs: CATALOG_VIEW },
          { href: "/admin/pricing", label: "Currency & pricing", needs: CATALOG_VIEW },
        ],
      },
      {
        label: "Usage Pricing",
        pages: [
          { href: "/admin/usage-pricing", label: "Usage rates", needs: CATALOG_VIEW },
          { href: "/admin/usage-pricing/credit-packs", label: "Credit packs", needs: CATALOG_VIEW },
        ],
      },
    ],
  },
  {
    key: "billing",
    label: "Billing & Subscriptions",
    icon: "card",
    sections: [
      {
        label: "Orders & Payments",
        pages: [
          { href: "/admin/orders", label: "Orders", needs: PAYMENT_VIEW },
          { href: "/admin/invoices", label: "Invoices", needs: PAYMENT_VIEW },
          { href: "/admin/payments", label: "Payments", needs: PAYMENT_VIEW },
          { href: "/admin/payments/failed", label: "Failed & stuck", needs: PAYMENT_VIEW },
          { href: "/admin/refunds", label: "Refunds", needs: PAYMENT_VIEW },
          { href: "/admin/credit-notes", label: "Credit notes", needs: PAYMENT_VIEW },
        ],
      },
      {
        label: "Subscriptions",
        pages: [{ href: "/admin/subscriptions", label: "Subscriptions", needs: CUSTOMER_VIEW }],
      },
      {
        label: "Partner Earnings & Payouts",
        pages: [
          { href: "/admin/commissions", label: "Commission entries", needs: COMMISSION_VIEW },
          { href: "/admin/payouts", label: "Payouts", needs: COMMISSION_VIEW },
          { href: "/admin/partners/withdrawals", label: "Withdrawals", needs: PAYOUT_MANAGE },
        ],
      },
    ],
  },
  {
    key: "wallets",
    label: "Wallets & Credits",
    icon: "wallet",
    sections: [
      { label: "Platform Wallet", pages: [{ href: "/admin/wallets", label: "Platform Wallet", needs: WALLET_VIEW, exact: true }] },
      { label: "AI Credits", pages: [{ href: "/admin/wallets/ai-credits", label: "AI Credits", needs: WALLET_VIEW }] },
      { label: "Consumption Wallet", pages: [{ href: "/admin/wallets/consumption", label: "Consumption Wallet", needs: WALLET_VIEW }] },
      { label: "Transaction Controls", pages: [{ href: "/admin/wallets/transactions", label: "Transaction Controls", needs: WALLET_VIEW }] },
    ],
  },
  {
    key: "support",
    label: "Support",
    icon: "help",
    sections: [
      {
        label: "Tickets",
        pages: [
          { href: "/admin/tickets", label: "Tickets", needs: SUPPORT_VIEW },
          { href: "/admin/partners/tickets", label: "Partner tickets", needs: PARTNER_VIEW },
          { href: "/admin/diagnostics", label: "Diagnostics", needs: SUPPORT_VIEW },
        ],
      },
      { label: "Help Content", pages: [{ href: "/admin/help-content", label: "Help Content", needs: SUPPORT_VIEW }] },
    ],
  },
  {
    key: "reports",
    label: "Reports",
    icon: "chart",
    sections: [
      {
        label: "Reports",
        pages: [
          { href: "/admin/reports/sales", label: "Sales", needs: REPORT_VIEW },
          { href: "/admin/reports/payments", label: "Payments", needs: REPORT_VIEW },
          { href: "/admin/reports/renewals", label: "Subscription Renewals", needs: REPORT_VIEW },
          { href: "/admin/reports/wallet-movements", label: "Wallet Movements", needs: REPORT_VIEW },
          { href: "/admin/reports/usage", label: "Usage by Product", needs: REPORT_VIEW },
          { href: "/admin/reports/partner-conversions", label: "Partner Conversions", needs: REPORT_VIEW },
          { href: "/admin/reports/commissions", label: "Commissions", needs: REPORT_VIEW },
          { href: "/admin/reports/payouts", label: "Payouts", needs: REPORT_VIEW },
        ],
      },
    ],
  },
  {
    key: "settings",
    label: "Settings",
    icon: "settings",
    sections: [
      {
        label: "Platform Configuration",
        pages: [
          { href: "/admin/settings", label: "Platform settings", needs: "platform.staff_settings.view", exact: true },
          { href: "/admin/communication", label: "Email templates", needs: "platform.staff_communication.view" },
          { href: "/admin/ai", label: "AI platform", needs: "platform.staff_ai.view" },
        ],
      },
      {
        label: "Domains & Apps",
        pages: [{ href: "/admin/settings/domains", label: "Domains & Apps", needs: "platform.staff_settings.view" }],
      },
      {
        label: "Access & Security",
        pages: [
          { href: "/admin/security", label: "Staff access", needs: "platform.staff_security.view", exact: true },
          { href: "/admin/settings/login-clients", label: "Login clients", needs: ["platform.staff_settings.view", "platform.staff_integration.view"] },
          { href: "/admin/security/sign-ins", label: "Sign-in audit", needs: "platform.staff_audit.view" },
          // 2026-11-10: restricted KYC storage - who opened which document, across all applications.
          { href: "/admin/settings/kyc-access", label: "KYC document access", needs: "platform.staff_kyc.review" },
          { href: "/admin/audit", label: "Audit log", needs: "platform.staff_audit.view" },
        ],
      },
    ],
  },
];

function allowed(page: ModulePage, permissions: string[]): boolean {
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
  })).filter((module) => module.sections.length > 0);
}

export interface Location {
  module: AdminModule;
  section: ModuleSection;
  page: ModulePage;
}

/** Where an address sits. The longest matching address wins, so a page beneath
 * another is its own page, not part of the one above it. */
export function locate(pathname: string, modules: AdminModule[]): Location | null {
  const path = pathname.replace(/\/$/, "") || "/admin";
  let best: { location: Location; weight: number } | null = null;
  for (const mod of modules) {
    for (const section of mod.sections) {
      for (const page of section.pages) {
        const matches = page.exact
          ? path === page.href
          : path === page.href || path.startsWith(`${page.href}/`) || Boolean(page.alsoMatch?.test(path));
        if (matches && (!best || page.href.length > best.weight)) {
          best = { location: { module: mod, section, page }, weight: page.href.length };
        }
      }
    }
  }
  return best?.location ?? null;
}
