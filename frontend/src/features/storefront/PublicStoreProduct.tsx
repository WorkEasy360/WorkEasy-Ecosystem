"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { publicApi, type PublicStoreImage, type PublicStoreProductPage, type PublicStoreVideo } from "@/lib/api/public";
import { LoadGate, money, useLoad } from "@/features/controlplane/ui";
import { ProductMark } from "@/features/controlplane/productmanager/ProductMark";
import { MarkdownLite } from "@/features/console/MarkdownLite";
import { usePortalRouter } from "@/lib/portalRouter";
import { MAX_INTENT_QUANTITY } from "@/features/site/SiteShell";
import { PlanComparison, comparisonCell, type ComparablePlan } from "./PlanComparison";
import { BADGE_LABEL, PER, StoreCard, storeDetailsClass, taxNote } from "./StoreBrowser";
import { AddToCart, PublicCartBar, useStoreBuy } from "./storeBuying";
import { productHref } from "./PublicStore";
import { StoreIcon } from "./storeIcons";
import browse from "./StoreBrowser.module.css";
import styles from "./PublicStore.module.css";

/**
 * `/store/product/<slug>` - one product of the public Store (2026-11-02,
 * laid out as the owner's checkout mock-up on 2026-11-03): on the left the
 * product - its pictures, what it is, its features and benefits; on the right
 * the checkout box - choose a plan and billing period, the quantity, an order
 * summary and "Proceed to secure checkout". Below, the plan comparison with
 * Buy now / Add to cart / Start free trial on every plan, and related products.
 *
 * Every price and plan is the server's (`GET public/store/products/<slug>`).
 * The box's total is only an estimate from those prices: buying is
 * `useStoreBuy`'s, which hands identifiers (plan, period, quantity) to the
 * organization's checkout, and that checkout prices the order again.
 */

type Data = PublicStoreProductPage;
type Plan = Data["plans"][number];

const PERIOD: Record<string, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  semi_annual: "6 months",
  annual: "Yearly",
};

const TERM: Record<string, string> = {
  monthly: "1 month",
  quarterly: "3 months",
  semi_annual: "6 months",
  annual: "1 year",
};

const MAX_QTY = Math.min(999, MAX_INTENT_QUANTITY);

/** A price as the page shows it: whole amounts without the ".00" (₹8,399), others as `money`. */
function amount(minor: number, currency: string): string {
  if (currency === "JPY" || minor % 100 !== 0) return money(minor, currency);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(minor / 100);
  } catch {
    return money(minor, currency);
  }
}

/** The plan's price for one billing period in the page's currency, if it has one. */
function priceOf(plan: Plan, interval: string, currency: string | null) {
  const price = plan.prices.find((x) => x.interval === interval && x.amount_minor !== null && (!currency || x.currency === currency));
  return price && price.amount_minor !== null ? { ...price, amount_minor: price.amount_minor } : undefined;
}

