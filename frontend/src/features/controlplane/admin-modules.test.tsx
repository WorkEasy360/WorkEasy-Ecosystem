import { describe, expect, it } from "vitest";
import { ADMIN_MODULES, adminModules, locate } from "./adminModules";
import { adminNav } from "./adminNav";

const EVERYTHING = [
  ...new Set(
    ADMIN_MODULES.flatMap((m) => m.sections.flatMap((s) => s.pages.flatMap((p) => (p.needs === undefined ? [] : [p.needs].flat())))),
  ),
];

describe("the Admin console's map", () => {
  it("has the modules the brief names, in its order, and nothing beneath them in the sidebar", () => {
    const items = adminNav(EVERYTHING)[0].items;
    expect(items.map((i) => i.label)).toEqual(ADMIN_MODULES.map((m) => m.label));
    expect(items.every((i) => !i.children)).toBe(true);
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

  it("places an address by its longest match, a record's own page, and nothing for an unknown one", () => {
    const modules = adminModules(EVERYTHING);
    expect(locate("/admin", modules)?.module.key).toBe("dashboard");
    expect(locate("/admin/", modules)?.module.key).toBe("dashboard");
    expect(locate("/admin/orders", modules)?.page.label).toBe("Orders");
    expect(locate("/admin/customers/abc", modules)?.page.label).toBe("Organizations");
    expect(locate("/admin/partners", modules)?.page.label).toBe("Partner overview");
    expect(locate("/admin/partners/product-requests", modules)?.page.label).toBe("Product requests");
    expect(locate("/admin/partners/0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11/profile", modules)?.page.label).toBe("All partners");
    expect(locate("/admin/notifications", modules)).toBeNull();
    expect(locate("/adminish", modules)).toBeNull();
  });

  it("marks only the module holding the address as current", () => {
    const items = adminNav(EVERYTHING, "/admin/payments")[0].items;
    expect(items.filter((i) => i.current).map((i) => i.label)).toEqual(["Billing & Subscriptions"]);
  });
});
