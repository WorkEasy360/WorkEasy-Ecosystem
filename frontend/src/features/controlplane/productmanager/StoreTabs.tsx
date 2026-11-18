"use client";

import { PageHeader } from "../kit";

/** Super Admin → Store: the area's pages (ADR-056, coupons ADR-058). Since
 * 2026-11-18 (admin-ux phase 1) Store is one tab of Products & Pricing: its
 * Overview is the tab, and the other three open from buttons in the Overview's
 * header, with a breadcrumb back - no second row of tabs. */
const TABS = [
  { key: "dashboard", href: "/admin/store/dashboard", label: "Overview", subtitle: "The store at a glance: catalog, sales and requests." },
  { key: "browse", href: "/admin/store", label: "Customer preview", subtitle: "Every product as customers find it in the store." },
  { key: "rules", href: "/admin/store/commercial-rules", label: "Commercial rules", subtitle: "What each sales channel pays and earns, per product." },
  // ADR-058.
  { key: "coupons", href: "/admin/store/coupons", label: "Coupons", subtitle: "Discount codes customers can use at checkout." },
] as const;

export type StoreTab = (typeof TABS)[number]["key"];

/** The Store's header: its title and the page's one-line subtitle, actions on
 * the right. Every Store page uses it, so the title never moves. */
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
  return <PageHeader title="Store" subtitle={tab.subtitle} info={info} primary={primary} actions={actions} />;
}
