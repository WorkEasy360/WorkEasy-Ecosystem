"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { consoleApi, type StoreProductPage } from "@/lib/api/console";
import { ApiError } from "@/lib/api/client";
import { LoadGate, money, useAction, useLoad } from "@/features/controlplane/ui";
import { ProductMark } from "@/features/controlplane/productmanager/ProductMark";
import { LaunchButton } from "@/features/launcher/LaunchButton";
import { PlanComparison, type ComparablePlan } from "@/features/storefront/PlanComparison";
import { BADGE_LABEL, PER } from "@/features/storefront/StoreBrowser";
import styles from "@/features/storefront/StoreBrowser.module.css";
import { CartBar } from "./ExploreScreen";
import { MarkdownLite } from "./MarkdownLite";
import { useOrgAccess } from "./OrgAccess";
import { useCart } from "./cart";
import { trialDenialMessage } from "./trialCopy";

/**
 * One product of the organization's Store (ADR-056): the listing, screenshots,
 * key features, benefits and use cases, the plan comparison and related
 * products. Every price and every button's condition is the server's
 * (`GET orgs/<slug>/store/<product>`): a held product offers Open (launch
 * re-checks the entitlement), a trial its days left, and each plan Buy, Add to
 * cart or Start trial - which the server re-prices and re-checks.
 */

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section>
      <h2>{title}</h2>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

