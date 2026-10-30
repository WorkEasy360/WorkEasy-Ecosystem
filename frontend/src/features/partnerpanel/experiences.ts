/**
 * The three partner workspaces - Reseller, White-label, Affiliate (ADR-047).
 *
 * A workspace is a **navigation composition over the one partner panel**, not a
 * second app and not a kind of account. Each is a list of entries, and an entry
 * is a page of the shared panel plus the permission that makes it useful; the
 * sidebar, the route guard and the tests all read this one file. Three rules:
 *
 * - **Permissions are the only input.** `needs(can)` asks the signed-in member's
 *   server-resolved permissions; nothing here names a capability or reads the
 *   landing preference. A workspace is *offered* by the server (the grants earn
 *   it), and an entry is *drawn* only when its permission is held - and every
 *   request behind a link is authorized again by the backend.
 * - **A page belongs to a workspace by being listed here.** `/partner/affiliate/users`
 *   is not "hidden": the affiliate workspace has no such route, so asking for it
 *   is refused as not part of that workspace, whatever the person's permissions.
 * - **Plain data, no imports** (like `menu.ts`): `next.config.ts` reads it for the
 *   short addresses (`/reseller/dashboard` -> `/partner/reseller/dashboard`).
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
}

export interface WorkspaceGroup {
  title: string;
  entries: WorkspaceEntry[];
}

export interface WorkspaceDef {
  key: ExperienceKey;
  /** The address segment: `/partner/<slug>/...`. */
  slug: "reseller" | "white-label" | "affiliate";
  label: string;
  /** One line under the workspace name in the shell and on the chooser. */
  lead: string;
  groups: WorkspaceGroup[];
  /** Friendly addresses that open a canonical page (`customers` -> `users`). */
  aliases: Record<string, string>;
}

// --- the entries every workspace is built from ---------------------------------------------------------

const dashboard = (label = "Dashboard"): WorkspaceEntry => ({
  key: "dashboard",
  label,
  icon: "dashboard",
  path: "dashboard",
  // The upgrade pages hang off the dashboard's "Grow Your Partnership" section
  // (ADR-049). They are not a menu entry; whether an upgrade is offered, or may be
  // asked for, is the server's answer on the page and again on every request.
  also: ["upgrade", "upgrade/reseller", "upgrade/white-label"],
  needs: (can) => can("dashboard.view"),
});

/** The pages the settings family spans, linked to from the account page. */
const SETTINGS_PAGES = [
  "communication/channel",
  "communication/templates",
  "communication/alerts",
  "communication/logs",
  "integrations",
  "auth-configuration",
  "shared-resources",
  "channel-manager",
  "api-keys",
  "translations",
];

const customers = (): WorkspaceEntry => ({
  key: "customers",
  label: "Customers",
  icon: "users",
  path: "users",
  needs: (can) => can("customer.view"),
});

const subscriptions = (): WorkspaceEntry => ({
  key: "subscriptions",
  label: "Subscriptions",
  icon: "card",
  path: "users/subscriptions",
  needs: (can) => can("customer.view"),
});

const productsAndPlans = (label: string): WorkspaceEntry => ({
  key: "products",
  label,
  icon: "store",
  path: "partner-products",
  also: ["product-manager", "product-billing"],
  needs: (can) => can("product.view"),
});

/** ADR-055: the Store Console - My Products, Explore Products and My Requests,
 * as tabs of one entry. An affiliate asks to promote a product, a reseller to
 * sell one; `also` keeps the pages the entry replaces in the workspace. */
const storeConsole = (needs: (can: Can) => boolean, also: string[] = []): WorkspaceEntry => ({
  key: "store-console",
  label: "Store Console",
  icon: "store",
  path: "store-console",
  also: ["store-console/explore", "store-console/requests", ...also],
  needs,
});

const pricing = (label: string): WorkspaceEntry => ({
  key: "pricing",
  label,
  icon: "tag",
  path: "plans",
  also: ["plans/add-ons", "pricing/currency", "pricing/packages", "pricing/rules"],
  needs: (can) => can("pricing.view"),
});

