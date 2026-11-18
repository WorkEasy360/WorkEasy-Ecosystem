"use client";

import { Suspense, createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Logo } from "@/components/brand/Logo";
import { Icon } from "@/components/sidebar/icons";
import { publicApi, type PublicStorePage, type SiteSettings } from "@/lib/api/public";
import { orgAccountApi, type OrgNotification } from "@/lib/api/orgAccount";
import { usePortals } from "@/lib/portalContext";
import { StoreAccountProvider, useMyAccount, useStoreAccount } from "./account";
import { POLICIES, myAccountLinks, partnerPaths, storePaths } from "./links";
import styles from "./Store.module.css";

/**
 * The Digital Store's frame (ADR-065, the owner's two reference images): a white
 * header with the WorkEasy360 logo and its "Store" badge, the four menus -
 * Products, Recharge, Partners, Help - the product search, and either Sign In /
 * Get Started or the buyer's bell and account menu (organization, My Account's
 * six User Admin pages, Sign out); a mobile menu with the same four entries; and
 * a footer of Products, Support, Partners, Legal and the company.
 *
 * Checkout is a step, never a menu, and there is no cart: one product, one plan
 * and one organization per order. Every menu entry is a link only - the pages
 * behind them, and the server behind those, decide what anyone may see or buy.
 */

/** The Store's public catalog for the menus and the footer - loaded once per page. */
export function useStoreCatalog(): PublicStorePage | null {
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

export function useSite(): SiteSettings | null {
  const [site, setSite] = useState<SiteSettings | null>(null);
  useEffect(() => {
    let live = true;
    publicApi.site().then(
      (s) => live && setSite(s),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, []);
  return site;
}

/** The partner portal's address for a path (its own host in production). */
export function usePartnerHref(): (path: string) => string {
  const origins = usePortals();
  return (path: string) => (origins ? `${origins.partner}${path}` : path);
}

function useOutside(open: boolean, close: () => void, wrap: React.RefObject<HTMLElement | null>, button?: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        button?.current?.focus();
      }
    };
    const onClick = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open, close, wrap, button]);
}

type MenuItem = { href: string; label: string; hint?: string; icon?: string; external?: boolean };

function NavMenu({ label, items, active }: { label: string; items: MenuItem[]; active: boolean }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  useOutside(open, () => setOpen(false), wrap, button);
  return (
    <div className={styles.menuWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={`${styles.navButton} ${active ? styles.navActive : ""}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
      >
        {label}
        <Icon name="chevronDown" size={16} className={styles.caret} />
      </button>
      {open && (
        <ul id={id} className={styles.menu}>
          {items.map((item) => (
            <li key={item.href + item.label}>
              {item.external ? (
                <a href={item.href} className={styles.menuItem} onClick={() => setOpen(false)}>
                  {item.icon && <Icon name={item.icon} size={18} className={styles.menuIcon} />}
                  <span className={styles.menuText}>
                    <span>{item.label}</span>
                    {item.hint && <span className={styles.menuHint}>{item.hint}</span>}
                  </span>
                </a>
              ) : (
                <Link href={item.href} className={styles.menuItem} onClick={() => setOpen(false)}>
                  {item.icon && <Icon name={item.icon} size={18} className={styles.menuIcon} />}
                  <span className={styles.menuText}>
                    <span>{item.label}</span>
                    {item.hint && <span className={styles.menuHint}>{item.hint}</span>}
                  </span>
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The four menus, from the public catalog where they list products. */
export function useMenus(page: PublicStorePage | null): { key: string; label: string; prefix: string; items: MenuItem[] }[] {
  const partner = usePartnerHref();
  const categories = (page?.facets.categories ?? []).filter((c) => c.count > 0);
  const soon = (page?.facets.statuses ?? []).find((s) => s.value === "coming_soon" && s.count > 0);
  return [
    {
      key: "products",
      label: "Products",
      prefix: "/store/product",
      items: [
        { href: storePaths.products, label: "All products", hint: page ? `${page.total} product${page.total === 1 ? "" : "s"}` : undefined, icon: "apps" },
        ...categories.map((c) => ({
          href: `${storePaths.products}?category=${encodeURIComponent(c.value)}`,
          label: c.label,
          hint: `${c.count} product${c.count === 1 ? "" : "s"}`,
          icon: "box",
        })),
        ...(soon ? [{ href: `${storePaths.products}?status=coming_soon`, label: "Coming soon", hint: `${soon.count}`, icon: "clock" }] : []),
      ],
    },
    {
      key: "recharge",
      label: "Recharge",
      prefix: storePaths.recharge,
      items: [
        { href: `${storePaths.recharge}#platform`, label: "Platform Wallet", hint: "Money for subscriptions", icon: "wallet" },
        { href: `${storePaths.recharge}#ai-credits`, label: "AI Credits", hint: "Credit packs for AI features", icon: "sparkles" },
        { href: `${storePaths.recharge}#consumption`, label: "Consumption Wallet", hint: "Messaging, API and webhooks", icon: "coins" },
      ],
    },
    {
      key: "partners",
      label: "Partners",
      prefix: storePaths.partners,
      items: [
        { href: storePaths.partners, label: "Become a Partner", hint: "Affiliate and Reseller programmes", icon: "handshake" },
        { href: `${storePaths.partners}#affiliate`, label: "Affiliate programme", icon: "megaphone" },
        { href: `${storePaths.partners}#reseller`, label: "Reseller programme", icon: "store" },
        { href: partner(partnerPaths.signIn), label: "Partner sign in", hint: "Existing partners", icon: "login", external: true },
      ],
    },
    {
      key: "help",
      label: "Help",
      prefix: "/store/help",
      items: [
        { href: storePaths.help, label: "Purchase help & FAQs", icon: "help" },
        { href: `${storePaths.help}#contact`, label: "Contact support", icon: "headset" },
        { href: `${storePaths.help}#policies`, label: "Policies", icon: "book" },
      ],
    },
  ];
}