export function StoreProductScreen({ productCode }: { productCode: string }) {
  const { slug, can } = useOrgAccess();
  const router = useRouter();
  const canBuy = can("commercial.subscription.manage");
  const cart = useCart(slug);
  const [round, setRound] = useState(0);
  const [load] = useLoad(() => consoleApi.storeProduct(slug, productCode), [slug, productCode, round]);
  const { act, busy, feedback } = useAction(() => setRound((n) => n + 1));

  return (
    <div className={styles.page}>
      <Link href={`/o/${slug}/store`}>
        <span aria-hidden="true">←</span> Store
      </Link>
      {canBuy && <CartBar slug={slug} />}
      {feedback}
      <LoadGate load={load} what="this product">
        {(data: StoreProductPage) => {
          const p = data.product;
          const held = p.trial_state === "owned" || p.trial_state === "active";
          const sellable = canBuy && p.availability === "available";
          const startTrial = (planCode: string) =>
            act(async () => {
              try {
                await consoleApi.startTrial(slug, p.code, planCode, crypto.randomUUID());
              } catch (err) {
                throw new ApiError(err instanceof ApiError ? err.status : 500, { detail: trialDenialMessage(err) });
              }
            }, `Trial started: ${p.name}.`);
          const cta = (plan: ComparablePlan, interval: string) => {
            if (!sellable) return null;
            if (plan.plan_code === p.held_plan_code && p.trial_state === "owned") return <span>Your current plan</span>;
            const trialHere = p.trial_state === "available" && p.trial.plan_codes.includes(plan.plan_code);
            const priced = plan.prices.some((x) => x.interval === interval && x.amount_minor !== null);
            const label = p.trial_state === "owned" ? "Change to this plan" : p.trial_state === "active" ? "Upgrade" : "Buy now";
            return (
              <div className={styles.actions}>
                {trialHere && (
                  <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={() => void startTrial(plan.plan_code)}>
                    Start {p.trial.duration_days ? `${p.trial.duration_days}-day ` : ""}free trial
                  </button>
                )}
                {plan.billing_type !== "free_trial" && priced && p.trial_state !== "owned" && (
                  <>
                    <button
                      type="button"
                      className={trialHere ? "w360-btn w360-btn-secondary" : "w360-btn w360-btn-primary"}
                      disabled={busy}
                      aria-label={`${label}: ${plan.name}`}
                      onClick={() => router.push(`/o/${slug}/checkout?plan=${plan.plan_version_id}&interval=${interval}`)}
                    >
                      {label}
                    </button>
                    <button
                      type="button"
                      className="w360-btn w360-btn-secondary"
                      disabled={busy || cart.has(plan.plan_version_id)}
                      aria-label={cart.has(plan.plan_version_id) ? `In cart: ${plan.name}` : `Add to cart: ${plan.name}`}
                      onClick={() =>
                        cart.add({ plan_version_id: plan.plan_version_id, product_code: p.code, product_name: p.name, plan_name: plan.name }, interval)
                      }
                    >
                      {cart.has(plan.plan_version_id) ? "In cart" : "Add to cart"}
                    </button>
                  </>
                )}
                {p.trial_state === "owned" && plan.plan_code !== p.held_plan_code && (
                  <Link className="w360-btn w360-btn-secondary" href={`/o/${slug}/billing`}>
                    {label}
                  </Link>
                )}
              </div>
            );
          };
          return (
            <>
              <header className={styles.productHero}>
                <ProductMark name={p.title} icon={p.icon} src={p.logo?.url ?? null} size={72} />
                <div>
                  <h1 className={styles.pageTitle}>{p.title}</h1>
                  <p className={styles.lead}>{p.summary}</p>
                  <ul className={styles.chips} aria-label="About this product">
                    {p.category && <li className={styles.chip}>{p.category}</li>}
                    {p.subcategory && <li className={styles.chip}>{p.subcategory}</li>}
                    {p.badges.map((b) => (
                      <li key={b} className={`${styles.chip} ${styles.chipTrial}`}>
                        {BADGE_LABEL[b] ?? b}
                      </li>
                    ))}
                  </ul>
                  {p.status === "owned" && <p role="status">You have {p.name}{p.renews_at ? `; it renews on ${new Date(p.renews_at).toLocaleDateString()}` : ""}.</p>}
                  {p.status === "trial" && <p role="status">Your free trial is running{p.trial_days_left != null ? `: ${p.trial_days_left} day${p.trial_days_left === 1 ? "" : "s"} left` : ""}.</p>}
                  {p.status === "coming_soon" && <p role="status">Coming soon.</p>}
                  <div className={styles.actions}>
                    {held && <LaunchButton orgSlug={slug} productCode={p.code} productName={p.name} />}
                    {p.price && (
                      <span>
                        From <strong>{money(p.price.amount_minor, p.price.currency)}</strong>
                        {PER[p.price.interval] ?? ""}
                      </span>
                    )}
                    {!data.billing_account && canBuy && <span className={styles.hint}>Prices appear after billing details are set; checkout asks for them.</span>}
                  </div>
                </div>
              </header>

              {p.screenshots.length > 0 && (
                <ul className={styles.shots} aria-label="Screenshots">
                  {p.screenshots.map((s) => (
                    <li key={s.url}>
                      {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                      <img src={s.url} alt={s.caption || `${p.name} screenshot`} width={s.width} height={s.height} />
                    </li>
                  ))}
                </ul>
              )}

              {p.description && (
                <section>
                  <h2>Overview</h2>
                  <MarkdownLite text={p.description} />
                </section>
              )}
              <div className={styles.productBullets}>
                <Bullets title="Key features" items={p.highlights} />
                <Bullets title="Benefits" items={p.benefits} />
                <Bullets title="Use cases" items={p.use_cases} />
              </div>

              <PlanComparison plans={data.plans} rows={data.comparison} renderCta={cta} currentPlanCode={p.held_plan_code} />
              <p className={styles.hint}>
                Prices are in {data.currency ?? "your billing currency"}, before any tax. The checkout prices every order again on the server; existing subscriptions keep the price they were bought at.
              </p>

              {data.related.length > 0 && (
                <section aria-labelledby="related-title">
                  <h2 id="related-title">Related products</h2>
                  <ul className={styles.grid}>
                    {data.related.map((r) => (
                      <li key={r.code} className={styles.card}>
                        <div className={styles.body}>
                          <div className={styles.titleRow}>
                            <ProductMark name={r.title} icon={r.icon} src={null} size={40} />
                            <h3 className={styles.title}>
                              <Link href={`/o/${slug}/store/${r.code}`}>{r.title}</Link>
                            </h3>
                          </div>
                          <p className={styles.summary}>{r.summary}</p>
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
              {(p.documentation_url || p.support_url) && (
                <p>
                  {p.documentation_url && <a href={p.documentation_url}>Documentation</a>}
                  {p.documentation_url && p.support_url && " · "}
                  {p.support_url && <a href={p.support_url}>Support</a>}
                </p>
              )}
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}
