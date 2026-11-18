import { describe, expect, it } from "vitest";
import { ADMIN_MODULES, adminModules, locate, relatedOf, trailOf } from "./adminModules";
import { adminNav } from "./adminNav";

const EVERYTHING = [
  ...new Set(
    ADMIN_MODULES.flatMap((m) => m.sections.flatMap((s) => s.pages.flatMap((p) => (p.needs === undefined ? [] : [p.needs].flat())))),
  ),
];

const tabs = (key: string, permissions = EVERYTHING) =>
  adminModules(permissions)
    .find((m) => m.key === key)
    ?.sections.map((s) => s.label);

describe("the Admin console's map", () => {
  it("has the nine sidebar entries the brief names, in its order, with no submenus", () => {
    const items = adminNav(EVERYTHING)[0].items;
    expect(items.map((i) => i.label)).toEqual([
      "Dashboard",
      "Customers",
      "Partners",
      "Products & Pricing",
      "Billing",
      "Wallets & Credits",
      "Support",
      "Reports",
      "Settings",
    ]);
    // Affiliate, Reseller and White Label are the Partners list's type chips now.
    expect(items.filter((i) => i.children || i.reorderKey)).toEqual([]);
  });

  it("gives each module the brief's one row of tabs", () => {
    expect(tabs("customers")).toEqual(["Organizations", "Users", "Product setup", "Product access"]);
    expect(tabs("partners")).toEqual(["Overview", "Partners", "Applications", "Commissions", "Payouts", "Requests"]);
    expect(tabs("products")).toEqual(["Products", "Plans", "Usage pricing", "Store"]);
    expect(tabs("billing")).toEqual(["Needs attention", "Orders", "Subscriptions", "Invoices", "Payments", "Refunds", "Credit notes"]);
    expect(tabs("wallets")).toEqual(["Platform wallet", "Consumption wallet", "AI credits", "Controls"]);
    expect(tabs("support")).toEqual(["Tickets", "Diagnostics", "Help articles"]);
    expect(tabs("reports")).toEqual(["Reports"]);
    expect(tabs("settings")).toEqual(["Platform settings", "Email templates", "AI platform", "Domains & apps", "Team & roles", "Security"]);
    expect(ADMIN_MODULES.find((m) => m.key === "settings")?.layout).toBe("menu");
  });

  it("keeps every page's own grant where it moved (hard rule 4)", () => {
    const needs = (href: string) => ADMIN_MODULES.flatMap((m) => m.sections.flatMap((s) => s.pages)).find((p) => p.href === href)?.needs;
    // From Billing into Partners, with Billing's grants.
    expect(needs("/admin/commissions")).toBe("platform.staff_commission.view");
    expect(needs("/admin/payouts")).toBe("platform.staff_commission.view");
    expect(needs("/admin/partners/withdrawals")).toBe("platform.staff_payout.manage");
    // Partner tickets joined Tickets: either grant opens it.
    expect(needs("/admin/tickets")).toEqual(["platform.staff_support.view", "platform.staff_partner.view"]);
    expect(needs("/admin/subscriptions")).toBe("platform.staff_customer.view");
    expect(needs("/admin/settings/login-clients")).toEqual(["platform.staff_settings.view", "platform.staff_integration.view"]);
  });

  it("shows a Billing-only staff member Partners with just the tabs their grants open", () => {
    const billing = [
      "platform.staff_customer.view",
      "platform.staff_payment.view",
      "platform.staff_commission.view",
      "platform.staff_payout.manage",
    ];
    expect(tabs("partners", billing)).toEqual(["Commissions", "Payouts"]);
    expect(tabs("billing", billing)).toEqual(["Needs attention", "Orders", "Subscriptions", "Invoices", "Payments", "Refunds", "Credit notes"]);
    const payouts = adminModules(billing).find((m) => m.key === "partners")!.sections.find((s) => s.label === "Payouts")!;
    expect(payouts.pages.map((p) => p.href)).toEqual(["/admin/payouts", "/admin/partners/withdrawals"]);
    // Payout management alone: Withdrawals is the tab's page.
    const alone = adminModules(["platform.staff_payout.manage"]).find((m) => m.key === "partners")!;
    expect(alone.sections.map((s) => [s.label, s.pages[0].href])).toEqual([["Payouts", "/admin/partners/withdrawals"]]);
  });

  it("opens Support for a staff member with only the partner grant, at Tickets", () => {
    const items = adminNav(["platform.staff_partner.view"])[0].items;
    expect(items.find((i) => i.label === "Support")?.href).toBe("/admin/tickets");
    expect(tabs("support", ["platform.staff_partner.view"])).toEqual(["Tickets"]);
  });

  it("opens Partners for a KYC reviewer at Applications", () => {
    const items = adminNav(["platform.staff_kyc.view"])[0].items;
    expect(items.find((i) => i.label === "Partners")?.href).toBe("/admin/partners/applications");
  });

  it("lists each address once", () => {
    const hrefs = ADMIN_MODULES.flatMap((m) => m.sections.flatMap((s) => s.pages.map((p) => p.href)));
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });

  it("always offers the dashboard, and a module only with a page this person may open", () => {
    expect(adminModules([]).map((m) => m.key)).toEqual(["dashboard"]);
    expect(adminModules(["platform.staff_audit.view"]).map((m) => m.key)).toEqual(["dashboard", "settings"]);
  });

  it("opens a module at the first page its holder may see", () => {
    const items = adminNav(["platform.staff_user.view"])[0].items;
    expect(items.find((i) => i.label === "Customers")?.href).toBe("/admin/users");
  });

  it("places an address by its longest match, says a record's page is one, and nothing for an unknown one", () => {
    const modules = adminModules(EVERYTHING);
    expect(locate("/admin", modules)?.module.key).toBe("dashboard");
    expect(locate("/admin/", modules)?.module.key).toBe("dashboard");
    expect(locate("/admin/orders", modules)?.page.label).toBe("Orders");
    expect(locate("/admin/payments/failed", modules)?.page.label).toBe("Needs attention");
    expect(locate("/admin/customers/abc", modules)).toMatchObject({ page: { label: "Organizations" }, detail: true });
    expect(locate("/admin/customers/setup", modules)).toMatchObject({ page: { label: "Product setup" }, detail: false });
    expect(locate("/admin/partners", modules)?.page.label).toBe("Overview");
    expect(locate("/admin/partners/upgrade-fees", modules)?.section.label).toBe("Requests");
    expect(locate(`/admin/partners/0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11/profile`, modules)).toMatchObject({ section: { label: "Partners" }, detail: true });
    // The list with its Add dialog open is the list, not a record.
    expect(locate("/admin/partners/new", modules)).toMatchObject({ section: { label: "Partners" }, detail: false });
    expect(locate("/admin/products/new", modules)).toMatchObject({ section: { label: "Products" }, detail: false });
    expect(locate("/admin/store", modules)).toMatchObject({ section: { label: "Store" }, page: { label: "Customer preview" } });
    expect(locate("/admin/store/coupons", modules)?.page.label).toBe("Coupons");
    expect(locate("/admin/commissions", modules)?.section.label).toBe("Commissions");
    expect(locate("/admin/notifications", modules)).toBeNull();
    expect(locate("/adminish", modules)).toBeNull();
  });

  it("names the breadcrumb Section › Tab, then the page when it is not the tab's own", () => {
    const modules = adminModules(EVERYTHING);
    const at = (path: string) => trailOf(locate(path, modules)!);
    expect(at("/admin/partners/applications")).toEqual([
      { label: "Partners", href: "/admin/partners" },
      { label: "Applications", href: "/admin/partners/applications" },
    ]);
    expect(at("/admin/partners/kyc-requirements")).toEqual([
      { label: "Partners", href: "/admin/partners" },
      { label: "Applications", href: "/admin/partners/applications" },
      { label: "KYC requirements", href: "/admin/partners/kyc-requirements" },
    ]);
    expect(at("/admin/audit")).toEqual([
      { label: "Settings", href: "/admin/settings" },
      { label: "Security" },
      { label: "Audit log", href: "/admin/audit" },
    ]);
    expect(at("/admin")).toEqual([{ label: "Dashboard", href: "/admin" }]);
  });

  it("offers a tab's other pages as buttons on its own page only", () => {
    const modules = adminModules(EVERYTHING);
    const related = (path: string) => relatedOf(locate(path, modules)!).map((p) => p.label);
    expect(related("/admin/partners/applications")).toEqual(["KYC requirements"]);
    expect(related("/admin/partners/commission-rules")).toEqual(["Commission entries"]);
    expect(related("/admin/payouts")).toEqual(["Withdrawals"]);
    expect(related("/admin/partners/product-requests")).toEqual(["Upgrade fees"]);
    expect(related("/admin/plans")).toEqual(["Currency & pricing"]);
    expect(related("/admin/usage-pricing")).toEqual(["Credit packs"]);
    expect(related("/admin/store/dashboard")).toEqual(["Customer preview", "Commercial rules", "Coupons"]);
    expect(related("/admin/partners/kyc-requirements")).toEqual([]);
    expect(related("/admin/partners/applications/0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11")).toEqual([]);
    // Settings lists its pages in its menu.
    expect(related("/admin/settings/login-clients")).toEqual([]);
  });

  it("offers the palette the filtered ways in, under the same grants", () => {
    const partners = adminModules(["platform.staff_partner.view"]).flatMap((m) => m.shortcuts ?? []);
    expect(partners.map((s) => s.href)).toEqual([
      "/admin/partners/all?type=affiliate",
      "/admin/partners/all?type=reseller",
      "/admin/partners/all?type=white_label",
      "/admin/tickets?audience=partner",
    ]);
    const reports = adminModules(["platform.staff_report.view"]).flatMap((m) => m.shortcuts ?? []);
    expect(reports).toHaveLength(8);
    expect(adminModules([]).flatMap((m) => m.shortcuts ?? [])).toEqual([]);
  });

  it("marks only the module holding the address as current", () => {
    const items = adminNav(EVERYTHING, "/admin/payments")[0].items;
    expect(items.filter((i) => i.current).map((i) => i.label)).toEqual(["Billing"]);
    const partners = adminNav(EVERYTHING, "/admin/commissions")[0].items;
    expect(partners.filter((i) => i.current).map((i) => i.label)).toEqual(["Partners"]);
  });
});
