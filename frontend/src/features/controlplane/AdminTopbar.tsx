"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { adminPanelApi } from "@/lib/api/adminPanel";
import { adminApi, type AttentionItem } from "@/lib/api/controlPlane";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import { usePortalRouter } from "@/lib/portalRouter";
import { adminModules } from "./adminModules";
import { Modal } from "./Modal";
import { Menu, type MenuItem } from "./kit/Menu";
import { initialsOf } from "./kit/Layout";
import { formatRelative } from "./kit/format";

import styles from "./AdminTopbar.module.css";

const can = (permissions: string[], key: string) => permissions.includes(key);

/**
 * The Admin's top bar (2026-11-09): the command palette (Ctrl/⌘K), the Create
 * menu, notifications, help and the settings shortcut. Everything
 * here is a way to a page the staff member could already reach from the menu -
 * each page and each request behind it checks the grant again.
 */
export function AdminTools({ permissions }: { permissions: string[] }) {
  return (
    <>
      <AdminSearch permissions={permissions} />
      <div className={styles.spacer} />
      <NewMenu permissions={permissions} />
      <Bell permissions={permissions} />
      <HelpLink permissions={permissions} />
      {can(permissions, "platform.staff_settings.view") && (
        <Link href="/admin/settings" className={styles.iconButton} aria-label="Platform settings" title="Platform settings">
          <Icon name="settings" size={20} />
        </Link>
      )}
    </>
  );
}

/** Help: the console's own help library for those who may read it, otherwise the
 * same help page the account menu offers. */
function HelpLink({ permissions }: { permissions: string[] }) {
  const portals = usePortals();
  const icon = <Icon name="help" size={20} />;
  if (can(permissions, "platform.staff_support.view")) {
    return (
      <Link href="/admin/help-content" className={styles.iconButton} aria-label="Help" title="Help">
        {icon}
      </Link>
    );
  }
  const href = portalHref(portals, "app", "/contact");
  return /^https?:/.test(href) ? (
    <a href={href} className={styles.iconButton} aria-label="Help" title="Help">
      {icon}
    </a>
  ) : (
    <Link href={href} className={styles.iconButton} aria-label="Help" title="Help">
      {icon}
    </Link>
  );
}

// --- the command palette ------------------------------------------------------------

type Group = "Recent" | "Pages" | "Settings" | "Customers" | "Subscriptions" | "Users" | "Partners" | "Products" | "Orders" | "Invoices";
const GROUP_ORDER: Group[] = ["Recent", "Pages", "Settings", "Customers", "Subscriptions", "Users", "Partners", "Products", "Orders", "Invoices"];

interface Result {
  id: string;
  group: Group;
  label: string;
  hint: string;
  href: string;
  icon: string;
}

const RECENT_KEY = "w360.admin.recent";

function readRecent(): Result[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    const list = raw ? (JSON.parse(raw) as Result[]) : [];
    return Array.isArray(list) ? list.filter((r) => typeof r?.href === "string" && r.href.startsWith("/admin")).slice(0, 5) : [];
  } catch {
    return [];
  }
}

function remember(result: Result) {
  try {
    const next = [{ ...result, id: `recent-${result.href}`, group: "Recent" as const }, ...readRecent().filter((r) => r.href !== result.href)];
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next.slice(0, 5)));
  } catch {
    /* private mode: nothing remembered */
  }
}

/** Every page this staff member may open, with the module it sits in. */
function pagesFor(permissions: string[]): Result[] {
  return adminModules(permissions).flatMap((module) =>
    module.sections.flatMap((section) =>
      section.pages.map((page) => ({
        id: `page-${page.href}`,
        group: (module.key === "settings" ? "Settings" : "Pages") as Group,
        label: page.label,
        hint: module.label,
        href: page.href,
        icon: module.icon,
      })),
    ),
  );
}

const KIND_TO_GROUP: Record<string, { group: Group; icon: string }> = {
  customer: { group: "Customers", icon: "building" },
  subscription: { group: "Subscriptions", icon: "card" },
  user: { group: "Users", icon: "user" },
  partner: { group: "Partners", icon: "handshake" },
  product: { group: "Products", icon: "box" },
  order: { group: "Orders", icon: "receipt" },
  invoice: { group: "Invoices", icon: "receipt" },
};

/** Live results from the server for the words typed: only what this staff member's
 * grants cover, decided there. A failure is said as a failure, never "no results". */
