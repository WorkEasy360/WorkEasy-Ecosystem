"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { consoleApi, type OrgStoreCard, type OrgStorePage } from "@/lib/api/console";
import { ApiError } from "@/lib/api/client";
import { useAction } from "@/features/controlplane/ui";
import { StoreBrowser, StoreCard, planTypeLabels, type BadgeTone } from "@/features/storefront/StoreBrowser";
import { CartBar, ExploreActions, exploreOffer } from "./ExploreScreen";
import { useOrgAccess } from "./OrgAccess";
import { useCart } from "./cart";
import { trialDenialMessage } from "./trialCopy";
import cp from "@/features/controlplane/ControlPlane.module.css";

/**
 * The organization's Store: Explore's products as a marketplace page - the
 * shared Store layout over `GET orgs/<slug>/store`, which lists exactly what
 * Explore lists (narrowed and ordered by the organization's partner), priced
 * in the organization's billing currency.
 *
 * Each card's buttons are Explore's own (`ExploreActions`): open a product the
 * organization holds, start a free trial, buy, add to the cart. The browser
 * sends only *what* to buy; the checkout prices it and the trial is re-checked
 * on the server. Buying and trialling are the owner's
 * (`commercial.subscription.manage`); everyone else may browse.
 */

const BADGE: Record<NonNullable<OrgStoreCard["ribbon"]>["kind"], BadgeTone> = {
  owned: "success",
  trial_active: "success",
  coming_soon: "soon",
  expired: "warning",
  new: "new",
  trial: "trial",
};

export function StoreScreen() {
  const { slug, can } = useOrgAccess();
  const router = useRouter();
  const canBuy = can("commercial.subscription.manage");
  const cart = useCart(slug);
  // A started trial changes what the cards offer: fetch the page again.
  const [round, setRound] = useState(0);
  const { act, busy, feedback } = useAction(() => setRound((n) => n + 1));

  const startTrial = (product: OrgStoreCard, planCode: string) =>
    act(async () => {
      try {
        await consoleApi.startTrial(slug, product.code, planCode, crypto.randomUUID());
      } catch (err) {
        throw new ApiError(err instanceof ApiError ? err.status : 500, { detail: trialDenialMessage(err) });
      }
    }, `Trial started: ${product.name}.`);

  return (
    <StoreBrowser<OrgStoreCard, OrgStorePage>
      title="Store"
      lead="Products your organization can add. Prices are in your billing currency, before any tax."
      load={(query) => consoleApi.store(slug, { ...query })}
      loadKey={`${slug}#${round}`}
      forbidden="You don't have access to this organization's store."
      nothingYet={{ title: "No products are available right now." }}
      above={() => (
        <>
          {canBuy ? <CartBar slug={slug} /> : <p className={cp.muted}>You can browse. Only the organization owner can buy or start a trial.</p>}
          {feedback}
        </>
      )}
      renderCard={(card, page, onCategory) => {
        const { pricedAtCheckout } = exploreOffer(card, page.billing_account);
        return (
          <StoreCard
            key={card.code}
            code={card.code}
            title={card.title}
            href={`/o/${slug}/store/${card.code}`}
            icon={card.icon}
            logoSrc={card.logo_url}
            bannerSrc={card.banner_url}
            ribbon={card.ribbon && { label: card.ribbon.label, tone: BADGE[card.ribbon.kind] }}
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
            noPrice={card.availability === "coming_soon" ? "Coming soon" : pricedAtCheckout ? "Priced at checkout" : "Contact sales"}
            actions={
              <ExploreActions
                slug={slug}
                product={card}
                canBuy={canBuy}
                busy={busy}
                hasAccount={page.billing_account}
                onStartTrial={(planCode) => startTrial(card, planCode)}
                onBuy={(planVersionId, interval) => router.push(`/o/${slug}/checkout?plan=${planVersionId}&interval=${interval}`)}
                inCart={(id) => cart.has(id)}
                onAddToCart={(item, interval) => cart.add(item, interval)}
              />
            }
          />
        );
      }}
    />
  );
}