const orders = (label: string): WorkspaceEntry => ({
  key: "orders",
  label,
  icon: "receipt",
  path: "payments",
  also: ["payments/balance"],
  needs: (can) => can("customer.view"),
});

const commissions = (label = "Commissions"): WorkspaceEntry => ({
  key: "commissions",
  label,
  icon: "currency",
  path: "payments/commissions",
  also: ["payments/balance"],
  needs: (can) => can("commission.view"),
});

const payouts = (): WorkspaceEntry => ({
  key: "payouts",
  label: "Payouts",
  icon: "handshake",
  path: "payouts",
  also: ["affiliates/payouts"],
  needs: (can) => can("payout.view"),
});

const subResellers = (): WorkspaceEntry => ({
  key: "sub-resellers",
  label: "Sub-Resellers",
  icon: "network",
  path: "agency",
  needs: (can) => can("subpartner.view"),
});

const referrals = (): WorkspaceEntry => ({
  key: "referrals",
  label: "Referrals",
  icon: "gift",
  path: "affiliates",
  also: ["affiliates/conversions", "affiliates/settings"],
  needs: (can) => can("referral.view"),
});

const team = (): WorkspaceEntry => ({
  key: "team",
  label: "Team & Permissions",
  icon: "lock",
  path: "team",
  also: ["roles"],
  needs: (can) => can("member.view"),
});

const reports = (): WorkspaceEntry => ({
  key: "reports",
  label: "Reports & Analytics",
  icon: "chart",
  path: "reports",
  needs: (can) => can("dashboard.view"),
});

const support = (): WorkspaceEntry => ({
  key: "support",
  label: "Support",
  icon: "help",
  path: "support-manager",
  also: ["notifications"],
  needs: (can) => can("dashboard.view"),
});

const settings = (): WorkspaceEntry => ({
  key: "settings",
  label: "Settings",
  icon: "settings",
  path: "account",
  also: SETTINGS_PAGES,
  needs: (can) => can("profile.view"),
});

const COMMERCE_ALIASES: Record<string, string> = {
  customers: "users",
  "customers/subscriptions": "users/subscriptions",
  subscriptions: "users/subscriptions",
  products: "partner-products",
  pricing: "plans",
  orders: "payments",
  sales: "payments",
  commissions: "payments/commissions",
  "sub-resellers": "agency",
  referrals: "affiliates",
  settings: "account",
  support: "support-manager",
};

// --- the workspaces -----------------------------------------------------------------------------------------

