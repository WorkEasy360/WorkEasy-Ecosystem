"use client";

import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useId, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import { NavIcon } from "@/components/sidebar/icons";
import { usePartner } from "@/features/controlplane/ControlShell";
import { money } from "@/features/controlplane/ui";
import { PageHeader as KitPageHeader } from "@/features/controlplane/kit";
import type { CurrencyAmount } from "@/lib/api/partnerPanel";
import moduleNav from "@/features/controlplane/ModuleNav.module.css";
import styles from "./PartnerPanel.module.css";

/** The signed-in partner and what the server says this person may do. Every
 * check here only decides what to draw; the API authorizes each request again. */
export function usePanel() {
  const { partner, base: workspaceBase } = usePartner();
  const held = new Set(partner?.permissions ?? []);
  return {
    id: partner?.partner_id ?? "",
    partner,
    // Inside a workspace (`/partner/reseller`) every link a screen builds stays
    // in it; the generic panel is `/partner/<id>`.
    base: workspaceBase ?? `/partner/${partner?.partner_id ?? ""}`,
    active: partner?.status === "active",
    can: (...keys: string[]) => keys.some((k) => held.has(`platform.partner_${k}`)),
  };
}

/**
 * Every partner page's header (2026-11-11): the consoles' one header (kit
 * PageHeader) - the title where every page has it, a one-line subtitle, the
 * page's actions on the right, tabs below - and the browser tab's name.
 */
export function PageHeader({
  title,
  lead,
  actions,
  tabs,
}: {
  title: string;
  lead?: string;
  actions?: React.ReactNode;
  tabs?: React.ReactNode;
}) {
  // A workspace tab's own page is titled by its menu (see EntryTabs).
  const menuTitle = useContext(PageTitleContext);
  return <KitPageHeader title={menuTitle ?? title} subtitle={lead} actions={actions} tabs={tabs} />;
}

/**
 * The title the workspace's menu gives the page now open (2026-11-11): a module's
 * landing tab is titled by the module ("Customers", "Platform Wallet"), every
 * other tab by its own label ("Plan Activation", "Transactions") - so a page's
 * title, its tab, its sidebar entry and its browser tab always agree. `null`
 * (a tab's sub-page, an older address, a page outside a tabbed entry): the
 * page's own title.
 */
const PageTitleContext = createContext<string | null>(null);

/** A section that explains why something is not there, in the server's words.
 * It is a statement, not an error: nothing is broken. */
export function NotAvailable({ title, reasons }: { title: string; reasons: string[] }) {
  const id = useId();
  return (
    <section className={styles.notice} aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {reasons.map((r) => (
        <p key={r}>{r}</p>
      ))}
    </section>
  );
}

/** Nothing here yet: a soft mint illustration, a dark headline, muted words,
 * and (passed as children) the action that fills it. */
export function EmptyState({
  title,
  icon = "sparkles",
  children,
}: {
  title: string;
  /** A sidebar icon name for the illustration; decorative only. */
  icon?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={styles.emptyState}>
      <span className={styles.emptyArt} aria-hidden="true">
        <NavIcon name={icon} />
      </span>
      <div className={styles.emptyText}>
        <strong>{title}</strong>
        {children}
      </div>
    </div>
  );
}

/** Amounts in several currencies are listed, never summed. */
export function Amounts({ amounts }: { amounts: CurrencyAmount[] | null | undefined }) {
  if (!amounts || amounts.length === 0) return <span>—</span>;
  return (
    <ul className={styles.amounts}>
      {amounts.map((a) => (
        <li key={a.currency}>{money(a.amount_minor, a.currency)}</li>
      ))}
    </ul>
  );
}

export function Kpi({
  label,
  value,
  note,
  unavailable,
  icon,
}: {
  label: string;
  value?: React.ReactNode;
  note?: string;
  /** Why the figure is missing - shown instead of a zero that could be mistaken for a fact. */
  unavailable?: string;
  /** A sidebar icon name, drawn green on a light mint disc; decorative only. */
  icon?: string;
}) {
  return (
    <li className={styles.kpi}>
      <span className={styles.kpiHead}>
        <span className={styles.kpiLabel}>{label}</span>
        {icon && (
          <span className={styles.kpiIcon} aria-hidden="true">
            <NavIcon name={icon} />
          </span>
        )}
      </span>
      {unavailable ? (
        <span className={styles.kpiNote}>{unavailable}</span>
      ) : (
        <>
          <span className={styles.kpiValue}>{value}</span>
          {note && <span className={styles.kpiNote}>{note}</span>}
        </>
      )}
    </li>
  );
}

