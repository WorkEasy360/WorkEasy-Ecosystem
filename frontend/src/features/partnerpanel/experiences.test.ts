import { describe, it, expect } from "vitest";
import {
  EXPERIENCE_ORDER,
  SHORT_ADDRESSES,
  WORKSPACES,
  entriesFor,
  experienceFromSlug,
  firstAllowedPath,
  pageAllowed,
  servedPaths,
  workspaceHome,
  type ExperienceKey,
} from "./experiences";
import { PAGE_PATHS } from "./workspacePages";

/**
 * The three workspaces are navigation compositions over one panel (ADR-047). These
 * tests read the data file: which entries each workspace has, what each one needs,
 * and that nothing in it decides anything beyond a permission.
 */

const ALL = (...keys: string[]) => keys.some(() => true);
const HOLDING = (...held: string[]) => (...keys: string[]) => keys.some((k) => held.includes(k));

const labels = (key: ExperienceKey, can = ALL) =>
  WORKSPACES[key].groups.flatMap((g) => g.entries.filter((e) => e.needs(can)).map((e) => e.label));

describe("what each workspace offers", () => {
  it("Reseller is the approved list", () => {
    expect(labels("reseller")).toEqual([
      "Dashboard",
      "Customers",
      "Products & Plans",
      "Pricing & Margins",
      "Sales & Orders",
      "Subscriptions",
      "Commissions",
      "Payouts",
      "Sub-Resellers",
      "Referrals",
      "Team & Permissions",
      "Reports & Analytics",
      "Support",
      "Settings",
    ]);
  });

  it("White-label adds the brand, domain and website entries to the commercial ones", () => {
    expect(labels("white_label")).toEqual([
      "Dashboard",
      "Brand Settings",
      "Custom Domain",
      "Website Branding",
      "Products & Plans",
      "Pricing & Plans",
      "Customers",
      "Subscriptions",
      "Orders & Billing",
      "Commissions",
      "Payouts",
      "Sub-Resellers",
      "Referrals",
      "Team & Permissions",
      "Reports & Analytics",
      "Support",
      "Settings",
    ]);
    expect(WORKSPACES.white_label.groups.map((g) => g.title)).toContain("Commissions & Payouts");
  });

  it("Affiliate is the short list", () => {
    expect(labels("affiliate")).toEqual([
      "Dashboard",
      "Referral Links",
      "Referral Codes",
      "Campaigns",
      "Marketing Materials",
      "Leads & Referrals",
      "Conversions",
      "Commissions",
      "Payouts",
      "Reports & Analytics",
      "Profile & Settings",
      "Support",
    ]);
  });

  it("the affiliate workspace serves none of the customer, pricing, brand or sub-partner pages", () => {
    const served = servedPaths(WORKSPACES.affiliate);
    for (const path of [
      "users",
      "users/subscriptions",
      "plans",
      "pricing/currency",
      "pricing/packages",
      "partner-products",
      "product-manager",
      "product-billing",
      "payments",
      "agency",
      "brand-settings",
      "brand-settings/url-manager",
      "brand-settings/white-label-setup",
      "appearance",
      "team",
      "api-keys",
    ]) {
      expect(served.has(path), path).toBe(false);
    }
  });

  it("the reseller workspace serves no affiliate-only page and no brand page", () => {
    const served = servedPaths(WORKSPACES.reseller);
    for (const path of ["referral-links", "referral-codes", "campaigns", "marketing-materials", "leads", "brand-settings", "brand-settings/url-manager"]) {
      expect(served.has(path), path).toBe(false);
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

  it("no path is listed twice in one workspace under different entries by accident", () => {
    for (const key of EXPERIENCE_ORDER) {
      const owners = new Map<string, string[]>();
      for (const g of WORKSPACES[key].groups) {
        for (const e of g.entries) {
          for (const path of [e.path, ...(e.also ?? [])]) owners.set(path, [...(owners.get(path) ?? []), e.key]);
        }
      }
      // A page may belong to two entries (payments/commissions is also reached from the
      // payouts entry's family) - but never the same entry twice.
      for (const [path, entries] of owners) expect(new Set(entries).size, `${key}: ${path}`).toBe(entries.length);
    }
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
    ]);
    for (const key of EXPERIENCE_ORDER) {
      for (const entry of WORKSPACES[key].groups.flatMap((g) => g.entries)) {
        const asked = new Set<string>();
        entry.needs((...keys) => {
          keys.forEach((k) => asked.add(k));
          return false;
        });
        entry.landing?.((...keys) => {
          keys.forEach((k) => asked.add(k));
          return false;
        });
        expect(asked.size, `${key}/${entry.key} asks for something`).toBeGreaterThan(0);
        for (const k of asked) expect(known.has(k), `${key}/${entry.key}: ${k}`).toBe(true);
      }
    }
  });

  const AFFILIATE = HOLDING("dashboard.view", "profile.view", "member.view", "referral.view", "commission.view", "payout.view", "product.view");

  it("an affiliate's permissions open the affiliate pages and none of the commercial ones", () => {
    const def = WORKSPACES.affiliate;
    for (const path of ["dashboard", "referral-links", "leads", "payments/commissions", "payouts", "reports", "account", "support-manager"]) {
      expect(pageAllowed(def, path, AFFILIATE), path).toBe(true);
    }
    expect(pageAllowed(def, "users", AFFILIATE)).toBe(false);
    expect(pageAllowed(def, "plans", AFFILIATE)).toBe(false);
  });

  it("a finance role (money, no customers) is not offered the customer pages in the reseller workspace", () => {
    const finance = HOLDING("dashboard.view", "profile.view", "pricing.view", "commission.view", "payout.view", "product.view");
    const def = WORKSPACES.reseller;
    expect(pageAllowed(def, "users", finance)).toBe(false);
    expect(pageAllowed(def, "users/subscriptions", finance)).toBe(false);
    expect(pageAllowed(def, "payments/commissions", finance)).toBe(true);
    expect(pageAllowed(def, "plans", finance)).toBe(true);
  });

  it("a payout-only member lands where it can read, and an ended partner on its money", () => {
    const ended = HOLDING("commission.view", "payout.view");
    expect(pageAllowed(WORKSPACES.reseller, "dashboard", ended)).toBe(false);
    expect(firstAllowedPath(WORKSPACES.reseller, ended)).toBe("payments/commissions");
    expect(firstAllowedPath(WORKSPACES.affiliate, HOLDING())).toBeNull();
  });

  it("white-label's brand entries need the brand and domain views", () => {
    const def = WORKSPACES.white_label;
    const none = HOLDING("dashboard.view");
    expect(pageAllowed(def, "brand-settings", none)).toBe(false);
    expect(pageAllowed(def, "brand-settings", HOLDING("branding.view"))).toBe(true);
    expect(pageAllowed(def, "brand-settings/url-manager", HOLDING("branding.view"))).toBe(false);
    expect(pageAllowed(def, "brand-settings/url-manager", HOLDING("domain.view"))).toBe(true);
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

  it("entriesFor finds the entry that owns a page, and none for a stranger", () => {
    expect(entriesFor(WORKSPACES.reseller, "users").map((e) => e.key)).toEqual(["customers"]);
    expect(entriesFor(WORKSPACES.reseller, "pricing/rules").map((e) => e.key)).toEqual(["pricing"]);
    expect(entriesFor(WORKSPACES.affiliate, "users")).toEqual([]);
  });
});
