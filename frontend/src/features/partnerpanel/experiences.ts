/**
 * The partner panel's menu (ADR-063, the Partner Admin Panel specification of
 * 08 November 2026): **exactly eight entries** - Dashboard, Products & Referrals,
 * Customers, Billing & Subscriptions, Platform Wallet, Earnings & Payouts, Help
 * and Settings - the same for every partner. Every other page is a tab of one of
 * them; statuses are filters inside the pages and actions are buttons.
 *
 * What a partner sees is computed at runtime from two server facts:
 *
 * - **Permissions.** An entry (and each of its tabs) is drawn only when the
 *   signed-in member's server-resolved permissions make it useful (`needs(can)`).
 *   Nothing is drawn locked: a module the partner may not use is not shown.
 * - **Onboarding.** Until a partner may trade (its KYC approved and its account
 *   active - `partner.onboarding.cleared`), only the entries and tabs marked
 *   `onboarding` are drawn: its onboarding status, support, business and KYC,
 *   and its own security.
 *
 * Every request behind a link is authorized again by the backend; this file
 * decides only what to draw. It names permissions, never a capability or the
 * landing preference, and stays **plain data, no imports**: `next.config.ts`
 * reads it for the short addresses (`/reseller/dashboard` ->
 * `/partner/reseller/dashboard`).
 *
 * Every page keeps its address: the older menus' addresses are pages or aliases
 * here, so a bookmark opens the same page.
 */

export type ExperienceKey = "reseller" | "white_label" | "affiliate";

/** Answers "does the signed-in member hold any of these `platform.partner_<key>` permissions?" */
export type Can = (...keys: string[]) => boolean;

export interface WorkspaceEntry {
  key: string;
  label: string;
  icon: string;
  /** The page, relative to the workspace: `dashboard`, `users`, `payments/commissions`. */
  path: string;
  /** Other pages that belong to this entry (its tabs and the pages it links to). */
  also?: string[];
  /** Whether the person's permissions make the page useful. */
  needs: (can: Can) => boolean;
  /** Where the link opens when the first page is not one this person can read. */
  landing?: (can: Can) => string;
  /** The entry's pages as tabs of one page, in order. Each tab's pages are also
   * in `also`; a tab with `needs` is drawn - and its pages opened - only for a
   * person whose permissions make it useful. */
  tabs?: WorkspaceTab[];
  /** The partner levels that include this entry (2026-11-03). Every other
   * level sees it locked. Absent: every level. */
  levels?: readonly ExperienceKey[];
  /** One line on the upgrade dialog: what the locked entry would open. */
  pitch?: string;
  /** Drawn while the partner may not trade yet (ADR-063). */
  onboarding?: boolean;
}

export interface WorkspaceTab {
  label: string;
  /** The page the tab opens, relative to the workspace. */
  path: string;
  /** The tab's other pages - an older address of its page, or its sub-pages. */
  also?: string[];
  needs?: (can: Can) => boolean;
  /** Drawn while the partner may not trade yet (ADR-063). */
  onboarding?: boolean;
}

export interface WorkspaceGroup {
  title?: string;
  entries: WorkspaceEntry[];
}

export interface WorkspaceDef {
  key: ExperienceKey;
  /** The address segment: `/partner/<slug>/...`. */
  slug: "reseller" | "white-label" | "affiliate";
  label: string;
  /** The level as the specification names it. */
  level: { number: 1 | 2 | 3; name: string };
  /** One line under the workspace name in the shell and on the chooser. */
  lead: string;
  groups: WorkspaceGroup[];
  /** Friendly addresses that open a canonical page (`customers` -> `users`). */
  aliases: Record<string, string>;
}

// --- levels ---------------------------------------------------------------------------------------------

export const ALL_LEVELS: readonly ExperienceKey[] = ["affiliate", "reseller", "white_label"];

/** Level 1, 2 and 3, in the order a partner grows. */
export const LEVEL_ORDER: readonly ExperienceKey[] = ["affiliate", "reseller", "white_label"];

// --- the entries ------------------------------------------------------------------------------------------
//
// The Partner Admin Panel specification of 08 November 2026 (ADR-063): exactly
// eight entries. Every other page is a tab of one of them (or a page an entry's
// tab links to), statuses are filters inside the pages, and actions are buttons.
// An entry is drawn only when the person's permissions make it useful; nothing is
// drawn locked. Every page the earlier menus served keeps its address here.

