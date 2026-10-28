"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import {
  publicApi,
  type PublicBundle,
  type PublicCatalog,
  type PublicPlan,
  type PublicPrice,
  type PublicProduct,
  type PublicProductUpdate,
} from "@/lib/api/public";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { MarkdownLite } from "@/features/console/MarkdownLite";
import { ProductIcon, productIconKnown } from "@/components/ProductIcon";
import {
  Monogram,
  priceText,
  rememberIntent,
  type PurchaseIntent,
  useSiteSettings,
} from "./SiteShell";
import styles from "./Site.module.css";

const CYCLES = [
  { key: "monthly", label: "Monthly" },
  { key: "quarterly", label: "Quarterly" },
  { key: "semi_annual", label: "Bi-annual" },
  { key: "annual", label: "Annual" },
];

// --- buying -------------------------------------------------------------------------------

/** Buy / start a trial: remember what was chosen, then sign up (or, when
 * already signed in, go to the dashboard, which opens the checkout). The
 * choice is only an intent - the checkout prices it on the server. */
function useBuy() {
  const router = useRouter();
  const session = useSession();
  const signedIn = session.status === "ready" && session.session.authenticated;
  return (intent: PurchaseIntent) => {
    rememberIntent(intent);
    router.push(signedIn ? "/dashboard" : "/signup");
  };
}

function priceFor(prices: PublicPrice[], interval: string): PublicPrice | undefined {
  return prices.find((p) => p.interval === interval && p.amount_minor !== null);
}

function PriceLine({ prices, interval }: { prices: PublicPrice[]; interval: string }) {
  const price = priceFor(prices, interval);
  if (!price || price.amount_minor === null) {
    return <p className={styles.priceNote}>Not offered {interval.replace(/_/g, " ")}</p>;
  }
  return (
    <p className={styles.price}>
      {priceText(price.amount_minor, price.currency, interval)}
      {price.model === "per_seat" && <span className={styles.priceNote}> per seat</span>}
    </p>
  );
}

function CycleSwitch({
  cycles,
  value,
  onChange,
}: {
  cycles: typeof CYCLES;
  value: string;
  onChange: (v: string) => void;
}) {
  if (cycles.length < 2) return null;
  return (
    <div className={styles.segmented} role="group" aria-label="Billing cycle">
      {cycles.map((c) => (
        <button key={c.key} type="button" aria-pressed={value === c.key} onClick={() => onChange(c.key)}>
          {c.label}
        </button>
      ))}
    </div>
  );
}

