import type { SidebarGroup, SidebarItem } from "@/components/sidebar/SidebarShell";

/** One page of a sidebar entry, kept at its own address. */
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

/** One entry under a menu: a page, or pages drawn as tabs above whichever is open. */
export interface OrgSection {
  label: string;
  tabs: OrgTab[];
}

/** One of the seven menus. */
export interface OrgModule {
  key: string;
  label: string;
  icon: string;
  sections: OrgSection[];
}

/**
 * The User Admin Panel's sidebar (2026-11-11, the owner's specification of 08
 * November): exactly seven menus - Dashboard, My Products, Billing &
 * Subscriptions, Wallets & Credits, Organization & Team, Help, Settings - with
 * their pages as sub-menus, shown when the menu is opened.
 *
 * Navigation only: every page keeps its address (old links and bookmarks open the
 * same page), and an entry appears only when the member holds the permission its
 * page needs. The seven menus themselves always appear; a menu none of whose
 * pages this member may open leads to its first page, which says who can help
 * ("Contact Organization Admin") - the backend refuses the data either way.
 */
export function organizationModules(can: (permission: string) => boolean): OrgModule[] {
  const subscriptions = can("platform.billing.view") || can("commercial.subscription.view");
  const billing = can("platform.billing.view");
  const security = can("platform.security.view");
  const audit = can("platform.audit.view");
  return [
    {
      key: "dashboard",
      label: "Dashboard",
      icon: "dashboard",
      sections: [{ label: "Dashboard", tabs: [{ path: "dashboard", label: "Dashboard" }] }],
    },
    {
      key: "products",
      label: "My Products",
      icon: "layers",
      sections: [
        { label: "All Products", tabs: [{ path: "apps", label: "My Products" }] },
        { label: "WorkEasy360 Store", tabs: [{ path: "store", label: "Store", nested: true }] },
        { label: "Plans & Trials", tabs: [{ path: "explore", label: "Plans & Trials" }] },
      ],
    },
    {
      key: "billing",
      label: "Billing & Subscriptions",
      icon: "card",
      sections: [
        { label: "Subscriptions", tabs: [{ path: "settings/billing", label: "Subscriptions", when: subscriptions }] },
        { label: "Orders & Payments", tabs: [{ path: "orders", label: "Orders & Payments", when: subscriptions }] },
        { label: "Billing Profile", tabs: [{ path: "settings/billing/profile", label: "Billing Profile", when: billing }] },
      ],
    },
    {
      key: "wallets",
      label: "Wallets & Credits",
      icon: "wallet",
      sections: [
        { label: "Overview", tabs: [{ path: "wallets", label: "Overview", when: billing }] },
        { label: "Platform Wallet", tabs: [{ path: "wallets/platform", label: "Platform Wallet", when: billing }] },
        { label: "AI Credits", tabs: [{ path: "wallets/ai-credits", label: "AI Credits", when: billing }] },
        { label: "Consumption Wallet", tabs: [{ path: "wallets/consumption", label: "Consumption Wallet", when: billing }] },
        { label: "Usage & Reports", tabs: [{ path: "wallets/usage", label: "Usage & Reports", when: billing }] },
      ],
    },
    {
      key: "organization",
      label: "Organization & Team",
      icon: "building",
      sections: [
        { label: "Organization", tabs: [{ path: "settings/organization", label: "Organization" }] },
        {
          label: "Members",
          tabs: [
            { path: "settings/members", label: "Members", when: can("platform.member.view") },
            { path: "settings/invitations", label: "Invitations", when: can("platform.member.invite") },
          ],
        },
        {
          label: "Product Access",
          tabs: [{ path: "settings/products", label: "Product Access & Seats", when: can("platform.seat.assign") }],
        },
      ],
    },
    {
      key: "help",
      label: "Help",
      icon: "headset",
      sections: [{ label: "Help", tabs: [{ path: "help", label: "Help" }] }],
    },
    {
      key: "settings",
      label: "Settings",
      icon: "settings",
      sections: [
        { label: "My Profile", tabs: [{ path: "settings/profile", label: "My Profile" }] },
        {
          label: "Account & Security",
          tabs: [
            { path: "settings/security", label: "Password & Sessions" },
            // ADR-044: the person's own product sign-in preferences - every member.
            { path: "settings/sign-in-preferences", label: "Sign-in Preferences" },
          ],
        },
        { label: "Notifications", tabs: [{ path: "settings/notifications", label: "Notification Preferences" }] },
        {
          label: "Activity History",
          tabs: [
            { path: "settings/audit", label: "Admin Activity", when: audit },
            { path: "settings/sign-ins", label: "Sign-in Activity", when: audit },
          ],
        },
        {
          label: "Organization Security",
          tabs: [
            { path: "settings/security/authentication", label: "Authentication", when: security },
            { path: "settings/security/network", label: "Network Access", when: security },
          ],
        },
        {
          label: "Integrations & AI",
          tabs: [
            {
              path: "settings/integrations",
              label: "Integrations",
              when: can("platform.connection.view"),
              // The delivery log is opened from Integrations.
              also: ["settings/webhooks"],
            },
            { path: "settings/automations", label: "Automations", when: can("platform.automation.view") },
            { path: "settings/ai", label: "AI", when: can("platform.ai.view") },
            { path: "settings/analytics", label: "Analytics", when: can("platform.analytics.view") },
          ],
        },
      ],
    },
  ];
}

