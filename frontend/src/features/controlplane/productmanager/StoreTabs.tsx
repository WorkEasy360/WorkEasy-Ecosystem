"use client";

import Link from "next/link";
import styles from "./ProductManager.module.css";

/** Super Admin → Store: the area's three pages (ADR-056). */
const TABS = [
  { key: "dashboard", href: "/admin/store/dashboard", label: "Overview" },
  { key: "browse", href: "/admin/store", label: "Browse" },
  { key: "rules", href: "/admin/store/commercial-rules", label: "Commercial rules" },
] as const;

export function StoreTabs({ current }: { current: (typeof TABS)[number]["key"] }) {
  return (
    <nav aria-label="Store sections" className={styles.toolbar}>
      {TABS.map((t) => (
        <Link
          key={t.key}
          href={t.href}
          aria-current={t.key === current ? "page" : undefined}
          className={t.key === current ? "w360-btn w360-btn-primary" : "w360-btn w360-btn-secondary"}
        >
          {t.label}
        </Link>
      ))}
    </nav>
  );
}
