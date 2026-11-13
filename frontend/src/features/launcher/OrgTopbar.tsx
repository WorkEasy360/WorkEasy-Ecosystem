"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { getOrganizations } from "@/lib/api/auth";
import { getAppDirectory } from "@/lib/api/launcher";
import { orgAccountApi, type OrgNotification } from "@/lib/api/orgAccount";
import type { OrganizationSummary } from "@/lib/api/types";
import { usePortalRouter } from "@/lib/portalRouter";
import { Menu, formatRelative, type MenuItem } from "@/features/controlplane/kit";
import { Modal } from "@/features/controlplane/Modal";
import topbar from "@/features/controlplane/AdminTopbar.module.css";
import styles from "./OrgTopbar.module.css";
import { organizationPages } from "./orgNav";

/**
 * The User Admin Panel's top bar (2026-11-11, the owner's specification and
 * reference image): the organization switcher, a search over this member's pages
 * and products, notifications, help and the personal account menu.
 *
 * Nothing here decides access: what each lists comes from the member's own
 * permissions and the server's answers, and every page it opens is authorized
 * again there.
 */

// --- organization switcher ----------------------------------------------------------

/** The current organization. Other organizations are offered only when this person
 * belongs to more than one; with one there is nothing to choose, so nothing opens. */
export function OrgSwitcher({ slug, name }: { slug: string; name: string }) {
  const [others, setOthers] = useState<OrganizationSummary[] | null>(null);
  const [more, setMore] = useState(false);

  useEffect(() => {
    let live = true;
    getOrganizations({ limit: 20 }).then(
      (page) => {
        if (!live) return;
        setOthers(page.organizations.filter((o) => o.slug !== slug));
        setMore(page.has_more);
      },
      // The switcher still names the current organization; the chooser stays one link away.
      () => live && setOthers([]),
    );
    return () => {
      live = false;
    };
  }, [slug]);

  const content = (
    <>
      <Icon name="building" size={18} />
      <span className={styles.orgName}>{name}</span>
    </>
  );
  if (!others || others.length === 0) {
    return (
      <span className={styles.orgPill} title={name}>
        {content}
      </span>
    );
  }
  const items: MenuItem[] = [
    ...others.map((o) => ({ label: o.display_name, icon: "building", href: `/o/${encodeURIComponent(o.slug)}/dashboard` })),
    { label: more ? "All organizations…" : "Manage organizations", icon: "apps", href: "/organizations/select", separator: true },
  ];
  return (
    <Menu
      label={`Current organization: ${name}. Switch organization`}
      items={items}
      align="start"
      triggerClassName={styles.orgPill}
      header={
        <>
          <strong>{name}</strong>
          <span>Current organization</span>
        </>
      }
      triggerContent={
        <>
          {content}
          <span aria-hidden="true" className={styles.orgChevron}>
            <Icon name="chevronDown" size={16} />
          </span>
        </>
      }
    />
  );
}

// --- search -----------------------------------------------------------------------------

interface Result {
  id: string;
  group: "Pages" | "Products";
  label: string;
  hint: string;
  href: string;
  icon: string;
  words: string;
}

/** The words a page is also found by ("invoices" finds Orders & Payments). */
const KEYWORDS: Record<string, string> = {
  dashboard: "home overview summary",
  apps: "products open launch setup seats",
  store: "buy shop marketplace explore",
  explore: "trial plans pricing upgrade",
  "settings/billing": "subscriptions renew cancel plan",
  orders: "orders payments invoices receipts refund",
  "settings/billing/profile": "billing address tax gst email business",
  wallets: "wallet balance credits",
  "wallets/platform": "add money top up deductions transactions",
  "wallets/ai-credits": "ai credit packs buy allowance usage",
  "wallets/consumption": "recharge whatsapp sms email api webhook charges",
  "wallets/usage": "usage export report",
  "settings/organization": "company business profile partner support contact",
  "settings/members": "team users people roles remove",
  "settings/invitations": "invite team member",
  "settings/products": "product access seats assign",
  help: "support tickets guides contact",
  "settings/profile": "name account",
  "settings/security": "password sessions sign out",
  "settings/notifications": "alerts notifications",
  "settings/audit": "activity history log",
};

/** The search box: the member's pages by name (and the words above), and the
 * products the organization holds. Ctrl/⌘K opens it from anywhere. */
