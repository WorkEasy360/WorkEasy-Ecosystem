import { describe, it, expect } from "vitest";
import { readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

import nextConfig from "../../../next.config";
import { APPROVED_LABELS, LEGACY_REDIRECTS, PARTNER_MENU } from "./menu";

/**
 * The partner portal's menu is a closed set (ADR-046). These tests are the
 * architecture pin: adding a sidebar item, a page or a route outside the
 * approved structure fails here, on purpose, and has to be decided.
 */

/** The approved sidebar, verbatim from the product brief. */
const APPROVED_SIDEBAR = [
  "Dashboards",
  "Users",
  "Agency",
  "Affiliates",
  "Plans & Add-ons",
  "Currency & Pricing",
  "Payments",
  "Product Billing",
  "Brand Settings",
  "Product Manager",
  "Appearance",
  "Communication",
  "Integrations",
  "Partner Products",
  "Auth Configuration",
  "Shared Resources",
  "Account",
  "Channel Manager",
  "API Key Manager",
  "Translation Manager",
  "Role & Permissions",
  "Team Members",
  "Support Manager",
  "Notification Center",
];

const APPROVED_GROUPS = [
  ["Menu", ["Dashboards"]],
  ["Audience", ["Users", "Agency", "Affiliates"]],
  ["Billing", ["Plans & Add-ons", "Currency & Pricing", "Payments", "Product Billing"]],
  ["Panel customization", ["Brand Settings", "Product Manager", "Appearance", "Communication", "Integrations"]],
  ["New product settings", ["Partner Products", "Auth Configuration", "Shared Resources"]],
  ["Settings", ["Account", "Channel Manager", "API Key Manager", "Translation Manager"]],
  ["Team", ["Role & Permissions", "Team Members"]],
  ["Help", ["Support Manager", "Notification Center"]],
] as const;

/** The tabs inside the top-level pages. They are routes, not sidebar items. */
const TAB_ROUTES = [
  "/users/subscriptions",
  "/affiliates/conversions",
  "/affiliates/payouts",
  "/affiliates/settings",
  "/plans/add-ons",
  "/pricing/packages",
  "/pricing/rules",
  "/payments/commissions",
  "/payments/balance",
  "/brand-settings/url-manager",
  "/brand-settings/white-label-setup",
  "/communication/templates",
  "/communication/alerts",
  "/communication/logs",
];

const APP = join(__dirname, "..", "..", "app", "partner", "[partnerId]");

function pageRoutes(dir = APP): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...pageRoutes(full));
    else if (name === "page.tsx") out.push(`/${relative(APP, dir).split(sep).join("/")}`.replace(/^\/\.$/, ""));
  }
  return out.map((r) => (r === "/" ? "" : r));
}

describe("the approved partner menu", () => {
  it("is exactly the approved 24 entries, in order", () => {
    expect(APPROVED_LABELS).toEqual(APPROVED_SIDEBAR);
    expect(APPROVED_LABELS).toHaveLength(24);
    expect(new Set(APPROVED_LABELS).size).toBe(24);
  });

  it("is grouped exactly as approved", () => {
    expect(PARTNER_MENU.map((g) => [g.title, g.entries.map((e) => e.label)])).toEqual(APPROVED_GROUPS.map(([t, e]) => [t, [...e]]));
  });

  it("has no sidebar entry that is a tab, an old name or a nested item", () => {
    const banned = ["Customers", "Sub-Partners", "Products", "Pricing", "Subscriptions", "Orders", "Commissions", "Payouts", "Currencies", "Partner Balance", "Payment Links", "Domains", "Users & Permissions", "Activity", "Settings", "Channels", "Templates", "Alerts", "Logs", "Authentication", "Branding", "Set up white-label"];
    for (const label of banned) expect(APPROVED_LABELS, label).not.toContain(label);
    for (const entry of PARTNER_MENU.flatMap((g) => g.entries)) {
      expect(Object.keys(entry).sort(), entry.key).toEqual(expect.arrayContaining(["key", "label", "icon", "path", "needs"]));
      expect(entry).not.toHaveProperty("children");
    }
  });

  it("names every entry by a permission of the partner portal and nothing else", () => {
    const asked = new Set<string>();
    for (const entry of PARTNER_MENU.flatMap((g) => g.entries)) {
      const can = (...keys: string[]) => {
        keys.forEach((k) => asked.add(k));
        return false;
      };
      entry.needs(can);
      entry.landing?.(can);
    }
    // That each key is declared by the backend's permission registry is pinned by
    // backend/tests/partner_menu/test_architecture.py (this suite runs without the backend).
    expect(asked.size).toBeGreaterThan(10);
    for (const key of asked) expect(key, key).toMatch(/^[a-z]+\.[a-z]+$/);
  });
});

describe("the partner routes", () => {
  it("serve only the approved pages and their tabs", () => {
    const entryPaths = PARTNER_MENU.flatMap((g) => g.entries).map((e) => e.path);
    const allowed = new Set([...entryPaths, ...TAB_ROUTES]);
    const found = pageRoutes();
    expect(found.filter((r) => !allowed.has(r))).toEqual([]);
    expect(entryPaths.filter((r) => !found.includes(r))).toEqual([]);
    expect(TAB_ROUTES.filter((r) => !found.includes(r))).toEqual([]);
  });

  it("have exactly one page per approved top-level segment, none left over from the old menu", () => {
    const segments = new Set(pageRoutes().map((r) => r.split("/")[1] ?? ""));
    expect([...segments].sort()).toEqual(
      [
        "",
        "account",
        "affiliates",
        "agency",
        "api-keys",
        "appearance",
        "auth-configuration",
        "brand-settings",
        "channel-manager",
        "communication",
        "integrations",
        "notifications",
        "partner-products",
        "payments",
        "plans",
        "pricing",
        "product-billing",
        "product-manager",
        "roles",
        "shared-resources",
        "support-manager",
        "team",
        "translations",
        "users",
      ].sort(),
    );
  });

  it("send every old address to the page that now holds the feature, and render nothing of their own", () => {
    const found = new Set(pageRoutes());
    const sources = new Set<string>();
    for (const { from, to } of LEGACY_REDIRECTS) {
      expect(found.has(from), `${from} must no longer be a page`).toBe(false);
      expect(found.has(to), `${to} must be a real page`).toBe(true);
      expect(from).not.toBe(to);
      expect(sources.has(from), `${from} listed twice`).toBe(false);
      sources.add(from);
    }
    // No chain: a destination is never itself redirected.
    for (const { to } of LEGACY_REDIRECTS) expect(sources.has(to), `${to} is redirected again`).toBe(false);
  });

  it("cover every route the old portal served", () => {
    const old = [
      "/customers", "/subscriptions", "/sub-partners", "/referrals", "/commissions", "/payouts", "/balance", "/orders", "/payment-links",
      "/currencies", "/pricing", "/pricing/currencies", "/branding", "/domains", "/white-label", "/products", "/authentication", "/settings", "/activity",
      "/communication", "/communication/channels",
    ];
    expect(old.filter((r) => !LEGACY_REDIRECTS.some((x) => x.from === r))).toEqual([]);
  });

  it("are served as permanent redirects by the app, from the same list", async () => {
    const rules = await nextConfig.redirects!();
    expect(rules).toHaveLength(LEGACY_REDIRECTS.length);
    for (const { from, to } of LEGACY_REDIRECTS) {
      expect(rules).toContainEqual({ source: `/partner/:partnerId${from}`, destination: `/partner/:partnerId${to}`, permanent: true });
    }
  });
});