export const WORKSPACES: Record<ExperienceKey, WorkspaceDef> = {
  reseller: {
    key: "reseller",
    slug: "reseller",
    label: "Reseller",
    lead: "Sell Workeasy360 products to customers.",
    aliases: {
      ...COMMERCE_ALIASES,
      // ADR-055: Products & Plans became the Store Console's My Products tab.
      products: "store-console",
      store: "store-console",
      "explore-products": "store-console/explore",
    },
    groups: [
      { title: "Menu", entries: [dashboard()] },
      {
        title: "Customers and sales",
        entries: [
          customers(),
          storeConsole((can) => can("product.view"), ["partner-products", "product-manager", "product-billing"]),
          pricing("Pricing & Margins"),
          orders("Sales & Orders"),
          subscriptions(),
        ],
      },
      { title: "Earnings", entries: [commissions(), payouts()] },
      { title: "Network", entries: [subResellers(), referrals()] },
      { title: "Account", entries: [team(), reports(), support(), settings()] },
    ],
  },
  white_label: {
    key: "white_label",
    slug: "white-label",
    label: "White-label",
    lead: "Run Workeasy360 under your own brand.",
    aliases: {
      ...COMMERCE_ALIASES,
      brand: "brand-settings",
      domain: "brand-settings/url-manager",
      domains: "brand-settings/url-manager",
      branding: "brand-settings/white-label-setup",
    },
    groups: [
      { title: "Menu", entries: [dashboard()] },
      {
        title: "Brand",
        entries: [
          {
            key: "brand",
            label: "Brand Settings",
            icon: "palette",
            path: "brand-settings",
            also: ["appearance"],
            needs: (can) => can("branding.view"),
          },
          {
            key: "domain",
            label: "Custom Domain",
            icon: "network",
            path: "brand-settings/url-manager",
            needs: (can) => can("domain.view"),
          },
          {
            key: "website",
            label: "Website Branding",
            icon: "sparkles",
            path: "brand-settings/white-label-setup",
            needs: (can) => can("branding.view"),
          },
        ],
      },
      {
        title: "Customers and sales",
        entries: [
          productsAndPlans("Products & Plans"),
          pricing("Pricing & Plans"),
          customers(),
          subscriptions(),
          orders("Orders & Billing"),
        ],
      },
      { title: "Commissions & Payouts", entries: [commissions(), payouts()] },
      // Held only when the partner was also given them: this workspace contains
      // the Reseller one, so these entries keep it complete (ADR-047).
      { title: "Network", entries: [subResellers(), referrals()] },
      { title: "Account", entries: [team(), reports(), support(), settings()] },
    ],
  },
  affiliate: {
    key: "affiliate",
    slug: "affiliate",
    label: "Affiliate",
    lead: "Promote Workeasy360 and earn commission.",
    aliases: {
      "referral-link": "referral-links",
      // ADR-055: My Products became the Store Console's first tab.
      "my-products": "store-console",
      store: "store-console",
      "explore-products": "store-console/explore",
      links: "referral-links",
      codes: "referral-codes",
      materials: "marketing-materials",
      referrals: "leads",
      "leads-referrals": "leads",
      commissions: "payments/commissions",
      profile: "account",
      settings: "account",
      support: "support-manager",
    },
    groups: [
      { title: "Menu", entries: [dashboard()] },
      {
        title: "Promote",
        entries: [
          // ADR-055: the Store Console - My Products (ADR-054's page: what this
          // affiliate is approved to promote, a link for each page, feature and
          // update), Explore Products and My Requests, as tabs of one entry.
          storeConsole((can) => can("referral.view")),
          {
            key: "referral-links",
            label: "Referral Links",
            icon: "network",
            path: "referral-links",
            needs: (can) => can("referral.view"),
          },
          {
            key: "referral-codes",
            label: "Referral Codes",
            icon: "key",
            path: "referral-codes",
            needs: (can) => can("referral.view"),
          },
          {
            key: "campaigns",
            label: "Campaigns",
            icon: "bolt",
            path: "campaigns",
            needs: (can) => can("referral.view"),
          },
          {
            key: "materials",
            label: "Marketing Materials",
            icon: "sparkles",
            path: "marketing-materials",
            needs: (can) => can("referral.view"),
          },
        ],
      },
      {
        title: "Results",
        entries: [
          {
            key: "leads",
            label: "Leads & Referrals",
            icon: "users",
            path: "leads",
            needs: (can) => can("referral.view"),
          },
          {
            key: "conversions",
            label: "Conversions",
            icon: "bolt",
            path: "conversions",
            also: ["affiliates", "affiliates/conversions", "affiliates/settings"],
            needs: (can) => can("referral.view"),
          },
          commissions(),
          payouts(),
        ],
      },
      {
        title: "Account",
        entries: [
          reports(),
          { ...settings(), label: "Profile & Settings", also: [] },
          support(),
        ],
      },
    ],
  },
};

export const EXPERIENCE_ORDER: ExperienceKey[] = ["reseller", "white_label", "affiliate"];

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

/** Every page a workspace serves: its entries and what belongs to them. */
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

/** Whether the person's permissions make this page of the workspace useful: some
 * entry that owns it is drawn for them. Convenience only - the API refuses the rest. */
export function pageAllowed(def: WorkspaceDef, path: string, can: Can): boolean {
  return entriesFor(def, path).some((entry) => entry.needs(can));
}

/** The first page this person can open - where a landing goes when the dashboard
 * is not one they can read (an ended partner reads its commission and nothing else). */
export function firstAllowedPath(def: WorkspaceDef, can: Can): string | null {
  for (const group of def.groups) {
    for (const entry of group.entries) {
      if (entry.needs(can)) return entry.landing?.(can) ?? entry.path;
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
