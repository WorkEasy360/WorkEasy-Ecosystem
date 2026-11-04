import { describe, it, expect } from "vitest";
import {
  CONTAINS,
  EXPERIENCE_ORDER,
  LEVEL_ORDER,
  SHORT_ADDRESSES,
  WORKSPACES,
  entriesFor,
  experienceFromSlug,
  firstAllowedPath,
  isLocked,
  lockedEntryFor,
  pageAllowed,
  servedPaths,
  tabOf,
  tabbedEntryFor,
  unlockLevel,
  workspaceHome,
  type ExperienceKey,
} from "./experiences";
import { PAGE_PATHS } from "./workspacePages";

/**
 * The unified partner dashboard (2026-11-03): one fourteen-entry menu for every
 * partner level. These tests read the data file: the entries and their order,
 * which level sees which entry locked, what each entry and tab needs, and that
 * no page of the earlier menus was lost.
 */

const ALL = (...keys: string[]) => keys.some(() => true);
const HOLDING =
  (...held: string[]) =>
  (...keys: string[]) =>
    keys.some((k) => held.includes(k));

const entries = (key: ExperienceKey) => WORKSPACES[key].groups.flatMap((g) => g.entries);
const labels = (key: ExperienceKey) => entries(key).map((e) => e.label);
const locked = (key: ExperienceKey) => entries(key).filter((e) => isLocked(WORKSPACES[key], e)).map((e) => e.label);
const entry = (key: ExperienceKey, entryKey: string) => entries(key).find((e) => e.key === entryKey)!;

const FOURTEEN = [
  "Dashboard",
  "Affiliate",
  "Reseller",
  "White Label",
  "Product Store",
  "Customers",
  "Sales & Orders",
  "Commission & Earnings",
  "Wallet & Payouts",
  "Communication",
  "Staff",
  "Helpdesk",
  "Reports & Analytics",
  "Settings",
];

/**
 * Every page the earlier menus served (the 2026-11-02/03 consolidated menus,
 * copied from the tests that pinned them), by workspace. Each must still open
 * in its workspace - served, or an alias of a served page.
 */
