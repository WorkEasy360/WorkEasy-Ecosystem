import { describe, it, expect } from "vitest";
import type { SidebarGroup } from "@/components/sidebar/SidebarShell";
import { organizationNav, organizationTabs } from "./orgNav";

const ALL = [
  "platform.billing.view",
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

const shown = (groups: SidebarGroup[]) =>
  groups.map((g) => ({ title: g.title, items: g.items.filter((i) => i.when !== false).map((i) => i.label) })).filter((g) => g.items.length);
const current = (groups: SidebarGroup[]) => groups.flatMap((g) => g.items).filter((i) => i.when !== false && i.current).map((i) => i.label);

/** The 22 entries the sidebar listed before 2026-11-03: where each now lives. */
const MAPPING: [old: string, path: string, entry: string, tab: string][] = [
  ["Dashboard", "dashboard", "Dashboard", "Dashboard"],
  ["My products", "apps", "Product Hub", "My Products"],
  ["Store", "store", "Product Hub", "Store"],
  ["Explore products", "explore", "Product Hub", "Explore Products"],
  ["Subscriptions & billing", "settings/billing", "Billing & Orders", "Subscriptions & Billing"],
  ["Orders", "orders", "Billing & Orders", "Orders"],
  ["Organization", "settings/organization", "Organization & Team", "Organization"],
  ["Members", "settings/members", "Organization & Team", "Members"],
  ["Invitations", "settings/invitations", "Organization & Team", "Invitations"],
  ["Profile", "settings/profile", "Profile", "Profile"],
  ["Product access", "settings/products", "Product Access", "Product Access"],
  ["Integrations", "settings/integrations", "Integrations & Automation", "Integrations"],
  ["Automations", "settings/automations", "Integrations & Automation", "Automations"],
  ["AI", "settings/ai", "AI", "AI"],
  ["Analytics", "settings/analytics", "Analytics", "Analytics"],
  ["Authentication", "settings/security/authentication", "Login & Authentication", "Authentication"],
  ["Sign-in preferences", "settings/sign-in-preferences", "Login & Authentication", "Sign-in Preferences"],
  ["Network access", "settings/security/network", "Security Center", "Network Access"],
  ["My security", "settings/security", "Security Center", "My Security"],
  ["Audit log", "settings/audit", "Activity & Audit", "Audit Log"],
  ["Sign-in activity", "settings/sign-ins", "Activity & Audit", "Sign-in Activity"],
  ["Support", "help", "Support", "Support"],
];

const SINGLE = new Set(["Dashboard", "Profile", "Product Access", "AI", "Analytics", "Support"]);

describe("the organization sidebar (13 entries, 2026-11-03)", () => {
  it("shows an administrator the 13 entries under their headings", () => {
    expect(shown(organizationNav("acme", admin, "/o/acme/dashboard"))).toEqual([
      { title: "Menu", items: ["Dashboard", "Product Hub", "Billing & Orders"] },
      { title: "Organization & team", items: ["Organization & Team", "Profile"] },
      { title: "App management", items: ["Product Access", "Integrations & Automation", "AI", "Analytics"] },
      { title: "Security", items: ["Login & Authentication", "Security Center"] },
      { title: "Audit", items: ["Activity & Audit"] },
      { title: "Help", items: ["Support"] },
    ]);
  });

  it.each(MAPPING)("keeps %s at its address, under %s -> %s", (_old, path, entry, tab) => {
    const pathname = `/o/acme/${path}`;
    // Exactly one entry is current: no double highlight (My Security vs Network Access).
    expect(current(organizationNav("acme", admin, pathname))).toEqual([entry]);
    const tabs = organizationTabs("acme", admin, pathname);
    if (SINGLE.has(entry)) {
      expect(tabs).toEqual([]);
    } else {
      expect(tabs.filter((t) => t.current)).toEqual([{ href: pathname, label: tab, current: true }]);
    }
  });

  it("reaches every one of the 22 pages from the sidebar or a tab", () => {
    const reachable = new Set<string>();
    for (const { path } of MAPPING.map(([, path]) => ({ path }))) {
      const pathname = `/o/acme/${path}`;
      organizationNav("acme", admin, pathname).forEach((g) => g.items.forEach((i) => reachable.add(i.href)));
      organizationTabs("acme", admin, pathname).forEach((t) => reachable.add(t.href));
    }
    expect([...reachable].sort()).toEqual(MAPPING.map(([, path]) => `/o/acme/${path}`).sort());
  });

  it("keeps the pages reached from inside a tab under it", () => {
    expect(current(organizationNav("acme", admin, "/o/acme/store/easy_crm"))).toEqual(["Product Hub"]);
    expect(organizationTabs("acme", admin, "/o/acme/store/easy_crm").find((t) => t.current)?.label).toBe("Store");
    expect(current(organizationNav("acme", admin, "/o/acme/settings/webhooks"))).toEqual(["Integrations & Automation"]);
    expect(organizationTabs("acme", admin, "/o/acme/settings/webhooks").find((t) => t.current)?.label).toBe("Integrations");
  });

  it("draws no tabs and highlights nothing on a page no entry owns (checkout)", () => {
    expect(current(organizationNav("acme", admin, "/o/acme/checkout"))).toEqual([]);
    expect(organizationTabs("acme", admin, "/o/acme/checkout")).toEqual([]);
  });

  it("shows a plain member only what their permissions open, as before", () => {
    expect(shown(organizationNav("acme", member, "/o/acme/dashboard"))).toEqual([
      { title: "Menu", items: ["Dashboard", "Product Hub"] },
      { title: "Organization & team", items: ["Organization & Team", "Profile"] },
      { title: "Security", items: ["Login & Authentication", "Security Center"] },
      { title: "Help", items: ["Support"] },
    ]);
    // A member's entries open the page they may see, and draw only one tab - so none.
    const nav = organizationNav("acme", member, "/o/acme/dashboard").flatMap((g) => g.items);
    expect(nav.find((i) => i.label === "Login & Authentication")?.href).toBe("/o/acme/settings/sign-in-preferences");
    expect(nav.find((i) => i.label === "Security Center")?.href).toBe("/o/acme/settings/security");
    expect(nav.find((i) => i.label === "Organization & Team")?.href).toBe("/o/acme/settings/organization");
    expect(organizationTabs("acme", member, "/o/acme/settings/security")).toEqual([]);
    expect(organizationTabs("acme", member, "/o/acme/settings/organization")).toEqual([]);
  });

  it("gives each tab the permission its sidebar link needed", () => {
    const tabsFor = (can: (p: string) => boolean, path: string) =>
      organizationTabs("acme", can, `/o/acme/${path}`).map((t) => t.label);
    expect(tabsFor(holding("platform.member.view"), "settings/members")).toEqual(["Organization", "Members"]);
    expect(tabsFor(holding("platform.member.invite"), "settings/invitations")).toEqual(["Organization", "Invitations"]);
    expect(tabsFor(holding("commercial.subscription.view"), "orders")).toEqual(["Subscriptions & Billing", "Orders"]);
    expect(tabsFor(holding("platform.connection.view", "platform.automation.view"), "settings/automations")).toEqual([
      "Integrations",
      "Automations",
    ]);
    // Automations alone: a one-page entry that opens Automations.
    const automationsOnly = holding("platform.automation.view");
    expect(tabsFor(automationsOnly, "settings/automations")).toEqual([]);
    expect(
      organizationNav("acme", automationsOnly, "/o/acme/dashboard")
        .flatMap((g) => g.items)
        .find((i) => i.label === "Integrations & Automation")?.href,
    ).toBe("/o/acme/settings/automations");
    expect(tabsFor(holding("platform.security.view"), "settings/security")).toEqual(["Network Access", "My Security"]);
  });

  it("highlights nothing for a page the member's permissions do not open (as before)", () => {
    expect(current(organizationNav("acme", member, "/o/acme/settings/audit"))).toEqual([]);
    expect(organizationTabs("acme", member, "/o/acme/settings/audit")).toEqual([]);
  });
});