export function OrgSearch({ slug, can }: { slug: string; can: (permission: string) => boolean }) {
  const [open, setOpen] = useState(false);

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
    <div className={`${topbar.search} ${styles.search}`}>
      <button
        type="button"
        className={topbar.searchOpener}
        aria-haspopup="dialog"
        // Named by its visible words plus the hidden shortcut (WCAG 2.5.3, label in name):
        // a different aria-label left voice-control users unable to say what they see.
        onClick={() => setOpen(true)}
      >
        <Icon name="search" size={16} />
        <span className={topbar.searchPlaceholder}>Search products, subscriptions, invoices, tickets…</span>
        <kbd className={topbar.kbd} aria-hidden="true">
          Ctrl K
        </kbd>
        <span className="w360-visually-hidden"> (Ctrl+K)</span>
      </button>
      {open && <SearchPalette slug={slug} can={can} onClose={() => setOpen(false)} />}
    </div>
  );
}

function SearchPalette({ slug, can, onClose }: { slug: string; can: (permission: string) => boolean; onClose: () => void }) {
  const router = usePortalRouter();
  const titleId = useId();
  const inputId = useId();
  const listId = useId();
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const [products, setProducts] = useState<Result[]>([]);
  const base = `/o/${slug}`;

  const pages = useMemo<Result[]>(
    () =>
      organizationPages(slug, can).map((p) => {
        const path = p.href.slice(base.length + 1);
        return {
          id: `page-${p.href}`,
          group: "Pages",
          label: p.label,
          hint: p.module,
          href: p.href,
          icon: p.icon,
          words: `${p.label} ${p.module} ${KEYWORDS[path] ?? ""}`.toLowerCase(),
        };
      }),
    // `can` is rebuilt with every render of the shell; the permissions behind it are not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [slug, base],
  );

  // The organization's products, read when the search opens (only what this member may see).
  useEffect(() => {
    let live = true;
    getAppDirectory(slug).then(
      ({ apps }) =>
        live &&
        setProducts(
          apps
            .filter((a) => a.state !== "no_entitlement")
            .map((a) => ({
              id: `product-${a.product_code}`,
              group: "Products",
              label: a.display_name,
              hint: a.plan?.name ?? "My Products",
              href: `${base}/apps?product=${encodeURIComponent(a.product_code)}`,
              icon: "box",
              words: `${a.display_name} ${a.tagline} ${a.category}`.toLowerCase(),
            })),
        ),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [slug, base]);

  const query = text.trim().toLowerCase();
  const results = useMemo(() => {
    if (!query) return pages.slice(0, 8);
    const hit = (r: Result) => query.split(/\s+/).every((w) => r.words.includes(w));
    return [...pages.filter(hit).slice(0, 8), ...products.filter(hit).slice(0, 6)];
  }, [query, pages, products]);
  const current = Math.min(active, Math.max(0, results.length - 1));
  const optionId = (i: number) => `${listId}-${i}`;
  const go = (r: Result | undefined) => {
    if (!r) return;
    onClose();
    router.push(r.href);
  };

  return (
    <Modal labelledBy={titleId} onClose={onClose} size="md">
      <div className={topbar.palette}>
        <h2 id={titleId} className="w360-visually-hidden">
          Search your organization
        </h2>
        <div className={topbar.paletteInputRow}>
          <Icon name="search" size={18} />
          <label htmlFor={inputId} className="w360-visually-hidden">
            Search pages and products
          </label>
          <input
            id={inputId}
            className={topbar.paletteInput}
            type="search"
            role="combobox"
            aria-expanded={results.length > 0}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={results.length ? optionId(current) : undefined}
            autoComplete="off"
            placeholder="Search products, subscriptions, invoices, tickets…"
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
          <kbd className={topbar.paletteKbd} aria-hidden="true">
            Esc
          </kbd>
        </div>
        <ul id={listId} role="listbox" aria-label="Search results" className={topbar.paletteList}>
          {results.map((r, i) => (
            <li key={r.id} role="presentation">
              {(i === 0 || results[i - 1].group !== r.group) && (
                <p className={topbar.paletteGroup} aria-hidden="true">
                  {r.group}
                </p>
              )}
              <div
                id={optionId(i)}
                role="option"
                aria-selected={i === current}
                className={topbar.paletteOption}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  go(r);
                }}
              >
                <Icon name={r.icon} size={16} />
                <span className={topbar.paletteLabel}>{r.label}</span>
                <span className={topbar.resultHint}>{r.hint}</span>
              </div>
            </li>
          ))}
        </ul>
        <p className={topbar.paletteStatus} aria-live="polite">
          {query && results.length === 0 ? `No results for “${text.trim()}”.` : ""}
        </p>
      </div>
    </Modal>
  );
}

