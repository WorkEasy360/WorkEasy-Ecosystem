"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { consoleApi, type ExploreProduct, type ListPrice, type MarketplaceOffers } from "@/lib/api/console";
import { ApiError } from "@/lib/api/client";
import { ProductIcon, productIconKnown } from "@/components/ProductIcon";
import { LoadGate, money, useAction, useLoad } from "@/features/controlplane/ui";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { MarkdownLite } from "./MarkdownLite";
import { PageHeader } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";
import { trialDenialMessage } from "./trialCopy";

const CYCLES = [
  { key: "monthly", label: "Monthly", per: "month" },
  { key: "quarterly", label: "Quarterly", per: "quarter" },
  { key: "semi_annual", label: "Bi-annual", per: "6 months" },
  { key: "annual", label: "Annual", per: "year" },
] as const;
type Cycle = (typeof CYCLES)[number];

type Plan = MarketplaceOffers["plans"][number];
type Bundle = MarketplaceOffers["bundles"][number];

/**
 * Explore products (Phase 9; extended Phase 11). Two sections:
 *
 * - **Products** (`ExploreProducts`, new): every product the organization
 *   does not already hold, as a card - icon, category, whether a free trial
 *   is open to it right now, and the server's own price. Step 11 of the
 *   brief: a product already held never shows a purchase CTA here at all -
 *   it belongs to My products instead.
 * - **Plans & bundles** (`Offers`, unchanged): the existing plan-and-bundle
 *   marketplace, for add-ons and bundled packages that span several products.
 *
 * Choosing a plan directly opens the checkout, which asks the server for the
 * exact price and takes payment before anything is granted - the browser
 * sends only *what* to buy, never an amount. A trial has no checkout at all
 * (`consoleApi.startTrial`): nothing is invoiced, and nothing is granted until
 * the server has re-checked that the trial is actually still on offer. Buying
 * and trialling are both the owner's (`commercial.subscription.manage`).
 */
export function ExploreScreen() {
  const { slug, can } = useOrgAccess();
  const [load] = useLoad(() => consoleApi.marketplace(slug), [slug]);
  const canBuy = can("commercial.subscription.manage");

  return (
    <div className={styles.page}>
      <PageHeader
        title="Explore products"
        lead="Products your organization doesn't have yet. Prices are in your billing currency, before any tax."
      />
      <ExploreProducts slug={slug} canBuy={canBuy} />
      <LoadGate load={load} what="plans and bundles">
        {(data) => <Offers data={data} slug={slug} canBuy={canBuy} />}
      </LoadGate>
    </div>
  );
}

/** Phase 11's product cards - what "Explore More Products" (Step 11) means:
 * every product not already held, each with the one action that applies. */
function ExploreProducts({ slug, canBuy }: { slug: string; canBuy: boolean }) {
  const router = useRouter();
  const [load, reload] = useLoad(() => consoleApi.explore(slug), [slug]);
  const { act, busy, feedback } = useAction(reload);

  const startTrial = (product: ExploreProduct, planCode: string) =>
    act(async () => {
      try {
        await consoleApi.startTrial(slug, product.code, planCode, crypto.randomUUID());
      } catch (err) {
        throw new ApiError(err instanceof ApiError ? err.status : 500, { detail: trialDenialMessage(err) });
      }
    }, `Trial started: ${product.name}.`);

  const buy = (planVersionId: string, interval: string) =>
    router.push(`/o/${slug}/checkout?plan=${planVersionId}&interval=${interval}`);

  return (
    <LoadGate load={load} what="products">
      {(data) => {
        const discoverable = data.products.filter((p) => p.trial_state !== "owned");
        if (discoverable.length === 0) {
          return (
            <div className={local.notice}>
              <p>Your organization already has every product Workeasy360 offers.</p>
            </div>
          );
        }
        return (
          <section aria-labelledby="explore-products-title" className={local.section}>
            <h2 id="explore-products-title" className={local.sectionTitle}>
              Products
            </h2>
            {!canBuy && (
              <p className={cp.muted}>You can browse. Only the organization owner can buy or start a trial.</p>
            )}
            <div className={local.offers}>
              {discoverable.map((product) => (
                <ExploreProductCard
                  key={product.code}
                  product={product}
                  canBuy={canBuy}
                  busy={busy}
                  hasAccount={data.billing_account}
                  onStartTrial={(planCode) => startTrial(product, planCode)}
                  onBuy={buy}
                />
              ))}
            </div>
            {feedback}
          </section>
        );
      }}
    </LoadGate>
  );
}

