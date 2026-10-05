"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NavIcon } from "./icons";
import styles from "./SidebarShell.module.css";

export interface SidebarItem {
  href: string;
  label: string;
  icon?: string;
  /** Show only when true. Navigation convenience, never security: every
   * request behind a link is authorized again by the backend. */
  when?: boolean;
  /** Active only on an exact match (an overview page whose URL prefixes others). */
  exact?: boolean;
  children?: SidebarItem[];
}

export interface SidebarGroup {
  /** A small heading ("AUDIENCE", "BILLING"); omit for an untitled group. */
  title?: string;
  items: SidebarItem[];
}

function isActive(pathname: string, item: SidebarItem): boolean {
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}

const visible = (items: SidebarItem[]) => items.filter((i) => i.when !== false);

/**
 * The consoles' layout (Phase 8 extension): a top bar, a grouped left
 * navigation and the page. One component for the organization console, the
 * Super Admin and the partner portal, so keyboard, screen-reader and small-
 * screen behaviour is written once:
 *
 * - the navigation is a labelled landmark; the current page carries
 *   `aria-current="page"` (and a visible marker, not colour alone);
 * - a section with sub-pages is a disclosure button (`aria-expanded`), open
 *   by default when it contains the current page;
 * - below 900px the navigation becomes a drawer behind a "Menu" button: it
 *   takes focus when opened, closes on Escape, on the backdrop and on
 *   navigation, and gives focus back to the button.
 */
export function SidebarShell({
  area,
  context,
  groups,
  account,
  banner,
  children,
  brandName,
  brandLogoUrl,
}: {
  area: string;
  context?: React.ReactNode;
  groups: SidebarGroup[];
  account?: React.ReactNode;
  banner?: React.ReactNode;
  children: React.ReactNode;
  /** A white-label customer's own brand (Phase 11, Step 30) - the organization
   * console only. Omit for Super Admin and the partner portal, which always
   * show Workeasy360 itself. */
  brandName?: string | null;
  brandLogoUrl?: string | null;
}) {
  const pathname = usePathname();
  const navId = useId();
  // The drawer is open *on the page it was opened on*: navigating closes it -
  // the page the person chose is the point - without an effect to reset it.
  const [openOn, setOpenOn] = useState<string | null>(null);
  const open = openOn !== null && openOn === pathname;
  const setOpen = (next: boolean | ((was: boolean) => boolean)) => {
    const value = typeof next === "function" ? next(open) : next;
    setOpenOn(value ? pathname : null);
  };
  const toggle = useRef<HTMLButtonElement | null>(null);
  const nav = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    nav.current?.querySelector<HTMLElement>("a, button")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpenOn(null);
        toggle.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className={styles.frame}>
      <header className={styles.topbar}>
        <button
          ref={toggle}
          type="button"
          className={styles.menuButton}
          aria-expanded={open}
          aria-controls={navId}
          aria-label="Menu"
          onClick={() => setOpen((v) => !v)}
        >
          <span aria-hidden="true" className={styles.menuGlyph}>
            ☰
          </span>
          <span className={styles.menuLabel}>Menu</span>
        </button>
        {brandName ? (
          <span className={styles.brandRow}>
            {brandLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element -- same-origin only (CSP img-src 'self'); next/image is unused elsewhere in this app.
              <img className={styles.brandLogo} src={brandLogoUrl} alt="" />
            )}
            <span className={styles.brandName}>{brandName}</span>
          </span>
        ) : (
          <span className={styles.brand}>Workeasy360</span>
        )}
        {context && <div className={styles.context}>{context}</div>}
        <div className={styles.spacer} />
        {account}
      </header>

      {open && (
        <button
          type="button"
          className={styles.backdrop}
          aria-label="Close menu"
          tabIndex={-1}
          onClick={() => setOpen(false)}
        />
      )}

      <nav
        id={navId}
        ref={nav}
        aria-label={area}
        className={styles.sidebar}
        data-open={open || undefined}
      >
        {groups.map((group, index) => {
          const items = visible(group.items);
          if (items.length === 0) return null;
          return (
            <div key={group.title ?? `group-${index}`} className={styles.group}>
              {group.title && <p className={styles.groupTitle}>{group.title}</p>}
              <ul className={styles.list}>
                {items.map((item) => (
                  <NavEntry key={item.href} item={item} pathname={pathname} />
                ))}
              </ul>
            </div>
          );
        })}
      </nav>

      <main id="w360-main" className={styles.main}>
        {banner}
        {children}
      </main>
    </div>
  );
}

function NavEntry({ item, pathname }: { item: SidebarItem; pathname: string }) {
  const children = item.children ? visible(item.children) : [];
  const containsCurrent = children.some((c) => isActive(pathname, c));
  const [expanded, setExpanded] = useState(containsCurrent);
  const [contained, setContained] = useState(containsCurrent);
  const listId = useId();

  // Arriving on one of its pages opens the section (adjusted while rendering,
  // React's pattern for state that follows a prop).
  if (containsCurrent !== contained) {
    setContained(containsCurrent);
    if (containsCurrent) setExpanded(true);
  }

  if (children.length > 0) {
    return (
      <li>
        <button
          type="button"
          className={styles.link}
          aria-expanded={expanded}
          aria-controls={listId}
          data-contains-current={containsCurrent || undefined}
          onClick={() => setExpanded((v) => !v)}
        >
          <NavIcon name={item.icon} />
          <span className={styles.linkText}>{item.label}</span>
          <span aria-hidden="true" className={styles.chevron} data-expanded={expanded || undefined}>
            ›
          </span>
        </button>
        {expanded && (
          <ul id={listId} className={styles.subList}>
            {children.map((child) => (
              <NavEntry key={child.href} item={child} pathname={pathname} />
            ))}
          </ul>
        )}
      </li>
    );
  }

  const active = isActive(pathname, item);
  return (
    <li>
      <Link
        href={item.href}
        className={styles.link}
        aria-current={active ? "page" : undefined}
        data-active={active || undefined}
      >
        <NavIcon name={item.icon} />
        <span className={styles.linkText}>{item.label}</span>
      </Link>
    </li>
  );
}

// --- the account menu ------------------------------------------------------------

export interface AccountMenuEntry {
  label: string;
  href?: string;
  onSelect?: () => void;
}

/** The avatar button and its menu. Closes on Escape (focus returns to the
 * button) and on a click outside it. */
export function AccountMenu({ name, entries }: { name: string; entries: AccountMenuEntry[] }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    const onClick = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  return (
    <div className={styles.accountWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={styles.avatar}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Account menu for ${name || "your account"}`}
        onClick={() => setOpen((v) => !v)}
      >
        {initials(name)}
      </button>
      {open && (
        <div id={menuId} className={styles.menu} role="menu">
          {entries.map((entry) =>
            entry.href ? (
              <Link
                key={entry.label}
                href={entry.href}
                role="menuitem"
                className={styles.menuItem}
                onClick={() => setOpen(false)}
              >
                {entry.label}
              </Link>
            ) : (
              <button
                key={entry.label}
                type="button"
                role="menuitem"
                className={styles.menuItem}
                onClick={() => {
                  setOpen(false);
                  entry.onSelect?.();
                }}
              >
                {entry.label}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}

export function initials(name: string): string {
  const parts = name.trim().split(/[\s@.]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0][0]?.toUpperCase() ?? "?";
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}
