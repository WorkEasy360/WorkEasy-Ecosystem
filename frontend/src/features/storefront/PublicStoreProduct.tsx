"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { publicApi, type PublicStoreProductPage } from "@/lib/api/public";
import { LoadGate, money, useLoad } from "@/features/controlplane/ui";
import { ProductMark } from "@/features/controlplane/productmanager/ProductMark";
import { MarkdownLite } from "@/features/console/MarkdownLite";
import { PlanComparison, type ComparablePlan } from "./PlanComparison";
import { BADGE_LABEL, PER } from "./StoreBrowser";
import { AddToCart, PublicCartBar, useStoreBuy } from "./storeBuying";
import { productHref } from "./PublicStore";
import browse from "./StoreBrowser.module.css";
import styles from "./PublicStore.module.css";

/**
 * `/store/product/<slug>` - one product of the public Store (2026-11-02): the
 * full listing the Super Admin wrote (overview, screenshots, key features,
 * benefits, use cases, links), a box to buy it, the plan comparison with Buy
 * now / Add to cart / Start free trial on every plan, and related products.
 * Every price and plan is the server's (`GET public/store/products/<slug>`);
 * buying is `useStoreBuy`'s, and the checkout prices it again.
 */

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>{title}</h2>
      <ul className={styles.ticks}>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