function useLiveResults(query: string): { results: Result[]; loading: boolean; failed: boolean } {
  const [state, setState] = useState<{ query: string; results: Result[]; failed: boolean }>({ query: "", results: [], failed: false });

  useEffect(() => {
    if (query.length < 2) return;
    let live = true;
    const t = window.setTimeout(() => {
      adminPanelApi.search(query).then(
        (found) => {
          if (!live) return;
          setState({
            query,
            failed: false,
            results: found.results.flatMap((row) => {
              const look = KIND_TO_GROUP[row.kind];
              return look
                ? [{ id: `${row.kind}-${row.id}`, group: look.group, label: row.label, hint: row.hint, href: row.href, icon: look.icon }]
                : [];
            }),
          });
        },
        () => live && setState({ query, results: [], failed: true }),
      );
    }, 200);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [query]);

  if (query.length < 2) return { results: [], loading: false, failed: false };
  const current = state.query === query;
  return { results: current ? state.results : [], loading: !current, failed: current && state.failed };
}

/**
 * The command palette: the top-bar box (or Ctrl/⌘K anywhere) opens it. Pages
 * and settings by name, and live results grouped as Users, Partners, Products
 * and Orders; recent items when nothing is typed. Arrow keys move, Enter
 * opens, Escape closes.
 */
export function AdminSearch({ permissions }: { permissions: string[] }) {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className={styles.search}>
      <button ref={opener} type="button" className={styles.searchOpener} aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <Icon name="search" size={16} />
        <span className={styles.searchPlaceholder}>Search customers, partners, subscriptions, invoices…</span>
        <kbd className={styles.kbd} aria-hidden="true">
          Ctrl K
        </kbd>
        <span className="w360-visually-hidden"> (Ctrl+K)</span>
      </button>
      {open && <CommandPalette permissions={permissions} onClose={() => setOpen(false)} />}
    </div>
  );
}

function CommandPalette({ permissions, onClose }: { permissions: string[]; onClose: () => void }) {
  const router = usePortalRouter();
  const titleId = useId();
  const inputId = useId();
  const listId = useId();
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const [recent] = useState<Result[]>(() => (typeof window === "undefined" ? [] : readRecent()));
  const pages = useMemo(() => pagesFor(permissions), [permissions]);
  const query = text.trim();
  const live = useLiveResults(query);

  const results = useMemo(() => {
    if (!query) return recent.length ? recent : pages.slice(0, 8);
    const needle = query.toLowerCase();
    const matched = pages.filter((p) => p.label.toLowerCase().includes(needle) || p.hint.toLowerCase().includes(needle)).slice(0, 6);
    const all = [...matched, ...live.results];
    return GROUP_ORDER.flatMap((g) => all.filter((r) => r.group === g));
  }, [query, pages, recent, live.results]);

  const current = Math.min(active, Math.max(0, results.length - 1));

  const go = (result: Result | undefined) => {
    if (!result) return;
    remember(result);
    onClose();
    router.push(result.href);
  };

  const optionId = (i: number) => `${listId}-${i}`;
  const startsGroup = results.map((r, i) => i === 0 || results[i - 1].group !== r.group);

  return (
    <Modal labelledBy={titleId} onClose={onClose} size="md">
      <div className={styles.palette}>
        <h2 id={titleId} className="w360-visually-hidden">
          Search the console
        </h2>
        <div className={styles.paletteInputRow}>
          <Icon name="search" size={18} />
          <label htmlFor={inputId} className="w360-visually-hidden">
            Search anything
          </label>
          <input
            id={inputId}
            className={styles.paletteInput}
            type="search"
            role="combobox"
            aria-expanded={results.length > 0}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={results.length ? optionId(current) : undefined}
            autoComplete="off"
            placeholder="Search customers, partners, subscriptions, invoices or pages…"
            value={text}
            maxLength={80}
            onChange={(e) => {
              setText(e.target.value);
              setActive(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" && results.length) {
                e.preventDefault();
                setActive((current + 1) % results.length);
              } else if (e.key === "ArrowUp" && results.length) {
                e.preventDefault();
                setActive((current - 1 + results.length) % results.length);
              } else if (e.key === "Enter") {
                e.preventDefault();
                go(results[current]);
              }
            }}
          />
          <kbd className={styles.paletteKbd} aria-hidden="true">
            Esc
          </kbd>
        </div>
        <ul id={listId} role="listbox" aria-label="Search results" className={styles.paletteList}>
          {results.map((r, i) => {
            const heading = startsGroup[i] ? r.group : null;
            return (
              <li key={r.id} role="presentation">
                {heading && (
                  <p className={styles.paletteGroup} aria-hidden="true">
                    {heading === "Recent" ? "Recent" : heading}
                  </p>
                )}
                <div
                  id={optionId(i)}
                  role="option"
                  aria-selected={i === current}
                  className={styles.paletteOption}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    go(r);
                  }}
                >
                  <Icon name={r.icon} size={16} />
                  <span className={styles.paletteLabel}>{r.label}</span>
                  <span className={styles.resultHint}>{r.hint}</span>
                </div>
              </li>
            );
          })}
        </ul>
        <p className={styles.paletteStatus} aria-live="polite">
          {live.loading ? "Searching…" : live.failed ? "Records could not be searched right now." : query && results.length === 0 ? `No results for “${query}”.` : ""}
        </p>
      </div>
    </Modal>
  );
}

