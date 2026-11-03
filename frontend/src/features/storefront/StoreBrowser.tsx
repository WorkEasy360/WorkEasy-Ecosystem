"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api/client";
import { describeError, money } from "@/features/controlplane/ui";
import { ProductMark } from "@/features/controlplane/productmanager/ProductMark";
import styles from "./StoreBrowser.module.css";

/**
 * The Store's marketplace page, shared by every portal - the Super Admin's
 * catalog, a customer organization's, an affiliate's, a reseller's and a
 * white-label partner's: a filter panel on the left (category, program, plan
 * type, status, price range), a toolbar (search, currency, sort), the filters
 * in use as removable chips, and a grid of product cards.
 *
 * It sends each filter to the portal's own endpoint exactly as chosen and
 * draws what comes back - which products match, the counts beside each
 * filter, the order and the prices - computing none of it. What a card says
 * and what its buttons do is the portal's (`renderCard`); who may see which
 * product is the server's answer, never this page's.
 */

export interface StoreFacet {
  value: string;
  label: string;
  count: number;
}

export interface StorePrice {
  amount_minor: number;
  currency: string;
  interval: string;
  /** ADR-056: the price before a promotional discount, when there is one. */
  list_amount_minor?: number | null;
  discount_bp?: number;
}

/** ADR-056: the curated collections a product belongs to. */
export const BADGE_LABEL: Record<string, string> = { featured: "Featured", popular: "Popular", new: "New" };

export interface StoreDeal {
  amount_minor: number;
  compare_at_minor: number;
  currency: string;
  save_percent: number;
}

export interface StoreBrowsePage<C> {
  currency: string | null;
  currencies: string[];
  count: number;
  total: number;
  products: C[];
  facets: {
    categories: StoreFacet[];
    statuses: StoreFacet[];
    plan_types: StoreFacet[];
    /** The Super Admin's channel filter; other portals have none. */
    programs?: StoreFacet[];
    /** ADR-056: Featured / Popular / New. */
    collections?: StoreFacet[];
    price: { min_minor: number | null; max_minor: number | null };
  };
  sorts: { value: string; label: string }[];
}

export interface StoreQuery {
  q?: string;
  category?: string;
  program?: string;
  status?: string;
  plan_type?: string;
  price_min?: string;
  price_max?: string;
  currency?: string;
  sort?: string;
  collection?: string;
}

export const PER: Record<string, string> = {
  monthly: "/month",
  quarterly: "/quarter",
  semi_annual: "/6 months",
  annual: "/year",
  one_time: " one-time",
};

export type BadgeTone = "new" | "trial" | "soon" | "quiet" | "archived" | "success" | "warning";

const BADGE: Record<BadgeTone, string> = {
  new: styles.badgeNew,
  trial: styles.badgeTrial,
  soon: styles.badgeSoon,
  quiet: styles.badgeQuiet,
  archived: styles.badgeArchived,
  success: styles.badgeNew,
  warning: styles.badgeWarning,
};

const TONES = [styles.toneBlue, styles.toneGreen, styles.toneOrange, styles.tonePurple, styles.toneRed];

