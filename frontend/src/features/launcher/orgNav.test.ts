import { describe, it, expect } from "vitest";
import type { SidebarGroup, SidebarItem } from "@/components/sidebar/SidebarShell";
import { organizationNav, organizationPages, organizationPlace, organizationTabs } from "./orgNav";

const ALL = [
  "platform.billing.view",
  "platform.billing.manage",
  "commercial.subscription.view",
  "platform.member.view",
  "platform.member.invite",
  "platform.seat.assign",
  "platform.connection.view",
  "platform.automation.view",
  "platform.ai.view",
  "platform.analytics.view",
  "platform.security.view",
  "platform.audit.view",
];
const holding = (...held: string[]) => (p: string) => held.includes(p);
const admin = holding(...ALL);
const member = holding("platform.organization.view");

const SEVEN = [
  "Dashboard",
  "My Products",
  "Billing & Subscriptions",
  "Wallets & Credits",
  "Organization & Team",
  "Help",
  "Settings",
];

const items = (groups: SidebarGroup[]): SidebarItem[] => groups.flatMap((g) => g.items);
const menus = (groups: SidebarGroup[]) => items(groups).filter((i) => i.when !== false).map((i) => i.label);
const current = (groups: SidebarGroup[]) => items(groups).filter((i) => i.current).map((i) => i.label);
const currentChild = (groups: SidebarGroup[]) =>
  items(groups)
    .flatMap((i) => i.children ?? [])
    .filter((c) => c.current)
    .map((c) => c.label);

/** Every page of the User Admin Panel: its menu, its sub-menu entry, and its tab
 * (null when the entry is one page). */
const PAGES: [path: string, menu: string, entry: string | null, tab: string | null][] = [
  ["dashboard", "Dashboard", null, null],
  ["apps", "My Products", "All Products", null],
  ["store", "My Products", "WorkEasy360 Store", null],
  ["explore", "My Products", "Plans & Trials", null],
  ["settings/billing", "Billing & Subscriptions", "Subscriptions", null],
  ["orders", "Billing & Subscriptions", "Orders & Payments", null],
  ["settings/billing/profile", "Billing & Subscriptions", "Billing Profile", null],
  ["wallets", "Wallets & Credits", "Overview", null],
  ["wallets/platform", "Wallets & Credits", "Platform Wallet", null],
  ["wallets/ai-credits", "Wallets & Credits", "AI Credits", null],
  ["wallets/consumption", "Wallets & Credits", "Consumption Wallet", null],
  ["wallets/usage", "Wallets & Credits", "Usage & Reports", null],
  ["settings/organization", "Organization & Team", "Organization", null],
  ["settings/members", "Organization & Team", "Members", "Members"],
  ["settings/invitations", "Organization & Team", "Members", "Invitations"],
  ["settings/products", "Organization & Team", "Product Access", null],
  ["help", "Help", null, null],
  ["settings/profile", "Settings", "My Profile", null],
  ["settings/security", "Settings", "Account & Security", "Password & Sessions"],
  ["settings/sign-in-preferences", "Settings", "Account & Security", "Sign-in Preferences"],
  ["settings/notifications", "Settings", "Notifications", null],
  ["settings/audit", "Settings", "Activity History", "Admin Activity"],
  ["settings/sign-ins", "Settings", "Activity History", "Sign-in Activity"],
  ["settings/security/authentication", "Settings", "Organization Security", "Authentication"],
  ["settings/security/network", "Settings", "Organization Security", "Network Access"],
  ["settings/integrations", "Settings", "Integrations & AI", "Integrations"],
  ["settings/automations", "Settings", "Integrations & AI", "Automations"],
  ["settings/ai", "Settings", "Integrations & AI", "AI"],
  ["settings/analytics", "Settings", "Integrations & AI", "Analytics"],
];