// --- the New menu --------------------------------------------------------------------

const NEW_ITEMS: { label: string; href: string; permission: string; icon: string }[] = [
  { label: "New customer", href: "/admin/customers", permission: "platform.staff_customer.manage", icon: "building" },
  { label: "New partner", href: "/admin/partners/new", permission: "platform.staff_partner.manage", icon: "handshake" },
  { label: "New product", href: "/admin/products/new", permission: "platform.staff_catalog.manage", icon: "box" },
  { label: "New plan", href: "/admin/plans", permission: "platform.staff_catalog.manage", icon: "tag" },
  { label: "New credit pack", href: "/admin/usage-pricing/credit-packs", permission: "platform.staff_pricing.manage", icon: "wallet" },
  { label: "New help article", href: "/admin/help-content", permission: "platform.staff_support.manage", icon: "book" },
  { label: "New Admin", href: "/admin/security", permission: "platform.staff_security.manage", icon: "shield" },
];

function NewMenu({ permissions }: { permissions: string[] }) {
  const items: MenuItem[] = NEW_ITEMS.filter((item) => can(permissions, item.permission)).map((item) => ({
    label: item.label,
    href: item.href,
    icon: item.icon,
  }));
  if (items.length === 0) return null;
  return (
    <Menu
      label="Create"
      items={items}
      triggerClassName={styles.newButton}
      triggerContent={
        <>
          <Icon name="plus" size={16} />
          <span className={styles.newLabel}>Create</span>
          <Icon name="chevronDown" size={16} />
        </>
      }
    />
  );
}

// --- notifications ---------------------------------------------------------------------

const ATTENTION: Record<AttentionItem["key"], { one: string; many: string; href: string; icon: string }> = {
  partner_upgrades: {
    one: "upgrade request waits for a decision",
    many: "upgrade requests wait for a decision",
    href: "/admin/partners/all",
    icon: "arrowUp",
  },
  draft_partners: {
    one: "partner is still a draft",
    many: "partners are still drafts",
    href: "/admin/partners/all?status=draft",
    icon: "handshake",
  },
  payouts: { one: "payout awaits action", many: "payouts await action", href: "/admin/partners/withdrawals", icon: "wallet" },
  kyc_reviews: {
    one: "partner application waits for KYC review",
    many: "partner applications wait for KYC review",
    href: "/admin/partners/applications",
    icon: "shield",
  },
  failed_payments: {
    one: "failed payment or activation needs attention",
    many: "failed payments or activations need attention",
    href: "/admin/payments/failed",
    icon: "alert",
  },
  low_balance: { one: "wallet is low on balance", many: "wallets are low on balance", href: "/admin/wallets?low=1", icon: "wallet" },
  support_tickets: { one: "support ticket is still open", many: "support tickets are still open", href: "/admin/tickets", icon: "ticket" },
};

const SEEN_KEY = "w360.admin.notifications.seen";