/** A product without a banner gets a steady colour of its own. */
function toneOf(code: string): string {
  let hash = 0;
  for (const ch of code) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return TONES[hash % TONES.length];
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

const labelOf = (facets: StoreFacet[], value: string) => facets.find((f) => f.value === value)?.label ?? value;

/** The plan types a card is bought in, by the page's own labels. */
export function planTypeLabels(types: string[], facets: StoreFacet[]): string[] {
  return types.map((t) => labelOf(facets, t));
}

// --- one card ----------------------------------------------------------------------------

export interface StoreCardProps {
  code: string;
  title: string;
  /** Where the title links to, if anywhere. */
  href?: string;
  icon: string;
  logoSrc: string | null;
  bannerSrc: string | null;
  ribbon?: { label: string; tone: BadgeTone } | null;
  category: string;
  onCategory: (category: string) => void;
  summary: string;
  /** ADR-056 collections the product is in (featured, popular, new). */
  badges?: string[];
  /** Short tags under the summary: where it is sold, how it is billed. */
  chips?: { label: string; trial?: boolean }[];
  chipsLabel?: string;
  /** Portal facts under the tags (an access status, what a sale earns). */
  facts?: ReactNode;
  price: StorePrice | null;
  deal?: StoreDeal | null;
  /** What the price block says when there is no price. */
  noPrice: string;
  /** One action beside the price (Manage). */
  aside?: ReactNode;
  /** The card's buttons, in a row under the price. */
  actions?: ReactNode;
}

export function StoreCard(props: StoreCardProps) {
  const { code, title, href, icon, logoSrc, bannerSrc, ribbon, category, onCategory, summary, chips = [], price, deal, badges = [] } = props;
  const shown = badges.filter((b) => BADGE_LABEL[b] && !(b === "new" && ribbon?.tone === "new"));
  return (
    <li className={styles.card}>
      <div className={`${styles.media} ${bannerSrc ? "" : toneOf(code)}`}>
        {bannerSrc ? (
          // eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image
          <img className={styles.banner} src={bannerSrc} alt="" />
        ) : (
          <ProductMark name={title} icon={icon} src={logoSrc} size={72} />
        )}
        {ribbon && <span className={`${styles.badge} ${BADGE[ribbon.tone]}`}>{ribbon.label}</span>}
      </div>

      <div className={styles.body}>
        <div className={styles.titleRow}>
          {/* Without a banner the picture already is the product's mark. */}
          {bannerSrc && <ProductMark name={title} icon={icon} src={logoSrc} size={40} />}
          <div className={styles.titleText}>
            <h3 className={styles.title}>{href ? <Link href={href}>{title}</Link> : title}</h3>
            {category ? (
              <button type="button" className={styles.category} onClick={() => onCategory(category)} aria-label={`Show ${category} products`}>
                {category}
              </button>
            ) : (
              <span className={styles.categoryNone}>No category</span>
            )}
          </div>
        </div>
        {shown.length > 0 && (
          <ul className={styles.chips} aria-label="Collections">
            {shown.map((b) => (
              <li key={b} className={`${styles.chip} ${styles.chipTrial}`}>
                {BADGE_LABEL[b]}
              </li>
            ))}
          </ul>
        )}
        <p className={summary ? styles.summary : `${styles.summary} ${styles.summaryEmpty}`}>{summary || "No short description yet."}</p>
        {chips.length > 0 && (
          <ul className={styles.chips} aria-label={props.chipsLabel ?? "Plans"}>
            {chips.map((c) => (
              <li key={c.label} className={c.trial ? `${styles.chip} ${styles.chipTrial}` : styles.chip}>
                {c.label}
              </li>
            ))}
          </ul>
        )}
        {props.facts && <div className={styles.facts}>{props.facts}</div>}
      </div>

      <div className={styles.footer}>
        <div className={styles.priceBlock}>
          {price ? (
            <>
              <span className={styles.priceLabel}>From</span>
              <p className={styles.price}>
                <strong className={styles.amount}>{money(price.amount_minor, price.currency)}</strong>
                <span className={styles.per}>{PER[price.interval] ?? ` ${price.interval}`}</span>
              </p>
              {price.list_amount_minor != null && price.list_amount_minor > price.amount_minor && (
                <p className={styles.deal}>
                  <s className={styles.compare} aria-label={`instead of ${money(price.list_amount_minor, price.currency)}`}>
                    {money(price.list_amount_minor, price.currency)}
                  </s>{" "}
                  <span className={styles.save}>{Math.round((price.discount_bp ?? 0) / 100)}% off</span>
                </p>
              )}
              {deal && (
                <p className={styles.deal}>
                  {money(deal.amount_minor, deal.currency)}/year{" "}
                  <s className={styles.compare} aria-label={`instead of ${money(deal.compare_at_minor, deal.currency)}`}>
                    {money(deal.compare_at_minor, deal.currency)}
                  </s>{" "}
                  <span className={styles.save}>Save {deal.save_percent}%</span>
                </p>
              )}
            </>
          ) : (
            <>
              <span className={styles.priceLabel}>Price</span>
              <p className={styles.noPrice}>{props.noPrice}</p>
            </>
          )}
        </div>
        {props.aside}
      </div>
      {props.actions && <div className={styles.actions}>{props.actions}</div>}
    </li>
  );
}

// --- the page -----------------------------------------------------------------------------

interface Filters {
  collection: string;
  category: string;
  program: string;
  status: string[];
  planType: string[];
  priceMin: string;
  priceMax: string;
  currency: string;
  sort: string;
}

const START: Filters = {
  collection: "all",
  category: "",
  program: "all",
  status: [],
  planType: [],
  priceMin: "",
  priceMax: "",
  currency: "",
  sort: "recommended",
};

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function Checks({
  legend,
  facets,
  chosen,
  onToggle,
}: {
  legend: string;
  facets: StoreFacet[];
  chosen: string[];
  onToggle: (value: string) => void;
}) {
  return (
    <fieldset className={styles.options}>
      <legend className="w360-visually-hidden">{legend}</legend>
      {facets.map((f) => (
        <label key={f.value} className={styles.option}>
          <input type="checkbox" checked={chosen.includes(f.value)} onChange={() => onToggle(f.value)} />
          <span className={styles.optionLabel}>{f.label}</span>
          <span className={styles.count}>{f.count}</span>
        </label>
      ))}
    </fieldset>
  );
}

function Skeleton() {
  return (
    <ul className={styles.grid} aria-hidden="true">
      {Array.from({ length: 6 }, (_, i) => (
        <li key={i} className={`${styles.card} ${styles.skeleton}`}>
          <div className={styles.media} />
          <div className={styles.body}>
            <span className={styles.skeletonLine} />
            <span className={`${styles.skeletonLine} ${styles.skeletonShort}`} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export interface StoreBrowserProps<C, P extends StoreBrowsePage<C>> {
  title: string;
  lead: string;
  /** Buttons beside the title. */
  headActions?: ReactNode;
  /** Fetches one page for these filters - the portal's own endpoint. */
  load: (query: StoreQuery) => Promise<P>;
  /** Changes when the page must be fetched again (a portal, an action done). */
  loadKey: string;
  /** The status filter's heading ("Status", "Access"). */
  statusTitle?: string;
  renderCard: (card: C, page: P, onCategory: (category: string) => void) => ReactNode;
  /** Notices and forms drawn above the toolbar, from the page. */
  above?: (page: P) => ReactNode;
  /** What an empty Store says, and offers. */
  nothingYet: { title: string; action?: ReactNode };
  /** The refusal shown when the endpoint says no. */
  forbidden: string;
}

type Result<P> = { stamp: string; page?: P; error?: string; forbidden?: boolean };

export function StoreBrowser<C, P extends StoreBrowsePage<C>>({
  title,
  lead,
  headActions,
  load,
  loadKey,
  statusTitle = "Status",
  renderCard,
  above,
  nothingYet,
  forbidden,
}: StoreBrowserProps<C, P>) {
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 250);
  const [f, setF] = useState<Filters>(START);
  const [typedMin, setTypedMin] = useState("");
  const [typedMax, setTypedMax] = useState("");
  const [result, setResult] = useState<Result<P> | null>(null);
  const ids = { search: useId(), sort: useId(), currency: useId(), count: useId(), min: useId(), max: useId(), cat: useId(), program: useId(), collection: useId() };

  const query: StoreQuery = {
    q: q || undefined,
    category: f.category || undefined,
    program: f.program === "all" ? undefined : f.program,
    status: f.status.join(",") || undefined,
    plan_type: f.planType.join(",") || undefined,
    price_min: f.priceMin || undefined,
    price_max: f.priceMax || undefined,
    currency: f.currency || undefined,
    sort: f.sort === "recommended" ? undefined : f.sort,
    collection: f.collection === "all" ? undefined : f.collection,
  };
  const stamp = `${loadKey}|${JSON.stringify(query)}`;

  // The latest `load` is used for the latest stamp; the stamp alone decides when.
  // (Effects run in order, so the ref is current before the fetch below.)
  const loader = useRef(load);
  useEffect(() => {
    loader.current = load;
  });
  useEffect(() => {
    let live = true;
    const asked = JSON.parse(stamp.slice(stamp.indexOf("|") + 1)) as StoreQuery;
    loader.current(asked).then(
      (page) => {
        if (live) setResult({ stamp, page });
      },
      (err: unknown) => {
        if (!live) return;
        const refused = err instanceof ApiError && (err.status === 403 || err.status === 404);
        // A refused filter keeps the last page on screen beside its message.
        setResult((prev) => ({ stamp, page: prev?.page, error: describeError(err), forbidden: refused }));
      },
    );
    return () => {
      live = false;
    };
  }, [stamp]);

  const set = (changes: Partial<Filters>) => setF((prev) => ({ ...prev, ...changes }));
  const clear = () => {
    setSearch("");
    setTypedMin("");
    setTypedMax("");
    setF((prev) => ({ ...START, currency: prev.currency, sort: prev.sort }));
  };
  const onCategory = (category: string) => set({ category });

  const page = result?.page;
  const pending = result?.stamp !== stamp;

  /** The filters in force, each removable on its own. */
  function chips(data: P): { key: string; label: string; remove: () => void }[] {
    const out: { key: string; label: string; remove: () => void }[] = [];
    if (q) out.push({ key: "q", label: `“${q}”`, remove: () => setSearch("") });
    if (f.collection !== "all" && data.facets.collections) {
      out.push({ key: "collection", label: labelOf(data.facets.collections, f.collection), remove: () => set({ collection: "all" }) });
    }
    if (f.category) out.push({ key: "category", label: f.category, remove: () => set({ category: "" }) });
    if (f.program !== "all" && data.facets.programs) {
      out.push({ key: "program", label: labelOf(data.facets.programs, f.program), remove: () => set({ program: "all" }) });
    }
    for (const s of f.status) {
      out.push({ key: `status-${s}`, label: labelOf(data.facets.statuses, s), remove: () => set({ status: toggle(f.status, s) }) });
    }
    for (const t of f.planType) {
      out.push({ key: `plan-${t}`, label: labelOf(data.facets.plan_types, t), remove: () => set({ planType: toggle(f.planType, t) }) });
    }
    if (f.priceMin || f.priceMax) {
      const range = f.priceMin && f.priceMax ? `${f.priceMin}–${f.priceMax}` : f.priceMin ? `${f.priceMin} or more` : `up to ${f.priceMax}`;
      out.push({
        key: "price",
        label: `Price ${range}${data.currency ? ` ${data.currency}` : ""}`,
        remove: () => {
          setTypedMin("");
          setTypedMax("");
          set({ priceMin: "", priceMax: "" });
        },
      });
    }
    return out;
  }

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <div>
          <h1 className={styles.pageTitle}>{title}</h1>
          <p className={styles.lead}>{lead}</p>
        </div>
        {headActions && <div className={styles.headActions}>{headActions}</div>}
      </div>

      {result?.forbidden && !page ? (
        <p role="alert">{forbidden}</p>
      ) : !page && result?.error ? (
        <p role="alert">{result.error}</p>
      ) : (
        <>
          {page && above && <div className={styles.above}>{above(page)}</div>}
          <div className={styles.layout}>
            <aside className={styles.panel} aria-label="Store filters">
              {page ? (
                <>
                  {page.facets.collections && (
                    <section className={styles.section} aria-labelledby={ids.collection}>
                      <h2 id={ids.collection} className={styles.sectionTitle}>
                        Collections
                      </h2>
                      <ul className={styles.categories}>
                        {page.facets.collections.map((c) => (
                          <li key={c.value}>
                            <button type="button" className={styles.categoryItem} aria-pressed={f.collection === c.value} onClick={() => set({ collection: c.value })}>
                              <span>{c.label}</span> <span className={styles.count}>{c.count}</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}

                  <section className={styles.section} aria-labelledby={ids.cat}>
                    <h2 id={ids.cat} className={styles.sectionTitle}>
                      Category
                    </h2>
                    <ul className={styles.categories}>
                      <li>
                        <button type="button" className={styles.categoryItem} aria-pressed={!f.category} onClick={() => set({ category: "" })}>
                          <span>All categories</span>
                        </button>
                      </li>
                      {page.facets.categories.map((c) => (
                        <li key={c.value}>
                          <button
                            type="button"
                            className={styles.categoryItem}
                            aria-pressed={f.category === c.value}
                            onClick={() => set({ category: f.category === c.value ? "" : c.value })}
                          >
                            <span>{c.label}</span> <span className={styles.count}>{c.count}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>

                  {page.facets.programs && (
                    <section className={styles.section} aria-labelledby={ids.program}>
                      <h2 id={ids.program} className={styles.sectionTitle}>
                        Program
                      </h2>
                      <fieldset className={styles.options}>
                        <legend className="w360-visually-hidden">Program</legend>
                        {page.facets.programs.map((p) => (
                          <label key={p.value} className={styles.option}>
                            <input type="radio" name="store-program" value={p.value} checked={f.program === p.value} onChange={() => set({ program: p.value })} />
                            <span className={styles.optionLabel}>{p.label}</span>
                            <span className={styles.count}>{p.count}</span>
                          </label>
                        ))}
                      </fieldset>
                    </section>
                  )}

                  <details className={styles.fold}>
                    <summary>Plan type</summary>
                    <Checks legend="Plan type" facets={page.facets.plan_types} chosen={f.planType} onToggle={(v) => set({ planType: toggle(f.planType, v) })} />
                  </details>
                  <details className={styles.fold}>
                    <summary>{statusTitle}</summary>
                    <Checks legend={statusTitle} facets={page.facets.statuses} chosen={f.status} onToggle={(v) => set({ status: toggle(f.status, v) })} />
                  </details>
                  <details className={styles.fold}>
                    <summary>Price range</summary>
                    <form
                      className={styles.priceForm}
                      onSubmit={(e) => {
                        e.preventDefault();
                        set({ priceMin: typedMin.trim(), priceMax: typedMax.trim() });
                      }}
                    >
                      <div>
                        <label htmlFor={ids.min}>Min{page.currency ? ` (${page.currency})` : ""}</label>
                        <input id={ids.min} className="w360-input" inputMode="decimal" placeholder="0" value={typedMin} onChange={(e) => setTypedMin(e.target.value)} />
                      </div>
                      <div>
                        <label htmlFor={ids.max}>Max{page.currency ? ` (${page.currency})` : ""}</label>
                        <input id={ids.max} className="w360-input" inputMode="decimal" placeholder="Any" value={typedMax} onChange={(e) => setTypedMax(e.target.value)} />
                      </div>
                      <button type="submit" className="w360-btn w360-btn-secondary">
                        Apply
                      </button>
                    </form>
                    {page.facets.price.min_minor != null && page.facets.price.max_minor != null && page.currency && (
                      <p className={styles.hint}>
                        Prices run from {money(page.facets.price.min_minor, page.currency)} to {money(page.facets.price.max_minor, page.currency)}.
                      </p>
                    )}
                  </details>
                </>
              ) : (
                <p className={styles.hint}>Loading filters…</p>
              )}
            </aside>

            <section className={styles.results} aria-labelledby={ids.count} aria-busy={pending}>
              <div className={styles.toolbar}>
                <div className={styles.search} role="search">
                  <label className="w360-visually-hidden" htmlFor={ids.search}>
                    Search products
                  </label>
                  <svg className={styles.searchIcon} aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" focusable="false">
                    <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
                    <path d="m20 20-4-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                  <input
                    id={ids.search}
                    className={`w360-input ${styles.searchInput}`}
                    type="search"
                    placeholder="Search by name, category, tag or description"
                    value={search}
                    maxLength={100}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                {page && page.currencies.length > 1 && (
                  <div className={styles.control}>
                    <label htmlFor={ids.currency}>Currency</label>
                    <select id={ids.currency} className="w360-input" value={page.currency ?? ""} onChange={(e) => set({ currency: e.target.value })}>
                      {page.currencies.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <div className={styles.control}>
                  <label htmlFor={ids.sort}>Sort by:</label>
                  <select id={ids.sort} className="w360-input" value={f.sort} onChange={(e) => set({ sort: e.target.value })}>
                    {(page?.sorts ?? [{ value: "recommended", label: "Recommended" }]).map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {page ? (
                <>
                  <div className={styles.meta}>
                    <p id={ids.count} className={styles.showing} aria-live="polite">
                      {page.count === page.total ? (
                        <>
                          <strong>{page.count}</strong> product{page.count === 1 ? "" : "s"}
                        </>
                      ) : (
                        <>
                          Showing <strong>{page.count}</strong> of {page.total} products
                        </>
                      )}
                    </p>
                    {chips(page).length > 0 && (
                      <ul className={styles.active} aria-label="Filters in use">
                        {chips(page).map((c) => (
                          <li key={c.key}>
                            <button type="button" className={styles.activeChip} onClick={c.remove} aria-label={`Remove filter: ${c.label}`}>
                              {c.label} <span aria-hidden="true">×</span>
                            </button>
                          </li>
                        ))}
                        <li>
                          <button type="button" className={styles.clearAll} onClick={clear}>
                            Clear filters
                          </button>
                        </li>
                      </ul>
                    )}
                  </div>
                  {result?.error && (
                    <p role="alert" className={styles.error}>
                      {result.error}
                    </p>
                  )}

                  {page.products.length === 0 ? (
                    <div className={styles.empty}>
                      <p className={styles.emptyTitle}>{page.total === 0 ? nothingYet.title : "No product matches these filters."}</p>
                      {page.total === 0 ? (
                        nothingYet.action
                      ) : (
                        <button type="button" className="w360-btn w360-btn-secondary" onClick={clear}>
                          Clear filters
                        </button>
                      )}
                    </div>
                  ) : (
                    <ul className={styles.grid} aria-label="Products">
                      {page.products.map((card) => renderCard(card, page, onCategory))}
                    </ul>
                  )}
                </>
              ) : (
                <>
                  <p id={ids.count} className="w360-visually-hidden" aria-live="polite">
                    Loading the store…
                  </p>
                  <Skeleton />
                </>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

/** The class a portal's own button beside the price uses. */
export const storeAsideClass = styles.manage;
