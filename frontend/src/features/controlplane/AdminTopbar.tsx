"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { NavIcon } from "@/components/sidebar/icons";
import type { SidebarItem } from "@/components/sidebar/SidebarShell";
import { adminApi, type AttentionItem } from "@/lib/api/controlPlane";
import { usePortalRouter } from "@/lib/portalRouter";
import { adminNav } from "./adminNav";

import styles from "./AdminTopbar.module.css";

const can = (permissions: string[], key: string) => permissions.includes(key);

/**
 * The Super Admin's top bar (2026-10-26 redesign): search, the New menu, the
 * notification bell and the settings shortcut. Everything here is a way to a
 * page the staff member could already reach from the menu - each page and each
 * request behind it checks the grant again.
 */
export function AdminTools({ permissions }: { permissions: string[] }) {
  return (
    <>
      <AdminSearch permissions={permissions} />
      <div className={styles.spacer} />
      <NewMenu permissions={permissions} />
      <Bell permissions={permissions} />
      {can(permissions, "platform.staff_settings.view") && (
        <Link href="/admin/settings" className={styles.iconButton} aria-label="Platform settings" title="Platform settings">
          <NavIcon name="settings" />
        </Link>
      )}
    </>
  );
}

// --- search ------------------------------------------------------------------------

interface Result {
  id: string;
  label: string;
  hint: string;
  href: string;
}

/** Every page in this staff member's menu, with the section it sits in. */
function pagesFor(permissions: string[]): Result[] {
  const out: Result[] = [];
  const walk = (items: SidebarItem[], section: string) => {
    for (const item of items) {
      if (item.when === false) continue;
      if (item.children) walk(item.children, item.label);
      else out.push({ id: `page-${item.href}`, label: item.label, hint: section || "Page", href: item.href });
    }
  };
  for (const group of adminNav(permissions)) walk(group.items, "");
  return out;
}

/** "Search anything": the pages of the console by name, and a search of users
 * or partners for the words typed. Ctrl+K (⌘K) jumps here from anywhere. */