function readSeen(): Record<string, number> {
  try {
    return JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

/** What is waiting for staff (only what their grants cover), read when the
 * console opens and again whenever the panel is opened. "Mark all read"
 * remembers the counts on this browser, so only new items are counted as
 * unread. A failed read shows no count rather than a wrong one. */
function Bell({ permissions }: { permissions: string[] }) {
  const [items, setItems] = useState<AttentionItem[] | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [seen, setSeen] = useState<Record<string, number>>(() => (typeof window === "undefined" ? {} : readSeen()));
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const panelId = useId();
  const key = permissions.join(",");

  useEffect(() => {
    let live = true;
    adminApi.attention().then(
      (r) => {
        if (!live) return;
        setItems(r.items);
        setCheckedAt(Date.now());
      },
      () => live && setItems(null),
    );
    return () => {
      live = false;
    };
  }, [key, open]);

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

  const shown = (items ?? []).filter((item) => ATTENTION[item.key] && item.count > 0);
  const total = shown.reduce((sum, item) => sum + item.count, 0);
  const unread = shown.reduce((sum, item) => sum + Math.max(0, item.count - (seen[item.key] ?? 0)), 0);
  const markAllRead = () => {
    const next = Object.fromEntries(shown.map((item) => [item.key, item.count]));
    setSeen(next);
    try {
      window.localStorage.setItem(SEEN_KEY, JSON.stringify(next));
    } catch {
      /* private mode */
    }
  };

  return (
    <div className={styles.popoverWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={styles.iconButton}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={total > 0 ? `Notifications: ${total} waiting` : "Notifications"}
        title="Notifications"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="bell" size={20} />
        {unread > 0 && (
          <span aria-hidden="true" className={styles.badge}>
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div id={panelId} className={`${styles.popover} ${styles.bellPanel}`}>
          <div className={styles.popoverHead}>
            <p className={styles.popoverTitle}>Notifications</p>
            {unread > 0 && (
              <button type="button" className={styles.linkButton} onClick={markAllRead}>
                Mark all read
              </button>
            )}
          </div>
          {items === null ? (
            <p className={styles.empty}>Notifications could not be loaded right now.</p>
          ) : shown.length === 0 ? (
            <p className={styles.empty}>You&apos;re all caught up.</p>
          ) : (
            <ul className={styles.attention}>
              {shown.map((item) => {
                const words = ATTENTION[item.key];
                const isNew = item.count > (seen[item.key] ?? 0);
                return (
                  <li key={item.key}>
                    <Link href={words.href} className={styles.noteItem} onClick={() => setOpen(false)}>
                      <span className={styles.noteIcon}>
                        <Icon name={words.icon} size={16} />
                      </span>
                      <span className={styles.noteText}>
                        <span>
                          <strong>{item.count}</strong> {item.count === 1 ? words.one : words.many}
                        </span>
                        <span className={styles.noteTime}>{checkedAt ? `Checked ${formatRelative(checkedAt).toLowerCase()}` : ""}</span>
                      </span>
                      {isNew && (
                        <span className={styles.unreadDot}>
                          <span className="w360-visually-hidden">Unread</span>
                        </span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
          <div className={styles.popoverFoot}>
            <Link href="/admin/notifications" className={styles.linkButton} onClick={() => setOpen(false)}>
              View all
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

// --- the account button --------------------------------------------------------------

/** The staff member's role, as far as the console can tell from the grants it holds. */
export function roleLabel(permissions: string[]): string {
  if (permissions.includes("platform.staff_security.manage")) return "Admin";
  if (permissions.some((p) => p.endsWith(".manage"))) return "Staff admin";
  return "Staff (read-only)";
}

/** Avatar, full name and role; the menu has the name and email, then the
 * account's links, and Sign out set apart at the end. */
export function AdminAccount({
  name,
  email,
  role,
  entries,
}: {
  name: string;
  email: string;
  role: string;
  entries: MenuItem[];
}) {
  const shownName = name || email || "Your account";
  return (
    <Menu
      label={`Account menu for ${shownName}`}
      items={entries}
      align="end"
      triggerClassName={styles.accountButton}
      header={
        <>
          <strong>{shownName}</strong>
          {email && email !== shownName && <span>{email}</span>}
          <span>{role}</span>
        </>
      }
      triggerContent={
        <>
          <span aria-hidden="true" className={styles.avatar}>
            {initialsOf(shownName)}
          </span>
          <span aria-hidden="true" className={styles.accountText}>
            <span className={styles.accountName}>{shownName}</span>
            <span className={styles.accountRole}>{role}</span>
          </span>
          <span className={styles.accountChevron} aria-hidden="true">
            <Icon name="chevronDown" size={16} />
          </span>
        </>
      }
    />
  );
}