function Ticks({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className={styles.tickSection}>
      <h2 className={styles.sectionHeading}>{title}</h2>
      <ul className={styles.ticks}>
        {items.map((item) => (
          <li key={item}>
            <span className={styles.tick} aria-hidden="true">
              <StoreIcon name="check" size={14} />
            </span>
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

type Slide = { kind: "video"; video: PublicStoreVideo } | { kind: "image"; image: PublicStoreImage };

function PlayMark({ size }: { size: number }) {
  return (
    <span className={styles.playMark} style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 24 24" width={size * 0.42} height={size * 0.42}>
        <path d="M8 5.5v13l11-6.5-11-6.5Z" fill="currentColor" />
      </svg>
    </span>
  );
}

/** The player's address: the server's fixed one, asked to start at once (the
 * visitor has just pressed Play) and, on YouTube, to suggest only this channel. */
function playerSrc(video: Extract<PublicStoreVideo, { embed_url: string }>): string {
  return `${video.embed_url}?${video.provider === "youtube" ? "autoplay=1&rel=0&modestbranding=1" : "autoplay=1&dnt=1"}`;
}

/** The product's video and pictures: a large stage, and thumbnails that choose
 * what it shows. The video comes first; its player loads only when the visitor
 * presses Play, so a page view never contacts YouTube or Vimeo by itself. */
function Gallery({
  title,
  icon,
  logo,
  images,
  video,
  code,
}: {
  title: string;
  icon: string;
  logo: string | null;
  images: PublicStoreImage[];
  video: PublicStoreVideo | null;
  code: string;
}) {
  const slides: Slide[] = [...(video ? [{ kind: "video" as const, video }] : []), ...images.map((image) => ({ kind: "image" as const, image }))];
  const [shown, setShown] = useState(0);
  const [playing, setPlaying] = useState(false);
  const current = slides[Math.min(shown, slides.length - 1)];
  const poster = images[0];
  // A picture fills the stage edge to edge, at its own shape kept between 3:2 and 8:3; a video is 16:9.
  const ratio =
    current?.kind === "video"
      ? 16 / 9
      : current && current.image.width > 0 && current.image.height > 0
        ? Math.min(8 / 3, Math.max(3 / 2, current.image.width / current.image.height))
        : undefined;
  const choose = (i: number) => {
    setShown(i);
    setPlaying(false);
  };
  const caption = current?.kind === "image" ? current.image.caption : "";

  return (
    <div className={styles.gallery}>
      <div
        className={`${styles.stage} ${current ? styles.stageFilled : toneClass(code)} ${current?.kind === "video" ? styles.stageVideo : ""}`}
        style={ratio ? { aspectRatio: String(ratio) } : undefined}
      >
        {current?.kind === "video" ? (
          playing && current.video.provider === "upload" ? (
            <video className={styles.player} src={current.video.url} controls autoPlay playsInline preload="auto" aria-label={`${title} - video`}>
              <a href={current.video.url}>Download the video</a>
            </video>
          ) : playing && current.video.provider !== "upload" ? (
            <iframe
              className={styles.player}
              src={playerSrc(current.video)}
              title={`${title} - video`}
              allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
              sandbox="allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox"
            />
          ) : (
            <button type="button" className={`${styles.poster} ${poster ? "" : toneClass(code)}`} onClick={() => setPlaying(true)} aria-label={`Play video: ${title}`}>
              {poster ? (
                <>
                  {/* The picture whole, over a blurred copy that fills the 16:9 stage. */}
                  {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                  <img className={styles.posterFill} src={poster.url} alt="" />
                  {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                  <img className={styles.posterImage} src={poster.url} alt="" />
                </>
              ) : (
                <span className={styles.posterName}>{title}</span>
              )}
              <span className={styles.posterPlay}>
                <PlayMark size={76} />
                <span className={styles.posterLabel}>Watch video</span>
              </span>
            </button>
          )
        ) : current ? (
          // eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image
          <img className={styles.stageImage} src={current.image.url} alt={current.image.caption || title} width={current.image.width} height={current.image.height} />
        ) : (
          <div className={styles.stageMark}>
            <ProductMark name={title} icon={icon} src={logo} size={72} />
            <span className={styles.stageName}>{title}</span>
          </div>
        )}
      </div>
      {caption && <p className={styles.stageCaption}>{caption}</p>}
      {slides.length > 1 && (
        <ul className={styles.thumbs} aria-label="Video and pictures">
          {slides.map((slide, i) => (
            <li key={slide.kind === "video" ? "video" : slide.image.url}>
              <button
                type="button"
                className={styles.thumb}
                aria-pressed={i === shown}
                aria-label={
                  slide.kind === "video"
                    ? "Show the video"
                    : `Show picture ${video ? i : i + 1}${slide.image.caption ? `: ${slide.image.caption}` : ""}`
                }
                onClick={() => choose(i)}
              >
                {slide.kind === "video" ? (
                  <span className={`${styles.thumbVideo} ${poster ? "" : toneClass(code)}`}>
                    {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                    {poster && <img src={poster.url} alt="" />}
                    <PlayMark size={34} />
                  </span>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image
                  <img src={slide.image.url} alt="" />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const TONES = [styles.toneBlue, styles.toneGreen, styles.toneOrange, styles.tonePurple, styles.toneTeal];

function toneClass(code: string): string {
  // FNV-1a: similar codes (easy_crm, easy_project) still land on different colours.
  let hash = 2166136261;
  for (const ch of code) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619) >>> 0;
  return TONES[hash % TONES.length];
}

/** The checkout box: plan, period, quantity, the order summary and the way to pay. */
function CheckoutBox({
  data,
  busy,
  signedIn,
  buy,
  onCurrency,
}: {
  data: Data;
  busy: boolean;
  signedIn: boolean;
  buy: ReturnType<typeof useStoreBuy>["buy"];
  onCurrency: (currency: string) => void;
}) {
  const p = data.product;
  const ids = { plans: useId(), qty: useId(), currency: useId(), summary: useId() };
  const soon = p.status === "coming_soon";
  const buyable = data.plans.filter((plan) => plan.kind === "standard" && plan.billing_type !== "free_trial");
  const periods = [...new Set(buyable.flatMap((plan) => plan.prices.filter((x) => x.amount_minor !== null).map((x) => x.interval)))].filter(
    (i) => i !== "one_time",
  );
  const startPeriod = p.price && periods.includes(p.price.interval) ? p.price.interval : (periods[0] ?? "monthly");
  const [period, setPeriod] = useState(startPeriod);
  const inPeriod = buyable.filter((plan) => priceOf(plan, period, data.currency));
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [qty, setQty] = useState(1);

  const fallback = inPeriod.find((plan) => plan.recommended) ?? inPeriod.find((plan) => plan.name === p.price?.plan_name) ?? inPeriod[0];
  const plan = inPeriod.find((x) => x.plan_version_id === chosenId) ?? fallback;
  const price = plan ? priceOf(plan, period, data.currency) : undefined;
  const trialPlan = !soon && p.trial.available ? data.plans.find((x) => p.trial.plan_codes.includes(x.plan_code)) : undefined;
  const discounted = price && price.list_amount_minor != null && price.list_amount_minor > price.amount_minor;
  const setup = price?.setup_fee_minor ?? 0;
  const subtotal = price ? price.amount_minor * qty : 0;
  const per = PER[period] ?? "";
  const setQuantity = (n: number) => setQty(Math.max(1, Math.min(MAX_QTY, Number.isFinite(n) ? Math.trunc(n) : 1)));

  return (
    <aside className={styles.checkout} aria-label={`Buy ${p.title}`}>
      {discounted && price && (
        <p className={styles.offer}>
          <StoreIcon name="gift" size={22} />
          Special offer - save {Math.round((price.discount_bp ?? 0) / 100)}%
        </p>
      )}
      <div className={styles.checkoutBody}>
        {soon ? (
          <p className={styles.bigPrice}>Coming soon</p>
        ) : price ? (
          <div className={styles.priceRow}>
            <div className={styles.priceMain}>
              {discounted && <span className={styles.offPill}>{Math.round((price.discount_bp ?? 0) / 100)}% OFF</span>}
              <div>
                {discounted && (
                  <s className={styles.listPrice} aria-label={`instead of ${amount(price.list_amount_minor!, price.currency)}`}>
                    {amount(price.list_amount_minor!, price.currency)}
                    {per}
                  </s>
                )}
                <p className={styles.bigPrice}>
                  {amount(price.amount_minor, price.currency)}
                  <span className={styles.bigPer}>{per}</span> <span className={styles.taxNote}>{taxNote(price.currency)}</span>
                </p>
              </div>
            </div>
            {discounted && (
              <p className={styles.youSave}>
                You save
                <strong>{amount((price.list_amount_minor! - price.amount_minor) * qty, price.currency)}</strong>
              </p>
            )}
          </div>
        ) : (
          <p className={styles.bigPrice}>{trialPlan ? "Free trial" : "Contact sales"}</p>
        )}

        {!soon && periods.length > 1 && (
          <div className={styles.periods} role="group" aria-label="Billing period">
            {periods.map((i) => (
              <button key={i} type="button" className={styles.period} aria-pressed={period === i} onClick={() => setPeriod(i)}>
                {PERIOD[i] ?? i}
              </button>
            ))}
          </div>
        )}

        {!soon && inPeriod.length > 0 && plan && price && (
          <>
            <fieldset className={styles.planChoice}>
              <legend id={ids.plans} className={styles.boxHeading}>
                Choose a plan
              </legend>
              {inPeriod.map((x) => {
                const xp = priceOf(x, period, data.currency)!;
                const id = `${ids.plans}-${x.plan_version_id}`;
                return (
                  <label key={x.plan_version_id} htmlFor={id} className={styles.planOption}>
                    <input
                      id={id}
                      type="radio"
                      name={ids.plans}
                      value={x.plan_version_id}
                      checked={x.plan_version_id === plan.plan_version_id}
                      onChange={() => setChosenId(x.plan_version_id)}
                    />
                    <span className={styles.planText}>
                      <span className={styles.planName}>
                        {x.name}
                        {x.recommended && <span className={styles.recommended}>Recommended</span>}
                      </span>
                      {x.caption && <span className={styles.planCaption}>{x.caption}</span>}
                    </span>
                    <span className={styles.planPrice}>
                      {amount(xp.amount_minor, xp.currency)}
                      <span className={styles.planPer}>{per}</span>
                    </span>
                  </label>
                );
              })}
            </fieldset>

            <div className={styles.qtyRow}>
              <label htmlFor={ids.qty} className={styles.boxHeading}>
                Quantity
              </label>
              <div className={styles.stepper}>
                <button type="button" aria-label="Decrease quantity" disabled={qty <= 1} onClick={() => setQuantity(qty - 1)}>
                  −
                </button>
                <input
                  id={ids.qty}
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={MAX_QTY}
                  value={qty}
                  onChange={(e) => setQuantity(Number(e.target.value))}
                />
                <button type="button" aria-label="Increase quantity" disabled={qty >= MAX_QTY} onClick={() => setQuantity(qty + 1)}>
                  +
                </button>
              </div>
            </div>

            <section className={styles.summary} aria-labelledby={ids.summary}>
              <h2 id={ids.summary} className={styles.boxHeading}>
                Order summary
              </h2>
              <dl className={styles.summaryRows}>
                <div>
                  <dt>
                    {p.title} - {plan.name} ({TERM[period] ?? PERIOD[period] ?? period})
                    {qty > 1 ? ` × ${qty}` : ""}
                  </dt>
                  <dd>{amount(subtotal, price.currency)}</dd>
                </div>
                {setup > 0 && (
                  <div>
                    <dt>One-time set-up fee</dt>
                    <dd>{amount(setup, price.currency)}</dd>
                  </div>
                )}
                <div>
                  <dt>{price.currency === "INR" ? "GST" : "Tax"}</dt>
                  <dd className={styles.muted}>As applicable</dd>
                </div>
              </dl>
              <p className={styles.total}>
                <span>Estimated total</span>
                <strong>{amount(subtotal + setup, price.currency)}</strong>
              </p>
            </section>

            <button
              type="button"
              className={styles.payButton}
              disabled={busy}
              onClick={() => void buy({ plan: plan.plan_version_id, interval: period, trial: false, quantity: qty > 1 ? qty : undefined })}
            >
              <StoreIcon name="lock" size={18} />
              Proceed to Secure Checkout
              <StoreIcon name="arrow" size={18} />
            </button>
          </>
        )}

        <div className={styles.secondaryActions}>
          {!soon && plan && price && (
            <AddToCart
              planVersionId={plan.plan_version_id}
              productCode={p.code}
              productName={p.title}
              planName={plan.name}
              interval={period}
              label={p.title}
            />
          )}
          {trialPlan && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={busy}
              aria-label={`Start free trial: ${p.title}`}
              onClick={() => void buy({ productTrial: { product: p.code, plan: trialPlan.plan_code } })}
            >
              Start {p.trial.duration_days ? `${p.trial.duration_days}-day ` : ""}free trial
            </button>
          )}
          {!soon && !price && !trialPlan && (
            <Link href="/contact" className="w360-btn w360-btn-primary">
              Contact sales
            </Link>
          )}
        </div>

        <ul className={styles.assurances} aria-label="Why buy here">
          <li>
            <StoreIcon name="shield" size={22} />
            Secure checkout
          </li>
          <li>
            <StoreIcon name="bolt" size={22} />
            Instant activation
          </li>
          <li>
            <StoreIcon name="headset" size={22} />
            Expert support
          </li>
        </ul>

        <p className={styles.boxHint}>
          {signedIn
            ? "You are signed in - your checkout opens next, and confirms the final amount before you pay."
            : "You will create an account (or sign in) next. Your checkout confirms the final amount before you pay."}
        </p>
        {data.currencies.length > 1 && (
          <div className={styles.currency}>
            <label htmlFor={ids.currency}>Currency</label>
            <select id={ids.currency} className="w360-input" value={data.currency ?? ""} onChange={(e) => onCurrency(e.target.value)}>
              {data.currencies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
    </aside>
  );
}

export function PublicStoreProductScreen({ slug }: { slug: string }) {
  const [currency, setCurrency] = useState<string | undefined>(undefined);
  const [load] = useLoad(() => publicApi.storeProduct(slug, currency), [slug, currency]);
  const { buy, busy, signedIn, dialog } = useStoreBuy();
  const router = usePortalRouter();

  return (
    <div className={`${styles.wrap} ${styles.productWrap}`}>
      <PublicCartBar onCheckout={(intent) => void buy(intent)} busy={busy} />
      <LoadGate load={load} what="this product">
        {(data: Data) => {
          const p = data.product;
          const soon = p.status === "coming_soon";
          const images = [...(p.banner ? [p.banner] : []), ...p.screenshots];
          const standard = data.plans.filter((x) => x.kind === "standard");
          // One plan: the checkout box already sells it, so its features read as a list
          // ("What's included") instead of a one-column comparison table.
          const onlyPlan = standard.length === 1 ? standard[0] : undefined;
          const included = onlyPlan
            ? data.comparison.flatMap((row) => {
                const value = row.values[onlyPlan.plan_version_id];
                if (value === undefined || value === null || value === false) return [];
                return [value === true ? row.label : `${row.label}: ${comparisonCell(row, value)}`];
              })
            : [];

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
              <nav aria-label="Breadcrumb" className={styles.crumbs}>
                <ol>
                  <li>
                    <Link href="/" aria-label="Home">
                      <StoreIcon name="home" size={16} />
                    </Link>
                  </li>
                  <li>
                    <Link href="/store">Store</Link>
                  </li>
                  {p.category && <li>{p.category}</li>}
                  {p.subcategory && <li>{p.subcategory}</li>}
                  <li aria-current="page">{p.title}</li>
                </ol>
              </nav>

              <div className={styles.productLayout}>
                <div className={styles.productMain}>
                  <header className={styles.productHead}>
                    <ProductMark name={p.title} icon={p.icon} src={p.logo?.url ?? null} size={72} />
                    <div className={styles.productHeadText}>
                      <div className={styles.productTitleRow}>
                        <h1 className={styles.productTitle}>{p.title}</h1>
                        {(p.subcategory || p.category) && <span className={styles.productPill}>{p.subcategory || p.category}</span>}
                      </div>
                      <p className={styles.productSummary}>{p.summary || p.tagline}</p>
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
                    </div>
                  </header>

                  <Gallery title={p.title} icon={p.icon} logo={p.logo?.url ?? null} images={images} video={p.video ?? null} code={p.code} />

                  <section>
                    <h2 className={styles.sectionHeading}>About {p.title}</h2>
                    <div className={styles.about}>{p.description ? <MarkdownLite text={p.description} /> : <p>{p.summary || p.tagline}</p>}</div>
                  </section>
                  <Ticks title="Key features" items={p.highlights} />
                  {!soon && <Ticks title="What's included" items={included} />}
                  <Ticks title="Key benefits" items={p.benefits} />
                  <Ticks title="Use cases" items={p.use_cases} />

                  <ul className={styles.trustStrip} aria-label="Our promise">
                    <li>
                      <StoreIcon name="shield" size={22} />
                      Secure payments
                    </li>
                    <li>
                      <StoreIcon name="bolt" size={22} />
                      Instant activation
                    </li>
                    <li>
                      <StoreIcon name="headset" size={22} />
                      Expert support
                    </li>
                    {p.trial.available && (
                      <li>
                        <StoreIcon name="gift" size={22} />
                        Free trial available
                      </li>
                    )}
                  </ul>
                </div>

                <CheckoutBox data={data} busy={busy} signedIn={signedIn} buy={buy} onCurrency={setCurrency} />
              </div>

              {!soon && !onlyPlan && (
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
                  <h2 id="related-title" className={styles.sectionHeading}>
                    Related products
                  </h2>
                  <ul className={browse.grid}>
                    {data.related.map((r) => (
                      <StoreCard
                        key={r.code}
                        code={r.code}
                        title={r.title}
                        href={productHref(r.slug)}
                        icon={r.icon}
                        logoSrc={null}
                        bannerSrc={null}
                        category={r.category}
                        onCategory={() => router.push("/store")}
                        summary={r.summary}
                        price={r.price}
                        noPrice="See details"
                        aside={
                          <Link href={productHref(r.slug)} className={storeDetailsClass} aria-label={`View details: ${r.title}`}>
                            View Details <StoreIcon name="arrow" size={14} />
                          </Link>
                        }
                      />
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