/** An entry whose pages are tabs: it opens the first tab, and owns every tab's
 * pages so the sidebar stays current on each of them. */
const tabbed = (entry: Omit<WorkspaceEntry, "path" | "also" | "tabs">, tabs: WorkspaceTab[]): WorkspaceEntry => ({
  ...entry,
  path: tabs[0].path,
  also: tabs.flatMap((t, i) => [...(i === 0 ? [] : [t.path]), ...(t.also ?? [])]),
  tabs,
});

/** Promoting needs the referral view - held by an affiliate, and by a reseller
 * or white-label partner granted the affiliate permission as well. */
const promotes = (can: Can) => can("referral.view");
/** Selling needs the pricing view - held by a partner granted reseller rights. */
const sells = (can: Can) => can("pricing.view");

/** 1. Dashboard: KYC and account status, referral performance, wallet and
 * earnings, quick actions; the reports and notifications beside it. */
const dashboard = (): WorkspaceEntry =>
  tabbed({ key: "dashboard", label: "Dashboard", icon: "dashboard", onboarding: true, needs: (can) => can("dashboard.view") }, [
    {
      label: "Overview",
      path: "dashboard",
      // The upgrade pages open from the dashboard's level card (ADR-049).
      also: ["upgrade", "upgrade/reseller", "upgrade/white-label"],
      onboarding: true,
    },
    { label: "Reports", path: "reports" },
    { label: "Notifications", path: "notifications", onboarding: true },
  ]);

/** 2. Products & Referrals: what the partner may promote or sell, its links and
 * their results, and the material Workeasy360 approved for selling. */
const productsAndReferrals = (key: ExperienceKey): WorkspaceEntry =>
  tabbed(
    {
      key: "products",
      label: "Products & Referrals",
      icon: "gift",
      needs: (can) => promotes(can) || sells(can),
      landing: () => "eligible-products",
    },
    [
      { label: "Eligible Products", path: "eligible-products", needs: (can) => promotes(can) || sells(can) },
      { label: "Referral Links", path: "referral-links", also: ["referral-codes", "campaigns"], needs: promotes },
      {
        label: "Referral Results",
        path: "leads",
        also: ["conversions", "affiliates", "affiliates/conversions", "affiliates/settings"],
        needs: promotes,
      },
      {
        label: "Sales Resources",
        path: "sales-resources",
        also: ["marketing-materials"],
        needs: (can) => promotes(can) || sells(can),
      },
      {
        label: "Product Store",
        path: "store",
        also: [
          "store-console",
          "store-console/explore",
          "store-console/requests",
          "partner-products",
          // A seller that also promotes asks to promote from its own copy of
          // the affiliate Store, because its `store` sells (2026-11-03).
          ...(key === "affiliate"
            ? []
            : ["promote/store", "promote/store-console", "promote/store-console/explore", "promote/store-console/requests"]),
        ],
        needs: (can) => promotes(can) || sells(can),
      },
      {
        label: "Pricing & Margins",
        path: "plans",
        also: ["plans/add-ons", "pricing/currency", "pricing/packages", "pricing/rules", "margin-calculator", "product-manager", "product-billing"],
        needs: sells,
      },
    ],
  );

/** 3. Customers: a reseller's assigned organizations, their products and set-up,
 * and Plan Activation from the Platform Wallet. */
const customers = (): WorkspaceEntry =>
  tabbed({ key: "customers", label: "Customers", icon: "users", needs: (can) => can("customer.view") }, [
    { label: "Assigned Organizations", path: "users" },
    { label: "Products & Setup", path: "customers/setup" },
    { label: "Plan Activation", path: "customers/activate", needs: (can) => can("pricing.view") },
    { label: "Downline Partners", path: "agency", needs: (can) => can("subpartner.view") },
  ]);

/** 4. Billing & Subscriptions: customers' subscriptions with their filters, the
 * partner's own billing records, and the customer orders behind them. */
const billing = (): WorkspaceEntry =>
  tabbed(
    {
      key: "billing",
      label: "Billing & Subscriptions",
      icon: "receipt",
      // Billing Records (the partner's own payments) is every trading partner's;
      // an ended partner, which keeps only its money views, opens its earnings.
      needs: (can) => can("customer.view") || can("dashboard.view"),
      landing: (can) => (can("customer.view") ? "users/subscriptions" : "billing-records"),
    },
    [
      { label: "Customer Subscriptions", path: "users/subscriptions", needs: (can) => can("customer.view") },
      { label: "Billing Records", path: "billing-records" },
      { label: "Customer Orders", path: "payments", needs: (can) => can("customer.view") },
    ],
  );

