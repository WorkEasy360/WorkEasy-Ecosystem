/**
 * The unified partner dashboard (2026-11-03, the owner's "Workeasy360 Unified
 * Partner Dashboard" specification, DOC WE360-PRD-2026-V4.2).
 *
 * **One panel, one menu.** Every partner - Level 1 Affiliate, Level 2 Reseller,
 * Level 3 White-label - sees the same fourteen entries in the same order. What
 * differs is computed at runtime:
 *
 * - **Level** (the workspace: `affiliate`, `reseller`, `white_label`). An entry
 *   the level does not include is drawn **locked** - a lock icon that opens the
 *   upgrade dialog - and its pages show an upgrade prompt, never the page.
 * - **Permissions.** Inside the level, an entry (and each of its tabs) is drawn
 *   only when the signed-in member's server-resolved permissions make it useful
 *   (`needs(can)`); a role that cannot use it does not see it at all - upgrading
 *   would not help, so it is not offered as a lock.
 *
 * Every request behind a link is authorized again by the backend; this file
 * decides only what to draw. It names permissions and levels, never a
 * capability or the landing preference, and stays **plain data, no imports**:
 * `next.config.ts` reads it for the short addresses
 * (`/reseller/dashboard` -> `/partner/reseller/dashboard`).
 *
 * Every page keeps its address: the older menus' addresses are aliases or
 * pages of an entry here, so a bookmark opens the same page.
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
}

export interface WorkspaceTab {
  label: string;
  /** The page the tab opens, relative to the workspace. */
  path: string;
  /** The tab's other pages - an older address of its page, or its sub-pages. */
  also?: string[];
  needs?: (can: Can) => boolean;
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
const SELLING: readonly ExperienceKey[] = ["reseller", "white_label"];
const WHITE_LABEL_ONLY: readonly ExperienceKey[] = ["white_label"];

/** Level 1, 2 and 3, in the order a partner grows. */
export const LEVEL_ORDER: readonly ExperienceKey[] = ["affiliate", "reseller", "white_label"];

// --- the entries ------------------------------------------------------------------------------------------

/** An entry whose pages are tabs: it opens the first tab, and owns every tab's
 * pages so the sidebar stays current on each of them. */
const tabbed = (entry: Omit<WorkspaceEntry, "path" | "also" | "tabs">, tabs: WorkspaceTab[]): WorkspaceEntry => ({
  ...entry,
  path: tabs[0].path,
  also: tabs.flatMap((t, i) => [...(i === 0 ? [] : [t.path]), ...(t.also ?? [])]),
  tabs,
});

/** Promoting needs the referral view - held by an affiliate, and by a reseller or
 * white-label partner that also promotes (ADR-049 keeps an upgraded partner's grants). */
const promotes = (can: Can) => can("referral.view");

const dashboard = (): WorkspaceEntry => ({
  key: "dashboard",
  label: "Dashboard",
  icon: "dashboard",
  path: "dashboard",
  // The upgrade pages open from the header's Upgrade Plan action and the
  // dashboard's level card (ADR-049); they are not a menu entry.
  also: ["upgrade", "upgrade/reseller", "upgrade/white-label"],
  needs: (can) => can("dashboard.view"),
});

/** 2. Affiliate: link generator, UTM builder, click logs, commission attribution. */
const affiliate = (key: ExperienceKey): WorkspaceEntry =>
  tabbed(
    {
      key: "affiliate",
      label: "Affiliate",
      icon: "gift",
      needs: promotes,
      pitch: "Referral links, campaigns and conversion tracking.",
    },
    [
      { label: "Referral Links", path: "referral-links" },
      { label: "Referral Codes", path: "referral-codes" },
      { label: "Campaigns", path: "campaigns" },
      { label: "Leads", path: "leads" },
      { label: "Conversions", path: "conversions", also: ["affiliates/conversions"] },
      { label: "Link Overview", path: "affiliates" },
      { label: "Marketing Assets", path: "marketing-materials" },
      { label: "Programme Settings", path: "affiliates/settings" },
      // A higher level promotes from its own copy of the affiliate Store,
      // because its `store` sells (2026-11-03).
      ...(key === "affiliate"
        ? []
        : [
            {
              label: "Promote Products",
              path: "promote/store",
              also: ["promote/store-console", "promote/store-console/explore", "promote/store-console/requests"],
            },
          ]),
    ],
  );