function HeaderSearch({ onDone }: { onDone?: () => void }) {
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
        router.push(q ? `${storePaths.products}?q=${encodeURIComponent(q)}` : storePaths.products);
        onDone?.();
      }}
    >
      <label htmlFor={id} className="w360-visually-hidden">
        Search products
      </label>
      <Icon name="search" size={18} className={styles.searchIcon} />
      <input
        id={id}
        className={styles.searchInput}
        type="search"
        placeholder="Search products..."
        maxLength={100}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </form>
  );
}

function initials(name: string | undefined, email: string | undefined): string {
  const words = (name || email || "?").split(/[\s@.]+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

/** The bell: the organization's own alerts (renewals, payments, setup, low
 * balances), each opening its User Admin page. Read only. */
function Bell() {
  const account = useStoreAccount();
  const myAccount = useMyAccount();
  const slug = account.organization?.slug ?? null;
  const [items, setItems] = useState<OrgNotification[]>([]);
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLDivElement | null>(null);
  useOutside(open, () => setOpen(false), wrap);
  useEffect(() => {
    if (!slug) return;
    let live = true;
    orgAccountApi.notifications(slug).then(
      (r) => live && setItems(r.items),
      () => live && setItems([]),
    );
    return () => {
      live = false;
    };
  }, [slug]);
  if (!slug) return null;
  const count = items.length;
  return (
    <div className={styles.menuWrap} ref={wrap}>
      <button
        type="button"
        className={styles.iconButton}
        aria-expanded={open}
        aria-controls={id}
        aria-label={count ? `Notifications: ${count}` : "Notifications"}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="bell" size={22} />
        {count > 0 && (
          <span className={styles.dot} aria-hidden="true">
            {count > 9 ? "9+" : count}
          </span>
        )}
      </button>
      {open && (
        <div id={id} className={`${styles.menu} ${styles.menuEnd} ${styles.menuWide}`}>
          <p className={styles.menuTitle}>Notifications</p>
          {count === 0 ? (
            <p className={styles.menuEmpty}>Nothing needs your attention.</p>
          ) : (
            <ul className={styles.plainList}>
              {items.slice(0, 6).map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    className={styles.menuItem}
                    onClick={() => void myAccount.open(`/o/${encodeURIComponent(slug)}/${n.href.replace(/^\/+/, "")}`)}
                  >
                    <span className={styles.menuText}>
                      <span>{n.title}</span>
                      {n.detail && <span className={styles.menuHint}>{n.detail}</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function AccountMenu() {
  const account = useStoreAccount();
  const myAccount = useMyAccount();
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  useOutside(open, () => setOpen(false), wrap, button);
  const session = account.session;
  const org = account.organization;
  const links = myAccountLinks(org?.slug ?? null);
  return (
    <div className={styles.menuWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={styles.avatarButton}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
      >
        {/* The name is the initials shown plus whose account (WCAG 2.5.3: the
            visible label is part of the accessible one). */}
        <span className={styles.avatar}>{initials(session?.name, session?.email)}</span>
        <span className="w360-visually-hidden">Account menu for {session?.name || session?.email || "you"}</span>
        <Icon name="chevronDown" size={16} />
      </button>
      {open && (
        <div id={id} className={`${styles.menu} ${styles.menuEnd} ${styles.menuWide}`}>
          <div className={styles.who}>
            <strong>{session?.name || "Your account"}</strong>
            <span>{session?.email}</span>
          </div>
          {account.organizations.length > 1 ? (
            <label className={styles.orgPick}>
              <span>Organization</span>
              <select className="w360-input" value={org?.slug ?? ""} onChange={(e) => account.chooseOrganization(e.target.value)}>
                {account.organizations.map((o) => (
                  <option key={o.slug} value={o.slug}>
                    {o.display_name}
                  </option>
                ))}
              </select>
            </label>
          ) : org ? (
            <p className={styles.orgOne}>
              <Icon name="building" size={16} /> {org.display_name}
            </p>
          ) : null}
          <p className={styles.menuTitle}>My Account</p>
          <ul className={styles.plainList}>
            {links.map((l) => (
              <li key={l.key}>
                <button type="button" className={styles.menuItem} onClick={() => void myAccount.open(l.path)}>
                  <Icon name={l.icon} size={18} className={styles.menuIcon} />
                  <span className={styles.menuText}>
                    <span>{l.label}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className={`${styles.menuItem} ${styles.signOut}`} onClick={() => void account.signOut()}>
            <Icon name="logout" size={18} className={styles.menuIcon} />
            <span className={styles.menuText}>
              <span>Sign out</span>
            </span>
          </button>
        </div>
      )}
    </div>
  );
}

/** Sign in returns to the page the visitor was on. */
function useSignInHref(): string {
  const pathname = usePathname() ?? storePaths.home;
  const here = pathname === "/" ? storePaths.home : pathname;
  return `/login?next=${encodeURIComponent(here)}`;
}

function HeaderActions() {
  const account = useStoreAccount();
  const myAccount = useMyAccount();
  const signIn = useSignInHref();
  if (account.status === "loading") return <div className={styles.actions} aria-hidden="true" />;
  if (account.status === "signed_in") {
    return (
      <div className={styles.actions}>
        <Bell />
        <button type="button" className={styles.myAccount} onClick={() => void myAccount.open(myAccountLinks(account.organization?.slug ?? null)[0].path)}>
          <Icon name="user" size={18} />
          My Account
        </button>
        <AccountMenu />
      </div>
    );
  }
  return (
    <div className={styles.actions}>
      <Link href={signIn} className={styles.signIn}>
        Sign In
      </Link>
      <Link href="/signup?next=%2Fstore" className={styles.getStarted}>
        Get Started
      </Link>
    </div>
  );
}

function MobileMenu({ menus, onClose }: { menus: ReturnType<typeof useMenus>; onClose: () => void }) {
  const account = useStoreAccount();
  const myAccount = useMyAccount();
  const signIn = useSignInHref();
  return (
    <div className={styles.mobilePanel} id="store-mobile-menu">
      <Suspense fallback={null}>
        <HeaderSearch onDone={onClose} />
      </Suspense>
      <nav aria-label="Store">
        {menus.map((m) => (
          <details key={m.key} className={styles.mobileGroup}>
            <summary>{m.label}</summary>
            <ul>
              {m.items.map((item) => (
                <li key={item.href + item.label}>
                  {item.external ? (
                    <a href={item.href} onClick={onClose}>
                      {item.label}
                    </a>
                  ) : (
                    <Link href={item.href} onClick={onClose}>
                      {item.label}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </details>
        ))}
      </nav>
      <div className={styles.mobileActions}>
        {account.status === "signed_in" ? (
          <>
            <button type="button" className={styles.getStarted} onClick={() => void myAccount.open(myAccountLinks(account.organization?.slug ?? null)[0].path)}>
              My Account
            </button>
            <button type="button" className={styles.signIn} onClick={() => void account.signOut()}>
              Sign out
            </button>
          </>
        ) : (
          <>
            <Link href={signIn} className={styles.signIn} onClick={onClose}>
              Sign In
            </Link>
            <Link href="/signup?next=%2Fstore" className={styles.getStarted} onClick={onClose}>
              Get Started
            </Link>
          </>
        )}
      </div>
    </div>
  );
}

function StoreHeaderBar({ page }: { page: PublicStorePage | null }) {
  const pathname = usePathname() ?? "";
  const menus = useMenus(page);
  const [mobile, setMobile] = useState(false);
  const [seen, setSeen] = useState(pathname);
  if (seen !== pathname) {
    setSeen(pathname);
    setMobile(false);
  }
  return (
    <header className={styles.header}>
      <div className={styles.headerInner}>
        <Link href={storePaths.home} className={styles.brand} aria-label="WorkEasy360 Store home">
          <Logo compact />
          <span className={styles.storeBadge}>Store</span>
        </Link>
        <nav className={styles.nav} aria-label="Store">
          {menus.map((m) => (
            <NavMenu key={m.key} label={m.label} items={m.items} active={pathname.startsWith(m.prefix)} />
          ))}
        </nav>
        <div className={styles.searchSlot}>
          <Suspense fallback={<div className={styles.search} />}>
            <HeaderSearch />
          </Suspense>
        </div>
        <HeaderActions />
        <button
          type="button"
          className={styles.mobileToggle}
          aria-expanded={mobile}
          aria-controls="store-mobile-menu"
          aria-label={mobile ? "Close menu" : "Open menu"}
          onClick={() => setMobile((v) => !v)}
        >
          <Icon name={mobile ? "x" : "menu"} size={24} />
        </button>
      </div>
      {mobile && <MobileMenu menus={menus} onClose={() => setMobile(false)} />}
    </header>
  );
}

function StoreFooter({ page }: { page: PublicStorePage | null }) {
  const site = useSite();
  const partner = usePartnerHref();
  const myAccount = useMyAccount();
  const account = useStoreAccount();
  const products = (page?.products ?? []).filter((p) => p.status === "available").slice(0, 5);
  const year = new Date().getFullYear();
  return (
    <footer className={styles.footer}>
      <div className={styles.footerInner}>
        <div className={styles.footerBrand}>
          <Logo onBand />
          <p>The WorkEasy360 Digital Store: business apps, plans and credits for your organization, bought with one account.</p>
          {site?.support_email && (
            <p className={styles.footerContact}>
              <Icon name="mail" size={16} /> <a href={`mailto:${site.support_email}`}>{site.support_email}</a>
            </p>
          )}
          {site?.sales_email && site.sales_email !== site.support_email && (
            <p className={styles.footerContact}>
              <Icon name="chat" size={16} /> Sales: <a href={`mailto:${site.sales_email}`}>{site.sales_email}</a>
            </p>
          )}
        </div>
        <nav aria-label="Products" className={styles.footerCol}>
          <h2>Products</h2>
          <ul>
            {products.map((p) => (
              <li key={p.code}>
                <Link href={storePaths.product(p.slug)}>{p.title}</Link>
              </li>
            ))}
            <li>
              <Link href={storePaths.products}>All products</Link>
            </li>
            <li>
              <Link href={storePaths.recharge}>Recharge</Link>
            </li>
          </ul>
        </nav>
        <nav aria-label="Support" className={styles.footerCol}>
          <h2>Support</h2>
          <ul>
            <li>
              <Link href={storePaths.help}>Help & FAQs</Link>
            </li>
            <li>
              <Link href={`${storePaths.help}#contact`}>Contact support</Link>
            </li>
            <li>
              {account.status === "signed_in" ? (
                <button type="button" className={styles.footerLinkButton} onClick={() => void myAccount.open(myAccountLinks(account.organization?.slug ?? null)[0].path)}>
                  My Account
                </button>
              ) : (
                <Link href="/login?next=%2Fstore">Sign in</Link>
              )}
            </li>
          </ul>
        </nav>
        <nav aria-label="Partners" className={styles.footerCol}>
          <h2>Partners</h2>
          <ul>
            <li>
              <Link href={storePaths.partners}>Become a Partner</Link>
            </li>
            <li>
              <Link href={`${storePaths.partners}#affiliate`}>Affiliate programme</Link>
            </li>
            <li>
              <Link href={`${storePaths.partners}#reseller`}>Reseller programme</Link>
            </li>
            <li>
              <a href={partner(partnerPaths.signIn)}>Partner sign in</a>
            </li>
          </ul>
        </nav>
        <nav aria-label="Legal" className={styles.footerCol}>
          <h2>Legal</h2>
          <ul>
            {POLICIES.map((p) => (
              <li key={p.key}>
                <Link href={storePaths.policy(p.key)}>{p.label}</Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
      <p className={styles.footerLegal}>© {year} WorkEasy360. All rights reserved.</p>
    </footer>
  );
}

/** The Store's frame around a page, with the buyer's account state shared by the
 * header and the page. */
export function StoreShell({ children }: { children: ReactNode }) {
  return (
    <StoreAccountProvider>
      <ShellBody>{children}</ShellBody>
    </StoreAccountProvider>
  );
}

const CatalogContext = createContext<PublicStorePage | null>(null);

/** The public catalog the frame already loaded (menus, footer) - `null` while it
 * loads, or outside a Store frame. */
export function useShellCatalog(): PublicStorePage | null {
  return useContext(CatalogContext);
}

function ShellBody({ children }: { children: ReactNode }) {
  const page = useStoreCatalog();
  return (
    <CatalogContext.Provider value={page}>
      <div className={styles.shell}>
        <StoreHeaderBar page={page} />
        <main id="w360-main" className={styles.main}>
          {children}
        </main>
        <StoreFooter page={page} />
      </div>
    </CatalogContext.Provider>
  );
}