/** 5. Platform Wallet: the reseller's prepaid balance, Add Money, and every
 * movement - never its earnings. */
const platformWallet = (): WorkspaceEntry =>
  tabbed({ key: "platform-wallet", label: "Platform Wallet", icon: "card", needs: (can) => can("wallet.view") }, [
    { label: "Balance & Recharge", path: "platform-wallet" },
    { label: "Transactions", path: "platform-wallet/transactions" },
  ]);

/** 6. Earnings & Payouts: commissions (pending, available, reversed) and payouts. */
const earnings = (): WorkspaceEntry =>
  tabbed(
    {
      key: "earnings",
      label: "Earnings & Payouts",
      icon: "currency",
      needs: (can) => can("commission.view", "payout.view"),
      landing: (can) => (can("commission.view") ? "payments/commissions" : "wallet"),
    },
    [
      { label: "Commissions", path: "payments/commissions", also: ["payments/balance"], needs: (can) => can("commission.view") },
      { label: "Payouts", path: "wallet", also: ["payouts", "affiliates/payouts"], needs: (can) => can("payout.view") },
      { label: "Disputes", path: "disputes", needs: (can) => can("commission.view") },
    ],
  );

/** 7. Help: tickets to Workeasy360 (commission disputes and product access too),
 * help content, and a seller's desk for its own customers. */
const help = (): WorkspaceEntry =>
  tabbed({ key: "help", label: "Help", icon: "help", onboarding: true, needs: (can) => can("dashboard.view") }, [
    { label: "Support Tickets", path: "support-manager", onboarding: true },
    { label: "Help Content", path: "help", onboarding: true },
    { label: "Customer Desk", path: "desk", also: ["desk/replies"], needs: (can) => can("desk.view") },
  ]);

/** 8. Settings: business and KYC, staff and security, and - only with an
 * approved White Label permission - the white-label settings. */
const settings = (): WorkspaceEntry =>
  tabbed({ key: "settings", label: "Settings", icon: "settings", onboarding: true, needs: (can) => can("profile.view") }, [
    { label: "Business & KYC", path: "business-kyc", onboarding: true },
    { label: "My Account & Security", path: "account", onboarding: true },
    { label: "Staff", path: "team", also: ["roles"], needs: (can) => can("member.view") },
    {
      label: "White Label",
      path: "brand-settings",
      also: ["appearance", "brand-settings/url-manager", "brand-settings/white-label-setup", "translations"],
      needs: (can) => can("branding.view", "domain.view"),
    },
    {
      label: "Communication",
      path: "communication/channel",
      also: ["communication/templates", "communication/alerts", "communication/logs", "channel-manager"],
      needs: (can) => can("customer.view"),
    },
    { label: "Sign-in & API", path: "auth-configuration", also: ["api-keys", "integrations", "shared-resources"] },
  ]);

/** The eight entries, in the specification's order. */
function unifiedGroups(key: ExperienceKey): WorkspaceGroup[] {
  return [
    {
      entries: [
        dashboard(),
        productsAndReferrals(key),
        customers(),
        billing(),
        platformWallet(),
        earnings(),
        help(),
        settings(),
      ],
    },
  ];
}

// --- the older addresses ------------------------------------------------------------------------------------