/** 3. Reseller: wholesale catalogue, customer assignment, margin calculators. */
const reseller = (): WorkspaceEntry =>
  tabbed(
    {
      key: "reseller",
      label: "Reseller",
      icon: "handshake",
      levels: SELLING,
      needs: (can) => can("product.view", "pricing.view", "subpartner.view"),
      landing: (can) => (can("product.view") ? "store-console" : can("pricing.view") ? "plans" : "agency"),
      pitch: "Sell to your own customers at wholesale prices and keep the margin.",
    },
    [
      {
        label: "My Products",
        path: "store-console",
        also: ["partner-products"],
        needs: (can) => can("product.view"),
      },
      { label: "Wholesale Catalogue", path: "store-console/explore", needs: (can) => can("product.view") },
      { label: "My Requests", path: "store-console/requests", needs: (can) => can("product.view") },
      // Which granted products are for sale and shown to customers - for a
      // white-label partner, the platform visibility governance too.
      { label: "Product Settings", path: "product-manager", needs: (can) => can("product.view") },
      {
        label: "Pricing & Margins",
        path: "plans",
        also: ["plans/add-ons", "pricing/currency", "pricing/packages", "pricing/rules"],
        needs: (can) => can("pricing.view"),
      },
      { label: "Margin Calculator", path: "margin-calculator", needs: (can) => can("pricing.view") },
      { label: "Downline Partners", path: "agency", needs: (can) => can("subpartner.view") },
    ],
  );

/** 4. White Label: domain, logo and theme. */
const whiteLabel = (): WorkspaceEntry =>
  tabbed(
    {
      key: "white-label",
      label: "White Label",
      icon: "palette",
      levels: WHITE_LABEL_ONLY,
      needs: (can) => can("branding.view", "domain.view"),
      landing: (can) => (can("branding.view") ? "brand-settings" : "brand-settings/url-manager"),
      pitch: "Your own logo, colours, domain and branded customer portal.",
    },
    [
      { label: "Brand Settings", path: "brand-settings", also: ["appearance"], needs: (can) => can("branding.view") },
      { label: "Website Branding", path: "brand-settings/white-label-setup", needs: (can) => can("branding.view") },
      { label: "Custom Domain", path: "brand-settings/url-manager", needs: (can) => can("domain.view") },
      { label: "Translations", path: "translations", needs: (can) => can("profile.view") },
    ],
  );

/** 5. Product Store: an affiliate browses (and asks to promote); a seller transacts. */
const productStore = (key: ExperienceKey): WorkspaceEntry =>
  key === "affiliate"
    ? tabbed({ key: "product-store", label: "Product Store", icon: "store", needs: promotes }, [
        { label: "Explore Products", path: "store" },
        { label: "Approved / Promoting", path: "store-console" },
        { label: "Request Access", path: "store-console/explore" },
        { label: "My Requests", path: "store-console/requests" },
      ])
    : {
        key: "product-store",
        label: "Product Store",
        icon: "store",
        path: "store",
        needs: (can) => can("product.view"),
      };

const customers = (): WorkspaceEntry =>
  tabbed(
    {
      key: "customers",
      label: "Customers",
      icon: "users",
      levels: SELLING,
      needs: (can) => can("customer.view"),
      pitch: "Create customer workspaces and manage their plans.",
    },
    [
      { label: "Customers", path: "users" },
      { label: "Subscriptions", path: "users/subscriptions" },
    ],
  );

const salesOrders = (): WorkspaceEntry =>
  tabbed(
    {
      key: "sales-orders",
      label: "Sales & Orders",
      icon: "receipt",
      levels: SELLING,
      needs: (can) => can("customer.view", "product.view"),
      landing: (can) => (can("customer.view") ? "payments" : "product-billing"),
      pitch: "Orders, renewals and billing for your customers.",
    },
    [
      { label: "Orders", path: "payments", needs: (can) => can("customer.view") },
      { label: "Product Billing", path: "product-billing", needs: (can) => can("product.view") },
    ],
  );

