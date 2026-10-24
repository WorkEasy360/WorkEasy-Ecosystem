"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { publicApi, type PublicProduct, type SiteSettings } from "@/lib/api/public";
import { money } from "@/features/controlplane/ui";
import { Logo } from "@/components/brand/Logo";
import styles from "./Site.module.css";

// --- shared pieces ---------------------------------------------------------------------

const PER: Record<string, string> = {
  monthly: "month",
  quarterly: "quarter",
  semi_annual: "6 months",
  annual: "year",
};

/** "₹999.00 / month". */
export function priceText(amountMinor: number, currency: string, interval: string): string {
  return `${money(amountMinor, currency)} / ${PER[interval] ?? interval.replace(/_/g, " ")}`;
}

/** A product's monogram tile: its initials on one of six fixed colours,
 * picked from its code - never a colour or an image from the server. */
export function Monogram({ product }: { product: Pick<PublicProduct, "code" | "name"> }) {
  let hash = 0;
  for (const ch of product.code) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const accent = styles[`accent${hash % 6}`];
  const initials = product.name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <span aria-hidden="true" className={`${styles.monogram} ${accent}`}>
      {initials || "?"}
    </span>
  );
}

/** A purchase - or a product trial - someone started before they had an
 * account (Phase 9, extended Phase 11): validated before use, and read once
 * at the organization's front door - never a redirect address.
 *
 * **`localStorage`, with a TTL, not `sessionStorage`.** The email-verification
 * link a signup follows very often opens in a *new* tab or a mail app's
 * in-app browser, which starts with an empty `sessionStorage` - the intent
 * would silently vanish on the one journey it exists for. `localStorage`
 * survives that; the TTL below is what stops a stale, long-forgotten intent
 * from resurfacing on an unrelated visit weeks later. */
export const PURCHASE_INTENT = "w360.purchase-intent";
const INTENT_TTL_MS = 24 * 60 * 60 * 1000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const INTERVALS = new Set(Object.keys(PER));
/** Matches the backend's `ProductCode`/plan-code shapes exactly (a lower-case
 * word, `_` allowed) - the two things a Phase 11 trial intent may name. */
const CODE = /^[a-z][a-z0-9_]{0,63}$/;

export interface PurchaseIntent {
  plan?: string;
  bundle?: string;
  interval?: string;
  trial?: boolean;
  /** Phase 11: start this product's own trial - no checkout, no price, no
   * plan-version id. Mutually exclusive with `plan`/`bundle`. */
  productTrial?: { product: string; plan: string };
}

export function cleanIntent(raw: PurchaseIntent | null | undefined): PurchaseIntent | null {
  if (!raw) return null;
  if (raw.productTrial) {
    const { product, plan } = raw.productTrial;
    return CODE.test(product ?? "") && CODE.test(plan ?? "") ? { productTrial: { product, plan } } : null;
  }
  const plan = raw.plan && UUID.test(raw.plan) ? raw.plan : undefined;
  const bundle = raw.bundle && UUID.test(raw.bundle) ? raw.bundle : undefined;
  if (!plan && !bundle) return null;
  return {
    plan,
    bundle: plan ? undefined : bundle,
    interval: raw.interval && INTERVALS.has(raw.interval) ? raw.interval : undefined,
    trial: raw.trial === true,
  };
}

export function rememberIntent(intent: PurchaseIntent): void {
  const clean = cleanIntent(intent);
  if (!clean) return;
  try {
    localStorage.setItem(PURCHASE_INTENT, JSON.stringify({ ...clean, savedAt: Date.now() }));
  } catch {
    // Private mode: the visitor chooses again after signing in.
  }
}

export function takeIntent(): PurchaseIntent | null {
  try {
    const raw = localStorage.getItem(PURCHASE_INTENT);
    localStorage.removeItem(PURCHASE_INTENT);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PurchaseIntent & { savedAt?: unknown };
    if (typeof parsed.savedAt !== "number" || Date.now() - parsed.savedAt > INTENT_TTL_MS) return null;
    return cleanIntent(parsed);
  } catch {
    return null;
  }
}

/** The query string an org checkout reads for an order intent. Never called
 * for a `productTrial` intent, which has no checkout to open. */
export function intentQuery(intent: PurchaseIntent): string {
  const params = new URLSearchParams();
  if (intent.plan) params.set("plan", intent.plan);
  if (intent.bundle) params.set("bundle", intent.bundle);
  if (intent.interval) params.set("interval", intent.interval);
  if (intent.trial) params.set("trial", "1");
  return params.toString();
}

// --- data --------------------------------------------------------------------------------