/** Where a product card sends someone to choose a plan when it cannot price
 * one itself (no billing details yet, so no currency). */
const PACKAGES_ANCHOR = "product-packages";

const CYCLE_PER: Record<string, string> = { monthly: "month", quarterly: "quarter", semi_annual: "6 months", annual: "year" };

/** The plan `starting_price` names, matched by value - the same technique the
 * public storefront uses (`SitePages.planForStartingPrice`), so "Buy" here
 * buys exactly the plan the shown price belongs to. */
function planForExploreStartingPrice(product: ExploreProduct) {
  const sp = product.starting_price;
  if (!sp) return undefined;
  return product.plans.find(
    (p) =>
      p.kind === "standard" &&
      p.prices.some((price) => price.interval === sp.interval && price.amount_minor === sp.amount_minor && price.currency === sp.currency),
  );
}

function ExploreProductCard({
  product,
  canBuy,
  busy,
  hasAccount,
  onStartTrial,
  onBuy,
}: {
  product: ExploreProduct;
  canBuy: boolean;
  busy: boolean;
  hasAccount: boolean;
  onStartTrial: (planCode: string) => void;
  onBuy: (planVersionId: string, interval: string) => void;
}) {
  const titleId = useId();
  const trialPlan =
    product.trial_state === "available"
      ? product.plans.find((p) => product.trial.plan_codes.includes(p.plan_code))
      : undefined;
  const buyPlan = trialPlan ? undefined : planForExploreStartingPrice(product);
  // No billing details yet means no currency, so the server prices nothing -
  // the product is still for sale; the checkout asks for the details first.
  const pricedAtCheckout = !hasAccount && !product.starting_price && product.plans.some((p) => p.kind === "standard");
  const priceInterval = buyPlan?.prices.find((p) => p.amount_minor === product.starting_price?.amount_minor)?.interval ?? "monthly";

  const badge: { label: string; tone: string } | null =
    product.trial_state === "active"
      ? { label: "Trial active", tone: "w360-badge-success" }
      : product.trial_state === "used"
        ? { label: "Trial used", tone: "w360-badge-neutral" }
        : trialPlan
          ? { label: "Free trial available", tone: "w360-badge-success" }
          : null;

  return (
    <article className={`w360-card ${local.offer}`} aria-labelledby={titleId}>
      <div className={local.offerHead}>
        {productIconKnown(product.icon) ? (
          <span aria-hidden="true" className={local.offerIcon}>
            <ProductIcon icon={product.icon} />
          </span>
        ) : (
          <span aria-hidden="true" className={local.offerIcon}>
            {product.name
              .split(/\s+/)
              .filter(Boolean)
              .slice(0, 2)
              .map((w) => w[0]?.toUpperCase())
              .join("") || "?"}
          </span>
        )}
        <h3 id={titleId} className={local.offerTitle}>
          {product.name}
        </h3>
      </div>
      {product.category && <p className={local.caption}>{product.category}</p>}
      {product.tagline && <p className={local.caption}>{product.tagline}</p>}
      {badge && (
        <ul className={local.badges} aria-label="Trial status">
          <li className={`w360-badge ${badge.tone}`}>{badge.label}</li>
        </ul>
      )}
      {product.availability === "coming_soon" ? (
        <p className={local.priceNote}>Coming soon</p>
      ) : product.starting_price ? (
        <p className={local.price}>
          {money(product.starting_price.amount_minor, product.starting_price.currency)}
          <span className={local.priceNote}>
            {" "}
            / {CYCLE_PER[product.starting_price.interval] ?? product.starting_price.interval.replace(/_/g, " ")}
          </span>
        </p>
      ) : pricedAtCheckout ? (
        <p className={local.priceNote}>Priced in your billing currency at checkout</p>
      ) : (
        <p className={local.priceNote}>Contact sales for pricing</p>
      )}
      {canBuy && product.availability === "available" && (
        <div className={local.offerAction}>
          {trialPlan ? (
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              aria-label={`Start free trial: ${product.name}`}
              disabled={busy}
              onClick={() => onStartTrial(trialPlan.plan_code)}
            >
              Start free trial
            </button>
          ) : buyPlan ? (
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              aria-label={`Buy: ${product.name}`}
              disabled={busy}
              onClick={() => onBuy(buyPlan.plan_version_id, priceInterval)}
            >
              Buy
            </button>
          ) : pricedAtCheckout ? (
            <a href={`#${PACKAGES_ANCHOR}`} className="w360-btn w360-btn-primary" aria-label={`See plans: ${product.name}`}>
              See plans
            </a>
          ) : product.trial_state !== "used" ? (
            <Link href={`/products/${product.slug}`} className="w360-btn w360-btn-secondary">
              Learn more
            </Link>
          ) : null}
        </div>
      )}
    </article>
  );
}

