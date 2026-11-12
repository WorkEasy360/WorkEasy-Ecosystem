"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { publicApi, type PublicStoreProductPage, type PublicTax } from "@/lib/api/public";
import { storeHostApi, type StoreRate } from "@/lib/api/storeHost";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { MarkdownLite } from "@/features/console/MarkdownLite";
import { comparisonCell } from "@/features/storefront/PlanComparison";
import { shelfPrice, taxNote } from "@/features/storefront/StoreBrowser";
import { Gallery } from "./Gallery";
import { ProductCard, ProductTile, freeOffers, tileIcon } from "./ProductCard";
import { checkoutHref, trialHref } from "./checkoutLinks";
import { storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * One product of the Store (`/store/product/<slug>`): Overview (what it is, its
 * features, pictures, setup requirements and how to reach it), Plans & Pricing
 * (every plan for each billing period, the renewal, seats, included usage, the
 * usage rates beyond it), App Availability (the web app and the official
 * app-store links only), and the plan box - Buy Now, Start Free Plan, Start Trial.
 *
 * Every plan, price, allowance and link is the server's. The box's total is an
 * estimate from those prices; the checkout asks the server for the real quote,
 * and Buy Now hands it only identifiers (plan, period, seats). A Coming Soon
 * product shows everything and sells nothing.
 */

type Data = PublicStoreProductPage;
type Plan = Data["plans"][number];

export const PERIOD_LABEL: Record<string, string> = { monthly: "Monthly", quarterly: "Quarterly", semi_annual: "6 Months", annual: "Yearly" };
export const PERIOD_TERM: Record<string, string> = { monthly: "1 month", quarterly: "3 months", semi_annual: "6 months", annual: "12 months" };
const PER: Record<string, string> = { monthly: "month", quarterly: "quarter", semi_annual: "6 months", annual: "year" };

/** The meters a plan may include, in words. */
export const METER_LABEL: Record<string, string> = {
  ai_tokens: "AI tokens",
  whatsapp: "WhatsApp messages",
  sms: "SMS messages",
  email: "Emails",
  api_trigger: "API calls",
  webhook: "Webhook deliveries",
};

const APP_LABEL: Record<string, { label: string; icon: string }> = {
  android: { label: "Android", icon: "phone" },
  ios: { label: "iOS (iPhone & iPad)", icon: "phone" },
  windows: { label: "Windows", icon: "monitor" },
  macos: { label: "macOS", icon: "monitor" },
};

/** ADR-058: the GST the checkout will add on `taxable` - the server's rule
 * (rate, whole rupees, half up). An estimate; the checkout's quote is the amount. */
export function taxOn(tax: PublicTax | null | undefined, currency: string, taxable: number): number {
  if (!tax || currency !== "INR" || taxable <= 0) return 0;
  return Math.round((taxable * tax.rate_bp) / 1_000_000) * 100;
}

export function priceOf(plan: Plan, interval: string, currency: string | null) {
  const row = plan.prices.find((x) => x.interval === interval && x.amount_minor !== null && (!currency || x.currency === currency));
  return row && row.amount_minor !== null ? { ...row, amount_minor: row.amount_minor } : undefined;
}

export function renewalText(plan: Plan, interval: string, amount: string | null): string {
  const per = PER[interval] ?? interval;
  if (plan.renewal_rule === "none") return `Ends after ${PERIOD_TERM[interval] ?? "one period"}; it does not renew.`;
  if (plan.renewal_rule === "manual") return `Renews when you choose to renew${amount ? `, at ${amount} per ${per}` : ""}.`;
  return amount ? `Renews every ${per} at the plan's price, currently ${amount} per ${per}.` : `Renews every ${per}.`;
}

export function rateText(rate: StoreRate): string {
  const per = rate.per_units === 1 ? "each" : `per ${rate.per_units.toLocaleString()}`;
  if (rate.wallet === "ai_credits") return `${rate.unit_amount.toLocaleString()} AI credit${rate.unit_amount === 1 ? "" : "s"} ${per}`;
  return `${shelfPrice(rate.unit_amount, rate.currency)} ${per}`;
}

function Ticks({ id, title, items }: { id?: string; title: string; items: string[] | undefined }) {
  if (!items || items.length === 0) return null;
  return (
    <section id={id} className={styles.detailSection}>
      <h2 className={styles.detailHeading}>{title}</h2>
      <ul className={shell.ticks}>
        {items.map((item) => (
          <li key={item}>
            <Icon name="check" size={16} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PlanBox({ data, period, setPeriod, planId, setPlanId }: { data: Data; period: string; setPeriod: (p: string) => void; planId: string | null; setPlanId: (id: string) => void }) {
  const p = data.product;
  const soon = p.status === "coming_soon";
  const ids = { plans: useId(), qty: useId() };
  const buyable = data.plans.filter((x) => x.kind === "standard" && x.billing_type !== "free_trial");
  const periods = [...new Set(buyable.flatMap((x) => x.prices.filter((r) => r.amount_minor !== null).map((r) => r.interval)))].filter((i) => i !== "one_time");
  const inPeriod = buyable.filter((x) => priceOf(x, period, data.currency));
  const plan = inPeriod.find((x) => x.plan_version_id === planId) ?? inPeriod.find((x) => x.recommended) ?? inPeriod[0];
  const price = plan ? priceOf(plan, period, data.currency) : undefined;
  const perSeat = price?.model === "per_seat";
  const [qty, setQty] = useState(1);
  const seats = perSeat ? qty : 1;
  const setup = price?.setup_fee_minor ?? 0;
  const subtotal = price ? price.amount_minor * seats : 0;
  const tax = price ? taxOn(data.tax, price.currency, subtotal + setup) : 0;
  const trialPlan = !soon && p.trial.available ? data.plans.find((x) => p.trial.plan_codes.includes(x.plan_code)) : undefined;
  const free = price && price.amount_minor === 0;

  if (soon) {
    return (
      <aside className={`${shell.card} ${styles.planBox}`} aria-label={`Buy ${p.title}`}>
        <p className={styles.boxStatus}>
          <span className={`${shell.chip} ${shell.chipSoon}`}>Coming Soon</span>
        </p>
        <p className={styles.boxBig}>Not on sale yet</p>
        <p className={styles.boxHint}>{p.title} is coming soon. Its plans and prices appear here when it goes on sale.</p>
      </aside>
    );
  }

  return (
    <aside className={`${shell.card} ${styles.planBox}`} aria-label={`Buy ${p.title}`}>
      {price ? (
        <p className={styles.boxBig}>
          {shelfPrice(price.amount_minor, price.currency)}
          <span className={styles.boxPer}>
            {perSeat ? "/user" : ""}/{PER[period] ?? period}
          </span>{" "}
          <span className={styles.boxTax}>{taxNote(price.currency)}</span>
        </p>
      ) : trialPlan ? (
        <p className={styles.boxBig}>Free trial</p>
      ) : (
        <p className={styles.boxBig}>Pricing on request</p>
      )}
      {price && price.list_amount_minor != null && price.list_amount_minor > price.amount_minor && (
        <p className={styles.boxHint}>
          <s>
            {shelfPrice(price.list_amount_minor, price.currency)}/{PER[period]}
          </s>{" "}
          - {Math.round((price.discount_bp ?? 0) / 100)}% off
        </p>
      )}

      {periods.length > 1 && (
        <div className={styles.periods} role="group" aria-label="Billing period">
          {periods.map((i) => (
            <button key={i} type="button" className={styles.period} aria-pressed={period === i} onClick={() => setPeriod(i)}>
              {PERIOD_LABEL[i] ?? i}
            </button>
          ))}
        </div>
      )}

      {inPeriod.length > 0 && plan && (
        <fieldset className={styles.planChoice}>
          <legend className={styles.boxLabel}>Select Plan</legend>
          {inPeriod.map((x) => {
            const xp = priceOf(x, period, data.currency)!;
            const id = `${ids.plans}-${x.plan_version_id}`;
            return (
              <label key={x.plan_version_id} htmlFor={id} className={styles.planOption}>
                <input id={id} type="radio" name={ids.plans} checked={x.plan_version_id === plan.plan_version_id} onChange={() => setPlanId(x.plan_version_id)} />
                <span className={styles.planOptionText}>
                  <strong>
                    {x.name}
                    {x.recommended && <span className={styles.recommended}>Recommended</span>}
                  </strong>
                  {x.caption && <span>{x.caption}</span>}
                </span>
                <span className={styles.planOptionPrice}>{xp.amount_minor === 0 ? "Free" : shelfPrice(xp.amount_minor, xp.currency)}</span>
              </label>
            );
          })}
        </fieldset>
      )}

      {plan && price && perSeat && (
        <div className={styles.qtyRow}>
          <label htmlFor={ids.qty} className={styles.boxLabel}>
            Users / Seats
          </label>
          <div className={styles.stepper}>
            <button type="button" aria-label="Fewer seats" disabled={qty <= 1} onClick={() => setQty((n) => Math.max(1, n - 1))}>
              −
            </button>
            <input
              id={ids.qty}
              type="number"
              inputMode="numeric"
              min={1}
              max={plan.max_seats ?? 999}
              value={qty}
              onChange={(e) => setQty(Math.max(1, Math.min(plan.max_seats ?? 999, Math.trunc(Number(e.target.value) || 1))))}
            />
            <button type="button" aria-label="More seats" disabled={qty >= (plan.max_seats ?? 999)} onClick={() => setQty((n) => n + 1)}>
              +
            </button>
          </div>
        </div>
      )}

      {plan && price && !free && (
        <dl className={styles.estimate}>
          <div>
            <dt>
              {plan.name} · {PERIOD_TERM[period] ?? period}
              {seats > 1 ? ` × ${seats}` : ""}
            </dt>
            <dd>{shelfPrice(subtotal, price.currency)}</dd>
          </div>
          {setup > 0 && (
            <div>
              <dt>One-time set-up fee</dt>
              <dd>{shelfPrice(setup, price.currency)}</dd>
            </div>
          )}
          {data.tax && price.currency === "INR" && (
            <div>
              <dt>{data.tax.description}</dt>
              <dd>{shelfPrice(tax, price.currency)}</dd>
            </div>
          )}
          <div className={styles.estimateTotal}>
            <dt>Estimated total</dt>
            <dd>{shelfPrice(subtotal + setup + tax, price.currency)}</dd>
          </div>
        </dl>
      )}

      <div className={styles.boxActions}>
        {plan && price && !free && (
          <Link href={checkoutHref({ plan: plan.plan_version_id, interval: period, quantity: seats })} className={`${shell.primary} ${styles.wide}`}>
            <Icon name="lock" size={18} /> Buy Now
          </Link>
        )}
        {plan && free && (
          <Link href={checkoutHref({ plan: plan.plan_version_id, interval: period })} className={`${shell.primary} ${styles.wide}`}>
            Start Free Plan
          </Link>
        )}
        {trialPlan && (
          <Link href={trialHref(p.code, trialPlan.plan_code)} className={`${shell.secondary} ${styles.wide}`}>
            Start {p.trial.duration_days ? `${p.trial.duration_days}-day ` : ""}Free Trial
          </Link>
        )}
        {!price && !trialPlan && (
          <Link href={`${storePaths.help}#contact`} className={`${shell.secondary} ${styles.wide}`}>
            Contact sales
          </Link>
        )}
      </div>
      <p className={styles.boxHint}>One product, one plan and one organization per order. The checkout confirms the final amount before you pay.</p>
    </aside>
  );
}

function PlansSection({ data, period, onSelect }: { data: Data; period: string; onSelect: (planId: string, period: string) => void }) {
  const [rates, setRates] = useState<StoreRate[]>([]);
  const currency = data.currency ?? "";
  useEffect(() => {
    let live = true;
    if (!currency) return;
    storeHostApi.recharge(currency).then(
      (r) => live && setRates(r.rates),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [currency]);
  const standard = data.plans.filter((x) => x.kind === "standard");
  const addOns = data.plans.filter((x) => x.kind === "add_on");
  if (data.product.status === "coming_soon") return null;
  return (
    <section id="plans" className={styles.detailSection} aria-labelledby="plans-title">
      <h2 id="plans-title" className={shell.sectionTitle}>
        Plans &amp; Pricing
      </h2>
      <p className={shell.sectionLead}>
        Prices are in {data.currency ?? "the Store's currency"}
        {data.tax ? `, before ${data.tax.description}, which the checkout adds` : ", before any tax"}.
      </p>
      {standard.length === 0 ? (
        <p className={`${shell.card} ${shell.empty}`}>No plans are on sale online yet. Contact us for a quote.</p>
      ) : (
        <ul className={styles.planCards}>
          {standard.map((plan) => {
            const price = priceOf(plan, period, data.currency) ?? plan.prices.map((r) => priceOf(plan, r.interval, data.currency)).find(Boolean);
            const interval = price?.interval ?? period;
            const amount = price ? shelfPrice(price.amount_minor, price.currency) : null;
            const usage = Object.entries(plan.included_usage ?? {}).filter(([, n]) => typeof n === "number" && n > 0);
            const features = data.comparison
              .filter((row) => row.key !== "seats")
              .flatMap((row) => {
                const value = row.values[plan.plan_version_id];
                if (value === undefined || value === null || value === false) return [];
                return [value === true ? row.label : `${row.label}: ${comparisonCell(row as never, value)}`];
              });
            return (
              <li key={plan.plan_version_id} className={`${shell.card} ${styles.planCard} ${plan.recommended ? styles.planCardRecommended : ""}`}>
                {plan.recommended && <span className={styles.ribbon}>Recommended</span>}
                <h3 className={styles.planName}>{plan.name}</h3>
                {plan.caption && <p className={styles.planCaption}>{plan.caption}</p>}
                <p className={styles.planPrice}>
                  {plan.billing_type === "free_trial" ? (
                    "Free trial"
                  ) : price ? (
                    <>
                      <strong>{price.amount_minor === 0 ? "Free" : amount}</strong>
                      {price.amount_minor > 0 && (
                        <span>
                          {price.model === "per_seat" ? " /user" : " "}/{PER[interval]} {taxNote(price.currency)}
                        </span>
                      )}
                    </>
                  ) : (
                    "Not offered for this period"
                  )}
                </p>
                <dl className={styles.planFacts}>
                  <div>
                    <dt>Validity</dt>
                    <dd>{PERIOD_TERM[interval] ?? interval} from activation</dd>
                  </div>
                  {price && price.amount_minor > 0 && (
                    <div>
                      <dt>Renewal</dt>
                      <dd>{renewalText(plan, interval, amount)}</dd>
                    </div>
                  )}
                  <div>
                    <dt>Seats</dt>
                    <dd>
                      {plan.included_seats} included{plan.max_seats && plan.max_seats !== plan.included_seats ? `, up to ${plan.max_seats}` : ""}
                    </dd>
                  </div>
                  {price?.setup_fee_minor ? (
                    <div>
                      <dt>Set-up fee</dt>
                      <dd>{shelfPrice(price.setup_fee_minor, price.currency)} once</dd>
                    </div>
                  ) : null}
                  {usage.length > 0 && (
                    <div>
                      <dt>Included each month</dt>
                      <dd>
                        <ul className={styles.inlineList}>
                          {usage.map(([meter, n]) => (
                            <li key={meter}>
                              {n.toLocaleString()} {METER_LABEL[meter] ?? meter}
                            </li>
                          ))}
                        </ul>
                      </dd>
                    </div>
                  )}
                </dl>
                {features.length > 0 && (
                  <ul className={`${shell.ticks} ${styles.planFeatures}`}>
                    {features.map((f) => (
                      <li key={f}>
                        <Icon name="check" size={16} />
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {plan.billing_type !== "free_trial" && price && (
                  <button type="button" className={shell.secondary} onClick={() => onSelect(plan.plan_version_id, interval)}>
                    Select Plan
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {addOns.length > 0 && (
        <p className={styles.boxHint}>
          Add-ons for {data.product.title} ({addOns.map((a) => a.name).join(", ")}) are bought from My Products once you have a plan.
        </p>
      )}
      {rates.length > 0 && (
        <div className={`${shell.card} ${styles.ratesCard}`}>
          <h3 className={styles.detailHeading}>Usage beyond what plans include</h3>
          <p className={styles.boxHint}>Charged from your Consumption Wallet (AI tokens from AI Credits), at the rates in force when used.</p>
          <table className={styles.ratesTable}>
            <caption className="w360-visually-hidden">Additional usage rates</caption>
            <thead>
              <tr>
                <th scope="col">Service</th>
                <th scope="col">Rate</th>
                <th scope="col">Free each month</th>
              </tr>
            </thead>
            <tbody>
              {rates.map((r) => (
                <tr key={r.meter}>
                  <th scope="row">{r.label}</th>
                  <td>{rateText(r)}</td>
                  <td>{r.included_units > 0 ? r.included_units.toLocaleString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Availability({ data }: { data: Data }) {
  const access = data.product.access;
  const apps = Object.entries(access?.apps ?? {}).filter(([, url]) => typeof url === "string" && url.startsWith("https://"));
  return (
    <section id="apps" className={styles.detailSection} aria-labelledby="apps-title">
      <h2 id="apps-title" className={shell.sectionTitle}>
        App Availability
      </h2>
      <ul className={styles.appList}>
        <li className={shell.card}>
          <Icon name="browser" size={26} />
          <span>
            <strong>Web application</strong>
            <span>{access?.web ? "Opens from My Products after purchase, in any modern browser." : "Not available on the web yet."}</span>
          </span>
        </li>
        {apps.map(([platform, url]) => (
          <li key={platform} className={shell.card}>
            <Icon name={APP_LABEL[platform]?.icon ?? "device"} size={26} />
            <span>
              <strong>{APP_LABEL[platform]?.label ?? platform}</strong>
              <a href={url} rel="noopener noreferrer" target="_blank">
                Get the official app <Icon name="external" size={14} />
              </a>
            </span>
          </li>
        ))}
      </ul>
      {apps.length === 0 && <p className={styles.boxHint}>No mobile or desktop app is listed for {data.product.title}.</p>}
      <p className={styles.boxHint}>Subscriptions are bought here on the web; an app opens what your organization already holds.</p>
    </section>
  );
}

export function ProductDetailsScreen({ slug }: { slug: string }) {
  const [load] = useLoad(() => publicApi.storeProduct(slug), [slug]);
  return (
    <div className={shell.container}>
      <LoadGate load={load} what="this product">
        {(data: Data) => <ProductPage data={data} />}
      </LoadGate>
    </div>
  );
}

function ProductPage({ data }: { data: Data }) {
  const p = data.product;
  const soon = p.status === "coming_soon";
  const buyable = data.plans.filter((x) => x.kind === "standard" && x.billing_type !== "free_trial");
  const periods = [...new Set(buyable.flatMap((x) => x.prices.filter((r) => r.amount_minor !== null).map((r) => r.interval)))];
  // Open on the recommended plan's period, else the first paid plan's - never on a
  // free plan's alone when something is for sale.
  const paidPeriod = (plan?: Plan) => plan?.prices.find((r) => (r.amount_minor ?? 0) > 0 && periods.includes(r.interval))?.interval;
  const start =
    paidPeriod(buyable.find((x) => x.recommended)) ??
    buyable.map((x) => paidPeriod(x)).find(Boolean) ??
    (p.price && periods.includes(p.price.interval) ? p.price.interval : (periods[0] ?? "monthly"));
  const [period, setPeriod] = useState(start);
  const [planId, setPlanId] = useState<string | null>(null);
  const images = [...(p.banner ? [p.banner] : []), ...p.screenshots];
  const offers = freeOffers({ plans: data.plans as never, trial: p.trial, status: p.status });

  useEffect(() => {
    document.title = `${p.title} - WorkEasy360 Store`;
  }, [p.title]);

  return (
    <>
      <nav aria-label="Breadcrumb" className={shell.crumbs}>
        <ol>
          <li>
            <Link href={storePaths.home}>Store</Link>
          </li>
          <li>
            <Link href={storePaths.products}>Products</Link>
          </li>
          {p.category && (
            <li>
              <Link href={`${storePaths.products}?category=${encodeURIComponent(p.category)}`}>{p.category}</Link>
            </li>
          )}
          <li aria-current="page">{p.title}</li>
        </ol>
      </nav>
      <header className={styles.detailHead}>
        <ProductTile code={p.code} logo={p.logo?.url ?? null} icon={tileIcon(p)} size={72} />
        <div>
          <h1 className={shell.pageTitle}>{p.title}</h1>
          <p className={shell.pageLead}>{p.summary || p.tagline}</p>
          <ul className={shell.chips} aria-label={`About ${p.title}`}>
            {p.category && <li className={`${shell.chip} ${shell.chipQuiet}`}>{p.category}</li>}
            <li className={`${shell.chip} ${soon ? shell.chipSoon : ""}`}>{soon ? "Coming Soon" : "Available"}</li>
            {offers.map((o) => (
              <li key={o} className={shell.chip}>
                {o}
              </li>
            ))}
            {p.refund_days ? <li className={`${shell.chip} ${shell.chipQuiet}`}>{p.refund_days}-day refund window</li> : null}
          </ul>
        </div>
      </header>
      <nav className={styles.detailTabs} aria-label="On this page">
        <a href="#overview">Overview</a>
        {!soon && <a href="#plans">Plans &amp; Pricing</a>}
        <a href="#apps">App Availability</a>
      </nav>
      <div className={styles.detailLayout}>
        <div className={styles.detailMain}>
          <section id="overview" className={styles.detailSection} aria-labelledby="overview-title">
            <h2 id="overview-title" className={shell.sectionTitle}>
              Overview
            </h2>
            <Gallery title={p.title} icon={p.icon} logo={p.logo?.url ?? null} images={images} video={p.video ?? null} code={p.code} />
            <div className={styles.about}>{p.description ? <MarkdownLite text={p.description} /> : <p>{p.summary || p.tagline}</p>}</div>
          </section>
          <Ticks title="Main features" items={p.highlights} />
          <Ticks title="Benefits" items={p.benefits} />
          <Ticks title="Use cases" items={p.use_cases} />
          <Ticks id="setup" title="Setup requirements" items={p.setup_requirements} />
          {(p.documentation_url || p.support_url) && (
            <p className={styles.docLinks}>
              {p.documentation_url && (
                <a href={p.documentation_url} rel="noopener noreferrer" target="_blank">
                  <Icon name="book" size={16} /> Documentation
                </a>
              )}
              {p.support_url && (
                <a href={p.support_url} rel="noopener noreferrer" target="_blank">
                  <Icon name="headset" size={16} /> Product support
                </a>
              )}
            </p>
          )}
        </div>
        <PlanBox data={data} period={period} setPeriod={setPeriod} planId={planId} setPlanId={setPlanId} />
      </div>
      <PlansSection
        data={data}
        period={period}
        onSelect={(id, interval) => {
          setPeriod(interval);
          setPlanId(id);
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />
      <Availability data={data} />
      {data.related.length > 0 && (
        <section className={styles.detailSection} aria-labelledby="related-title">
          <h2 id="related-title" className={shell.sectionTitle}>
            Related products
          </h2>
          <ul className={`${styles.productGrid} ${styles.productGridThree}`}>
            {data.related.map((r) => (
              <ProductCard
                key={r.code}
                card={{
                  ...r,
                  tagline: r.summary,
                  status: "available",
                  ribbon: null,
                  yearly_deal: null,
                  plans: [],
                  plan_types: [],
                  trial_days: null,
                  logo_url: null,
                  banner_url: null,
                  description: "",
                  marketing_url: "",
                  availability: "available",
                  features: [],
                  starting_price: null,
                  trial: { available: false, duration_days: null, seat_limit: null, payment_method_required: false, plan_codes: [] },
                }}
              />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
