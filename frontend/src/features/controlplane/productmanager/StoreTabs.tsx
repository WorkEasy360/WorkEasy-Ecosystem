"use client";

import { LinkTabs, PageHeader } from "../kit";

/** Super Admin → Store: the area's pages (ADR-056, coupons ADR-058), as one
 * page with underline tabs under its title (2026-11-07 design system). */
const TABS = [
  { key: "dashboard", href: "/admin/store/dashboard", label: "Overview", subtitle: "The store at a glance: catalog, sales and requests." },
  { key: "browse", href: "/admin/store", label: "Customer preview", subtitle: "Every product as customers find it in the store." },
  { key: "rules", href: "/admin/store/commercial-rules", label: "Commercial rules", subtitle: "What each sales channel pays and earns, per product." },
  // ADR-058.
  { key: "coupons", href: "/admin/store/coupons", label: "Coupons", subtitle: "Discount codes customers can use at checkout." },
] as const;

export type StoreTab = (typeof TABS)[number]["key"];

export function StoreTabs({ current }: { current: StoreTab }) {
  return (
    <LinkTabs
      label="Store sections"
      tabs={TABS.map((t) => ({ href: t.href, label: t.label, current: t.key === current }))}
    />
  );
}

/** The Store's header: title, the current tab's one-line subtitle, actions on
 * the right and the tabs below. Every Store tab uses it, so the title never moves. */
export function StoreHeader({
  current,
  primary,
  actions,
  info,
}: {
  current: StoreTab;
  primary?: React.ReactNode;
  actions?: React.ReactNode;
  info?: React.ReactNode;
}) {
  const tab = TABS.find((t) => t.key === current)!;
  return (
    <PageHeader
      title="Store"
      subtitle={tab.subtitle}
      info={info}
      primary={primary}
      actions={actions}
      tabs={<StoreTabs current={current} />}
    />
  );
}
