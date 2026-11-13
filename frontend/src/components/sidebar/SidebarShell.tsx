"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Logo } from "@/components/brand/Logo";
import { Icon, NavIcon } from "./icons";
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
  /** The URL prefix this entry is current under, when it differs from `href`
   * (a section entry whose link opens one of several pages beneath it). */
  match?: string;
  /** More prefixes the entry is current under (the pages of its section that
   * live elsewhere in the address space). */
  alsoMatch?: string[];
  /** Pages of the section whose addresses no prefix describes (one record's
   * own pages, `/admin/partners/<id>`). */
  alsoMatchPattern?: RegExp;
  /** When set, the entry is current on exactly these pages and no others - for
   * an entry whose address prefixes another entry's (a partner workspace's
   * Brand Studio at `brand-settings`, Custom Domain at `brand-settings/url-manager`). */
  exactPaths?: string[];
  /** Whether the entry is current, decided by the caller - for an entry that
   * knows its own pages (the organization console's tabbed entries). Wins
   * over every rule above. */
  current?: boolean;
  /** Drawn with a lock and opening `onSelect` instead of a page (2026-11-03:
   * a partner-dashboard entry the partner's level does not include). */
  locked?: boolean;
  /** What a locked entry does when chosen (the upgrade dialog). */
  onSelect?: () => void;
  children?: SidebarItem[];
  /** A section whose entries the person may put in their own order (dragged, or
   * Alt+Arrow keys), remembered on this browser under this key (ADR-062: the
   * Admin's Partners menu). Order only: what an entry opens never changes. */
  reorderKey?: string;
}

export interface SidebarGroup {
  /** A small heading ("AUDIENCE", "BILLING"); omit for an untitled group. */
  title?: string;
  items: SidebarItem[];
}

function isActive(pathname: string, item: SidebarItem): boolean {
  if (item.current !== undefined) return item.current;
  if (item.exact) return pathname === item.href;
  if (item.exactPaths) return item.exactPaths.includes(pathname);
  if (item.alsoMatchPattern?.test(pathname)) return true;
  return [item.match ?? item.href, ...(item.alsoMatch ?? [])].some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
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
  variant,
  navTitle,
  tools,
  accordion = false,
  railKey,
  navFooter,
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
  /** `admin`: the console look - the deep teal top bar and sidebar, the green
   * current page, the light sheet (the Super Admin since 2026-10-26, the Partner
   * panel since 2026-11-11). */
  variant?: "admin";
  /** A small heading above the navigation (the Admin's portal badge). */
  navTitle?: React.ReactNode;
  /** Controls in the top bar between the brand and the account menu (search,
   * quick actions); they take the free space the spacer would. */
  tools?: React.ReactNode;
  /** Only one section open at a time - the current page's (the Super Admin). */
  accordion?: boolean;
  /** Offer to fold the navigation to an icon rail, remembered on this browser
   * under this key (the Super Admin). */
  railKey?: string;
  /** Below the menus, above the rail toggle (the User Admin's Store card);
   * hidden while the menu is folded to its icon rail. */
  navFooter?: React.ReactNode;
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

  // Accordion: the section open is the current page's, until the person opens another.
  const sectionOf = (path: string | null) => {
    for (const group of groups) {
      for (const item of visible(group.items)) {
        if (item.children && (item.current || visible(item.children).some((c) => isActive(path ?? "", c)))) return item.href;
      }
    }
    return null;
  };
  const [openSection, setOpenSection] = useState<string | null>(() => sectionOf(pathname));
  const [sectionFor, setSectionFor] = useState(pathname);
  if (accordion && sectionFor !== pathname) {
    setSectionFor(pathname);
    const current = sectionOf(pathname);
    if (current) setOpenSection(current);
  }

  // The icon rail, remembered on this browser.
  // Read once, on the client: the consoles that fold their menu (the Super
  // Admin) render it only after the session has loaded in the browser.
  const [rail, setRailState] = useState(() => {
    if (!railKey || typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem(railKey) === "1";
    } catch {
      return false;
    }
  });
  const setRail = (next: boolean) => {
    setRailState(next);
    if (!railKey) return;
    try {
      window.localStorage.setItem(railKey, next ? "1" : "0");
    } catch {
      /* private mode */
    }
  };

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
    <div className={styles.frame} data-variant={variant} data-rail={(railKey && rail) || undefined}>
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
            <Icon name="menu" size={18} />
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
          <span className={styles.brand}>
            <Logo compact onBand={variant === "admin"} />
          </span>
        )}
        {context && <div className={styles.context}>{context}</div>}
        {tools ? <div className={styles.tools}>{tools}</div> : <div className={styles.spacer} />}
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
        {navTitle && <p className={styles.navTitle}>{navTitle}</p>}
        {groups.map((group, index) => {
          const items = visible(group.items);
          if (items.length === 0) return null;
          return (
            <div key={group.title ?? `group-${index}`} className={styles.group}>
              {group.title && <p className={styles.groupTitle}>{group.title}</p>}
              <ul className={styles.list}>
                {items.map((item) => (
                  <NavEntry
                    key={item.href}
                    item={item}
                    pathname={pathname}
                    expandedOverride={accordion ? openSection === item.href : undefined}
                    onToggle={
                      accordion
                        ? () => {
                            if (railKey && rail) {
                              setRail(false);
                              setOpenSection(item.href);
                            } else setOpenSection((s) => (s === item.href ? null : item.href));
                          }
                        : undefined
                    }
                  />
                ))}
              </ul>
            </div>
          );
        })}
        {navFooter && <div className={styles.navFooter}>{navFooter}</div>}
        {railKey && (
          <button
            type="button"
            className={styles.railToggle}
            aria-pressed={rail}
            onClick={() => setRail(!rail)}
          >
            <Icon name={rail ? "chevronRight" : "chevronLeft"} size={16} />
            <span className={styles.linkText}>{rail ? "Expand menu" : "Collapse menu"}</span>
          </button>
        )}
      </nav>

      <main id="w360-main" className={styles.main}>
        {banner}
        {children}
      </main>
    </div>
  );
}