const commission = (): WorkspaceEntry =>
  tabbed(
    { key: "commission", label: "Commission & Earnings", icon: "currency", needs: (can) => can("commission.view") },
    [
      { label: "Commissions", path: "payments/commissions" },
      { label: "Balance", path: "payments/balance" },
      { label: "Disputes", path: "disputes" },
    ],
  );

const wallet = (): WorkspaceEntry =>
  tabbed({ key: "wallet", label: "Earnings & Payouts", icon: "card", needs: (can) => can("payout.view") }, [
    { label: "Earnings", path: "wallet" },
    { label: "Payout History", path: "payouts", also: ["affiliates/payouts"] },
  ]);

const communication = (): WorkspaceEntry =>
  tabbed(
    {
      key: "communication",
      label: "Communication",
      icon: "mail",
      levels: SELLING,
      needs: (can) => can("profile.view"),
      pitch: "Email your customers under your own sender name.",
    },
    [
      // Alerts and logs are not built (partner_portal.NOT_IMPLEMENTED); their
      // old addresses stay pages of the Channels tab.
      { label: "Channels", path: "communication/channel", also: ["communication/alerts", "communication/logs"] },
      { label: "Templates", path: "communication/templates" },
      { label: "Channel Manager", path: "channel-manager" },
    ],
  );

const staff = (): WorkspaceEntry =>
  tabbed(
    {
      key: "staff",
      label: "Staff",
      icon: "lock",
      levels: SELLING,
      needs: (can) => can("member.view"),
      pitch: "Add sales, support and finance staff with their own roles.",
    },
    [
      { label: "Team", path: "team" },
      { label: "Roles", path: "roles" },
    ],
  );

/** 12. Helpdesk: every level raises tickets to Workeasy360; a seller also runs
 * a desk for its own customers. */
const helpdesk = (key: ExperienceKey): WorkspaceEntry =>
  tabbed({ key: "helpdesk", label: "Helpdesk", icon: "help", needs: (can) => can("dashboard.view") }, [
    { label: "My Tickets", path: "support-manager" },
    ...(key === "affiliate"
      ? []
      : [
          { label: "Customer Desk", path: "desk", needs: (can: Can) => can("desk.view") },
          { label: "Saved Replies", path: "desk/replies", needs: (can: Can) => can("desk.view") },
        ]),
    { label: "Notifications", path: "notifications" },
  ]);

const reports = (): WorkspaceEntry => ({
  key: "reports",
  label: "Reports & Analytics",
  icon: "chart",
  path: "reports",
  needs: (can) => can("dashboard.view"),
});

const settings = (): WorkspaceEntry =>
  tabbed({ key: "settings", label: "Settings", icon: "settings", needs: (can) => can("profile.view") }, [
    { label: "Profile", path: "account" },
    { label: "Integrations", path: "integrations" },
    { label: "API Keys", path: "api-keys" },
    { label: "Sign-in", path: "auth-configuration" },
    { label: "Shared Resources", path: "shared-resources" },
  ]);

/** The fourteen entries, in the specification's order, for one level. */
function unifiedGroups(key: ExperienceKey): WorkspaceGroup[] {
  return [
    {
      entries: [
        dashboard(),
        affiliate(key),
        reseller(),
        whiteLabel(),
        productStore(key),
        customers(),
        salesOrders(),
        commission(),
        wallet(),
        communication(),
        staff(),
        helpdesk(key),
        reports(),
        settings(),
      ],
    },
  ];
}

// --- the older addresses ------------------------------------------------------------------------------------

/** Addresses of the earlier menus (and the entries' own names) that open a page. */
const SHARED_ALIASES: Record<string, string> = {
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

/** Whether the person's permissions make this page of the workspace useful: some
 * unlocked entry that owns it is drawn for them. Convenience only - the API refuses the rest. */
export function pageAllowed(def: WorkspaceDef, path: string, can: Can): boolean {
  return entriesFor(def, path).some((entry) => {
    if (isLocked(def, entry) || !entry.needs(can)) return false;
    const tab = tabOf(entry, path);
    return !tab?.needs || tab.needs(can);
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
export function firstAllowedPath(def: WorkspaceDef, can: Can): string | null {
  for (const group of def.groups) {
    for (const entry of group.entries) {
      if (!isLocked(def, entry) && entry.needs(can)) return entry.landing?.(can) ?? entry.path;
    }
  }
  return null;
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
