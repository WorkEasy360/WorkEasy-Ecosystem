"use client";

import Link from "next/link";
import { publicApi, type PublicStoreCard, type PublicStorePage } from "@/lib/api/public";
import { planForStartingPrice, planForTrial } from "@/features/site/SitePages";
import { StoreBrowser, StoreCard, planTypeLabels, type BadgeTone } from "./StoreBrowser";
import { AddToCart, PublicCartBar, useStoreBuy } from "./storeBuying";
import styles from "./PublicStore.module.css";

/**
 * `/store` - the public Store (2026-11-02): every product a visitor may see,
 * as the Super Admin publishes them, laid out as a marketplace - categories
 * and filters on the left, search and sorting above, a card per product.
 * The shared Store layout over `GET public/store`; nothing here decides what
 * is for sale or at what price.
 *
 * Each card offers its product's details page, Buy now, Add to cart and its
 * free trial; the cart above the grid buys everything chosen in one checkout.
 * How a purchase reaches the checkout (signing up first, or straight there)
 * is `useStoreBuy`'s.
 */

const RIBBON: Record<NonNullable<PublicStoreCard["ribbon"]>["kind"], BadgeTone> = {
  coming_soon: "soon",
  new: "new",
  trial: "trial",
};

export const productHref = (slug: string) => `/store/product/${slug}`;

export function PublicStoreScreen() {
  const { buy, busy, signedIn, dialog } = useStoreBuy();

  return (
    <div className={styles.wrap}>
      <StoreBrowser<PublicStoreCard, PublicStorePage>
        title="Browse the store"
        lead="Every Workeasy360 product in one place. Pick one, sign in, and check out - it appears in your dashboard straight away."
        load={(query) => publicApi.store({ ...query })}
        loadKey="public"
        forbidden="The store is not available right now."
        nothingYet={{ title: "No products are on sale yet. Please check back soon." }}
        above={() => (
          <>
            <PublicCartBar onCheckout={(intent) => void buy(intent)} busy={busy} />
            <p className={styles.notice}>
              {signedIn ? (
                <>
                  You are signed in - Buy now goes straight to your checkout.{" "}
                  <Link href="/dashboard?mode=user">Go to my dashboard</Link>
                </>
              ) : (
                <>
                  <Link href="/login?mode=user">Log in</Link> or <Link href="/signup">create a free account</Link> to buy or
                  start a trial - your choice is kept while you sign up.
                </>
              )}
            </p>
          </>
        )}
        renderCard={(card, page, onCategory) => {
          const soon = card.status === "coming_soon";
          const trialPlan = !soon ? planForTrial(card) : undefined;
          const buyPlan = !soon ? planForStartingPrice(card) : undefined;
          const interval = card.starting_price?.interval ?? "monthly";
          return (
            <StoreCard
              key={card.code}
              code={card.code}
              title={card.title}
              href={productHref(card.slug)}
              icon={card.icon}
              logoSrc={card.logo_url}
              bannerSrc={card.banner_url}
              ribbon={card.ribbon && { label: card.ribbon.label, tone: RIBBON[card.ribbon.kind] }}
              category={card.category}
              onCategory={onCategory}
              summary={card.summary}
              badges={card.badges}
              chips={planTypeLabels(card.plan_types, page.facets.plan_types).map((label, i) => ({
                label,
                trial: card.plan_types[i] === "free_trial",
              }))}
              price={card.price}
              deal={card.yearly_deal}
              noPrice={soon ? "Coming soon" : "Contact sales"}
              actions={
                <>
                  {buyPlan && (
                    <>
                      <button
                        type="button"
                        className="w360-btn w360-btn-primary"
                        disabled={busy}
                        onClick={() => void buy({ plan: buyPlan.plan_version_id, interval, trial: false })}
                        aria-label={`Buy now: ${card.title}`}
                      >
                        Buy now
                      </button>
                      <AddToCart
                        planVersionId={buyPlan.plan_version_id}
                        productCode={card.code}
                        productName={card.title}
                        planName={buyPlan.name}
                        interval={interval}
                        label={card.title}
                      />
                    </>
                  )}
                  {trialPlan && (
                    <button
                      type="button"
                      className={`w360-btn ${buyPlan ? "w360-btn-secondary" : "w360-btn-primary"}`}
                      disabled={busy}
                      // The product's own trial (Phase 11) - no checkout.
                      onClick={() => void buy({ productTrial: { product: card.code, plan: trialPlan.plan_code } })}
                      aria-label={`Start free trial: ${card.title}`}
                    >
                      Start free trial
                    </button>
                  )}
                  {!soon && !buyPlan && !trialPlan && (
                    <Link href="/contact" className="w360-btn w360-btn-primary" aria-label={`Contact sales about ${card.title}`}>
                      Contact sales
                    </Link>
                  )}
                  <Link href={productHref(card.slug)} className="w360-btn w360-btn-secondary" aria-label={`View details: ${card.title}`}>
                    View details
                  </Link>
                </>
              }
            />
          );
        }}
      />
      {dialog}
    </div>
  );
}
