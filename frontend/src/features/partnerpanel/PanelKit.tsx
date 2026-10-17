"use client";

import Link from "next/link";
import { useId } from "react";
import { usePathname } from "next/navigation";
import { NavIcon } from "@/components/sidebar/icons";
import { usePartner } from "@/features/controlplane/ControlShell";
import { money } from "@/features/controlplane/ui";
import type { CurrencyAmount } from "@/lib/api/partnerPanel";
import styles from "./PartnerPanel.module.css";

/** The signed-in partner and what the server says this person may do. Every
 * check here only decides what to draw; the API authorizes each request again. */
export function usePanel() {
  const { partner } = usePartner();
  const held = new Set(partner?.permissions ?? []);
  return {
    id: partner?.partner_id ?? "",
    partner,
    base: `/partner/${partner?.partner_id ?? ""}`,
    active: partner?.status === "active",
    can: (...keys: string[]) => keys.some((k) => held.has(`platform.partner_${k}`)),
  };
}

export function PageHeader({
  title,
  lead,
  actions,
}: {
  title: string;
  lead?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className={styles.header}>
      <div>
        <h1>{title}</h1>
        {lead && <p className={styles.lead}>{lead}</p>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}

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

/** The tabs inside a top-level page (Affiliates: Conversions, Payouts, Settings).
 * They are not sidebar items. A tab the person's permissions cannot read is not
 * drawn (`show: false`); the server still authorizes each request. */
export function SubNav({ items }: { items: { href: string; label: string; show?: boolean }[] }) {
  const pathname = usePathname() ?? "";
  const shown = items.filter((item) => item.show !== false);
  if (shown.length < 2) return null;
  return (
    <nav aria-label="Sections" className={styles.tabs}>
      {shown.map((item) => (
        <Link key={item.href} href={item.href} aria-current={pathname === item.href ? "page" : undefined}>
          {item.label}
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
