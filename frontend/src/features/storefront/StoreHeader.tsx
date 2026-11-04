"use client";

import { Suspense, useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Logo } from "@/components/brand/Logo";
import { PUBLIC_CART, useCart } from "@/features/console/cart";
import { useSession } from "@/lib/auth/useSession";
import { publicApi, type PublicStorePage } from "@/lib/api/public";
import { StoreIcon, categoryIcon } from "./storeIcons";
import styles from "./StoreHeader.module.css";

/**
 * The marketplace's own frame (2026-11-04, the owner's marketplace mock-up),
 * for `/store` and a Store product's page: a dismissible announcement, then a
 * dark header with the Store's search, menus built from the public Store
 * itself (categories, business suites, who it is for, AI tools), Partner
 * Offers, what is new, the cart, and Log in / Get Started.
 *
 * Every menu is the server's answer (`GET public/store`) - never a hard-coded
 * product list; a link only sets the Store's filters, which the server checks.
 */

const ANNOUNCEMENT_KEY = "w360.store-announcement";

/** The public Store, unfiltered, for the menus - loaded once per page. */
function useStoreMenu(): PublicStorePage | null {
  const [page, setPage] = useState<PublicStorePage | null>(null);
  useEffect(() => {
    let live = true;
    publicApi.store({}).then(
      (p) => live && setPage(p),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, []);
  return page;
}

/** Whether the announcement was dismissed in this browser - read from
 * storage, and hidden on the server render so a dismissed bar never flashes. */
const announcementListeners = new Set<() => void>();
function dismissedNow(): boolean {
  try {
    return window.localStorage.getItem(ANNOUNCEMENT_KEY) === "dismissed";
  } catch {
    return false;
  }
}
function subscribeAnnouncement(listener: () => void): () => void {
  announcementListeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    announcementListeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}
function dismissAnnouncement() {
  try {
    window.localStorage.setItem(ANNOUNCEMENT_KEY, "dismissed");
  } catch {
    // Storage refused: nothing to remember it by.
  }
  announcementListeners.forEach((l) => l());
}

function Announcement() {
  const [closed, setClosed] = useState(false);
  const dismissed = useSyncExternalStore(subscribeAnnouncement, dismissedNow, () => true);
  const shown = !dismissed && !closed;
  if (!shown) return null;
  return (
    <div className={styles.announcement} role="region" aria-label="Announcement">
      <p className={styles.announcementText}>
        <StoreIcon name="bolt" size={16} />
        <strong>One platform. Every possibility.</strong>
        <span className={styles.announcementRule} aria-hidden="true">
          |
        </span>
        <span>Discover powerful business tools, AI solutions and automation - all at Workeasy360.</span>
      </p>
      <button
        type="button"
        className={styles.announcementClose}
        aria-label="Dismiss announcement"
        onClick={() => {
          setClosed(true); // for this page, even when storage is refused
          dismissAnnouncement();
        }}
      >
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}

/** One header dropdown: a button, and a panel that Escape or a click outside closes. */
function Menu({ label, children, wide = false }: { label: string; children: (close: () => void) => ReactNode; wide?: boolean }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const button = useRef<HTMLButtonElement | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);

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
    <div className={styles.menuWrap} ref={wrap}>
      <button ref={button} type="button" className={styles.navButton} aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        {label}
        <svg className={styles.caret} viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false">
          <path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div id={id} className={`${styles.menu} ${wide ? styles.menuWide : ""}`}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

function MenuLink({ href, onPick, children, hint }: { href: string; onPick: () => void; children: ReactNode; hint?: string }) {
  return (
    <li>
      <Link href={href} className={styles.menuItem} onClick={onPick}>
        <span className={styles.menuItemText}>
          <span>{children}</span>
          {hint && <span className={styles.menuHint}>{hint}</span>}
        </span>
      </Link>
    </li>
  );
}

/** The products of one type, or a line saying none are listed yet. */
function TypeMenu({ page, type, empty, all, close }: { page: PublicStorePage | null; type: string; empty: string; all: string; close: () => void }) {
  const products = (page?.products ?? []).filter((p) => (p.product_type ?? "saas") === type);
  return (
    <ul className={styles.menuList}>
      {page && products.length === 0 && <li className={styles.menuEmpty}>{empty}</li>}
      {products.map((p) => (
        <MenuLink key={p.code} href={`/store/product/${p.slug}`} onPick={close} hint={p.summary || undefined}>
          {p.title}
        </MenuLink>
      ))}
      {products.length > 0 && (
        <MenuLink href={`/store?type=${type}`} onPick={close}>
          {all}
        </MenuLink>
      )}
    </ul>
  );
}

/** What is new in the Store: products wearing the "New" badge. */
function WhatsNew({ page }: { page: PublicStorePage | null }) {
  const fresh = (page?.products ?? []).filter((p) => p.badges?.includes("new") || p.ribbon?.kind === "new");
  const count = fresh.length;
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className={styles.menuWrap} ref={wrap}>
      <button
        type="button"
        className={styles.iconButton}
        aria-expanded={open}
        aria-controls={id}
        aria-label={count ? `What's new: ${count} new product${count === 1 ? "" : "s"}` : "What's new"}
        onClick={() => setOpen((v) => !v)}
      >
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
          <path
            d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {count > 0 && (
          <span className={styles.dot} aria-hidden="true">
            {count}
          </span>
        )}
      </button>
      {open && (
        <div id={id} className={`${styles.menu} ${styles.menuEnd}`}>
          <p className={styles.menuTitle}>What&apos;s new</p>
          <ul className={styles.menuList}>
            {count === 0 && <li className={styles.menuEmpty}>No new products right now.</li>}
            {fresh.map((p) => (
              <MenuLink key={p.code} href={`/store/product/${p.slug}`} onPick={() => setOpen(false)} hint={p.summary || undefined}>
                {p.title}
              </MenuLink>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function HeaderSearch() {
  const router = useRouter();
  const params = useSearchParams();
  const [text, setText] = useState(params.get("q") ?? "");
  const id = useId();
  return (
    <form
      role="search"
      className={styles.search}
      onSubmit={(e) => {
        e.preventDefault();
        const q = text.trim();
        router.push(q ? `/store?q=${encodeURIComponent(q)}` : "/store");
      }}
    >
      <label htmlFor={id} className="w360-visually-hidden">
        Search products
      </label>
      <svg className={styles.searchIcon} viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
        <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="m20 20-4-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <input
        id={id}
        className={styles.searchInput}
        type="search"
        placeholder="Search products, tools & solutions"
        maxLength={100}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </form>
  );
}

export function StoreHeader() {
  const page = useStoreMenu();
  const session = useSession();
  const signedIn = session.status === "ready" && session.session.authenticated;
  const { cart } = useCart(PUBLIC_CART);
  const inCart = cart.items.length;
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  // Closing the mobile menu on navigation.
  const [seenPath, setSeenPath] = useState(pathname);
  if (seenPath !== pathname) {
    setSeenPath(pathname);
    setNavOpen(false);
  }

  return (
    <>
      <Announcement />
      <header className={`${styles.header} ${navOpen ? styles.navOpen : ""}`}>
        <div className={styles.inner}>
          <Link href="/" className={styles.logo} aria-label="Workeasy360 home">
            <Logo compact onBand />
          </Link>
          <Suspense fallback={<div className={styles.search} />}>
            <HeaderSearch />
          </Suspense>
          <button
            type="button"
            className={styles.mobileToggle}
            aria-expanded={navOpen}
            onClick={() => setNavOpen((v) => !v)}
          >
            Menu
          </button>
          <nav className={styles.nav} aria-label="Store">
            <Menu label="All Products" wide>
              {(close) => (
                <ul className={`${styles.menuList} ${styles.menuColumns}`}>
                  <MenuLink href="/store" onPick={close}>
                    Browse all products
                  </MenuLink>
                  {(page?.facets.categories ?? []).map((c) => (
                    <li key={c.value}>
                      <Link href={`/store?category=${encodeURIComponent(c.value)}`} className={styles.menuItem} onClick={close}>
                        <StoreIcon name={categoryIcon(c.label)} size={18} className={styles.menuIcon} />
                        <span className={styles.menuItemText}>
                          <span>{c.label}</span>
                          <span className={styles.menuHint}>
                            {c.count} product{c.count === 1 ? "" : "s"}
                          </span>
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Menu>
            <Menu label="Business Suite">
              {(close) => (
                <TypeMenu page={page} type="business_suite" empty="Business suites are coming soon." all="See every business suite" close={close} />
              )}
            </Menu>
            <Menu label="Industry Solutions">
              {(close) => (
                <ul className={styles.menuList}>
                  {(page?.facets.best_for ?? []).map((b) => (
                    <MenuLink key={b.value} href={`/store?best_for=${b.value}`} onPick={close} hint={b.count ? `${b.count} product${b.count === 1 ? "" : "s"}` : undefined}>
                      For {b.label.toLowerCase()}
                    </MenuLink>
                  ))}
                  {page && (page.facets.best_for ?? []).length === 0 && <li className={styles.menuEmpty}>Solutions by industry are coming soon.</li>}
                </ul>
              )}
            </Menu>
            <Menu label="AI Tools">
              {(close) => <TypeMenu page={page} type="ai_tool" empty="AI tools are coming soon." all="See every AI tool" close={close} />}
            </Menu>
            <Link href="/login?mode=partner" className={styles.navLink}>
              Partner Offers
            </Link>
          </nav>
          <div className={styles.actions}>
            <WhatsNew page={page} />
            <Link href="/store#cart" className={styles.iconButton} aria-label={inCart ? `Cart: ${inCart} product${inCart === 1 ? "" : "s"}` : "Cart"}>
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
                <path
                  d="M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.5L21 8H6.2M10 20.5h.01M17 20.5h.01"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              {inCart > 0 && (
                <span className={styles.dot} aria-hidden="true">
                  {inCart}
                </span>
              )}
            </Link>
            {signedIn ? (
              <Link href="/dashboard?mode=user" className={styles.start}>
                My dashboard
              </Link>
            ) : (
              <>
                <Link href="/login?mode=user" className={styles.login}>
                  Log in
                </Link>
                <Link href="/signup" className={styles.start}>
                  Get Started
                </Link>
              </>
            )}
          </div>
        </div>
      </header>
    </>
  );
}
