"use client";

import { useId, useMemo, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import type { PublicStoreCard, PublicStorePage } from "@/lib/api/public";
import { categoryIcon, StoreIcon } from "@/features/storefront/storeIcons";
import { ProductCard, ProductTile, tileIcon } from "./ProductCard";
import { useShellCatalog } from "./StoreShell";
import { storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * The Store's home (the owner's marketplace image): a hero with three slides -
 * the platform's products, Recharge, the partner programmes - then the products
 * by category beside the Recharge and Become a Partner cards, the platform's
 * benefits, how buying works, and where to find help.
 *
 * Every product, category and price is the public catalog's. The benefits name
 * only what the catalog and the platform actually do - "Start free" only when a
 * product offers a free plan or a trial, "monthly and yearly" only when both are
 * on sale - never a promise of their own.
 */

type Card = PublicStoreCard & { highlights?: string[] };

function shortName(card: Card): string {
  return card.title.replace(/^(workeasy|easy)\s*/i, "").trim() || card.title;
}

/** The hero's picture: a laptop and a phone showing the platform's own products
 * as tiles. Decoration only (no figures), named by the text beside it. */
function HeroArt({ products }: { products: Card[] }) {
  const tiles = products.slice(0, 4);
  return (
    <div className={styles.heroArt} aria-hidden="true">
      <div className={styles.laptop}>
        <div className={styles.laptopScreen}>
          <div className={styles.laptopBar}>
            <span />
            <span />
            <span />
          </div>
          <div className={styles.laptopTiles}>
            {tiles.map((p) => (
              <div key={p.code} className={styles.laptopTile}>
                <ProductTile code={p.code} logo={p.logo_url} icon={tileIcon(p)} size={34} />
                <span>{shortName(p)}</span>
              </div>
            ))}
            {tiles.length === 0 && <div className={styles.laptopTile} />}
          </div>
          <svg className={styles.laptopChart} viewBox="0 0 220 70" preserveAspectRatio="none">
            <polyline points="0,58 30,46 60,50 90,30 120,36 150,18 180,24 220,8" fill="none" strokeWidth="3" />
          </svg>
        </div>
        <div className={styles.laptopBase} />
      </div>
      <div className={styles.phone}>
        {tiles.map((p) => (
          <div key={p.code} className={styles.phoneRow}>
            <ProductTile code={p.code} logo={p.logo_url} icon={tileIcon(p)} size={22} />
            <span>{shortName(p)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

type Slide = {
  key: string;
  title: React.ReactNode;
  text: string;
  cta: { href: string; label: string; icon: string };
  second?: { href: string; label: string; icon: string };
  points: string[];
};

function useSlides(page: PublicStorePage | null): Slide[] {
  const products = (page?.products ?? []).filter((p) => p.status === "available");
  const names = products.slice(0, 4).map((p) => p.title);
  const intervals = new Set(products.flatMap((p) => p.plans.flatMap((plan) => plan.prices.map((x) => x.interval))));
  const offers = products.some((p) => p.trial?.available || p.plans.some((plan) => plan.prices.some((x) => x.amount_minor === 0)));
  const productLine = names.length
    ? `${names.join(", ")}${products.length > names.length ? " and more" : ""} - the apps to run and grow your business.`
    : "The apps to run and grow your business, in one place.";
  return [
    {
      key: "apps",
      title: (
        <>
          All Your Business Apps
          <br />
          in <span className={styles.heroAccent}>One Platform</span>
        </>
      ),
      text: productLine,
      cta: { href: storePaths.products, label: "Explore Products", icon: "cart" },
      second: { href: "#how-it-works", label: "How It Works", icon: "play" },
      points: [
        "One account for every product",
        intervals.has("monthly") && intervals.has("annual") ? "Monthly and yearly plans" : "Plans for every team",
        offers ? "Free plans and trials on selected products" : "Clear prices before you pay",
        "Secure, verified checkout",
      ],
    },
    {
      key: "recharge",
      title: (
        <>
          Recharge &amp; <span className={styles.heroAccent}>Buy Credits</span>
        </>
      ),
      text: "Add money to your Platform Wallet, buy AI credit packs, and fund messaging, API and webhook usage - each balance kept separate.",
      cta: { href: storePaths.recharge, label: "Recharge Now", icon: "wallet" },
      points: ["Platform Wallet for subscriptions", "AI Credits for AI features", "Consumption Wallet for usage", "Credited only after payment is confirmed"],
    },
    {
      key: "partners",
      title: (
        <>
          Grow With Us as a <span className={styles.heroAccent}>Partner</span>
        </>
      ),
      text: "Join the Affiliate or Reseller programme: refer customers or sell WorkEasy360 products, with your own partner dashboard.",
      cta: { href: storePaths.partners, label: "Join Partner Program", icon: "handshake" },
      points: ["Affiliate and Reseller programmes", "One application with KYC", "Your own partner dashboard", "Approval by our team"],
    },
  ];
}

function Hero({ page }: { page: PublicStorePage | null }) {
  const slides = useSlides(page);
  const [index, setIndex] = useState(0);
  const slide = slides[index];
  const products = (page?.products ?? []).filter((p) => p.status === "available");
  const go = (step: number) => setIndex((i) => (i + step + slides.length) % slides.length);
  return (
    <section className={styles.hero} aria-roledescription="carousel" aria-label="WorkEasy360 Store">
      <button type="button" className={`${styles.heroArrow} ${styles.heroPrev}`} aria-label="Previous slide" onClick={() => go(-1)}>
        <Icon name="chevronLeft" size={22} />
      </button>
      <div className={styles.heroSlide} aria-roledescription="slide" aria-label={`${index + 1} of ${slides.length}`} aria-live="polite">
        <div className={styles.heroText}>
          <h1 className={styles.heroTitle}>{slide.title}</h1>
          <p className={styles.heroLead}>{slide.text}</p>
          <div className={styles.heroCtas}>
            <Link href={slide.cta.href} className={styles.heroPrimary}>
              <Icon name={slide.cta.icon} size={20} /> {slide.cta.label} <Icon name="chevronRight" size={18} />
            </Link>
            {slide.second && (
              <a href={slide.second.href} className={styles.heroSecondary}>
                <Icon name={slide.second.icon} size={18} /> {slide.second.label}
              </a>
            )}
          </div>
        </div>
        <HeroArt products={products as Card[]} />
        <ul className={styles.heroPoints}>
          {slide.points.map((p) => (
            <li key={p}>
              <Icon name="checkCircle" size={22} /> {p}
            </li>
          ))}
        </ul>
      </div>
      <button type="button" className={`${styles.heroArrow} ${styles.heroNext}`} aria-label="Next slide" onClick={() => go(1)}>
        <Icon name="chevronRight" size={22} />
      </button>
      <div className={styles.heroDots}>
        {slides.map((s, i) => (
          <button
            key={s.key}
            type="button"
            className={styles.heroDot}
            aria-label={`Show slide ${i + 1}`}
            aria-current={i === index ? "true" : undefined}
            onClick={() => setIndex(i)}
          />
        ))}
      </div>
    </section>
  );
}

function Discover({ page }: { page: PublicStorePage | null }) {
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const searchId = useId();
  const categories = (page?.facets.categories ?? []).filter((c) => c.count > 0);
  const shown = useMemo(() => {
    const words = query.trim().toLowerCase();
    return ((page?.products ?? []) as Card[])
      .filter((p) => !category || p.category === category)
      .filter((p) => !words || `${p.title} ${p.summary} ${p.tagline} ${p.category}`.toLowerCase().includes(words))
      .slice(0, 8);
  }, [page, category, query]);
  return (
    <section className={styles.discover} aria-labelledby="popular-title">
      <div className={styles.discoverMain}>
        {categories.length > 0 && (
          <div className={styles.categoryTabs} role="group" aria-label="Departments">
            <button type="button" className={styles.categoryTab} aria-pressed={category === ""} onClick={() => setCategory("")}>
              <StoreIcon name="grid" size={26} />
              All Products
            </button>
            {categories.map((c) => (
              <button key={c.value} type="button" className={styles.categoryTab} aria-pressed={category === c.value} onClick={() => setCategory(c.value)}>
                <StoreIcon name={categoryIcon(c.label)} size={26} />
                {c.label}
              </button>
            ))}
          </div>
        )}
        <div className={styles.discoverHead}>
          <div>
            <h2 id="popular-title" className={shell.sectionTitle}>
              {category ? category : "Popular Products"}
            </h2>
            <p className={shell.sectionLead}>Essential apps for your business. Start free where offered, or choose the right plan.</p>
          </div>
          <div className={styles.discoverTools}>
            <label htmlFor={searchId} className="w360-visually-hidden">
              Filter these products
            </label>
            <input
              id={searchId}
              type="search"
              className={styles.inlineSearch}
              placeholder="Find a product"
              value={query}
              maxLength={60}
              onChange={(e) => setQuery(e.target.value)}
            />
            <Link href={storePaths.products} className={styles.viewAll}>
              View All Products <Icon name="chevronRight" size={16} />
            </Link>
          </div>
        </div>
        {page === null ? (
          <ul className={styles.productGrid} aria-busy="true">
            {[0, 1, 2, 3].map((i) => (
              <li key={i} className={`${shell.card} ${styles.skeleton}`} />
            ))}
          </ul>
        ) : shown.length === 0 ? (
          <p className={`${shell.card} ${shell.empty}`}>
            {page.products.length === 0 ? "No products are on sale yet. Please check back soon." : "No product matches that. Try another word or department."}
          </p>
        ) : (
          <ul className={styles.productGrid}>
            {shown.map((card) => (
              <ProductCard key={card.code} card={card} />
            ))}
          </ul>
        )}
      </div>
      <aside className={styles.discoverAside} aria-label="More from the Store">
        <div className={`${styles.promo} ${styles.promoRecharge}`}>
          <Icon name="gift" size={40} className={styles.promoIcon} />
          <div>
            <h2 className={styles.promoTitle}>Recharge &amp; Buy Credits</h2>
            <p>Add AI credits, messaging and API balance for uninterrupted business operations.</p>
            <Link href={storePaths.recharge} className={shell.primary}>
              Recharge Now <Icon name="chevronRight" size={16} />
            </Link>
          </div>
          <span className={styles.promoArt} aria-hidden="true">
            <Icon name="wallet" size={64} />
          </span>
        </div>
        <div className={`${styles.promo} ${styles.promoPartner}`}>
          <Icon name="handshake" size={40} className={styles.promoIcon} />
          <div>
            <h2 className={styles.promoTitle}>Become a Partner</h2>
            <p>Earn with our Affiliate or Reseller programme.</p>
            <Link href={storePaths.partners} className={shell.primary}>
              Join Partner Program <Icon name="chevronRight" size={16} />
            </Link>
          </div>
          <span className={styles.promoArt} aria-hidden="true">
            <Icon name="users" size={64} />
          </span>
        </div>
      </aside>
    </section>
  );
}

function Benefits({ page }: { page: PublicStorePage | null }) {
  const products = (page?.products ?? []).filter((p) => p.status === "available");
  const intervals = new Set(products.flatMap((p) => p.plans.flatMap((plan) => plan.prices.map((x) => x.interval))));
  const offers = products.some((p) => p.trial?.available || p.plans.some((plan) => plan.prices.some((x) => x.amount_minor === 0)));
  const items = [
    offers ? { icon: "rocket", title: "Start Free", text: "Free plans and trials on selected products" } : null,
    {
      icon: "card",
      title: "Flexible Plans",
      text: intervals.has("monthly") && intervals.has("annual") ? "Monthly and yearly options" : "Clear plans and prices",
    },
    { icon: "layers", title: "Unified Ecosystem", text: "One account for every app" },
    { icon: "receipt", title: "Invoices for Every Purchase", text: "Kept in My Account" },
    { icon: "shield", title: "Verified Checkout", text: "Nothing activates before payment is confirmed" },
  ].filter(Boolean) as { icon: string; title: string; text: string }[];
  return (
    <ul className={`${shell.card} ${styles.benefits}`} aria-label="Why the WorkEasy360 Store">
      {items.map((b) => (
        <li key={b.title}>
          <Icon name={b.icon} size={30} />
          <span>
            <strong>{b.title}</strong>
            <span>{b.text}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

const STEPS = [
  { icon: "search", title: "Choose Product", text: "Browse the Store and open a product to see its plans." },
  { icon: "layers", title: "Select Plan", text: "Pick a plan and billing period. Prices show GST where it applies." },
  { icon: "user", title: "Sign In / Select Organization", text: "Use your WorkEasy360 account and choose the organization you buy for." },
  { icon: "card", title: "Complete Purchase", text: "Pay with the Platform Wallet, online, or both. We confirm the payment first." },
  { icon: "rocket", title: "Start Using Product", text: "Your subscription activates and the product opens from My Products." },
];

function HowItWorks() {
  return (
    <section id="how-it-works" className={styles.how} aria-labelledby="how-title">
      <h2 id="how-title" className={shell.sectionTitle}>
        How It Works
      </h2>
      <ol className={styles.steps}>
        {STEPS.map((s, i) => (
          <li key={s.title} className={shell.card}>
            <span className={styles.stepNumber}>{i + 1}</span>
            <Icon name={s.icon} size={26} className={styles.stepIcon} />
            <strong>{s.title}</strong>
            <span>{s.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

const HELP = [
  { icon: "settings", title: "Setup", text: "What happens after you buy, and how a product is set up.", href: `${storePaths.help}#setup` },
  { icon: "cart", title: "Purchasing", text: "Plans, trials and how checkout works.", href: `${storePaths.help}#purchase` },
  { icon: "receipt", title: "Billing", text: "Payments, GST, renewals and refunds.", href: `${storePaths.help}#payment` },
  { icon: "headset", title: "Support", text: "Raise a ticket or write to our team.", href: `${storePaths.help}#contact` },
];

function HelpSection() {
  return (
    <section className={styles.helpSection} aria-labelledby="help-title">
      <h2 id="help-title" className={shell.sectionTitle}>
        Need Help?
      </h2>
      <ul className={styles.helpGrid}>
        {HELP.map((h) => (
          <li key={h.title}>
            <Link href={h.href} className={`${shell.card} ${styles.helpCard}`}>
              <Icon name={h.icon} size={26} />
              <strong>{h.title}</strong>
              <span>{h.text}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function StoreHomeScreen() {
  const page = useShellCatalog();
  return (
    <div className={shell.container}>
      <Hero page={page} />
      <Discover page={page} />
      <Benefits page={page} />
      <HowItWorks />
      <HelpSection />
    </div>
  );
}
