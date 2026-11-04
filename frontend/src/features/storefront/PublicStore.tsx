"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { publicApi, type PublicStoreCard, type PublicStorePage } from "@/lib/api/public";
import { StoreBrowser, StoreCard, storeDetailsClass, type BadgeTone, type StoreStart } from "./StoreBrowser";
import { PublicCartBar, useStoreBuy } from "./storeBuying";
import { StoreIcon } from "./storeIcons";
import styles from "./PublicStore.module.css";

/**
 * `/store` - the public Store (2026-11-02): every product a visitor may see,
 * as the Super Admin publishes them, laid out as a marketplace - categories
 * and filters on the left, search and sorting above, a card per product.
 * The shared Store layout over `GET public/store`; nothing here decides what
 * is for sale or at what price.
 *
 * Each card shows its price and leads to its product's page (2026-11-03,
 * the owner's marketplace mock-up), where a plan, the quantity and the
 * checkout are chosen; the cart above the grid buys everything added there in
 * one checkout. How a purchase reaches the checkout (signing up first, or
 * straight there) is `useStoreBuy`'s.
 */

const RIBBON: Record<NonNullable<PublicStoreCard["ribbon"]>["kind"], BadgeTone> = {
  coming_soon: "soon",
  new: "new",
  trial: "trial",
};

export const productHref = (slug: string) => `/store/product/${slug}`;

/** The filters `/store?…` opens with (the header's menus link here). */
function startFrom(params: URLSearchParams): StoreStart {
  const list = (name: string) =>
    params
      .getAll(name)
      .flatMap((v) => v.split(","))
      .map((v) => v.trim())
      .filter(Boolean);
  return {
    q: params.get("q") ?? undefined,
    category: params.get("category") ?? undefined,
    collection: params.get("collection") ?? undefined,
    productType: params.get("type") ?? undefined,
    bestFor: list("best_for"),
    integrations: list("integration"),
  };
}

export function PublicStoreScreen() {
  const { buy, busy, signedIn, dialog } = useStoreBuy();
  const params = useSearchParams();
  const address = params.toString();

  return (
    <div className={styles.wrap}>
      <StoreBrowser<PublicStoreCard, PublicStorePage>
        // A header link changes the address: the page starts again from it.
        key={address}
        start={startFrom(params)}
        hideSearch
        title="Explore Workeasy360 Products"
        lead="Everything you need to market, sell, manage and grow your business - all in one platform."
        load={(query) => publicApi.store({ ...query })}
        loadKey="public"
        forbidden="The store is not available right now."
        nothingYet={{ title: "No products are on sale yet. Please check back soon." }}
        above={() => (
          <>
            <PublicCartBar onCheckout={(intent) => void buy(intent)} busy={busy} />
            {/* Log in and Get Started are in the header; a signed-in buyer is told checkout is one step. */}
            {signedIn && (
              <p className={styles.notice}>
                You are signed in - checkout opens straight away. <Link href="/dashboard?mode=user">Go to my dashboard</Link>
              </p>
            )}
          </>
        )}
        renderCard={(card, _page, onCategory) => {
          const soon = card.status === "coming_soon";
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
              price={card.price}
              noPrice={soon ? "Coming soon" : "Contact sales"}
              // Like a marketplace shelf: the price and one way in. Choosing a
              // plan, the quantity and paying happen on the product's page.
              aside={
                <Link href={productHref(card.slug)} className={storeDetailsClass} aria-label={`View details: ${card.title}`}>
                  View Details <StoreIcon name="arrow" size={14} />
                </Link>
              }
            />
          );
        }}
      />
      {dialog}
    </div>
  );
}