function Offers({ data, slug, canBuy }: { data: MarketplaceOffers; slug: string; canBuy: boolean }) {
  const router = useRouter();
  const busy = false;
  const everything = [...data.bundles, ...data.plans];
  const available = CYCLES.filter((c) => everything.some((o) => o.prices.some((p) => p.interval === c.key)));
  const [chosen, setChosen] = useState<string | null>(null);
  const cycle: Cycle = available.find((c) => c.key === chosen) ?? available[0] ?? CYCLES[0];
  const held = new Set(data.held_products);
  const standard = data.plans.filter((p) => p.kind !== "add_on");
  const addOns = data.plans.filter((p) => p.kind === "add_on");
  const planName = (code: string | null) => data.plans.find((p) => p.plan_code === code)?.name ?? code ?? "";

  /** Open the checkout for this choice - identifiers only in the address. */
  function buy(target: { plan_version_id?: string; bundle_version_id?: string }, _name: string, trial: boolean) {
    const params = new URLSearchParams({ interval: cycle.key });
    if (target.plan_version_id) params.set("plan", target.plan_version_id);
    if (target.bundle_version_id) params.set("bundle", target.bundle_version_id);
    if (trial) params.set("trial", "1");
    router.push(`/o/${slug}/checkout?${params.toString()}`);
  }

  const common = { cycle, currency: data.currency, canBuy, busy, hasAccount: data.billing_account };

  if (everything.length === 0) {
    return <p className={cp.muted}>Nothing is available to buy right now.</p>;
  }

  return (
    <>
      {!data.billing_account && (
        <div className={local.notice}>
          <p>
            Prices appear in your currency once your organization has billing details. Choose a product and the
            checkout asks for them first - or <Link href={`/o/${slug}/settings/billing`}>set them up now</Link>.
          </p>
        </div>
      )}
      {!canBuy && <p className={cp.muted}>You can browse the products. Only the organization owner can buy.</p>}
      {available.length > 1 && (
        <div>
          <div className={styles.segmented} role="group" aria-label="Billing cycle">
            {available.map((c) => (
              <button
                key={c.key}
                type="button"
                aria-pressed={cycle.key === c.key}
                onClick={() => setChosen(c.key)}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {data.bundles.length > 0 && (
        <OfferSection title="Plans" lead="Several products together, at one price.">
          {data.bundles.map((b) => (
            <OfferCard
              key={b.bundle_version_id}
              {...common}
              name={b.name}
              caption={b.caption}
              description={b.description}
              prices={b.prices}
              trialDays={b.billing_type === "free_trial" ? b.trial_days : 0}
              included={b.includes.length > 0 && b.includes.every((i) => held.has(i.product_code))}
              extra={<BundleIncludes bundle={b} />}
              onBuy={(trial) => buy({ bundle_version_id: b.bundle_version_id }, b.name, trial)}
            />
          ))}
        </OfferSection>
      )}
      {standard.length > 0 && (
        <OfferSection title="Product packages" lead="One product, at the plan that suits you." anchor={PACKAGES_ANCHOR}>
          {standard.map((p) => (
            <PlanCard key={p.plan_version_id} plan={p} common={common} held={held} addonFor="" onBuy={buy} />
          ))}
        </OfferSection>
      )}
      {addOns.length > 0 && (
        <OfferSection title="Add-ons" lead="Extras for a plan you already have.">
          {addOns.map((p) => (
            <PlanCard
              key={p.plan_version_id}
              plan={p}
              common={common}
              held={held}
              addonFor={planName(p.addon_for)}
              onBuy={buy}
            />
          ))}
        </OfferSection>
      )}
    </>
  );
}

type Common = { cycle: Cycle; currency: string | null; canBuy: boolean; busy: boolean; hasAccount: boolean };

function PlanCard({
  plan: p,
  common,
  held,
  addonFor,
  onBuy,
}: {
  plan: Plan;
  common: Common;
  held: Set<string>;
  addonFor: string;
  onBuy: (target: { plan_version_id: string }, name: string, trial: boolean) => void;
}) {
  return (
    <OfferCard
      {...common}
      name={p.name}
      caption={p.caption || p.product_name}
      description={p.description}
      prices={p.prices}
      trialDays={p.billing_type === "free_trial" ? p.trial_days : 0}
      included={p.kind !== "add_on" && held.has(p.product_code)}
      addonFor={p.kind === "add_on" ? addonFor : ""}
      extra={
        p.included_seats > 0 ? (
          <p className={local.caption}>
            {p.included_seats === 1 ? "1 seat included" : `${p.included_seats} seats included`}
          </p>
        ) : null
      }
      onBuy={(trial) => onBuy({ plan_version_id: p.plan_version_id }, p.name, trial)}
    />
  );
}

function BundleIncludes({ bundle }: { bundle: Bundle }) {
  if (bundle.includes.length === 0) return null;
  return (
    <ul className={local.includes} aria-label={`What ${bundle.name} includes`}>
      {bundle.includes.map((i) => (
        <li key={`${i.product_code}-${i.plan_name}`}>
          {i.product_name} ({i.plan_name}){i.quantity > 1 ? ` × ${i.quantity}` : ""}
        </li>
      ))}
    </ul>
  );
}

function OfferSection({
  title,
  lead,
  anchor,
  children,
}: {
  title: string;
  lead: string;
  anchor?: string;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <section id={anchor} className={local.section} aria-labelledby={id}>
      <div>
        <h2 id={id} className={local.sectionTitle}>
          {title}
        </h2>
        <p className={styles.lead}>{lead}</p>
      </div>
      <div className={local.offers}>{children}</div>
    </section>
  );
}

function priceFor(prices: ListPrice[], cycle: Cycle): ListPrice | undefined {
  return prices.find((p) => p.interval === cycle.key);
}

function OfferCard({
  name,
  caption,
  description,
  prices,
  trialDays,
  included,
  addonFor = "",
  extra,
  cycle,
  canBuy,
  busy,
  hasAccount,
  onBuy,
}: Common & {
  name: string;
  caption: string;
  description: string;
  prices: ListPrice[];
  trialDays: number;
  included: boolean;
  addonFor?: string;
  extra?: React.ReactNode;
  onBuy: (trial: boolean) => void;
}) {
  const titleId = useId();
  const price = priceFor(prices, cycle);
  const trial = trialDays > 0;
  const actionLabel = trial ? "Start free trial" : "Buy";
  const badges = [
    trial ? `Free trial · ${trialDays} days` : null,
    addonFor ? `Add-on for ${addonFor}` : null,
    included ? "Purchased" : null,
  ].filter((b): b is string => b !== null);

  return (
    <article className={`w360-card ${local.offer}`} aria-labelledby={titleId}>
      <h3 id={titleId} className={local.offerTitle}>
        {name}
      </h3>
      {caption && <p className={local.caption}>{caption}</p>}
      {badges.length > 0 && (
        <ul className={local.badges} aria-label="Offer details">
          {badges.map((b) => (
            <li key={b} className={b === "Purchased" ? "w360-badge w360-badge-success" : "w360-badge w360-badge-neutral"}>
              {b}
            </li>
          ))}
        </ul>
      )}
      {price && price.amount_minor !== null ? (
        <p className={local.price}>
          {money(price.amount_minor, price.currency)}
          <span className={local.priceNote}>
            {price.model === "per_seat" ? " per seat" : ""} / {cycle.per}
          </span>
        </p>
      ) : (
        <p className={local.priceNote}>Price on request</p>
      )}
      <MarkdownLite text={description} className={local.markdown} />
      {extra}
      {canBuy && !included && (
        <div className={local.offerAction}>
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            aria-label={`${actionLabel}: ${name}`}
            disabled={busy || (hasAccount && !price)}
            onClick={() => onBuy(trial)}
          >
            {actionLabel}
          </button>
        </div>
      )}
    </article>
  );
}
