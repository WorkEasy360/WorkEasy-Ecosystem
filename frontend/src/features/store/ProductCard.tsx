"use client";

import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import type { PublicStoreCard } from "@/lib/api/public";
import { shelfPrice, taxNote } from "@/features/storefront/StoreBrowser";
import { storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * One product on the Store's shelf (the owner's marketplace image): its logo or
 * icon tile, name, short description, category, three of its features, the
 * starting price per seat or per account, what is free or on trial, whether it is
 * available - and View Details / Buy Now. Coming Soon has no purchase action.
 * Every value is the server's (`GET public/store`); nothing here prices anything.
 */

const PER: Record<string, string> = { monthly: "month", quarterly: "quarter", semi_annual: "6 months", annual: "year" };

/** Which icon a product tile shows: the Product Manager's icon key where it maps
 * to one of ours, else one picked from the category's words. */
const ICON_FOR: [RegExp, string][] = [
  [/crm|sales|lead/i, "users"],
  [/help ?desk|support|ticket/i, "headset"],
  [/book|account|invoice|financ|bill/i, "receipt"],
  [/project|task|operat/i, "layers"],
  [/market|campaign/i, "megaphone"],
  [/\bhr\b|people|attendance|payroll/i, "users"],
  [/\bai\b|bot|gpt|intelligen/i, "sparkles"],
  [/chat|whatsapp|messag|communicat/i, "chat"],
];

export function tileIcon(card: { icon?: string; category?: string; name?: string; title?: string }): string {
  const words = `${card.icon ?? ""} ${card.category ?? ""} ${card.name ?? ""} ${card.title ?? ""}`;
  for (const [re, icon] of ICON_FOR) if (re.test(words)) return icon;
  return "box";
}

const TONES = ["toneGreen", "tonePurple", "toneBlue", "toneOrange", "toneTeal"] as const;

/** A colour for a product's tile, from its code (FNV-1a), never from the server. */
export function toneOf(code: string): (typeof TONES)[number] {
  let hash = 2166136261;
  for (const ch of code) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619) >>> 0;
  return TONES[hash % TONES.length];
}

export function ProductTile({ code, logo, icon, size = 56 }: { code: string; logo: string | null; icon: string; size?: number }) {
  return (
    <span className={`${styles.tile} ${styles[toneOf(code)]}`} style={{ width: size, height: size }} aria-hidden="true">
      {logo ? (
        // eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image
        <img src={logo} alt="" width={size} height={size} />
      ) : (
        <Icon name={icon} size={Math.round(size * 0.5)} />
      )}
    </span>
  );
}

/** "₹200 /user/month": the cheapest *paid* price (monthly first, as the server's
 * own "from" price), per seat when that plan is priced per seat. A free plan is its
 * own label ("Free plan"), never "₹0". */
export function startingPrice(card: Pick<PublicStoreCard, "price" | "plans">): { amount: string; per: string; tax: string } | null {
  const paid = (card.plans ?? []).flatMap((plan) =>
    plan.prices.filter((x) => (x.amount_minor ?? 0) > 0 && x.interval !== "one_time").map((x) => ({ plan, row: x })),
  );
  const pool = paid.filter((p) => p.row.interval === "monthly");
  const best = (pool.length ? pool : paid).sort((a, b) => (a.row.amount_minor ?? 0) - (b.row.amount_minor ?? 0))[0];
  if (best) {
    const period = PER[best.row.interval] ?? best.row.interval.replace(/_/g, " ");
    return {
      amount: shelfPrice(best.row.amount_minor ?? 0, best.row.currency),
      per: best.row.model === "per_seat" ? `/user/${period}` : `/${period}`,
      tax: taxNote(best.row.currency),
    };
  }
  const price = card.price;
  if (!price || price.amount_minor <= 0) return null;
  const period = PER[price.interval] ?? price.interval.replace(/_/g, " ");
  return { amount: shelfPrice(price.amount_minor, price.currency), per: `/${period}`, tax: taxNote(price.currency) };
}

/** Free plan or trial, only where the server says so. */
export function freeOffers(card: Pick<PublicStoreCard, "plans" | "trial" | "status">): string[] {
  if (card.status !== "available") return [];
  const offers: string[] = [];
  if (card.plans?.some((p) => p.prices.some((x) => x.amount_minor === 0))) offers.push("Free plan");
  if (card.trial?.available) offers.push(card.trial.duration_days ? `${card.trial.duration_days}-day free trial` : "Free trial");
  return offers;
}

export function ProductCard({ card }: { card: PublicStoreCard & { highlights?: string[] } }) {
  const soon = card.status === "coming_soon";
  const price = startingPrice(card);
  const offers = freeOffers(card);
  const popular = card.badges?.find((b) => b === "best_seller" || b === "popular");
  const href = storePaths.product(card.slug);
  const features = (card.highlights ?? []).slice(0, 3);
  return (
    <li className={`${shell.card} ${styles.productCard}`}>
      {popular && <span className={styles.ribbon}>{popular === "best_seller" ? "Best Seller" : "Popular"}</span>}
      {soon && <span className={`${styles.ribbon} ${styles.ribbonSoon}`}>Coming Soon</span>}
      <ProductTile code={card.code} logo={card.logo_url} icon={tileIcon(card)} />
      <h3 className={styles.cardTitle}>
        <Link href={href} className={styles.cardLink}>
          {card.title}
        </Link>
      </h3>
      <p className={styles.cardSummary}>{card.summary || card.tagline}</p>
      <ul className={shell.chips} aria-label={`About ${card.title}`}>
        {card.category && <li className={`${shell.chip} ${shell.chipQuiet}`}>{card.category}</li>}
        <li className={`${shell.chip} ${soon ? shell.chipSoon : ""}`}>{soon ? "Coming Soon" : "Available"}</li>
        {offers.map((o) => (
          <li key={o} className={shell.chip}>
            {o}
          </li>
        ))}
      </ul>
      {features.length > 0 && (
        <ul className={`${shell.ticks} ${styles.cardTicks}`}>
          {features.map((f) => (
            <li key={f}>
              <Icon name="check" size={16} />
              <span>{f}</span>
            </li>
          ))}
        </ul>
      )}
      <div className={styles.cardFoot}>
        <p className={styles.cardPrice}>
          {soon ? (
            <span className={styles.cardPriceNote}>Not on sale yet</span>
          ) : price ? (
            <>
              <strong>{price.amount}</strong> <span>{price.per}</span> <span className={styles.cardPriceNote}>{price.tax}</span>
            </>
          ) : offers.includes("Free plan") ? (
            <strong>Free</strong>
          ) : (
            <span className={styles.cardPriceNote}>Pricing on request</span>
          )}
        </p>
        <div className={styles.cardButtons}>
          <Link href={href} className={shell.secondary} aria-label={`View details: ${card.title}`}>
            View Details
          </Link>
          {soon ? (
            <span className={`${shell.primary} ${styles.disabledLink}`} aria-disabled="true">
              Coming Soon
            </span>
          ) : (
            <Link href={`${href}#plans`} className={shell.primary} aria-label={`Buy now: ${card.title}`}>
              Buy Now <Icon name="chevronRight" size={16} />
            </Link>
          )}
        </div>
      </div>
    </li>
  );
}