export function useSiteSettings(): SiteSettings | null {
  const [site, setSite] = useState<SiteSettings | null>(null);
  useEffect(() => {
    let cancelled = false;
    publicApi.site().then(
      (s) => !cancelled && setSite(s),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return site;
}

/** The catalog's products for the header menu - loaded once per page. */
function useMenuProducts(): PublicProduct[] {
  const [products, setProducts] = useState<PublicProduct[]>([]);
  useEffect(() => {
    let cancelled = false;
    publicApi.catalog().then(
      (c) => !cancelled && setProducts(c.products),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return products;
}

// --- the shell ---------------------------------------------------------------------------

function ProductsMenu({ products }: { products: PublicProduct[] }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
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
      <button
        ref={button}
        type="button"
        className={styles.navButton}
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((v) => !v)}
      >
        Products <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <ul id={menuId} className={styles.menu}>
          <li>
            <Link href="/products" className={styles.menuItem} onClick={() => setOpen(false)}>
              <span className={styles.menuItemText}>
                <span>Browse all products</span>
              </span>
            </Link>
          </li>
          {products.length === 0 && (
            <li className={styles.muted}>
              <span className={styles.menuItem}>No products are listed yet.</span>
            </li>
          )}
          {products.map((p) => (
            <li key={p.code}>
              <Link href={`/products/${p.slug}`} className={styles.menuItem} onClick={() => setOpen(false)}>
                <Monogram product={p} />
                <span className={styles.menuItemText}>
                  <span>
                    {p.name}
                    {p.availability === "coming_soon" && <span className={styles.muted}> · Coming soon</span>}
                  </span>
                  {p.tagline && <span className={styles.menuItemTagline}>{p.tagline}</span>}
                </span>
              </Link>
            </li>
          ))}
          <li>
            <Link href="/pricing" className={styles.menuItem} onClick={() => setOpen(false)}>
              <span className={styles.menuItemText}>
                <span>All products and pricing</span>
              </span>
            </Link>
          </li>
        </ul>
      )}
    </div>
  );
}

const NAV = [
  { href: "/services", label: "Services" },
  { href: "/pricing", label: "Pricing" },
  { href: "/contact", label: "Contact" },
];

/**
 * The public site's frame (Phase 9): the Workeasy360 logo, a Products menu
 * built from the catalog (never a hard-coded list), Services, Pricing and
 * Contact; on the right Sign in / Sign up / Get started - or, for someone
 * already signed in, their dashboard.
 */
export function SiteShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const session = useSession();
  const products = useMenuProducts();
  const [navOpen, setNavOpen] = useState(false);
  const signedIn = session.status === "ready" && session.session.authenticated;

  return (
    <div className={styles.site}>
      <header className={`${styles.header} ${navOpen ? styles.navOpen : ""}`}>
        <div className={styles.headerInner}>
          <Link href="/" className={styles.logo} aria-label="Workeasy360 home">
            <Logo compact />
          </Link>
          <button
            type="button"
            className={`w360-btn w360-btn-secondary ${styles.mobileToggle}`}
            aria-expanded={navOpen}
            onClick={() => setNavOpen((v) => !v)}
          >
            Menu
          </button>
          <nav className={styles.nav} aria-label="Main">
            <ProductsMenu products={products} />
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={styles.navLink}
                aria-current={pathname === item.href ? "page" : undefined}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className={styles.actions}>
            {signedIn ? (
              <Link href="/dashboard" className="w360-btn w360-btn-primary">
                Go to dashboard
              </Link>
            ) : (
              <>
                <Link href="/login" className={styles.navLink}>
                  Sign in
                </Link>
                <Link href="/signup" className="w360-btn w360-btn-secondary">
                  Sign up
                </Link>
                <Link href="/signup" className="w360-btn w360-btn-primary">
                  Get started
                </Link>
              </>
            )}
          </div>
        </div>
      </header>
      <main id="w360-main" className={styles.main}>
        {children}
      </main>
      <footer className={styles.footer}>
        <div className={styles.footerInner}>
          <div className={styles.footerBrand}>
            <Logo onBand />
            <p className={styles.footerTagline}>One account for every app your business runs on.</p>
          </div>
          <ul className={styles.footerLinks} aria-label="Footer">
            <li>
              <Link href="/products">Products</Link>
            </li>
            <li>
              <Link href="/pricing">Pricing</Link>
            </li>
            <li>
              <Link href="/services">Services</Link>
            </li>
            <li>
              <Link href="/contact">Contact</Link>
            </li>
            <li>
              <Link href="/login">Sign in</Link>
            </li>
          </ul>
        </div>
        <p className={styles.footerLegal}>© {new Date().getFullYear()} Workeasy360. All rights reserved.</p>
      </footer>
    </div>
  );
}