/** Addresses of the earlier menus (and the entries' own names) that open a page. */
const SHARED_ALIASES: Record<string, string> = {
  // ADR-063: the eight entries' names and their tabs' names.
  "products-referrals": "eligible-products",
  "eligible-products-list": "eligible-products",
  "referral-results": "leads",
  "billing-subscriptions": "users/subscriptions",
  "billing-records-list": "billing-records",
  "earnings-and-payouts": "payments/commissions",
  "help-content": "help",
  faq: "help",
  "support-tickets": "support-manager",
  "business-profile": "business-kyc",
  "staff-security": "account",
  security: "account",
  "customers-setup": "customers/setup",
  "plan-activation": "customers/activate",
  "add-money": "platform-wallet",
  recharge: "platform-wallet",
  // The specification's entry names.
  affiliate: "referral-links",
  "white-label": "brand-settings",
  "product-store": "store",
  "sales-orders": "payments",
  "commission-earnings": "payments/commissions",
  "wallet-payouts": "wallet",
  communication: "communication/channel",
  staff: "team",
  helpdesk: "support-manager",
  tickets: "support-manager",
  "reports-analytics": "reports",
  // The 2026-11-02 consolidated menus' names.
  earnings: "payments/commissions",
  "earnings-payouts": "payments/commissions",
  "referral-tools": "referral-links",
  "marketing-hub": "campaigns",
  "referral-performance": "leads",
  links: "referral-links",
  codes: "referral-codes",
  materials: "marketing-materials",
  "marketing-assets": "marketing-materials",
  commissions: "payments/commissions",
  settings: "account",
  support: "support-manager",
  customers: "users",
  "customers/subscriptions": "users/subscriptions",
  subscriptions: "users/subscriptions",
  pricing: "plans",
  orders: "payments",
  sales: "payments",
  "sub-resellers": "agency",
  downline: "agency",
};

const SELLER_ALIASES: Record<string, string> = {
  ...SHARED_ALIASES,
  reseller: "store-console",
  products: "store-console",
  "explore-products": "store-console/explore",
  "sales-management": "plans",
  "sales-billing": "plans",
  "customer-management": "users",
  "partner-network": "agency",
  "promote-products": "promote/store",
  referrals: "affiliates",
};

export const WORKSPACES: Record<ExperienceKey, WorkspaceDef> = {
  affiliate: {
    key: "affiliate",
    slug: "affiliate",
    label: "Affiliate",
    level: { number: 1, name: "Affiliate Partner" },
    lead: "Promote Workeasy360 and earn commission.",
    aliases: {
      ...SHARED_ALIASES,
      "referral-link": "referral-links",
      "my-products": "store-console",
      "explore-products": "store-console/explore",
      referrals: "leads",
      "leads-referrals": "leads",
      profile: "account",
    },
    groups: unifiedGroups("affiliate"),
  },
  reseller: {
    key: "reseller",
    slug: "reseller",
    label: "Reseller",
    level: { number: 2, name: "Reseller Partner" },
    lead: "Sell Workeasy360 products to your customers.",
    aliases: SELLER_ALIASES,
    groups: unifiedGroups("reseller"),
  },
  white_label: {
    key: "white_label",
    slug: "white-label",
    label: "White-label",
    level: { number: 3, name: "White Label Partner" },
    lead: "Run Workeasy360 under your own brand.",
    aliases: {
      ...SELLER_ALIASES,
      brand: "brand-settings",
      "brand-studio": "brand-settings",
      domain: "brand-settings/url-manager",
      domains: "brand-settings/url-manager",
      branding: "brand-settings/white-label-setup",
    },
    groups: unifiedGroups("white_label"),
  },
};

export const EXPERIENCE_ORDER: ExperienceKey[] = ["reseller", "white_label", "affiliate"];

/** The workspaces each one contains: an upgraded partner works in the higher
 * one, and the lower one's address opens the same page there. */
export const CONTAINS: Record<ExperienceKey, ExperienceKey[]> = {
  affiliate: [],
  reseller: ["affiliate"],
  white_label: ["reseller", "affiliate"],
};

// --- helpers (pure) ----------------------------------------------------------------------------------------------------

export const isExperienceKey = (value: unknown): value is ExperienceKey =>
  typeof value === "string" && Object.prototype.hasOwnProperty.call(WORKSPACES, value);

/** `white-label` -> `white_label`; anything that is not a workspace address -> null. */
export function experienceFromSlug(slug: string): ExperienceKey | null {
  const found = EXPERIENCE_ORDER.find((key) => WORKSPACES[key].slug === slug);
  return found ?? null;
}

export const workspaceBase = (key: ExperienceKey): string => `/partner/${WORKSPACES[key].slug}`;

/** Where a workspace opens: its dashboard. */
export const workspaceHome = (key: ExperienceKey): string => `${workspaceBase(key)}/dashboard`;

/** Whether this level sees the entry locked. */
export const isLocked = (def: WorkspaceDef, entry: WorkspaceEntry): boolean =>
  entry.levels !== undefined && !entry.levels.includes(def.key);

/** The lowest level that opens a locked entry (what the upgrade dialog offers). */
export const unlockLevel = (entry: WorkspaceEntry): ExperienceKey =>
  LEVEL_ORDER.find((level) => !entry.levels || entry.levels.includes(level)) ?? "white_label";

