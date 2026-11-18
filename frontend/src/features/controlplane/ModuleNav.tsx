"use client";

import { useMemo } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { adminModules, locate, relatedOf, trailOf, type ModulePage, type ModuleSection } from "./adminModules";
import { useAttentionCount } from "./attentionStore";
import { PagePlaceContext, type PagePlace } from "./kit/Layout";
import styles from "./ModuleNav.module.css";

/**
 * A module's own navigation around every page in it (2026-11-18, admin-ux phase 1):
 * ONE row of tabs above the page - or, for Settings, a menu down its left. The
 * sidebar stays at nine entries; this is the only level beneath them. A record's
 * own page (a customer, a ticket) draws no tabs: its breadcrumb leads back. A row
 * with a single entry says nothing, so it is not drawn. On a narrow screen a row wraps.
 *
 * It also tells the page where it is (`PagePlaceContext`): the breadcrumb a page
 * header shows, and the tab's other pages, offered as buttons in its header.
 */
export function ModuleNav({ permissions, children }: { permissions: string[]; children?: React.ReactNode }) {
  const pathname = usePathname() ?? "";
  const here = useMemo(() => locate(pathname, adminModules(permissions)), [pathname, permissions]);
  const place = useMemo<PagePlace | null>(
    () =>
      here
        ? {
            trail: trailOf(here),
            related: relatedOf(here).map((p) => ({ href: p.href, label: p.label })),
            detail: here.detail,
          }
        : null,
    [here],
  );
  const body = <PagePlaceContext.Provider value={place}>{children}</PagePlaceContext.Provider>;
  if (!here || here.detail) return body;
  const { module, section } = here;

  if (module.layout === "menu") {
    return (
      <div className={styles.menuLayout}>
        <nav aria-label={module.label} className={styles.menu}>
          {module.sections.map((s) => (
            <MenuSection key={s.label} section={s} current={here.page.href} />
          ))}
        </nav>
        <div className={styles.menuBody}>{body}</div>
      </div>
    );
  }

  if (module.sections.length < 2) return body;
  return (
    <>
      <div className={styles.wrap}>
        <nav aria-label={`${module.label} tabs`} className={styles.sections}>
          {module.sections.map((s) => {
            const current = s.label === section.label;
            return (
              <Link
                key={s.label}
                href={s.pages[0].href}
                className={styles.section}
                data-current={current || undefined}
                aria-current={current ? (here.page.href === s.pages[0].href ? "page" : "true") : undefined}
              >
                {s.label}
                {s.pages[0].badge && <TabCount page={s.pages[0]} />}
              </Link>
            );
          })}
        </nav>
      </div>
      {body}
    </>
  );
}

/** The bell's count for a tab: only above zero, and only once known. */
function TabCount({ page }: { page: ModulePage }) {
  const count = useAttentionCount(page.badge!);
  if (!count) return null;
  return (
    <span className={styles.count}>
      {count > 99 ? "99+" : count}
      <span className="w360-visually-hidden"> waiting</span>
    </span>
  );
}

/** One entry of the left menu - or, for a section of several pages (Security), its
 * heading and its pages beneath it: still one level of links. */
function MenuSection({ section, current }: { section: ModuleSection; current: string }) {
  const link = (p: ModulePage, label: string) => (
    <Link
      key={p.href}
      href={p.href}
      className={styles.menuLink}
      data-current={p.href === current || undefined}
      aria-current={p.href === current ? "page" : undefined}
    >
      {label}
    </Link>
  );
  if (section.pages.length === 1) return link(section.pages[0], section.label);
  const id = `menu-${section.label.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <div role="group" aria-labelledby={id} className={styles.menuGroup}>
      <p id={id} className={styles.menuHeading}>
        {section.label}
      </p>
      {section.pages.map((p) => link(p, p.label))}
    </div>
  );
}