function CurrencySelect({
  currencies,
  value,
  onChange,
}: {
  currencies: string[];
  value: string | null;
  onChange: (v: string) => void;
}) {
  const id = useId();
  if (currencies.length < 2) return null;
  return (
    <>
      <label htmlFor={id}>Currency</label>
      <select
        id={id}
        className={`w360-input ${styles.select}`}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
      >
        {currencies.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </>
  );
}

function cyclesIn(items: { prices: PublicPrice[] }[]): typeof CYCLES {
  const present = CYCLES.filter((c) => items.some((i) => priceFor(i.prices, c.key)));
  return present.length ? present : CYCLES.slice(0, 1);
}

function PlanCard({ plan, interval, productCode }: { plan: PublicPlan; interval: string; productCode: string }) {
  const buy = useBuy();
  const titleId = useId();
  // Two independent trial mechanisms can both name this plan (SiteShell's
  // PurchaseIntent docstring): the older per-plan subscription trial
  // (`trial_days`, priced through checkout) and the newer, independent
  // per-product trial (`trial_available`, no checkout - Phase 11). Where a
  // plan is eligible for the new one, that is what "Start free trial" starts;
  // it is the mechanism that lets a second product be trialled while the
  // first is already active or paid, which the older one cannot do.
  const productTrial = plan.trial_available;
  const legacyTrial = !productTrial && plan.trial_days > 0;
  const trial = productTrial || legacyTrial;
  const price = priceFor(plan.prices, interval);
  return (
    <article className={styles.tile} aria-labelledby={titleId}>
      <h3 id={titleId} className={styles.tileTitle}>
        {plan.name}
      </h3>
      {plan.caption && <p className={styles.tileText}>{plan.caption}</p>}
      <ul className={styles.badgeRow} aria-label="Plan details">
        {legacyTrial && <li className="w360-badge w360-badge-neutral">{plan.trial_days}-day free trial</li>}
        {productTrial && <li className="w360-badge w360-badge-success">Free trial available</li>}
        {plan.kind === "add_on" && <li className="w360-badge w360-badge-neutral">Add-on</li>}
        {plan.included_seats > 0 && (
          <li className="w360-badge w360-badge-neutral">
            {plan.included_seats === 1 ? "1 seat" : `${plan.included_seats} seats`}
          </li>
        )}
      </ul>
      <PriceLine prices={plan.prices} interval={interval} />
      <MarkdownLite text={plan.description} />
      {plan.kind !== "add_on" && (price || plan.billing_type === "free_trial" || productTrial) && (
        <div className={styles.tileFoot}>
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            onClick={() =>
              buy(
                productTrial
                  ? { productTrial: { product: productCode, plan: plan.plan_code } }
                  : { plan: plan.plan_version_id, interval, trial: legacyTrial },
              )
            }
            aria-label={`${trial ? "Start free trial" : "Buy"}: ${plan.name}`}
          >
            {trial ? "Start free trial" : "Buy"}
          </button>
        </div>
      )}
    </article>
  );
}

function BundleCard({ bundle, interval }: { bundle: PublicBundle; interval: string }) {
  const buy = useBuy();
  const titleId = useId();
  const price = priceFor(bundle.prices, interval);
  return (
    <article className={styles.tile} aria-labelledby={titleId}>
      <h3 id={titleId} className={styles.tileTitle}>
        {bundle.name}
      </h3>
      {bundle.caption && <p className={styles.tileText}>{bundle.caption}</p>}
      <ul aria-label={`What ${bundle.name} includes`}>
        {bundle.includes.map((i) => (
          <li key={`${i.product_code}-${i.plan_name}`}>
            {i.product_name} ({i.plan_name})
          </li>
        ))}
      </ul>
      <PriceLine prices={bundle.prices} interval={interval} />
      {price && (
        <div className={styles.tileFoot}>
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            onClick={() => buy({ bundle: bundle.bundle_version_id, interval })}
            aria-label={`Buy: ${bundle.name}`}
          >
            Buy
          </button>
        </div>
      )}
    </article>
  );
}

function ProductTile({ product }: { product: PublicProduct }) {
  const titleId = useId();
  const soon = product.availability === "coming_soon";
  return (
    <article className={styles.tile} aria-labelledby={titleId}>
      <div className={styles.tileHead}>
        <ProductVisual product={product} />
        <h3 id={titleId} className={styles.tileTitle}>
          {product.name}
        </h3>
      </div>
      <p className={styles.tileText}>{product.tagline || product.description.split("\n")[0]}</p>
      <div className={styles.tileFoot}>
        {soon ? (
          <span className="w360-badge w360-badge-neutral">Coming soon</span>
        ) : product.starting_price ? (
          <p className={styles.price}>
            <span className={styles.priceNote}>From </span>
            {priceText(product.starting_price.amount_minor, product.starting_price.currency, product.starting_price.interval)}
          </p>
        ) : (
          <span />
        )}
        <Link href={`/products/${product.slug}`} className="w360-btn w360-btn-secondary" aria-label={`Learn more about ${product.name}`}>
          Learn more
        </Link>
      </div>
    </article>
  );
}

/** The standard plan a product's `starting_price` names, matched by value
 * (interval, currency, amount) - so "Buy now" on the storefront buys exactly
 * the plan the price shown belongs to, without recomputing the backend's own
 * cheapest-plan rule (`marketplace._starting_price`) a second time. */
function planForStartingPrice(product: PublicProduct): PublicPlan | undefined {
  const sp = product.starting_price;
  if (!sp) return undefined;
  return product.plans.find(
    (p) =>
      p.kind === "standard" &&
      p.prices.some(
        (price) => price.interval === sp.interval && price.amount_minor === sp.amount_minor && price.currency === sp.currency,
      ),
  );
}

/** The first plan the product's *configured* trial (Phase 11) is offered on
 * - never a plan whose own `trial_days` is set, which is the older, separate
 * mechanism `PlanCard` already offers on the detail page. */
function planForTrial(product: PublicProduct): PublicPlan | undefined {
  if (!product.trial.available) return undefined;
  return product.plans.find((p) => product.trial.plan_codes.includes(p.plan_code));
}

/** A product's icon, or the monogram fallback - never both, and never a
 * remote image (Task 37/52). */
function ProductVisual({ product, size = 44 }: { product: PublicProduct; size?: number }) {
  if (productIconKnown(product.icon)) {
    return (
      <span aria-hidden="true" className={styles.storeIcon} style={{ width: size, height: size }}>
        <ProductIcon icon={product.icon} size={Math.round(size * 0.5)} />
      </span>
    );
  }
  return <Monogram product={product} />;
}

/** One of the storefront's four large cards (Step 3 of the brief): icon,
 * name, category, a one-line description, up to four features, whether a
 * trial is offered, the price the server computed (never invented here),
 * and exactly one primary action - Start Free Trial, else Buy Now, else
 * Contact Sales, in that order, so a card never shows two ways to do the
 * same thing. */
function ProductStoreCard({ product }: { product: PublicProduct }) {
  const buy = useBuy();
  const titleId = useId();
  const soon = product.availability === "coming_soon";
  const trialPlan = !soon ? planForTrial(product) : undefined;
  const buyPlan = !soon && !trialPlan ? planForStartingPrice(product) : undefined;
  const priceInterval = buyPlan?.prices.find((p) => p.amount_minor === product.starting_price?.amount_minor)?.interval;

  return (
    <article className={styles.storeCard} aria-labelledby={titleId}>
      <div className={styles.storeCardHead}>
        <ProductVisual product={product} size={56} />
        <div>
          <h2 id={titleId} className={styles.tileTitle}>
            {product.name}
          </h2>
          {product.category && <p className={styles.storeCategory}>{product.category}</p>}
        </div>
      </div>
      <p className={styles.tileText}>{product.tagline || product.description.split("\n")[0]}</p>
      {product.features.length > 0 && (
        <ul className={styles.storeFeatures} aria-label={`${product.name} features`}>
          {product.features.slice(0, 4).map((f) => (
            <li key={f.name}>{f.name}</li>
          ))}
        </ul>
      )}
      <div className={styles.storeCardFoot}>
        {!soon && product.starting_price && (
          <p className={styles.price}>
            <span className={styles.priceNote}>From </span>
            {priceText(product.starting_price.amount_minor, product.starting_price.currency, product.starting_price.interval)}
          </p>
        )}
        <ul className={styles.badgeRow} aria-label="Availability">
          {soon && <li className="w360-badge w360-badge-neutral">Coming soon</li>}
          {!soon && trialPlan && <li className="w360-badge w360-badge-success">Free trial available</li>}
          {!soon && !product.starting_price && (
            <li className="w360-badge w360-badge-neutral">Contact sales for pricing</li>
          )}
        </ul>
        <div className={styles.storeCardActions}>
          <Link href={`/products/${product.slug}`} className="w360-btn w360-btn-secondary" aria-label={`Learn more about ${product.name}`}>
            Learn more
          </Link>
          {trialPlan && (
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              // The product's own trial (Phase 11), as the plan tiles start it -
              // not the older checkout trial, which opens a paid order.
              onClick={() => buy({ productTrial: { product: product.code, plan: trialPlan.plan_code } })}
              aria-label={`Start free trial: ${product.name}`}
            >
              Start free trial
            </button>
          )}
          {!trialPlan && buyPlan && (
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              onClick={() => buy({ plan: buyPlan.plan_version_id, interval: priceInterval, trial: false })}
              aria-label={`Buy now: ${product.name}`}
            >
              Buy now
            </button>
          )}
          {!soon && !trialPlan && !buyPlan && (
            <Link href="/contact" className="w360-btn w360-btn-primary" aria-label={`Contact sales about ${product.name}`}>
              Contact sales
            </Link>
          )}
        </div>
      </div>
    </article>
  );
}

/** `/products` - the storefront (Step 3 of the brief): every catalog
 * product as a large card. Data-driven like every other page here - nothing
 * about the four products is hard-coded, and a fifth registered tomorrow
 * appears with no change to this file. */
export function ProductsPage() {
  const [load] = useLoad(() => publicApi.catalog(), []);
  return (
    <div className={styles.section}>
      <h1 className={styles.sectionTitle}>Products</h1>
      <p className={styles.sectionLead}>
        Each product is its own application, sold and supported separately, opened from one Workeasy360 account.
      </p>
      <LoadGate load={load} what="products">
        {(catalog) =>
          catalog.products.length === 0 ? (
            <p className={styles.muted}>Products will appear here soon.</p>
          ) : (
            <div className={styles.storeGrid}>
              {catalog.products.map((p) => (
                <ProductStoreCard key={p.code} product={p} />
              ))}
            </div>
          )
        }
      </LoadGate>
    </div>
  );
}

// --- pages -------------------------------------------------------------------------------

/** `/` - the ecosystem's front page. Every product on it comes from the
 * catalog; nothing is hard-coded. */
export function LandingPage() {
  const [load] = useLoad(() => publicApi.catalog(), []);
  return (
    <>
      <section className={styles.hero} aria-labelledby="hero-title">
        <div className={styles.heroInner}>
          <p className={styles.eyebrow}>One account. Every business app.</p>
          <h1 id="hero-title" className={styles.heroTitle}>
            Run your whole business on <span className={styles.highlight}>Workeasy360</span>
          </h1>
          <p className={styles.heroLead}>
            Sign up once, then add the products your business needs - each one ready the moment it is paid for, all
            under one sign-in, one organization and one bill.
          </p>
          <div className={styles.ctaRow}>
            <Link href="/signup" className="w360-btn w360-btn-primary">
              Get started - it&apos;s free to explore
            </Link>
            <Link href="/pricing" className="w360-btn w360-btn-secondary">
              See products and pricing
            </Link>
          </div>
        </div>
      </section>

      <div className={styles.surfaceBand}>
        <section className={styles.section} aria-labelledby="products-title">
          <h2 id="products-title" className={styles.sectionTitle}>
            Products
          </h2>
          <p className={styles.sectionLead}>Start with one. Add the rest whenever you are ready.</p>
          <LoadGate load={load} what="products">
            {(catalog) =>
              catalog.products.length === 0 ? (
                <p className={styles.muted}>Products will appear here soon.</p>
              ) : (
                <div className={styles.grid}>
                  {catalog.products.map((p) => (
                    <ProductTile key={p.code} product={p} />
                  ))}
                </div>
              )
            }
          </LoadGate>
        </section>
      </div>

      <div className={styles.creamBand}>
        <section className={styles.section} aria-labelledby="how-title">
          <h2 id="how-title" className={styles.sectionTitle}>
            How it works
          </h2>
          <ol className={styles.steps}>
            <li>
              <p className={styles.stepTitle}>Create your account</p>
              <p className={styles.muted}>Free. Confirm your email address and your workspace is ready.</p>
            </li>
            <li>
              <p className={styles.stepTitle}>Explore the products</p>
              <p className={styles.muted}>See what each one does and what it costs, in your currency.</p>
            </li>
            <li>
              <p className={styles.stepTitle}>Buy or start a trial</p>
              <p className={styles.muted}>Pay securely; access starts as soon as the payment is confirmed.</p>
            </li>
            <li>
              <p className={styles.stepTitle}>Open it from your dashboard</p>
              {/* Phase 12: no promise of single sign-on - no product performs it
                  yet (docs/phase-12/phase-12-status.md). True either way: */}
              <p className={styles.muted}>Every product your organization holds opens from the same dashboard.</p>
            </li>
          </ol>
        </section>
      </div>

      <section className={styles.band} aria-labelledby="start-title">
        <div className={styles.bandInner}>
          <h2 id="start-title" className={styles.bandTitle}>
            One sign-in. One organization. One bill.
          </h2>
          <p className={styles.bandLead}>Create your account free, then add products when you need them.</p>
          <div className={styles.ctaRow}>
            <Link href="/signup" className={`w360-btn ${styles.sunButton}`}>
              Get started
            </Link>
            <Link href="/pricing" className={`w360-btn ${styles.bandButton}`}>
              See pricing
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}

/** `/products/<slug>` - one product: what it does, its features, its plans. */
export function ProductPage({ slug }: { slug: string }) {
  const [currency, setCurrency] = useState<string | undefined>(undefined);
  const [load] = useLoad(() => publicApi.product(slug, currency), [slug, currency]);
  const [cycle, setCycle] = useState("monthly");
  // ADR-054: an affiliate's pricing link opens `?view=pricing` - straight to the plans.
  useEffect(() => {
    if (load.status === "ready" && new URLSearchParams(window.location.search).get("view") === "pricing") {
      document.getElementById("plans-title")?.scrollIntoView({ block: "start" });
    }
  }, [load.status]);
  return (
    <div className={styles.section}>
      <LoadGate load={load} what="this product">
        {({ product, bundles, currencies, currency: shown }) => {
          const cycles = cyclesIn([...product.plans, ...bundles]);
          const interval = cycles.some((c) => c.key === cycle) ? cycle : cycles[0].key;
          const standard = product.plans.filter((p) => p.kind !== "add_on");
          const addOns = product.plans.filter((p) => p.kind === "add_on");
          return (
            <>
              <div className={styles.productHeader}>
                <ProductVisual product={product} />
                <div>
                  <h1 className={styles.sectionTitle}>{product.name}</h1>
                  {product.category && <p className={styles.storeCategory}>{product.category}</p>}
                  {product.tagline && <p className={styles.muted}>{product.tagline}</p>}
                </div>
              </div>
              <MarkdownLite text={product.description} />
              {product.marketing_url && (
                <p>
                  <a href={product.marketing_url} rel="noopener noreferrer" target="_blank">
                    Read more about {product.name} (opens in a new tab)
                  </a>
                </p>
              )}
              {product.features.length > 0 && (
                <section aria-labelledby="features-title">
                  <h2 id="features-title" className={styles.sectionTitle}>
                    Features
                  </h2>
                  <ul className={styles.features}>
                    {product.features.map((f) => (
                      <li key={f.name}>
                        <p className={styles.featureName}>{f.name}</p>
                        {f.description && <p className={styles.featureText}>{f.description}</p>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              <section aria-labelledby="plans-title">
                <h2 id="plans-title" className={styles.sectionTitle}>
                  {product.availability === "coming_soon" ? "Coming soon" : "Plans"}
                </h2>
                {product.availability === "coming_soon" ? (
                  <p className={styles.muted}>
                    {product.name} is not on sale yet. <Link href="/signup">Create your account</Link> to be ready when
                    it launches.
                  </p>
                ) : (
                  <>
                    <div className={styles.toolbar}>
                      <CycleSwitch cycles={cycles} value={interval} onChange={setCycle} />
                      <CurrencySelect currencies={currencies} value={shown} onChange={setCurrency} />
                    </div>
                    <div className={styles.plans}>
                      {standard.map((p) => (
                        <PlanCard key={p.plan_version_id} plan={p} interval={interval} productCode={product.code} />
                      ))}
                    </div>
                    {addOns.length > 0 && (
                      <>
                        <h3 className={styles.tileTitle}>Add-ons</h3>
                        <div className={styles.plans}>
                          {addOns.map((p) => (
                            <PlanCard key={p.plan_version_id} plan={p} interval={interval} productCode={product.code} />
                          ))}
                        </div>
                      </>
                    )}
                    {bundles.length > 0 && (
                      <>
                        <h3 className={styles.tileTitle}>Save with a bundle</h3>
                        <div className={styles.plans}>
                          {bundles.map((b) => (
                            <BundleCard key={b.bundle_version_id} bundle={b} interval={interval} />
                          ))}
                        </div>
                      </>
                    )}
                  </>
                )}
              </section>
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}

/** `/pricing` - every product's plans and every bundle, in one currency. */
export function PricingPage() {
  const [currency, setCurrency] = useState<string | undefined>(undefined);
  const [load] = useLoad(() => publicApi.catalog(currency), [currency]);
  const [cycle, setCycle] = useState("monthly");
  return (
    <div className={styles.section}>
      <h1 className={styles.sectionTitle}>Pricing</h1>
      <p className={styles.sectionLead}>
        Prices are shown before any tax. You pay only for the products you add, and you can add more at any time.
      </p>
      <LoadGate load={load} what="pricing">
        {(catalog: PublicCatalog) => {
          const onSale = catalog.products.filter((p) => p.availability === "available");
          const cycles = cyclesIn([...onSale.flatMap((p) => p.plans), ...catalog.bundles]);
          const interval = cycles.some((c) => c.key === cycle) ? cycle : cycles[0].key;
          return (
            <>
              <div className={styles.toolbar}>
                <CycleSwitch cycles={cycles} value={interval} onChange={setCycle} />
                <CurrencySelect currencies={catalog.currencies} value={catalog.currency} onChange={setCurrency} />
              </div>
              {catalog.bundles.length > 0 && (
                <section aria-labelledby="bundles-title" className={styles.field}>
                  <h2 id="bundles-title" className={styles.sectionTitle}>
                    Bundles
                  </h2>
                  <div className={styles.plans}>
                    {catalog.bundles.map((b) => (
                      <BundleCard key={b.bundle_version_id} bundle={b} interval={interval} />
                    ))}
                  </div>
                </section>
              )}
              {onSale.map((product) => (
                <section key={product.code} aria-labelledby={`pricing-${product.code}`} className={styles.field}>
                  <div className={styles.productHeader}>
                    <ProductVisual product={product} />
                    <h2 id={`pricing-${product.code}`} className={styles.sectionTitle}>
                      <Link href={`/products/${product.slug}`}>{product.name}</Link>
                    </h2>
                  </div>
                  <div className={styles.plans}>
                    {product.plans
                      .filter((p) => p.kind !== "add_on")
                      .map((p) => (
                        <PlanCard key={p.plan_version_id} plan={p} interval={interval} productCode={product.code} />
                      ))}
                  </div>
                </section>
              ))}
              {onSale.length === 0 && catalog.bundles.length === 0 && (
                <p className={styles.muted}>Nothing is on sale yet.</p>
              )}
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}

/** `/services` - how Workeasy360 helps a business get going. */
export function ServicesPage() {
  const site = useSiteSettings();
  return (
    <div className={styles.section}>
      <h1 className={styles.sectionTitle}>Services</h1>
      <p className={styles.sectionLead}>
        Help from our team to get your business running on Workeasy360 quickly and correctly.
      </p>
      <ul className={styles.features}>
        <li>
          <p className={styles.featureName}>Onboarding</p>
          <p className={styles.featureText}>Setting up your organization, members, roles and security policy.</p>
        </li>
        <li>
          <p className={styles.featureName}>Data migration</p>
          <p className={styles.featureText}>Bringing your existing records across from the tools you use today.</p>
        </li>
        <li>
          <p className={styles.featureName}>Training</p>
          <p className={styles.featureText}>Sessions for your administrators and your team.</p>
        </li>
        <li>
          <p className={styles.featureName}>Support</p>
          <p className={styles.featureText}>Answers from people who know the products, when you need them.</p>
        </li>
      </ul>
      <p className={styles.field}>
        {site?.sales_email ? (
          <>
            Talk to us: <a href={`mailto:${site.sales_email}`}>{site.sales_email}</a>
          </>
        ) : (
          <Link href="/contact">Contact us</Link>
        )}
      </p>
    </div>
  );
}

/** `/contact` - where to write. The addresses are the platform settings'. */
export function ContactPage() {
  const site = useSiteSettings();
  return (
    <div className={styles.section}>
      <h1 className={styles.sectionTitle}>Contact</h1>
      <div className={`w360-card ${styles.contactCard} ${styles.authCard}`}>
        {site === null ? (
          <p aria-live="polite">Loading…</p>
        ) : site.sales_email || site.support_email ? (
          <dl>
            {site.sales_email && (
              <>
                <dt className={styles.featureName}>Sales</dt>
                <dd>
                  <a href={`mailto:${site.sales_email}`}>{site.sales_email}</a>
                </dd>
              </>
            )}
            {site.support_email && (
              <>
                <dt className={styles.featureName}>Support</dt>
                <dd>
                  <a href={`mailto:${site.support_email}`}>{site.support_email}</a>
                </dd>
              </>
            )}
          </dl>
        ) : (
          <p className={styles.muted}>Contact details will be published here soon.</p>
        )}
      </div>
    </div>
  );
}

/** `/products/<slug>/updates/<update>` - a published feature or release
 * (ADR-054): the page an affiliate's update link opens. The text is plain; the
 * longer announcement, when there is one, is a link on an approved host. */
export function ProductUpdatePage({ slug, updateSlug }: { slug: string; updateSlug: string }) {
  const [load] = useLoad(() => publicApi.productUpdate(slug, updateSlug), [slug, updateSlug]);
  return (
    <div className={styles.section}>
      <LoadGate load={load} what="this update">
        {(u: PublicProductUpdate) => (
          <article aria-labelledby="update-title">
            <p className={styles.storeCategory}>
              <Link href={`/products/${u.product.slug}`}>{u.product.name}</Link> · {u.kind === "feature" ? "New feature" : "Product update"}
              {u.published_at ? ` · ${new Date(u.published_at).toLocaleDateString()}` : ""}
            </p>
            <h1 id="update-title" className={styles.sectionTitle}>
              {u.title}
            </h1>
            {u.summary && <p className={styles.muted}>{u.summary}</p>}
            {u.banner_url && (
              // eslint-disable-next-line @next/next/no-img-element -- same-origin listing image
              <img src={u.banner_url} alt="" style={{ maxWidth: "100%", height: "auto" }} />
            )}
            {u.body.split("\n").map((line, i) => (line.trim() ? <p key={i}>{line}</p> : null))}
            {u.landing_url && (
              <p>
                <a href={u.landing_url} rel="noopener noreferrer" target="_blank">
                  Read the full announcement (opens in a new tab)
                </a>
              </p>
            )}
            <p>
              <Link className="w360-btn w360-btn-primary" href={`/products/${u.product.slug}?view=pricing`}>
                See {u.product.name} plans
              </Link>{" "}
              <Link className="w360-btn w360-btn-secondary" href="/signup">
                Create your account
              </Link>
            </p>
          </article>
        )}
      </LoadGate>
    </div>
  );
}
