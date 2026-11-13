import { describe, it, expect } from "vitest";
import {
  CONTAINS,
  EXPERIENCE_ORDER,
  SHORT_ADDRESSES,
  WORKSPACES,
  entriesFor,
  entryHref,
  entryVisible,
  experienceFromSlug,
  firstAllowedPath,
  isLocked,
  pageAllowed,
  servedPaths,
  tabOf,
  tabVisible,
  tabbedEntryFor,
  workspaceHome,
  type ExperienceKey,
} from "./experiences";
import { PAGE_PATHS } from "./workspacePages";

/**
 * The Partner Admin Panel of 08 November 2026 (ADR-063): exactly eight menus,
 * the same for every partner, every other page a tab of one of them. These tests
 * read the data file: the entries and their order, what each entry and tab needs,
 * what a partner that may not trade yet sees, and that no page of the earlier
 * menus was lost.
 */

const ALL = (...keys: string[]) => keys.some(() => true);
const HOLDING =
  (...held: string[]) =>
  (...keys: string[]) =>
    keys.some((k) => held.includes(k));

const entries = (key: ExperienceKey) => WORKSPACES[key].groups.flatMap((g) => g.entries);
const labels = (key: ExperienceKey) => entries(key).map((e) => e.label);
const entry = (key: ExperienceKey, entryKey: string) => entries(key).find((e) => e.key === entryKey)!;