const shownTabs = (section: OrgSection) => section.tabs.filter((tab) => tab.when !== false);
const shownSections = (menu: OrgModule) => menu.sections.filter((section) => shownTabs(section).length > 0);

/** Whether `pathname` is one of the tab's pages. Exact, so `settings/security`
 * (Password & Sessions) is not current on `settings/security/network`. */
function tabOwns(base: string, tab: OrgTab, pathname: string): boolean {
  if ([tab.path, ...(tab.also ?? [])].some((p) => pathname === `${base}/${p}`)) return true;
  return Boolean(tab.nested) && pathname.startsWith(`${base}/${tab.path}/`);
}

const sectionOwns = (base: string, section: OrgSection, pathname: string) =>
  section.tabs.some((tab) => tabOwns(base, tab, pathname));

/** The sidebar: seven menus. A menu with one page is a link; a menu with several
 * is a disclosure whose sub-menu lists them. Each entry opens its first page the
 * member may open and is current on any of its tabs' pages. */
export function organizationNav(slug: string, can: (permission: string) => boolean, pathname: string): SidebarGroup[] {
  const base = `/o/${slug}`;
  const items: SidebarItem[] = organizationModules(can).map((menu) => {
    const sections = shownSections(menu);
    const current = menu.sections.some((section) => sectionOwns(base, section, pathname));
    if (sections.length <= 1) {
      const section = sections[0] ?? menu.sections[0];
      const tab = shownTabs(section)[0] ?? section.tabs[0];
      return { href: `${base}/${tab.path}`, label: menu.label, icon: menu.icon, current };
    }
    return {
      // The section's key: a disclosure has no address of its own.
      href: `${base}#${menu.key}`,
      label: menu.label,
      icon: menu.icon,
      current,
      children: sections.map((section) => ({
        href: `${base}/${shownTabs(section)[0].path}`,
        label: section.label,
        current: sectionOwns(base, section, pathname),
      })),
    };
  });
  return [{ items }];
}

/** The tabs drawn above the open page: its entry's pages the member may open,
 * when there are two or more. None for a single page, or a page no entry owns
 * (checkout). */
export function organizationTabs(
  slug: string,
  can: (permission: string) => boolean,
  pathname: string,
): { href: string; label: string; current: boolean }[] {
  const base = `/o/${slug}`;
  for (const menu of organizationModules(can)) {
    for (const section of menu.sections) {
      if (!sectionOwns(base, section, pathname)) continue;
      const tabs = shownTabs(section);
      if (tabs.length < 2) return [];
      return tabs.map((tab) => ({ href: `${base}/${tab.path}`, label: tab.label, current: tabOwns(base, tab, pathname) }));
    }
  }
  return [];
}

/** Where the open page sits: its menu and entry ("Billing & Subscriptions ›
 * Orders & Payments"), for the top bar on small screens and the search. */
export function organizationPlace(
  slug: string,
  can: (permission: string) => boolean,
  pathname: string,
): { module: string; section: string } | null {
  const base = `/o/${slug}`;
  for (const menu of organizationModules(can)) {
    for (const section of menu.sections) {
      if (sectionOwns(base, section, pathname)) return { module: menu.label, section: section.label };
    }
  }
  return null;
}

/** Every page this member may open, for the top bar's search. */
export function organizationPages(
  slug: string,
  can: (permission: string) => boolean,
): { href: string; label: string; module: string; icon: string }[] {
  const base = `/o/${slug}`;
  return organizationModules(can).flatMap((menu) =>
    shownSections(menu).flatMap((section) =>
      shownTabs(section).map((tab) => ({
        href: `${base}/${tab.path}`,
        label: tab.label === section.label || shownTabs(section).length === 1 ? section.label : `${section.label} · ${tab.label}`,
        module: menu.label,
        icon: menu.icon,
      })),
    ),
  );
}