export function PublicStoreProductScreen({ slug }: { slug: string }) {
  const [currency, setCurrency] = useState<string | undefined>(undefined);
  const [load] = useLoad(() => publicApi.storeProduct(slug, currency), [slug, currency]);
  const { buy, busy, signedIn, dialog } = useStoreBuy();
  const currencyId = useId();

  return (
    <div className={styles.wrap}>
      <nav aria-label="Breadcrumb" className={styles.crumbs}>
        <Link href="/store">
          <span aria-hidden="true">←</span> Back to all products
        </Link>
      </nav>
      <PublicCartBar onCheckout={(intent) => void buy(intent)} busy={busy} />
      <LoadGate load={load} what="this product">
        {(data: PublicStoreProductPage) => {
          const p = data.product;
          const soon = p.status === "coming_soon";
          const standard = data.plans.filter((plan) => plan.kind === "standard" && plan.billing_type !== "free_trial");
          const trialPlan = !soon && p.trial.available ? data.plans.find((plan) => p.trial.plan_codes.includes(plan.plan_code)) : undefined;
          // The box's plan: the one whose price the "From" line shows.
          const boxPlan =
            !soon && p.price
              ? standard.find((plan) =>
                  plan.prices.some((x) => x.interval === p.price!.interval && x.amount_minor !== null && x.currency === p.price!.currency),
                )
              : undefined;
          const boxInterval = p.price?.interval ?? "monthly";

          const cta = (plan: ComparablePlan, interval: string) => {
            if (soon) return null;
            const trialHere = p.trial.available && p.trial.plan_codes.includes(plan.plan_code);
            const priced = plan.billing_type !== "free_trial" && plan.prices.some((x) => x.interval === interval && x.amount_minor !== null);
            return (
              <div className={styles.planActions}>
                {priced && (
                  <>
                    <button
                      type="button"
                      className="w360-btn w360-btn-primary"
                      disabled={busy}
                      aria-label={`Buy now: ${plan.name}`}
                      onClick={() => void buy({ plan: plan.plan_version_id, interval, trial: false })}
                    >
                      Buy now
                    </button>
                    <AddToCart
                      planVersionId={plan.plan_version_id}
                      productCode={p.code}
                      productName={p.title}
                      planName={plan.name}
                      interval={interval}
                      label={plan.name}
                    />
                  </>
                )}
                {trialHere && (
                  <button
                    type="button"
                    className="w360-btn w360-btn-secondary"
                    disabled={busy}
                    aria-label={`Start free trial: ${plan.name}`}
                    onClick={() => void buy({ productTrial: { product: p.code, plan: plan.plan_code } })}
                  >
                    Start free trial
                  </button>
                )}
              </div>
            );
          };

          return (
            <>
              <div className={styles.hero}>
                <div className={styles.heroMain}>
                  <div className={styles.heroTitleRow}>
                    <ProductMark name={p.title} icon={p.icon} src={p.logo?.url ?? null} size={72} />
                    <div>
                      <h1 className={styles.heroTitle}>{p.title}</h1>
                      <p className={styles.heroCategory}>
                        {[p.category, p.subcategory].filter(Boolean).join(" · ") || "Software"}
                      </p>
                    </div>
                  </div>
                  <p className={styles.heroSummary}>{p.summary}</p>
                  {(p.badges.length > 0 || p.tags.length > 0) && (
                    <ul className={browse.chips} aria-label="About this product">
                      {p.badges.map((b) => (
                        <li key={b} className={`${browse.chip} ${browse.chipTrial}`}>
                          {BADGE_LABEL[b] ?? b}
                        </li>
                      ))}
                      {p.tags.map((t) => (
                        <li key={t} className={browse.chip}>
                          {t}
                        </li>
                      ))}
                    </ul>
                  )}
                  {p.banner && (
                    // eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image
                    <img className={styles.heroBanner} src={p.banner.url} alt={p.banner.caption || `${p.title}`} width={p.banner.width} height={p.banner.height} />
                  )}
                </div>

                <aside className={styles.buyBox} aria-label={`Buy ${p.title}`}>
                  {soon ? (
                    <p className={styles.buyPrice}>Coming soon</p>
                  ) : p.price ? (
                    <>
                      <span className={styles.buyFrom}>From</span>
                      <p className={styles.buyPrice}>
                        {money(p.price.amount_minor, p.price.currency)}
                        <span className={styles.buyPer}>{PER[p.price.interval] ?? ""}</span>
                      </p>
                      {p.price.list_amount_minor != null && p.price.list_amount_minor > p.price.amount_minor && (
                        <p className={styles.buyDeal}>
                          <s>{money(p.price.list_amount_minor, p.price.currency)}</s> {Math.round((p.price.discount_bp ?? 0) / 100)}% off
                        </p>
                      )}
                      {p.yearly_deal && (
                        <p className={styles.buyDeal}>
                          or {money(p.yearly_deal.amount_minor, p.yearly_deal.currency)}/year - save {p.yearly_deal.save_percent}%
                        </p>
                      )}
                    </>
                  ) : (
                    <p className={styles.buyPrice}>Contact sales</p>
                  )}
                  {boxPlan && (
                    <>
                      <button
                        type="button"
                        className={`w360-btn w360-btn-primary ${styles.full}`}
                        disabled={busy}
                        aria-label={`Buy now: ${p.title}`}
                        onClick={() => void buy({ plan: boxPlan.plan_version_id, interval: boxInterval, trial: false })}
                      >
                        Buy now
                      </button>
                      <AddToCart
                        planVersionId={boxPlan.plan_version_id}
                        productCode={p.code}
                        productName={p.title}
                        planName={boxPlan.name}
                        interval={boxInterval}
                        label={p.title}
                      />
                    </>
                  )}
                  {trialPlan && (
                    <button
                      type="button"
                      className={`w360-btn w360-btn-secondary ${styles.full}`}
                      disabled={busy}
                      aria-label={`Start free trial: ${p.title}`}
                      onClick={() => void buy({ productTrial: { product: p.code, plan: trialPlan.plan_code } })}
                    >
                      Start {p.trial.duration_days ? `${p.trial.duration_days}-day ` : ""}free trial
                    </button>
                  )}
                  {!soon && !boxPlan && !trialPlan && (
                    <Link href="/contact" className={`w360-btn w360-btn-primary ${styles.full}`}>
                      Contact sales
                    </Link>
                  )}
                  {boxPlan && standard.length > 1 && (
                    <a className={styles.buyMore} href="#compare-title">
                      See all {standard.length} plans
                    </a>
                  )}
                  <p className={styles.buyHint}>
                    {signedIn ? "You are signed in - Buy now opens your checkout." : "You will create an account (or sign in) before paying."}
                  </p>
                  {data.currencies.length > 1 && (
                    <div className={styles.currency}>
                      <label htmlFor={currencyId}>Currency</label>
                      <select id={currencyId} className="w360-input" value={data.currency ?? ""} onChange={(e) => setCurrency(e.target.value)}>
                        {data.currencies.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                </aside>
              </div>

              {p.screenshots.length > 0 && (
                <ul className={browse.shots} aria-label="Screenshots">
                  {p.screenshots.map((s) => (
                    <li key={s.url}>
                      {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                      <img src={s.url} alt={s.caption || `${p.title} screenshot`} width={s.width} height={s.height} />
                    </li>
                  ))}
                </ul>
              )}

              <section className={styles.panel}>
                <h2 className={styles.panelTitle}>Overview</h2>
                {p.description ? <MarkdownLite text={p.description} /> : <p>{p.summary || p.tagline}</p>}
              </section>
              <div className={styles.bullets}>
                <Bullets title="Key features" items={p.highlights} />
                <Bullets title="Benefits" items={p.benefits} />
                <Bullets title="Use cases" items={p.use_cases} />
              </div>

              {!soon && (
                <div className={styles.panel}>
                  <PlanComparison plans={data.plans} rows={data.comparison} renderCta={cta} />
                  <p className={browse.hint}>
                    Prices are in {data.currency ?? "the store currency"}, before any tax. The checkout shows the final price.
                  </p>
                </div>
              )}

              {(p.documentation_url || p.support_url) && (
                <p>
                  {p.documentation_url && (
                    <a href={p.documentation_url} rel="noopener noreferrer">
                      Documentation
                    </a>
                  )}
                  {p.documentation_url && p.support_url && " · "}
                  {p.support_url && (
                    <a href={p.support_url} rel="noopener noreferrer">
                      Support
                    </a>
                  )}
                </p>
              )}

              {data.related.length > 0 && (
                <section aria-labelledby="related-title">
                  <h2 id="related-title" className={styles.panelTitle}>
                    Related products
                  </h2>
                  <ul className={browse.grid}>
                    {data.related.map((r) => (
                      <li key={r.code} className={browse.card}>
                        <div className={browse.body}>
                          <div className={browse.titleRow}>
                            <ProductMark name={r.title} icon={r.icon} src={null} size={40} />
                            <h3 className={browse.title}>
                              <Link href={productHref(r.slug)}>{r.title}</Link>
                            </h3>
                          </div>
                          <p className={browse.summary}>{r.summary}</p>
                          {r.price && (
                            <p>
                              From {money(r.price.amount_minor, r.price.currency)}
                              {PER[r.price.interval] ?? ""}
                            </p>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          );
        }}
      </LoadGate>
      {dialog}
    </div>
  );
}
