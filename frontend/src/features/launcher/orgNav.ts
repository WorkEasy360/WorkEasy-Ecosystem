import type { SidebarGroup } from "@/components/sidebar/SidebarShell";

/** One tab of a consolidated sidebar entry: a page that keeps its own address. */
export interface OrgTab {
  /** The page, below `/o/<slug>/`. */
  path: string;
  label: string;
  /** Drawn only when true. Navigation convenience, never security: the page
   * and every request behind it are authorized again by the backend. */
  when?: boolean;
  /** More pages the tab is current on (a page reached only from inside it). */
  also?: string[];
  /** The pages beneath `path` are the tab's too (`store/<product>`). */
  nested?: boolean;
}

/** One sidebar entry (2026-11-03): a single page, or a parent whose pages are tabs. */
export interface OrgModule {
  label: string;
  icon: string;
  tabs: OrgTab[];
}

/**
 * The organization console's sidebar: 13 entries in place of the 22 pages it
 * listed before 2026-11-03. Navigation only - every page keeps its address
 * (old links and bookmarks open the same page, now under its parent's tabs),
 * and each tab appears only when the member holds the permission its page
 * needs, exactly as its sidebar link did.
 */
export function organizationModules(can: (permission: string) => boolean): { title: string; modules: OrgModule[] }[] {
  const billing = can("platform.billing.view") || can("commercial.subscription.view");
  const security = can("platform.security.view");
  const audit = can("platform.audit.view");
  return [
    {
      title: "Menu",
      modules: [
        { label: "Dashboard", icon: "dashboard", tabs: [{ path: "dashboard", label: "Dashboard" }] },
        {
          label: "Product Hub",
          icon: "apps",
          tabs: [
            { path: "apps", label: "My Products" },
            { path: "store", label: "Store", nested: true },
            { path: "explore", label: "Explore Products" },
          ],
        },
        {
          label: "Billing & Orders",
          icon: "card",
          tabs: [
            { path: "settings/billing", label: "Subscriptions & Billing", when: billing },
            { path: "orders", label: "Orders", when: billing },
          ],
        },
      ],
    },
    {
      title: "Organization & team",
      modules: [
        {
          label: "Organization & Team",
          icon: "building",
          tabs: [
            { path: "settings/organization", label: "Organization" },
            { path: "settings/members", label: "Members", when: can("platform.member.view") },
            { path: "settings/invitations", label: "Invitations", when: can("platform.member.invite") },
          ],
        },
        { label: "Profile", icon: "users", tabs: [{ path: "settings/profile", label: "Profile" }] },
      ],
    },
    {
      title: "App management",
      modules: [
        {
          label: "Product Access",
          icon: "puzzle",
          tabs: [{ path: "settings/products", label: "Product Access", when: can("platform.seat.assign") }],
        },
        {
          label: "Integrations & Automation",
          icon: "bolt",
          tabs: [
            {
              path: "settings/integrations",
              label: "Integrations",
              when: can("platform.connection.view"),
              // The delivery log is opened from Integrations.
              also: ["settings/webhooks"],
            },
            { path: "settings/automations", label: "Automations", when: can("platform.automation.view") },
          ],
        },
        { label: "AI", icon: "sparkles", tabs: [{ path: "settings/ai", label: "AI", when: can("platform.ai.view") }] },
        {
          label: "Analytics",
          icon: "chart",
          tabs: [{ path: "settings/analytics", label: "Analytics", when: can("platform.analytics.view") }],
        },
      ],
    },
    {
      title: "Security",
      modules: [
        {
          label: "Login & Authentication",
          icon: "key",
          tabs: [
            { path: "settings/security/authentication", label: "Authentication", when: security },
            // ADR-044: the person's own product sign-in preferences - every member.
            { path: "settings/sign-in-preferences", label: "Sign-in Preferences" },
          ],
        },
        {
          label: "Security Center",
          icon: "shield",
          tabs: [
            { path: "settings/security/network", label: "Network Access", when: security },
            { path: "settings/security", label: "My Security" },
          ],
        },
      ],
    },
    {
      title: "Audit",
      modules: [
        {
          label: "Activity & Audit",
          icon: "audit",
          tabs: [
            { path: "settings/audit", label: "Audit Log", when: audit },
            { path: "settings/sign-ins", label: "Sign-in Activity", when: audit },
          ],
        },
      ],
    },
    {
      title: "Help",
      modules: [{ label: "Support", icon: "diagnostics", tabs: [{ path: "help", label: "Support" }] }],
    },
  ];
}

const shown = (entry: OrgModule) => entry.tabs.filter((tab) => tab.when !== false);

/** Whether `pathname` is one of the tab's pages. Exact, so `settings/security`
 * (My Security) is not current on `settings/security/network`. */
function tabOwns(base: string, tab: OrgTab, pathname: string): boolean {
  if ([tab.path, ...(tab.also ?? [])].some((p) => pathname === `${base}/${p}`)) return true;
  return Boolean(tab.nested) && pathname.startsWith(`${base}/${tab.path}/`);
}

/** The sidebar: each entry opens its first page the member may open, and is
 * current on any of its tabs' pages. An entry with no such page is not shown. */
export function organizationNav(slug: string, can: (permission: string) => boolean, pathname: string): SidebarGroup[] {
  const base = `/o/${slug}`;
  return organizationModules(can).map(({ title, modules }) => ({
    title,
    items: modules.map((entry) => {
      const tabs = shown(entry);
      return {
        href: `${base}/${(tabs[0] ?? entry.tabs[0]).path}`,
        label: entry.label,
        icon: entry.icon,
        when: tabs.length > 0,
        current: tabs.some((tab) => tabOwns(base, tab, pathname)),
      };
    }),
  }));
}

/** The tabs drawn above the open page: its entry's tabs the member may open,
 * when there are two or more. None for a single page, or a page no entry owns
 * (checkout). */
export function organizationTabs(
  slug: string,
  can: (permission: string) => boolean,
  pathname: string,
): { href: string; label: string; current: boolean }[] {
  const base = `/o/${slug}`;
  for (const { modules } of organizationModules(can)) {
    for (const entry of modules) {
      const tabs = shown(entry);
      if (!tabs.some((tab) => tabOwns(base, tab, pathname))) continue;
      if (tabs.length < 2) return [];
      return tabs.map((tab) => ({ href: `${base}/${tab.path}`, label: tab.label, current: tabOwns(base, tab, pathname) }));
    }
  }
  return [];
}