/** A workspace entry's tabs, drawn above its page (2026-11-02) - see `EntryTabs`. */
const EntryTabsContext = createContext<{
  publish: (notes: Record<string, string>) => void;
  /** The open tab's pages: a page's own links to them stay, as a second row. */
  pages: string[];
} | null>(null);

export interface EntryTab {
  href: string;
  label: string;
  /** Every address the tab is current at (its page's older routes too). */
  current: string[];
}

/**
 * The tabs of one consolidated workspace entry (Product Store: Explore
 * Products, Approved / Promoting, ...), above whichever of its pages is open.
 * A page's own `SubNav` lends its counts ("My Requests (2 pending)") to the
 * matching tab and keeps only its links to the open tab's other pages (Pricing
 * & Margins: Currency, Package & Pricing, Pricing Rule) as a second row - so no
 * link is drawn twice. Navigation only: each page is guarded and every request
 * authorized as before.
 */
export function EntryTabs({ tabs, title = null, children }: { tabs: EntryTab[]; title?: string | null; children: React.ReactNode }) {
  const pathname = usePathname() ?? "";
  const [notes, setNotes] = useState<Record<string, string>>({});
  const publish = useCallback(
    (next: Record<string, string>) => setNotes((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next)),
    [],
  );
  const pages = tabs.find((tab) => tab.current.includes(pathname))?.current;
  const key = (pages ?? []).join("|");
  const value = useMemo(() => ({ publish, pages: key ? key.split("|") : [] }), [publish, key]);
  return (
    <div className={styles.page}>
      {tabs.length > 1 && (
        // The Admin console's module sections (ModuleNav): a pill row, the open
        // section filled - the same look in both consoles.
        <nav aria-label="Sections" className={moduleNav.sections}>
          {tabs.map((tab) => {
            const current = tab.current.includes(pathname);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={moduleNav.section}
                data-current={current || undefined}
                aria-current={current ? "page" : undefined}
              >
                {tab.label}
                {notes[tab.href] ? ` ${notes[tab.href]}` : ""}
              </Link>
            );
          })}
        </nav>
      )}
      <PageTitleContext.Provider value={title}>
        <EntryTabsContext.Provider value={value}>{children}</EntryTabsContext.Provider>
      </PageTitleContext.Provider>
    </div>
  );
}

/** The tabs inside a top-level page (Affiliates: Conversions, Payouts, Settings).
 * They are not sidebar items. A tab the person's permissions cannot read is not
 * drawn (`show: false`); the server still authorizes each request. `note` is a
 * count after the label. Under a workspace entry's `EntryTabs` it keeps only the
 * links to the open tab's pages. */
export function SubNav({ items }: { items: { href: string; label: string; show?: boolean; note?: string }[] }) {
  const pathname = usePathname() ?? "";
  const entry = useContext(EntryTabsContext);
  const notes = JSON.stringify(Object.fromEntries(items.filter((i) => i.note).map((i) => [i.href, i.note])));
  useEffect(() => {
    entry?.publish(JSON.parse(notes) as Record<string, string>);
  }, [entry, notes]);
  const shown = items.filter((item) => item.show !== false && (!entry || entry.pages.includes(item.href)));
  if (shown.length < 2) return null;
  // Under a section: the underlined pages row; on its own: the sections' pill row.
  const [row, link] = entry ? [moduleNav.pages, moduleNav.page] : [moduleNav.sections, moduleNav.section];
  return (
    <nav aria-label={entry ? "Pages" : "Sections"} className={row}>
      {shown.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className={link}
          data-current={pathname === item.href || undefined}
          aria-current={pathname === item.href ? "page" : undefined}
        >
          {item.label}
          {item.note ? ` ${item.note}` : ""}
        </Link>
      ))}
    </nav>
  );
}

/** The tabs of the Users page: the customers, and where each one's subscription stands. */
export function UsersNav() {
  const { base } = usePanel();
  return (
    <SubNav
      items={[
        { href: `${base}/users`, label: "Customers" },
        { href: `${base}/users/subscriptions`, label: "Subscriptions" },
      ]}
    />
  );
}

export const monogram = (name: string) => (name.trim()[0] ?? "?").toUpperCase();