const SHARED_OLD = [
  "dashboard",
  "upgrade",
  "upgrade/reseller",
  "upgrade/white-label",
  "payments/balance",
  "payments/commissions",
  "payouts",
  "affiliates/payouts",
  "affiliates",
  "affiliates/conversions",
  "affiliates/settings",
  "referral-links",
  "referral-codes",
  "campaigns",
  "marketing-materials",
  "leads",
  "conversions",
  "reports",
  "support-manager",
  "notifications",
  "account",
  "store",
];
const SELLER_OLD = [
  ...SHARED_OLD,
  "users",
  "users/subscriptions",
  "partner-products",
  "product-manager",
  "product-billing",
  "plans",
  "plans/add-ons",
  "pricing/currency",
  "pricing/packages",
  "pricing/rules",
  "payments",
  "agency",
  "promote/store",
  "promote/store-console",
  "promote/store-console/explore",
  "promote/store-console/requests",
  "team",
  "roles",
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
const OLD_PAGES: Record<ExperienceKey, string[]> = {
  affiliate: [...SHARED_OLD, "store-console", "store-console/explore", "store-console/requests"],
  reseller: [...SELLER_OLD, "store-console", "store-console/explore", "store-console/requests"],
  white_label: [
    ...SELLER_OLD,
    "brand-settings",
    "appearance",
    "brand-settings/url-manager",
    "brand-settings/white-label-setup",
  ],
};
/** The earlier menus' short addresses, each a page or an alias. */
const OLD_ALIASES: Record<ExperienceKey, string[]> = {
  affiliate: [
    "product-store",
    "referral-tools",
    "marketing-hub",
    "referral-performance",
    "earnings",
    "earnings-payouts",
    "my-products",
    "explore-products",
    "links",
    "codes",
    "materials",
    "referrals",
    "leads-referrals",
    "referral-link",
    "commissions",
    "profile",
    "settings",
    "support",
  ],
  reseller: [
    "product-store",
    "sales-management",
    "earnings",
    "earnings-payouts",
    "products",
    "explore-products",
    "pricing",
    "orders",
    "sales",
    "commissions",
    "customers",
    "customers/subscriptions",
    "subscriptions",
    "sub-resellers",
    "referrals",
    "settings",
    "support",
    "promote-products",
    "referral-tools",
    "marketing-hub",
    "referral-performance",
    "links",
    "codes",
    "materials",
  ],
  white_label: [
    "brand-studio",
    "product-store",
    "sales-billing",
    "customer-management",
    "earnings",
    "earnings-payouts",
    "partner-network",
    "brand",
    "domain",
    "domains",
    "branding",
    "products",
    "customers",
    "orders",
    "sub-resellers",
    "referrals",
    "settings",
    "support",
    "promote-products",
  ],
};

describe("one menu for every level", () => {
  it("every workspace draws the same fourteen entries, in the specification's order", () => {
    for (const key of EXPERIENCE_ORDER) expect(labels(key), key).toEqual(FOURTEEN);
  });

  it("each level sees the entries above it locked", () => {
    expect(locked("affiliate")).toEqual(["Reseller", "White Label", "Customers", "Sales & Orders", "Communication", "Staff"]);
    expect(locked("reseller")).toEqual(["White Label"]);
    expect(locked("white_label")).toEqual([]);
  });

  it("a locked entry names the level that opens it and says what it opens", () => {
    expect(unlockLevel(entry("affiliate", "reseller"))).toBe("reseller");
    expect(unlockLevel(entry("affiliate", "customers"))).toBe("reseller");
    expect(unlockLevel(entry("reseller", "white-label"))).toBe("white_label");
    for (const key of EXPERIENCE_ORDER) {
      for (const e of entries(key).filter((x) => isLocked(WORKSPACES[key], x))) {
        expect(e.pitch, `${key}/${e.key}`).toBeTruthy();
      }
    }
  });

  it("the levels are numbered 1-3 in the order a partner grows", () => {
    expect(LEVEL_ORDER).toEqual(["affiliate", "reseller", "white_label"]);
    expect(LEVEL_ORDER.map((k) => WORKSPACES[k].level.number)).toEqual([1, 2, 3]);
  });

  it("a locked entry's pages are never opened, whatever the permissions, and are named as locked", () => {
    const aff = WORKSPACES.affiliate;
    for (const path of ["users", "users/subscriptions", "payments", "product-billing", "plans", "agency", "team", "roles", "communication/channel", "brand-settings"]) {
      expect(pageAllowed(aff, path, ALL), path).toBe(false);
      expect(lockedEntryFor(aff, path), path).toBeDefined();
    }
    const res = WORKSPACES.reseller;
    for (const path of ["brand-settings", "brand-settings/url-manager", "brand-settings/white-label-setup", "appearance", "translations"]) {
      expect(pageAllowed(res, path, ALL), path).toBe(false);
      expect(lockedEntryFor(res, path)?.key, path).toBe("white-label");
    }
    expect(lockedEntryFor(res, "users")).toBeUndefined();
    expect(lockedEntryFor(WORKSPACES.white_label, "brand-settings")).toBeUndefined();
    expect(pageAllowed(WORKSPACES.white_label, "brand-settings", ALL)).toBe(true);
  });

  it("every page of the earlier menus still opens in its workspace (no page lost)", () => {
    for (const key of EXPERIENCE_ORDER) {
      const def = WORKSPACES[key];
      const served = servedPaths(def);
      for (const path of OLD_PAGES[key]) expect(served.has(path), `${key}: ${path}`).toBe(true);
      for (const alias of OLD_ALIASES[key]) {
        expect(served.has(alias) || def.aliases[alias] !== undefined, `${key}: ${alias}`).toBe(true);
      }
    }
    // A page the old affiliate menu did not serve is served now only as a locked one.
    for (const path of ["users", "plans", "agency", "team", "payments"]) {
      expect(lockedEntryFor(WORKSPACES.affiliate, path), path).toBeDefined();
    }
  });

  it("the new pages are served: wallet, disputes, desk, saved replies, margin calculator", () => {
    for (const key of EXPERIENCE_ORDER) {
      const served = servedPaths(WORKSPACES[key]);
      for (const path of ["wallet", "disputes", "support-manager", "margin-calculator"]) expect(served.has(path), `${key}: ${path}`).toBe(true);
    }
    expect(servedPaths(WORKSPACES.reseller).has("desk")).toBe(true);
    expect(servedPaths(WORKSPACES.reseller).has("desk/replies")).toBe(true);
    // An affiliate raises tickets; it has no customer desk.
    expect(servedPaths(WORKSPACES.affiliate).has("desk")).toBe(false);
  });

  it("each page sits in one entry and one tab", () => {
    const MAPPING: [ExperienceKey, string, string, string][] = [
      ["reseller", "store-console", "reseller", "My Products"],
      ["reseller", "partner-products", "reseller", "My Products"],
      ["reseller", "store-console/explore", "reseller", "Wholesale Catalogue"],
      ["reseller", "product-manager", "reseller", "Product Settings"],
      ["reseller", "pricing/rules", "reseller", "Pricing & Margins"],
      ["reseller", "agency", "reseller", "Downline Partners"],
      ["reseller", "store", "product-store", ""],
      ["reseller", "promote/store-console", "affiliate", "Promote Products"],
      ["reseller", "payments", "sales-orders", "Orders"],
      ["reseller", "product-billing", "sales-orders", "Product Billing"],
      ["reseller", "users/subscriptions", "customers", "Subscriptions"],
      ["reseller", "payments/balance", "commission", "Balance"],
      ["reseller", "affiliates/payouts", "wallet", "Payout History"],
      ["reseller", "communication/logs", "communication", "Channels"],
      ["reseller", "roles", "staff", "Roles"],
      ["reseller", "desk", "helpdesk", "Customer Desk"],
      ["reseller", "api-keys", "settings", "API Keys"],
      ["white_label", "appearance", "white-label", "Brand Settings"],
      ["white_label", "brand-settings/url-manager", "white-label", "Custom Domain"],
      ["white_label", "translations", "white-label", "Translations"],
      ["affiliate", "store-console", "product-store", "Approved / Promoting"],
      ["affiliate", "affiliates/conversions", "affiliate", "Conversions"],
      ["affiliate", "affiliates", "affiliate", "Link Overview"],
      ["affiliate", "marketing-materials", "affiliate", "Marketing Assets"],
    ];
    for (const [key, path, owner, tab] of MAPPING) {
      const owners = entriesFor(WORKSPACES[key], path).filter((e) => !isLocked(WORKSPACES[key], e));
      expect(owners.map((e) => e.key), `${key}: ${path}`).toEqual([owner]);
      if (tab) expect(tabOf(owners[0], path)?.label, `${key}: ${path}`).toBe(tab);
    }
    expect(servedPaths(WORKSPACES.affiliate).has("promote/store")).toBe(false);
  });

  it("no page is owned by two open entries", () => {
    for (const key of EXPERIENCE_ORDER) {
      const owners = new Map<string, string[]>();
      for (const e of entries(key).filter((x) => !isLocked(WORKSPACES[key], x))) {
        for (const path of [e.path, ...(e.also ?? [])]) owners.set(path, [...(owners.get(path) ?? []), e.key]);
      }
      for (const [path, keys] of owners) expect(keys, `${key}: ${path}`).toHaveLength(1);
    }
  });

  it("an affiliate's Store Console is drawn under its Product Store, not the locked Reseller entry", () => {
    const def = WORKSPACES.affiliate;
    for (const path of ["store-console", "store-console/explore", "store-console/requests"]) {
      expect(tabbedEntryFor(def, path)?.key, path).toBe("product-store");
      expect(lockedEntryFor(def, path), path).toBeUndefined();
      expect(pageAllowed(def, path, HOLDING("referral.view")), path).toBe(true);
    }
  });

  it("every page a workspace serves has a page behind it, and every alias lands on a served page", () => {
    for (const key of EXPERIENCE_ORDER) {
      const def = WORKSPACES[key];
      const served = servedPaths(def);
      for (const path of served) expect(PAGE_PATHS, `${key}: ${path}`).toContain(path);
      for (const [alias, target] of Object.entries(def.aliases)) {
        expect(served.has(target), `${key}: ${alias} -> ${target}`).toBe(true);
        expect(served.has(alias), `${key}: ${alias} is also a page`).toBe(false);
      }
    }
  });

  it("the containment of an upgraded partner's workspaces is unchanged", () => {
    expect(CONTAINS.reseller).toEqual(["affiliate"]);
    expect(CONTAINS.white_label).toEqual(["reseller", "affiliate"]);
    expect(CONTAINS.affiliate).toEqual([]);
  });
});

describe("tabs keep their own permission", () => {
  it("the Reseller entry's tabs each need their own view", () => {
    const def = WORKSPACES.reseller;
    expect(pageAllowed(def, "plans", HOLDING("pricing.view"))).toBe(true);
    expect(pageAllowed(def, "margin-calculator", HOLDING("pricing.view"))).toBe(true);
    expect(pageAllowed(def, "plans", HOLDING("product.view"))).toBe(false);
    expect(pageAllowed(def, "store-console", HOLDING("pricing.view"))).toBe(false);
    expect(pageAllowed(def, "agency", HOLDING("subpartner.view"))).toBe(true);
    expect(pageAllowed(def, "agency", HOLDING("pricing.view"))).toBe(false);
    expect(entry("reseller", "reseller").landing?.(HOLDING("pricing.view"))).toBe("plans");
    expect(entry("reseller", "reseller").landing?.(HOLDING("subpartner.view"))).toBe("agency");
  });

  it("Sales & Orders: orders need the customer view, product billing the product view", () => {
    const def = WORKSPACES.reseller;
    expect(pageAllowed(def, "payments", HOLDING("customer.view"))).toBe(true);
    expect(pageAllowed(def, "payments", HOLDING("product.view"))).toBe(false);
    expect(pageAllowed(def, "product-billing", HOLDING("product.view"))).toBe(true);
    expect(entry("reseller", "sales-orders").landing?.(HOLDING("product.view"))).toBe("product-billing");
  });

  it("the Helpdesk's desk tabs need the desk view; every member has My Tickets", () => {
    const def = WORKSPACES.reseller;
    expect(pageAllowed(def, "support-manager", HOLDING("dashboard.view"))).toBe(true);
    expect(pageAllowed(def, "desk", HOLDING("dashboard.view"))).toBe(false);
    expect(pageAllowed(def, "desk", HOLDING("dashboard.view", "desk.view"))).toBe(true);
    expect(pageAllowed(def, "desk/replies", HOLDING("dashboard.view", "desk.view"))).toBe(true);
    expect(entry("affiliate", "helpdesk").tabs?.map((t) => t.label)).toEqual(["My Tickets", "Notifications"]);
  });

  it("White Label: the domain is its own permission, not the brand's", () => {
    const def = WORKSPACES.white_label;
    expect(pageAllowed(def, "brand-settings/url-manager", HOLDING("domain.view"))).toBe(true);
    expect(pageAllowed(def, "brand-settings", HOLDING("domain.view"))).toBe(false);
    expect(pageAllowed(def, "brand-settings/url-manager", HOLDING("branding.view"))).toBe(false);
    expect(entry("white_label", "white-label").landing?.(HOLDING("domain.view"))).toBe("brand-settings/url-manager");
  });

  it("money: commissions need the commission view, the wallet the payout view", () => {
    const def = WORKSPACES.affiliate;
    expect(pageAllowed(def, "wallet", HOLDING("payout.view"))).toBe(true);
    expect(pageAllowed(def, "payouts", HOLDING("payout.view"))).toBe(true);
    expect(pageAllowed(def, "payments/commissions", HOLDING("payout.view"))).toBe(false);
    expect(pageAllowed(def, "disputes", HOLDING("commission.view"))).toBe(true);
    expect(pageAllowed(def, "wallet", HOLDING("commission.view"))).toBe(false);
  });

  it("promoting needs the referral view, page by page, at every level", () => {
    for (const key of EXPERIENCE_ORDER) {
      const def = WORKSPACES[key];
      for (const path of ["referral-links", "referral-codes", "campaigns", "marketing-materials", "conversions", "affiliates/settings"]) {
        expect(pageAllowed(def, path, HOLDING("commission.view")), `${key}: ${path}`).toBe(false);
        expect(pageAllowed(def, path, HOLDING("referral.view")), `${key}: ${path}`).toBe(true);
      }
    }
    expect(pageAllowed(WORKSPACES.reseller, "promote/store", HOLDING("referral.view"))).toBe(true);
    // An affiliate's Product Store needs the referral view; a seller's the product view.
    expect(pageAllowed(WORKSPACES.affiliate, "store", HOLDING("referral.view"))).toBe(true);
    expect(pageAllowed(WORKSPACES.reseller, "store", HOLDING("referral.view"))).toBe(false);
    expect(pageAllowed(WORKSPACES.reseller, "store", HOLDING("product.view"))).toBe(true);
  });
});

describe("permissions are the only input", () => {
  it("names only partner permissions, never a capability, a partner type or the landing preference", () => {
    const known = new Set([
      "dashboard.view",
      "customer.view",
      "product.view",
      "pricing.view",
      "commission.view",
      "payout.view",
      "subpartner.view",
      "referral.view",
      "member.view",
      "profile.view",
      "branding.view",
      "domain.view",
      "desk.view",
    ]);
    for (const key of EXPERIENCE_ORDER) {
      for (const e of entries(key)) {
        const asked = new Set<string>();
        const record = (...keys: string[]) => {
          keys.forEach((k) => asked.add(k));
          return false;
        };
        e.needs(record);
        e.landing?.(record);
        for (const tab of e.tabs ?? []) tab.needs?.(record);
        expect(asked.size, `${key}/${e.key} asks for something`).toBeGreaterThan(0);
        for (const k of asked) expect(known.has(k), `${key}/${e.key}: ${k}`).toBe(true);
      }
    }
  });

  const AFFILIATE = HOLDING("dashboard.view", "profile.view", "member.view", "referral.view", "commission.view", "payout.view", "product.view");

  it("an affiliate's permissions open the affiliate pages and none of the commercial ones", () => {
    const def = WORKSPACES.affiliate;
    for (const path of ["dashboard", "referral-links", "leads", "payments/commissions", "wallet", "payouts", "reports", "account", "support-manager"]) {
      expect(pageAllowed(def, path, AFFILIATE), path).toBe(true);
    }
    expect(pageAllowed(def, "users", AFFILIATE)).toBe(false);
    expect(pageAllowed(def, "plans", AFFILIATE)).toBe(false);
    expect(pageAllowed(def, "team", AFFILIATE)).toBe(false);
  });

  it("a finance role (money, no customers) is not offered the customer pages in the reseller workspace", () => {
    const finance = HOLDING("dashboard.view", "profile.view", "pricing.view", "commission.view", "payout.view", "product.view");
    const def = WORKSPACES.reseller;
    expect(pageAllowed(def, "users", finance)).toBe(false);
    expect(pageAllowed(def, "users/subscriptions", finance)).toBe(false);
    expect(pageAllowed(def, "payments", finance)).toBe(false);
    expect(pageAllowed(def, "payments/commissions", finance)).toBe(true);
    expect(pageAllowed(def, "plans", finance)).toBe(true);
  });

  it("a payout-only member lands where it can read, and an ended partner on its money", () => {
    const ended = HOLDING("commission.view", "payout.view");
    expect(pageAllowed(WORKSPACES.reseller, "dashboard", ended)).toBe(false);
    expect(firstAllowedPath(WORKSPACES.reseller, ended)).toBe("payments/commissions");
    expect(firstAllowedPath(WORKSPACES.reseller, HOLDING("payout.view"))).toBe("wallet");
    expect(firstAllowedPath(WORKSPACES.reseller, HOLDING("customer.view"))).toBe("users");
    // A locked entry is never a landing.
    expect(firstAllowedPath(WORKSPACES.affiliate, HOLDING("customer.view"))).toBeNull();
    expect(firstAllowedPath(WORKSPACES.affiliate, HOLDING())).toBeNull();
  });
});

describe("addresses", () => {
  it("a slug names a workspace, and nothing else does", () => {
    expect(experienceFromSlug("white-label")).toBe("white_label");
    expect(experienceFromSlug("reseller")).toBe("reseller");
    expect(experienceFromSlug("affiliate")).toBe("affiliate");
    for (const bad of ["white_label", "admin", "", "Reseller", "11111111-1111-1111-1111-111111111111"]) {
      expect(experienceFromSlug(bad), bad).toBeNull();
    }
  });

  it("a workspace opens on its dashboard under the partner portal's own prefix", () => {
    expect(workspaceHome("reseller")).toBe("/partner/reseller/dashboard");
    expect(workspaceHome("white_label")).toBe("/partner/white-label/dashboard");
    expect(workspaceHome("affiliate")).toBe("/partner/affiliate/dashboard");
  });

  it("the short addresses redirect into that prefix and into nothing else", () => {
    expect(SHORT_ADDRESSES).toContainEqual({ from: "/reseller/:path*", to: "/partner/reseller/:path*" });
    expect(SHORT_ADDRESSES).toContainEqual({ from: "/affiliate", to: "/partner/affiliate/dashboard" });
    for (const { to } of SHORT_ADDRESSES) expect(to.startsWith("/partner/")).toBe(true);
  });

  it("the specification's entry names are addresses too", () => {
    for (const key of EXPERIENCE_ORDER) {
      const a = WORKSPACES[key].aliases;
      expect(a["wallet-payouts"], key).toBe("wallet");
      expect(a.helpdesk, key).toBe("support-manager");
      expect(a.staff, key).toBe("team");
      expect(a["commission-earnings"], key).toBe("payments/commissions");
    }
  });

  it("entriesFor finds the entry that owns a page, and none for a stranger", () => {
    expect(entriesFor(WORKSPACES.reseller, "users").map((e) => e.key)).toEqual(["customers"]);
    expect(entriesFor(WORKSPACES.reseller, "pricing/rules").map((e) => e.key)).toEqual(["reseller"]);
    expect(entriesFor(WORKSPACES.affiliate, "no-such-page")).toEqual([]);
  });
});