/** The order a person chose for a section's entries, remembered on this browser;
 * entries added since are kept, in their place at the end. */
function useEntryOrder(key: string | undefined, hrefs: string[]): [string[], (next: string[]) => void] {
  const [stored, setStored] = useState<string[] | null>(() => {
    if (!key || typeof window === "undefined") return null;
    try {
      const raw = JSON.parse(window.localStorage.getItem(key) ?? "null");
      return Array.isArray(raw) ? raw.filter((v): v is string => typeof v === "string") : null;
    } catch {
      return null;
    }
  });
  const order = stored ? [...stored.filter((h) => hrefs.includes(h)), ...hrefs.filter((h) => !stored.includes(h))] : hrefs;
  const save = (next: string[]) => {
    setStored(next);
    if (!key) return;
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      /* private mode: the order lasts for this page only */
    }
  };
  return [order, save];
}

/** What a reorderable section gives each of its entries' list items. */
type EntryProps = React.LiHTMLAttributes<HTMLLIElement> & { "data-dragging"?: boolean };

function NavEntry({
  item,
  pathname,
  expandedOverride,
  onToggle,
  entryProps,
  describedBy,
}: {
  item: SidebarItem;
  pathname: string;
  /** Set by an accordion: whether this section is the open one. */
  expandedOverride?: boolean;
  onToggle?: () => void;
  /** Drag-and-drop and keyboard handlers of a reorderable section's entry. */
  entryProps?: EntryProps;
  /** The instructions of a reorderable section, for its links. */
  describedBy?: string;
}) {
  const children = item.children ? visible(item.children) : [];
  // A section can say it holds the current page (the Admin's Partners, whose
  // tabs live elsewhere) as well as find it among its entries.
  const containsCurrent = children.length > 0 && (item.current === true || children.some((c) => isActive(pathname, c)));
  const [expandedOwn, setExpanded] = useState(containsCurrent);
  const expanded = expandedOverride ?? expandedOwn;
  const [contained, setContained] = useState(containsCurrent);
  const listId = useId();
  const helpId = useId();
  const [order, saveOrder] = useEntryOrder(
    item.reorderKey,
    children.map((c) => c.href),
  );
  const [dragging, setDragging] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const list = useRef<HTMLUListElement | null>(null);

  // Arriving on one of its pages opens the section (adjusted while rendering,
  // React's pattern for state that follows a prop).
  if (containsCurrent !== contained) {
    setContained(containsCurrent);
    if (containsCurrent) setExpanded(true);
  }

  // A section none of whose pages this person may open is not shown at all.
  if (item.children && children.length === 0) return null;
  if (children.length > 0) {
    const byHref = new Map(children.map((c) => [c.href, c]));
    const ordered = item.reorderKey ? order.map((h) => byHref.get(h)).filter((c): c is SidebarItem => Boolean(c)) : children;
    const move = (href: string, to: number, focus: boolean) => {
      const from = order.indexOf(href);
      if (from < 0 || to < 0 || to >= order.length || from === to) return;
      const next = [...order];
      next.splice(from, 1);
      next.splice(to, 0, href);
      saveOrder(next);
      const label = byHref.get(href)?.label ?? "";
      setAnnouncement(`${label} moved to position ${to + 1} of ${next.length}.`);
      if (focus) {
        // The link is the same element, moved: keep the keyboard on it.
        requestAnimationFrame(() => list.current?.querySelector<HTMLElement>(`[data-entry="${CSS.escape(href)}"] a, [data-entry="${CSS.escape(href)}"] button`)?.focus());
      }
    };
    const reorderProps = (child: SidebarItem): EntryProps => ({
      draggable: true,
      "data-dragging": dragging === child.href || undefined,
      onDragStart: (e) => {
        setDragging(child.href);
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", child.label);
      },
      onDragOver: (e) => {
        if (!dragging) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
      },
      onDrop: (e) => {
        e.preventDefault();
        if (dragging) move(dragging, order.indexOf(child.href), false);
        setDragging(null);
      },
      onDragEnd: () => setDragging(null),
      onKeyDown: (e) => {
        if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
        e.preventDefault();
        move(child.href, order.indexOf(child.href) + (e.key === "ArrowUp" ? -1 : 1), true);
      },
    });
    return (
      <li>
        <button
          type="button"
          className={styles.link}
          aria-expanded={expanded}
          aria-controls={listId}
          data-contains-current={containsCurrent || undefined}
          onClick={() => (onToggle ? onToggle() : setExpanded((v) => !v))}
        >
          <NavIcon name={item.icon} />
          <span className={styles.linkText}>{item.label}</span>
          <span aria-hidden="true" className={styles.chevron} data-expanded={expanded || undefined}>
            <Icon name="chevronRight" size={16} />
          </span>
        </button>
        {expanded && (
          <ul id={listId} ref={list} className={styles.subList}>
            {ordered.map((child) => (
              <NavEntry
                key={child.href}
                item={child}
                pathname={pathname}
                entryProps={item.reorderKey ? reorderProps(child) : undefined}
                describedBy={item.reorderKey ? helpId : undefined}
              />
            ))}
          </ul>
        )}
        {item.reorderKey && expanded && (
          <>
            <span id={helpId} className="w360-visually-hidden">
              To reorder, drag an entry, or press Alt with the Up or Down arrow.
            </span>
            <span className="w360-visually-hidden" aria-live="polite">
              {announcement}
            </span>
          </>
        )}
      </li>
    );
  }

  if (item.locked && item.onSelect) {
    return (
      <li data-entry={item.href} {...entryProps}>
        <button
          type="button"
          className={styles.link}
          data-locked
          onClick={item.onSelect}
          aria-haspopup="dialog"
          aria-describedby={describedBy}
        >
          <NavIcon name={item.icon} />
          <span className={styles.linkText}>{item.label}</span>
          <span className={styles.lockMark}>
            <NavIcon name="lock" />
            <span className="w360-visually-hidden"> (locked - upgrade to unlock)</span>
          </span>
        </button>
      </li>
    );
  }
  const active = isActive(pathname, item);
  return (
    <li data-entry={item.href} {...entryProps}>
      <Link
        href={item.href}
        className={styles.link}
        aria-current={active ? "page" : undefined}
        data-active={active || undefined}
        aria-describedby={describedBy}
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
export function AccountMenu({
  name,
  entries,
  showName = false,
}: {
  name: string;
  entries: AccountMenuEntry[];
  /** Show the name beside the avatar (the Super Admin's top bar). */
  showName?: boolean;
}) {
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
        className={showName ? styles.accountButton : styles.avatar}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={showName ? `Account menu for ${name || "your account"}` : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        {showName ? (
          <>
            <span aria-hidden="true" className={styles.avatar}>
              {initials(name)}
            </span>
            <span aria-hidden="true" className={styles.accountName}>
              {firstName(name)}
            </span>
            <span aria-hidden="true" className={styles.accountChevron}>
              <Icon name="chevronDown" size={16} />
            </span>
          </>
        ) : (
          // The initials are a picture of the name; the button's name, from its
          // content, says it in full (so the visible text is never left out of it).
          <>
            <span aria-hidden="true">{initials(name)}</span>
            <span className="w360-visually-hidden">Account menu for {name || "your account"}</span>
          </>
        )}
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

/** The first word of a name, or the part of an email before the @. */
export function firstName(name: string): string {
  const trimmed = name.trim();
  return (trimmed.includes("@") ? trimmed.split("@")[0] : trimmed.split(/\s+/)[0]) || "Account";
}

export function initials(name: string): string {
  const parts = name.trim().split(/[\s@.]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0][0]?.toUpperCase() ?? "?";
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}