describe("the User Admin Panel sidebar (seven menus, 2026-11-11)", () => {
  it("shows exactly the seven approved menus, in order, to an administrator", () => {
    expect(menus(organizationNav("acme", admin, "/o/acme/dashboard"))).toEqual(SEVEN);
  });

  it("shows the same seven menus to a plain member", () => {
    expect(menus(organizationNav("acme", member, "/o/acme/dashboard"))).toEqual(SEVEN);
  });

  it.each(PAGES)("keeps %s at its address, under %s -> %s", (path, menu, entry, tab) => {
    const pathname = `/o/acme/${path}`;
    const nav = organizationNav("acme", admin, pathname);
    // Exactly one menu is current, and at most one of its entries: no double highlight.
    expect(current(nav)).toEqual([menu]);
    expect(currentChild(nav)).toEqual(entry ? [entry] : []);
    const tabs = organizationTabs("acme", admin, pathname);
    if (tab) expect(tabs.filter((t) => t.current)).toEqual([{ href: pathname, label: tab, current: true }]);
    else expect(tabs).toEqual([]);
  });

  it("reaches every page from a menu, a sub-menu or a tab", () => {
    const reachable = new Set<string>();
    for (const [path] of PAGES) {
      const pathname = `/o/acme/${path}`;
      for (const item of items(organizationNav("acme", admin, pathname))) {
        if (!item.children) reachable.add(item.href);
        item.children?.forEach((c) => reachable.add(c.href));
      }
      organizationTabs("acme", admin, pathname).forEach((t) => reachable.add(t.href));
    }
    expect([...reachable].sort()).toEqual(PAGES.map(([path]) => `/o/acme/${path}`).sort());
  });

  it("draws a sub-menu only for a menu with more than one entry", () => {
    const nav = items(organizationNav("acme", admin, "/o/acme/dashboard"));
    expect(nav.filter((i) => !i.children).map((i) => i.label)).toEqual(["Dashboard", "Help"]);
    expect(nav.find((i) => i.label === "Wallets & Credits")?.children?.map((c) => c.label)).toEqual([
      "Overview",
      "Platform Wallet",
      "AI Credits",
      "Consumption Wallet",
      "Usage & Reports",
    ]);
  });

  it("keeps the pages reached from inside an entry under it", () => {
    expect(current(organizationNav("acme", admin, "/o/acme/store/easy_crm"))).toEqual(["My Products"]);
    expect(currentChild(organizationNav("acme", admin, "/o/acme/store/easy_crm"))).toEqual(["WorkEasy360 Store"]);
    expect(current(organizationNav("acme", admin, "/o/acme/settings/webhooks"))).toEqual(["Settings"]);
    expect(organizationTabs("acme", admin, "/o/acme/settings/webhooks").find((t) => t.current)?.label).toBe("Integrations");
  });

  it("draws no tabs and highlights nothing on a page no entry owns (checkout)", () => {
    expect(current(organizationNav("acme", admin, "/o/acme/checkout"))).toEqual([]);
    expect(organizationTabs("acme", admin, "/o/acme/checkout")).toEqual([]);
    expect(organizationPlace("acme", admin, "/o/acme/checkout")).toBeNull();
  });

  it("gives a plain member only the entries their permissions open", () => {
    const nav = items(organizationNav("acme", member, "/o/acme/dashboard"));
    const settings = nav.find((i) => i.label === "Settings");
    expect(settings?.children?.map((c) => c.label)).toEqual(["My Profile", "Account & Security", "Notifications"]);
    expect(nav.find((i) => i.label === "My Products")?.children?.map((c) => c.label)).toEqual([
      "All Products",
      "WorkEasy360 Store",
      "Plans & Trials",
    ]);
    // One entry: a plain link to it.
    expect(nav.find((i) => i.label === "Organization & Team")).toMatchObject({ href: "/o/acme/settings/organization" });
    expect(nav.find((i) => i.label === "Organization & Team")?.children).toBeUndefined();
    // No billing permission: the menu still leads to its first page, which says who can help.
    expect(nav.find((i) => i.label === "Billing & Subscriptions")).toMatchObject({ href: "/o/acme/settings/billing" });
    expect(nav.find((i) => i.label === "Wallets & Credits")).toMatchObject({ href: "/o/acme/wallets" });
    // A one-page entry for them: no tabs on Password & Sessions.
    expect(organizationTabs("acme", member, "/o/acme/settings/security").map((t) => t.label)).toEqual([
      "Password & Sessions",
      "Sign-in Preferences",
    ]);
    expect(organizationTabs("acme", member, "/o/acme/settings/organization")).toEqual([]);
  });

  it("gives each tab the permission its page needs", () => {
    const tabsFor = (can: (p: string) => boolean, path: string) =>
      organizationTabs("acme", can, `/o/acme/${path}`).map((t) => t.label);
    expect(tabsFor(holding("platform.member.view"), "settings/members")).toEqual([]);
    expect(tabsFor(holding("platform.member.view", "platform.member.invite"), "settings/invitations")).toEqual([
      "Members",
      "Invitations",
    ]);
    expect(tabsFor(holding("platform.connection.view", "platform.automation.view"), "settings/automations")).toEqual([
      "Integrations",
      "Automations",
    ]);
    expect(tabsFor(holding("platform.security.view"), "settings/security/network")).toEqual(["Authentication", "Network Access"]);
  });

  it("lists each page once for the search, under its menu", () => {
    const pages = organizationPages("acme", admin);
    expect(new Set(pages.map((p) => p.href)).size).toBe(pages.length);
    expect(pages.find((p) => p.href === "/o/acme/orders")).toMatchObject({ label: "Orders & Payments", module: "Billing & Subscriptions" });
    expect(organizationPages("acme", member).some((p) => p.href.includes("/wallets"))).toBe(false);
  });

  it("says where the open page sits, for the top bar", () => {
    expect(organizationPlace("acme", admin, "/o/acme/wallets/ai-credits")).toEqual({ module: "Wallets & Credits", section: "AI Credits" });
  });
});