const EIGHT = [
  "Dashboard",
  "Products & Referrals",
  "Customers",
  "Billing & Subscriptions",
  "Platform Wallet",
  "Earnings & Payouts",
  "Help",
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


describe("exactly eight menus", () => {
  it("every workspace draws the same eight entries, in the specification's order", () => {
    for (const key of EXPERIENCE_ORDER) expect(labels(key), key).toEqual(EIGHT);
  });

  it("nothing is drawn locked: a module the partner may not use is not drawn at all", () => {
    for (const key of EXPERIENCE_ORDER) {
      for (const e of entries(key)) expect(isLocked(WORKSPACES[key], e), `${key}/${e.key}`).toBe(false);
    }
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
  });

  it("the specification's new pages are served", () => {
    const fresh = [
      "eligible-products",
      "sales-resources",
      "customers/setup",
      "customers/activate",
      "billing-records",
      "platform-wallet",
      "platform-wallet/transactions",
      "help",
      "business-kyc",
    ];
    for (const key of EXPERIENCE_ORDER) {
      const served = servedPaths(WORKSPACES[key]);
      for (const path of fresh) expect(served.has(path), `${key}: ${path}`).toBe(true);
    }
  });

  it("each page sits in exactly one entry and one tab", () => {
    for (const key of EXPERIENCE_ORDER) {
      const def = WORKSPACES[key];
      for (const path of servedPaths(def)) {
        const owners = entriesFor(def, path);
        expect(owners.length, `${key}: ${path}`).toBe(1);
        const owner = owners[0];
        if (owner.tabs) expect(tabOf(owner, path), `${key}: ${path} has a tab`).toBeDefined();
      }
    }
  });

  it("the tabs follow the specification's sub-menus", () => {
    const tabs = (k: string) => entry("reseller", k).tabs!.map((t) => t.label);
    expect(tabs("dashboard")).toEqual(["Overview", "Reports", "Notifications"]);
    expect(tabs("products")).toEqual(["Eligible Products", "Referral Links", "Referral Results", "Sales Resources", "Product Store", "Pricing & Margins"]);
    expect(tabs("customers")).toEqual(["Assigned Organizations", "Products & Setup", "Plan Activation", "Downline Partners"]);
    expect(tabs("billing")).toEqual(["Customer Subscriptions", "Billing Records", "Customer Orders"]);
    expect(tabs("platform-wallet")).toEqual(["Balance & Recharge", "Transactions"]);
    expect(tabs("earnings")).toEqual(["Commissions", "Payouts", "Disputes"]);
    expect(tabs("help")).toEqual(["Support Tickets", "Help Content", "Customer Desk"]);
    expect(tabs("settings")).toEqual(["Business & KYC", "My Account & Security", "Staff", "White Label", "Communication", "Sign-in & API"]);
  });

  it("every served page has a page behind it, and every alias lands on a served page that is not itself an alias", () => {
    for (const key of EXPERIENCE_ORDER) {
      const def = WORKSPACES[key];
      const served = servedPaths(def);
      for (const path of served) expect(PAGE_PATHS.includes(path), `${key}: ${path} has a page`).toBe(true);
      for (const [alias, target] of Object.entries(def.aliases)) {
        expect(served.has(target), `${key}: ${alias} -> ${target}`).toBe(true);
        expect(served.has(alias), `${key}: ${alias} is a page, not an alias`).toBe(false);
      }
    }
  });

  it("the containment of an upgraded partner's workspaces is unchanged", () => {
    expect(CONTAINS).toEqual({ affiliate: [], reseller: ["affiliate"], white_label: ["reseller", "affiliate"] });
  });
});

describe("each module follows the permission it needs", () => {
  const def = WORKSPACES.reseller;
  const pages = (can: (...k: string[]) => boolean, paths: string[]) => paths.filter((p) => pageAllowed(def, p, can));

  it("Customers is a reseller's: assigned organizations need the customer view, activation selling", () => {
    expect(entryVisible(entry("reseller", "customers"), HOLDING("referral.view", "commission.view"))).toBe(false);
    expect(pages(HOLDING("customer.view"), ["users", "customers/setup", "customers/activate"])).toEqual(["users", "customers/setup"]);
    expect(pages(HOLDING("customer.view", "pricing.view"), ["customers/activate"])).toEqual(["customers/activate"]);
  });

  it("the Platform Wallet needs the wallet permission, which an affiliate never holds", () => {
    expect(entryVisible(entry("reseller", "platform-wallet"), HOLDING("wallet.view"))).toBe(true);
    expect(entryVisible(entry("affiliate", "platform-wallet"), HOLDING("referral.view", "commission.view", "payout.view"))).toBe(false);
  });

  it("referral links and results need the referral view; pricing needs the pricing view", () => {
    expect(pages(HOLDING("referral.view"), ["referral-links", "leads", "plans"])).toEqual(["referral-links", "leads"]);
    expect(pages(HOLDING("pricing.view"), ["referral-links", "leads", "plans", "margin-calculator"])).toEqual(["plans", "margin-calculator"]);
  });

  it("White Label settings need the brand or domain view - an approved White Label permission", () => {
    expect(pages(HOLDING("profile.view"), ["brand-settings", "brand-settings/url-manager"])).toEqual([]);
    expect(pages(HOLDING("profile.view", "domain.view"), ["brand-settings/url-manager"])).toEqual(["brand-settings/url-manager"]);
  });

  it("the customer desk needs the desk view; every member has support tickets and help content", () => {
    expect(pages(HOLDING("dashboard.view"), ["support-manager", "help", "desk"])).toEqual(["support-manager", "help"]);
    expect(pages(HOLDING("dashboard.view", "desk.view"), ["desk", "desk/replies"])).toEqual(["desk", "desk/replies"]);
  });

  it("commissions need the commission view, payouts the payout view", () => {
    expect(pages(HOLDING("payout.view"), ["payments/commissions", "wallet", "disputes"])).toEqual(["wallet"]);
    expect(entryHref(entry("reseller", "earnings"), HOLDING("payout.view"))).toBe("wallet");
  });

  it("names only partner permissions, never a capability, a partner type or the landing preference", () => {
    const known = new Set([
      "dashboard.view",
      "customer.view",
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
      "wallet.view",
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

  it("an affiliate sees its six modules and none of a reseller's", () => {
    const shown = entries("affiliate").filter((e) => entryVisible(e, AFFILIATE)).map((e) => e.label);
    expect(shown).toEqual(["Dashboard", "Products & Referrals", "Billing & Subscriptions", "Earnings & Payouts", "Help", "Settings"]);
    for (const path of ["users", "customers/activate", "platform-wallet", "plans", "agency"]) {
      expect(pageAllowed(WORKSPACES.affiliate, path, AFFILIATE), path).toBe(false);
    }
  });

  it("a payout-only member lands where it can read, and an ended partner on its money", () => {
    expect(firstAllowedPath(def, HOLDING("payout.view"))).toBe("wallet");
    expect(firstAllowedPath(def, HOLDING("commission.view", "payout.view"))).toBe("payments/commissions");
  });
});

describe("before a partner may trade (ADR-063)", () => {
  const ONBOARDING = HOLDING("dashboard.view", "profile.view");

  it("only onboarding status, support, business and KYC, and security are drawn", () => {
    for (const key of EXPERIENCE_ORDER) {
      const shown = entries(key).filter((e) => entryVisible(e, ALL, false)).map((e) => e.label);
      expect(shown, key).toEqual(["Dashboard", "Help", "Settings"]);
      const tabs = entries(key)
        .filter((e) => entryVisible(e, ALL, false))
        .flatMap((e) => (e.tabs ?? []).filter((t) => tabVisible(t, ALL, false)).map((t) => t.label));
      expect(tabs, key).toEqual(["Overview", "Notifications", "Support Tickets", "Help Content", "Business & KYC", "My Account & Security"]);
    }
  });

  it("no commercial page opens, whatever the permissions say", () => {
    for (const path of ["referral-links", "users", "customers/activate", "platform-wallet", "wallet", "reports", "team", "api-keys"]) {
      expect(pageAllowed(WORKSPACES.reseller, path, ALL, false), path).toBe(false);
    }
    for (const path of ["dashboard", "support-manager", "help", "business-kyc", "account", "notifications"]) {
      expect(pageAllowed(WORKSPACES.reseller, path, ONBOARDING, false), path).toBe(true);
    }
  });

  it("Settings opens on Business & KYC", () => {
    expect(entryHref(entry("reseller", "settings"), ONBOARDING, false)).toBe("business-kyc");
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

  it("the specification's names are addresses too", () => {
    for (const key of EXPERIENCE_ORDER) {
      const a = WORKSPACES[key].aliases;
      expect(a["products-referrals"], key).toBe("eligible-products");
      expect(a["plan-activation"], key).toBe("customers/activate");
      expect(a["add-money"], key).toBe("platform-wallet");
      expect(a["wallet-payouts"], key).toBe("wallet");
      expect(a.helpdesk, key).toBe("support-manager");
      expect(a.staff, key).toBe("team");
    }
  });

  it("entriesFor finds the entry that owns a page, and none for a stranger", () => {
    expect(entriesFor(WORKSPACES.reseller, "users").map((e) => e.key)).toEqual(["customers"]);
    expect(entriesFor(WORKSPACES.reseller, "pricing/rules").map((e) => e.key)).toEqual(["products"]);
    expect(tabbedEntryFor(WORKSPACES.affiliate, "store-console")?.key).toBe("products");
    expect(entriesFor(WORKSPACES.affiliate, "no-such-page")).toEqual([]);
  });
});
