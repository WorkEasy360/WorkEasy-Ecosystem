"use client";

import { useEffect, useId, useRef, useState, type MouseEvent, type ReactNode } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { publicApi, type PublicStoreProductPage, type PublicTax } from "@/lib/api/public";
import { storeHostApi, type StoreRate } from "@/lib/api/storeHost";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { MarkdownLite } from "@/features/console/MarkdownLite";
import { comparisonCell } from "@/features/storefront/PlanComparison";
import { shelfPrice, taxNote } from "@/features/storefront/StoreBrowser";
import { DemoVideo, Gallery } from "./Gallery";
import { Lightbox } from "./Lightbox";
import { ProductCard, ProductTile, freeOffers, tileIcon } from "./ProductCard";
import { checkoutHref, trialHref } from "./checkoutLinks";
import { storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * One product of the Store (`/store/product/<slug>`, redesigned 2026-11-16):
 * the hero (logo, name, what it is, category, availability, what is free, its
 * key highlights), the pictures (full screen on a click), and the sections the
 * sticky "On this page" bar names - Overview, Features, Screenshots, Demo,
 * Plans & Pricing (every plan for each billing period, the renewal, seats,
 * included usage, the usage rates beyond it) and App Availability (only what
 * the Super Admin configured) - beside the plan box: Buy Now, Start Free Plan,
 * Start Trial.
 *
 * Every plan, price, allowance and link is the server's. The box's total is an
 * estimate from those prices; the checkout asks the server for the real quote,
 * and Buy Now hands it only identifiers (plan, period, seats). A Coming Soon
 * product shows everything and sells nothing. `preview` (the Super Admin's
 * Product Publisher) draws the same page with nothing to buy and nothing that
 * leaves it.
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

/** The app stores a product may be listed in, in the order they are shown. */
const APP_ORDER = ["android", "ios", "windows", "macos"] as const;
const APP_LABEL: Record<(typeof APP_ORDER)[number], { label: string; icon: string }> = {
  android: { label: "Android", icon: "phone" },
  ios: { label: "iOS (iPhone & iPad)", icon: "phone" },
  windows: { label: "Desktop (Windows)", icon: "monitor" },
  macos: { label: "Desktop (macOS)", icon: "monitor" },
};

/** What is true of every purchase on the Store - the platform's own facts, no figures. */
const TRUST: { icon: string; text: string }[] = [
  { icon: "user", text: "One WorkEasy360 account for every product" },
  { icon: "apps", text: "Manage subscriptions in My Account" },
  { icon: "shield", text: "Secure checkout" },
];

const PREVIEW_NOTE = "Preview - purchasing opens once the product is published";

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

/** Smooth scrolling, unless the visitor asked for less motion. */
function scrollBehavior(): ScrollBehavior {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

/** Which of these sections the visitor is reading: the first one inside the band
 * under the sticky bars. Without IntersectionObserver (an old browser, jsdom) no
 * section is marked and the links still work. */
function useCurrentSection(ids: string[]): string | null {
  const [current, setCurrent] = useState<string | null>(null);
  const key = ids.join(" ");
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.IntersectionObserver !== "function") return;
    const order = key.split(" ");
    const inside = new Map<string, boolean>();
    const observer = new window.IntersectionObserver(
      (entries) => {
        for (const entry of entries) inside.set(entry.target.id, entry.isIntersecting);
        const first = order.find((id) => inside.get(id));
        if (first) setCurrent(first);
        // Above every section (the top of the page): the first one.
        else if ((document.getElementById(order[0])?.getBoundingClientRect().top ?? -1) > 0) setCurrent(order[0]);
      },
      { rootMargin: "-150px 0px -55% 0px" },
    );
    for (const id of order) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [key]);
  return current;
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

/** A purchase action: a link to the checkout - or, in a preview, the same-looking
 * button that does nothing. */
function Action({ preview, href, primary = false, children }: { preview: boolean; href: string; primary?: boolean; children: ReactNode }) {
  const className = `${primary ? shell.primary : shell.secondary} ${styles.wide}`;
  if (preview) {
    return (
      <button type="button" className={className} disabled aria-disabled="true">
        {children}
      </button>
    );
  }
  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}

function PlanBox({
  data,
  period,
  setPeriod,
  planId,
  setPlanId,
  preview,
  focusSignal,
}: {
  data: Data;
  period: string;
  setPeriod: (p: string) => void;
  planId: string | null;
  setPlanId: (id: string) => void;
  preview: boolean;
  /** Bumped by a plan card's Select Plan: the box comes into view, focus on the chosen plan. */
  focusSignal: number;
}) {
  const p = data.product;
  const soon = p.status === "coming_soon";
  const ids = { plans: useId(), qty: useId() };
  const box = useRef<HTMLElement | null>(null);
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

  useEffect(() => {
    const el = box.current;
    if (!focusSignal || !el) return;
    el.scrollIntoView?.({ behavior: scrollBehavior(), block: "start" });
    el.querySelector<HTMLInputElement>('input[type="radio"]:checked')?.focus({ preventScroll: true });
  }, [focusSignal]);

  if (soon) {
    return (
      <aside ref={box} className={`${shell.card} ${styles.planBox}`} aria-label={`Buy ${p.title}`}>
        <p className={styles.boxStatus}>
          <span className={`${shell.chip} ${shell.chipSoon}`}>Coming Soon</span>
        </p>
        <p className={styles.boxBig}>Not on sale yet</p>
        <p className={styles.boxHint}>{p.title} is coming soon. Its plans and prices appear here when it goes on sale.</p>
      </aside>
    );
  }

  return (
    <aside ref={box} className={`${shell.card} ${styles.planBox}`} aria-label={`Buy ${p.title}`}>
      <div className={styles.boxPrice}>
        {price && plan && (
          <p className={styles.boxKicker}>
            {plan.name} · {PERIOD_LABEL[period] ?? period}
          </p>
        )}
        {price ? (
          <p className={styles.boxBig}>
            {free ? (
              "Free"
            ) : (
              <>
                {shelfPrice(price.amount_minor, price.currency)}
                <span className={styles.boxPer}>
                  {perSeat ? "/user" : ""}/{PER[period] ?? period}
                </span>{" "}
                <span className={styles.boxTax}>{taxNote(price.currency)}</span>
              </>
            )}
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
            <span className={styles.boxSave}>{Math.round((price.discount_bp ?? 0) / 100)}% off</span>
          </p>
        )}
      </div>

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
          <Action preview={preview} primary href={checkoutHref({ plan: plan.plan_version_id, interval: period, quantity: seats })}>
            <Icon name="lock" size={18} /> Buy Now
          </Action>
        )}
        {plan && free && (
          <Action preview={preview} primary href={checkoutHref({ plan: plan.plan_version_id, interval: period })}>
            Start Free Plan
          </Action>
        )}
        {trialPlan && (
          <Action preview={preview} href={trialHref(p.code, trialPlan.plan_code)}>
            Start {p.trial.duration_days ? `${p.trial.duration_days}-day ` : ""}Free Trial
          </Action>
        )}
        {!price && !trialPlan && (
          <Action preview={preview} href={`${storePaths.help}#contact`}>
            Contact sales
          </Action>
        )}
      </div>
      {preview && <p className={styles.previewNote}>{PREVIEW_NOTE}</p>}
      <p className={styles.boxHint}>One product, one plan and one organization per order. The checkout confirms the final amount before you pay.</p>
      <ul className={styles.trustRow} aria-label="Buying on the WorkEasy360 Store">
        {TRUST.map((t) => (
          <li key={t.text}>
            <Icon name={t.icon} size={16} />
            <span>{t.text}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function PlansSection({ data, period, preview, onSelect }: { data: Data; period: string; preview: boolean; onSelect: (planId: string, period: string) => void }) {
  const [rates, setRates] = useState<StoreRate[]>([]);
  const currency = data.currency ?? "";
  useEffect(() => {
    let live = true;
    // A preview asks nothing of the Store host: its rates are the live page's business.
    if (!currency || preview) return;
    storeHostApi.recharge(currency).then(
      (r) => live && setRates(r.rates),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [currency, preview]);
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
                {plan.description?.trim() && <MarkdownLite text={plan.description} headingLevel={4} className={styles.planDescription} />}
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
                  <button type="button" className={plan.recommended ? shell.primary : shell.secondary} onClick={() => onSelect(plan.plan_version_id, interval)}>
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

/** How the product is reached: the web application when it has one, and each
 * official app-store link the Super Admin entered (https only) - nothing else. */
function Availability({ data }: { data: Data }) {
  const access = data.product.access;
  const links = access?.apps ?? {};
  const apps = APP_ORDER.flatMap((platform) => {
    const url = links[platform];
    return typeof url === "string" && url.startsWith("https://") ? [{ platform, url }] : [];
  });
  const web = access?.web === true;
  return (
    <section id="apps" className={styles.detailSection} aria-labelledby="apps-title">
      <h2 id="apps-title" className={shell.sectionTitle}>
        App Availability
      </h2>
      {web || apps.length > 0 ? (
        <ul className={styles.appList}>
          {web && (
            <li className={shell.card}>
              <span className={styles.appIcon}>
                <Icon name="browser" size={24} />
              </span>
              <span className={styles.appText}>
                <strong>Web application</strong>
                <span>Opens from My Products after purchase, in any modern browser.</span>
              </span>
            </li>
          )}
          {apps.map(({ platform, url }) => (
            <li key={platform} className={shell.card}>
              <span className={styles.appIcon}>
                <Icon name={APP_LABEL[platform].icon} size={24} />
              </span>
              <span className={styles.appText}>
                <strong>{APP_LABEL[platform].label}</strong>
                <a href={url} rel="noopener noreferrer" target="_blank">
                  Get the official app <Icon name="external" size={14} />
                </a>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.boxHint}>How to reach {data.product.title} - on the web or in an app - is listed here once it is available.</p>
      )}
      {apps.length > 0 && <p className={styles.boxHint}>Subscriptions are bought here on the web; an app opens what your organization already holds.</p>}
    </section>
  );
}

export function ProductDetailsScreen({ slug }: { slug: string }) {
  const [load] = useLoad(() => publicApi.storeProduct(slug), [slug]);
  return (
    <div className={shell.container}>
      <LoadGate load={load} what="this product">
        {(data: Data) => <StoreProductPage data={data} />}
      </LoadGate>
    </div>
  );
}

/** The product page itself - the Store's (`ProductDetailsScreen`), or the Super
 * Admin's preview of it (`preview`): the same page, but nothing to buy (the
 * buttons are drawn, disabled), no related products, inert breadcrumbs, the
 * tab's title left alone and no request of its own. */
export function StoreProductPage({ data, preview: previewProp = false }: { data: PublicStoreProductPage & { preview?: boolean }; preview?: boolean }) {
  const preview = previewProp || data.preview === true;
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
  const [focusBox, setFocusBox] = useState(0);
  const [viewing, setViewing] = useState<number | null>(null);
  const screenshots = p.screenshots ?? [];
  const images = [...(p.banner ? [p.banner] : []), ...screenshots];
  const video = p.video ?? null;
  const highlights = p.highlights ?? [];
  const offers = freeOffers({ plans: data.plans as never, trial: p.trial, status: p.status });
  const about = p.description?.trim() ? p.description : "";
  // The demo has its own section; the gallery is pictures only. With neither, the
  // product's mark stands in - never an invented picture.
  const showGallery = images.length > 0 || !video;

  const sections = [
    { id: "overview", label: "Overview" },
    ...(highlights.length > 0 ? [{ id: "features", label: "Features" }] : []),
    ...(screenshots.length > 0 ? [{ id: "screenshots", label: "Screenshots" }] : []),
    ...(video ? [{ id: "demo", label: "Demo" }] : []),
    ...(!soon ? [{ id: "plans", label: "Plans & Pricing" }] : []),
    { id: "apps", label: "App Availability" },
  ];
  const current = useCurrentSection(sections.map((s) => s.id));

  useEffect(() => {
    if (!preview) document.title = `${p.title} - WorkEasy360 Store`;
  }, [p.title, preview]);

  // In a preview the page sits inside the Super Admin's console: its section links
  // scroll the preview and leave the console's address alone.
  const jump = (e: MouseEvent<HTMLAnchorElement>, id: string) => {
    if (!preview) return;
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView?.({ behavior: scrollBehavior(), block: "start" });
  };

  return (
    <div className={styles.productPage} data-preview={preview || undefined}>
      <nav aria-label="Breadcrumb" className={shell.crumbs}>
        <ol>
          <li>{preview ? <span>Store</span> : <Link href={storePaths.home}>Store</Link>}</li>
          <li>{preview ? <span>Products</span> : <Link href={storePaths.products}>Products</Link>}</li>
          <li aria-current="page">{p.title}</li>
        </ol>
      </nav>

      <header className={styles.detailHead}>
        <ProductTile code={p.code} logo={p.logo?.url ?? null} icon={tileIcon(p)} size={72} />
        <div className={styles.detailHeadText}>
          <h1 className={shell.pageTitle}>{p.title}</h1>
          {(p.summary || p.tagline) && <p className={shell.pageLead}>{p.summary || p.tagline}</p>}
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
          {highlights.length > 0 && (
            <ul className={styles.heroHighlights} aria-label="Key highlights">
              {highlights.slice(0, 4).map((h) => (
                <li key={h}>
                  <Icon name="check" size={16} />
                  <span>{h}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </header>

      <nav className={styles.detailTabs} aria-label="On this page">
        {sections.map((s) => (
          <a key={s.id} href={`#${s.id}`} aria-current={current === s.id ? "location" : undefined} onClick={(e) => jump(e, s.id)}>
            {s.label}
          </a>
        ))}
      </nav>

      <div className={`${styles.detailLayout} ${showGallery ? "" : styles.detailLayoutBare}`}>
        {showGallery && (
          <div className={styles.detailMedia}>
            <Gallery title={p.title} icon={p.icon} logo={p.logo?.url ?? null} images={images} code={p.code} onOpen={setViewing} />
          </div>
        )}
        <PlanBox data={data} period={period} setPeriod={setPeriod} planId={planId} setPlanId={setPlanId} preview={preview} focusSignal={focusBox} />
        <div className={styles.detailMain}>
          <section id="overview" className={styles.detailSection} aria-labelledby="overview-title">
            <h2 id="overview-title" className={shell.sectionTitle}>
              Overview
            </h2>
            <div className={styles.about}>{about ? <MarkdownLite text={about} headingLevel={3} /> : <p>{p.summary || p.tagline}</p>}</div>
          </section>

          {highlights.length > 0 && (
            <section id="features" className={styles.detailSection} aria-labelledby="features-title">
              <h2 id="features-title" className={shell.sectionTitle}>
                Features
              </h2>
              <ul className={styles.featureGrid}>
                {highlights.map((h) => (
                  <li key={h} className={styles.featureCard}>
                    <span className={styles.featureCheck}>
                      <Icon name="check" size={16} />
                    </span>
                    <span>{h}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <Ticks title="Benefits" items={p.benefits} />
          <Ticks title="Use cases" items={p.use_cases} />

          {screenshots.length > 0 && (
            <section id="screenshots" className={styles.detailSection} aria-labelledby="screenshots-title">
              <h2 id="screenshots-title" className={shell.sectionTitle}>
                Screenshots
              </h2>
              <ul className={styles.shotGrid}>
                {screenshots.map((shot, i) => (
                  <li key={`${shot.url}-${i}`}>
                    <button type="button" className={styles.shot} onClick={() => setViewing(i + (p.banner ? 1 : 0))}>
                      <span className={styles.shotFrame}>
                        {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                        <img src={shot.url} alt="" loading="lazy" draggable={false} />
                      </span>
                      <span className={styles.shotCaption}>
                        <span className="w360-visually-hidden">View full screen:</span> {shot.caption || `Screenshot ${i + 1}`}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {video && (
            <section id="demo" className={styles.detailSection} aria-labelledby="demo-title">
              <h2 id="demo-title" className={shell.sectionTitle}>
                Product demo
              </h2>
              {video.title && <h3 className={styles.demoTitle}>{video.title}</h3>}
              {video.description && <p className={styles.demoLead}>{video.description}</p>}
              <div className={styles.demoPlayer}>
                <DemoVideo title={p.title} video={video} fallbackPoster={images[0]?.url ?? null} code={p.code} />
              </div>
            </section>
          )}

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
      </div>

      <PlansSection
        data={data}
        period={period}
        preview={preview}
        onSelect={(id, interval) => {
          setPeriod(interval);
          setPlanId(id);
          setFocusBox((n) => n + 1);
        }}
      />
      <Availability data={data} />
      {!preview && data.related.length > 0 && (
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
      {viewing !== null && images.length > 0 && <Lightbox title={p.title} images={images} start={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}