/** Every page a workspace serves: its entries and what belongs to them -
 * locked ones included, which answer with an upgrade prompt. */
export function servedPaths(def: WorkspaceDef): Set<string> {
  const paths = new Set<string>();
  for (const group of def.groups) {
    for (const entry of group.entries) {
      paths.add(entry.path);
      for (const extra of entry.also ?? []) paths.add(extra);
    }
  }
  return paths;
}

/** The entries (in any group) whose pages include `path`. */
export function entriesFor(def: WorkspaceDef, path: string): WorkspaceEntry[] {
  return def.groups
    .flatMap((g) => g.entries)
    .filter((entry) => entry.path === path || (entry.also ?? []).includes(path));
}

/** The locked entry that owns `path`, when every entry owning it is locked. */
export function lockedEntryFor(def: WorkspaceDef, path: string): WorkspaceEntry | undefined {
  const owners = entriesFor(def, path);
  return owners.length > 0 && owners.every((entry) => isLocked(def, entry)) ? owners[0] : undefined;
}

/** Whether an entry is drawn for this person (ADR-063): its permissions, and -
 * while the partner may not trade yet - only what onboarding allows. */
export const entryVisible = (entry: WorkspaceEntry, can: Can, cleared = true): boolean =>
  (cleared || entry.onboarding === true) && entry.needs(can);

/** Whether one of an entry's tabs is drawn for this person. */
export const tabVisible = (tab: WorkspaceTab, can: Can, cleared = true): boolean =>
  (cleared || tab.onboarding === true) && (!tab.needs || tab.needs(can));

/** Whether the person's permissions make this page of the workspace useful: some
 * unlocked entry that owns it is drawn for them. Convenience only - the API refuses the rest. */
export function pageAllowed(def: WorkspaceDef, path: string, can: Can, cleared = true): boolean {
  return entriesFor(def, path).some((entry) => {
    if (isLocked(def, entry) || !entryVisible(entry, can, cleared)) return false;
    const tab = tabOf(entry, path);
    return !tab || tabVisible(tab, can, cleared);
  });
}

/** The tab of `entry` that shows `path`, if the entry has tabs. */
export function tabOf(entry: WorkspaceEntry, path: string): WorkspaceTab | undefined {
  return entry.tabs?.find((t) => t.path === path || (t.also ?? []).includes(path));
}

/** The tabbed entry that owns `path`, if any - the tabs drawn above its page.
 * An unlocked owner wins: an affiliate's Store Console pages are also pages of
 * the (locked) Reseller entry, and must be drawn under its Product Store. */
export function tabbedEntryFor(def: WorkspaceDef, path: string): WorkspaceEntry | undefined {
  const owners = entriesFor(def, path).filter((entry) => tabOf(entry, path) !== undefined);
  return owners.find((entry) => !isLocked(def, entry)) ?? owners[0];
}

/** The first page this person can open - where a landing goes when the dashboard
 * is not one they can read (an ended partner reads its commission and nothing else). */
export function firstAllowedPath(def: WorkspaceDef, can: Can, cleared = true): string | null {
  for (const group of def.groups) {
    for (const entry of group.entries) {
      if (!isLocked(def, entry) && entryVisible(entry, can, cleared)) return entryHref(entry, can, cleared);
    }
  }
  return null;
}

/** Where an entry's link opens: its landing, else its first tab this person may open. */
export function entryHref(entry: WorkspaceEntry, can: Can, cleared = true): string {
  const landing = entry.landing?.(can);
  if (landing && (!entry.tabs || entry.tabs.some((t) => t.path === landing && tabVisible(t, can, cleared)))) return landing;
  const tab = entry.tabs?.find((t) => tabVisible(t, can, cleared));
  return tab?.path ?? entry.path;
}

/** The short addresses: `/reseller/...` -> `/partner/reseller/...`, so the URL a
 * person would guess works. A permanent redirect to a page that checks the
 * session and the grants like any other. */
export const SHORT_ADDRESSES: readonly { from: string; to: string }[] = EXPERIENCE_ORDER.flatMap((key) => {
  const slug = WORKSPACES[key].slug;
  return [
    { from: `/${slug}`, to: `/partner/${slug}/dashboard` },
    { from: `/${slug}/:path*`, to: `/partner/${slug}/:path*` },
  ];
});