export function AdminSearch({ permissions }: { permissions: string[] }) {
  const router = usePortalRouter();
  const inputId = useId();
  const listId = useId();
  const input = useRef<HTMLInputElement | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const pages = useMemo(() => pagesFor(permissions), [permissions]);

  const query = text.trim();
  const results = useMemo(() => {
    if (!query) return [];
    const needle = query.toLowerCase();
    const found = pages
      .filter((p) => p.label.toLowerCase().includes(needle) || p.hint.toLowerCase().includes(needle))
      .slice(0, 6);
    const q = encodeURIComponent(query.slice(0, 80));
    if (can(permissions, "platform.staff_user.view")) {
      found.push({ id: "users", label: `Search users for “${query}”`, hint: "Users", href: `/admin/users?q=${q}` });
    }
    if (can(permissions, "platform.staff_partner.view")) {
      found.push({
        id: "partners",
        label: `Search partners for “${query}”`,
        hint: "Partners",
        href: `/admin/partners/all?q=${q}`,
      });
    }
    return found;
  }, [query, pages, permissions]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
        input.current?.select();
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
  }, []);

  const go = (result: Result | undefined) => {
    if (!result) return;
    setOpen(false);
    setText("");
    router.push(result.href);
  };
  const shown = open && results.length > 0;
  const optionId = (i: number) => `${listId}-${i}`;

  return (
    <div className={styles.search} ref={wrap}>
      <form
        role="search"
        aria-label="Search the console"
        onSubmit={(e) => {
          e.preventDefault();
          go(results[active]);
        }}
      >
        <label htmlFor={inputId} className="w360-visually-hidden">
          Search anything
        </label>
        <span aria-hidden="true" className={styles.searchIcon}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
        </span>
        <input
          ref={input}
          id={inputId}
          className={styles.searchInput}
          type="search"
          role="combobox"
          aria-expanded={shown}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={shown ? optionId(active) : undefined}
          autoComplete="off"
          placeholder="Search anything…"
          value={text}
          maxLength={80}
          onChange={(e) => {
            setText(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && results.length) {
              e.preventDefault();
              setOpen(true);
              setActive((i) => (i + 1) % results.length);
            } else if (e.key === "ArrowUp" && results.length) {
              e.preventDefault();
              setActive((i) => (i - 1 + results.length) % results.length);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        <kbd className={styles.kbd} aria-hidden="true">
          Ctrl K
        </kbd>
      </form>
      <ul id={listId} role="listbox" aria-label="Search results" className={styles.results} hidden={!shown}>
        {results.map((r, i) => (
          <li
            key={r.id}
            id={optionId(i)}
            role="option"
            aria-selected={i === active}
            className={styles.result}
            onMouseEnter={() => setActive(i)}
            onMouseDown={(e) => {
              // Before the input's blur: navigate on the press, as a link would.
              e.preventDefault();
              go(r);
            }}
          >
            <span>{r.label}</span>
            <span className={styles.resultHint}>{r.hint}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- the New menu --------------------------------------------------------------------

const NEW_ITEMS: { label: string; href: string; permission: string }[] = [
  { label: "Add partner", href: "/admin/partners/new", permission: "platform.staff_partner.manage" },
  { label: "Add product", href: "/admin/products/new", permission: "platform.staff_catalog.manage" },
  { label: "New plan", href: "/admin/plans", permission: "platform.staff_catalog.manage" },
  { label: "Grant staff access", href: "/admin/security", permission: "platform.staff_security.manage" },
];

function NewMenu({ permissions }: { permissions: string[] }) {
  const items = NEW_ITEMS.filter((item) => can(permissions, item.permission));
  const { open, setOpen, wrap, button } = usePopover();
  const menuId = useId();
  if (items.length === 0) return null;
  return (
    <div className={styles.popoverWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={styles.newButton}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true">+</span> New <span aria-hidden="true" className={styles.caret}>⌄</span>
      </button>
      {open && (
        <div id={menuId} role="menu" className={styles.popover}>
          {items.map((item) => (
            <Link key={item.label} role="menuitem" href={item.href} className={styles.menuItem} onClick={() => setOpen(false)}>
              {item.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// --- the bell ------------------------------------------------------------------------

const ATTENTION: Record<AttentionItem["key"], { one: string; many: string; href: string }> = {
  partner_upgrades: {
    one: "upgrade request waits for a decision",
    many: "upgrade requests wait for a decision",
    href: "/admin/partners/all",
  },
  draft_partners: {
    one: "partner is still a draft",
    many: "partners are still drafts",
    href: "/admin/partners/all?status=draft",
  },
  payouts: { one: "payout awaits action", many: "payouts await action", href: "/admin/partners/all" },
};

/** What is waiting for staff (only what their grants cover), read when the
 * console opens and again whenever the bell is opened. A failed read shows no
 * count rather than a wrong one. */
function Bell({ permissions }: { permissions: string[] }) {
  const [items, setItems] = useState<AttentionItem[] | null>(null);
  const { open, setOpen, wrap, button } = usePopover();
  const panelId = useId();
  const key = permissions.join(",");

  useEffect(() => {
    let live = true;
    adminApi.attention().then(
      (r) => live && setItems(r.items),
      () => live && setItems(null),
    );
    return () => {
      live = false;
    };
  }, [key, open]);

  const total = (items ?? []).reduce((sum, item) => sum + item.count, 0);
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
        <NavIcon name="bell" />
        {total > 0 && (
          <span aria-hidden="true" className={styles.badge}>
            {total > 99 ? "99+" : total}
          </span>
        )}
      </button>
      {open && (
        <div id={panelId} className={`${styles.popover} ${styles.bellPanel}`}>
          <p className={styles.popoverTitle}>Waiting for you</p>
          {items === null ? (
            <p className={styles.empty}>Notifications could not be loaded right now.</p>
          ) : items.length === 0 ? (
            <p className={styles.empty}>Nothing is waiting for you.</p>
          ) : (
            <ul className={styles.attention}>
              {items.map((item) => {
                const words = ATTENTION[item.key];
                if (!words) return null;
                return (
                  <li key={item.key}>
                    <Link href={words.href} className={styles.menuItem} onClick={() => setOpen(false)}>
                      <strong>{item.count}</strong> {item.count === 1 ? words.one : words.many}
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** A button's popover: closes on Escape (focus back to the button) and on a click outside. */
function usePopover() {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
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
  return { open, setOpen, wrap, button };
}