// --- notifications -------------------------------------------------------------------

const CATEGORY_ICON: Record<OrgNotification["category"], string> = {
  setup: "settings",
  renewal: "calendar",
  payment: "receipt",
  low_balance: "wallet",
  ticket: "ticket",
};

const seenKey = (slug: string) => `w360.org.notifications.seen.${slug}`;

function readSeen(slug: string): string[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(seenKey(slug)) ?? "[]");
    return Array.isArray(raw) ? raw.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

/** Product setup, renewals, payments, low balances and ticket replies, as the server
 * derives them for this member (only what their permissions cover, and only the
 * kinds they keep on in Notification Preferences). Read when the console opens and
 * again whenever the panel opens; "Mark all read" is remembered on this browser. */
export function OrgBell({ slug }: { slug: string }) {
  const [items, setItems] = useState<OrgNotification[] | null | undefined>(undefined);
  const [seen, setSeen] = useState<string[]>(() => (typeof window === "undefined" ? [] : readSeen(slug)));
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const panelId = useId();
  const base = `/o/${slug}`;

  useEffect(() => {
    let live = true;
    orgAccountApi.notifications(slug).then(
      (r) => live && setItems(r.items),
      () => live && setItems(null),
    );
    return () => {
      live = false;
    };
  }, [slug, open]);

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

  const list = items ?? [];
  const unread = list.filter((n) => !seen.includes(n.id)).length;
  const markAllRead = () => {
    const next = list.map((n) => n.id);
    setSeen(next);
    try {
      window.localStorage.setItem(seenKey(slug), JSON.stringify(next));
    } catch {
      /* private mode */
    }
  };

  return (
    <div className={topbar.popoverWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={topbar.iconButton}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={unread > 0 ? `Notifications: ${unread} unread` : "Notifications"}
        title="Notifications"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="bell" size={20} />
        {unread > 0 && (
          <span aria-hidden="true" className={topbar.badge}>
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div id={panelId} className={`${topbar.popover} ${topbar.bellPanel}`}>
          <div className={topbar.popoverHead}>
            <p className={topbar.popoverTitle}>Notifications</p>
            {unread > 0 && (
              <button type="button" className={topbar.linkButton} onClick={markAllRead}>
                Mark all read
              </button>
            )}
          </div>
          {items === undefined ? (
            <p className={topbar.empty}>Loading…</p>
          ) : items === null ? (
            <p className={topbar.empty}>Notifications could not be loaded right now.</p>
          ) : list.length === 0 ? (
            <p className={topbar.empty}>You&apos;re all caught up.</p>
          ) : (
            <ul className={topbar.attention}>
              {list.map((n) => (
                <li key={n.id}>
                  <Link
                    href={`${base}/${n.href.replace(/^\/+/, "")}`}
                    className={topbar.noteItem}
                    onClick={() => setOpen(false)}
                    data-tone={n.tone}
                  >
                    <span className={`${topbar.noteIcon} ${styles.noteIcon}`} data-tone={n.tone}>
                      <Icon name={CATEGORY_ICON[n.category] ?? "bell"} size={16} />
                    </span>
                    <span className={topbar.noteText}>
                      <span>
                        <strong>{n.title}</strong>
                      </span>
                      {n.detail && <span className={styles.noteDetail}>{n.detail}</span>}
                      {n.occurred_at && <span className={topbar.noteTime}>{formatRelative(n.occurred_at)}</span>}
                    </span>
                    {!seen.includes(n.id) && (
                      <span className={topbar.unreadDot}>
                        <span className="w360-visually-hidden">Unread</span>
                      </span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <div className={topbar.popoverFoot}>
            <Link href={`${base}/settings/notifications`} className={topbar.linkButton} onClick={() => setOpen(false)}>
              Notification preferences
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

// --- help -------------------------------------------------------------------------------

export function OrgHelpLink({ slug }: { slug: string }) {
  return (
    <Link href={`/o/${slug}/help`} className={topbar.iconButton} aria-label="Help" title="Help">
      <Icon name="help" size={20} />
    </Link>
  );
}
