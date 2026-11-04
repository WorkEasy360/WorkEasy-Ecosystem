"use client";

import { useId, useState } from "react";
import { adminApi, type ConsoleProduct, type ProductChanges, type ProductType } from "@/lib/api/controlPlane";
import cp from "../ControlPlane.module.css";
import { TextField } from "../widgets";
import styles from "./ProductManager.module.css";

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

const LISTS = [
  { key: "highlights", label: "Key features", hint: "One per line. The product page's feature list." },
  { key: "benefits", label: "Benefits", hint: "One per line. What a customer gains." },
  { key: "use_cases", label: "Use cases", hint: "One per line. Who it is for and how they use it." },
] as const;
type ListKey = (typeof LISTS)[number]["key"];

const FLAGS = [
  { key: "best_seller", label: "Best Seller", hint: "The marketplace's Best Seller tag and collection." },
  { key: "featured", label: "Featured", hint: "Shown in the store's Featured collection." },
  { key: "popular", label: "Popular", hint: "Shown in the Popular collection." },
  { key: "badge_new", label: "New", hint: "Marked New now (a product is also New for 30 days after launch)." },
] as const;
type FlagKey = (typeof FLAGS)[number]["key"];

const MAX_ITEMS = 12;

/** The marketplace's "Product type" and "Best for" choices (2026-11-04) -
 * the server's own lists (`catalog.domain`), which it checks again. */
export const PRODUCT_TYPES: { value: ProductType; label: string }[] = [
  { value: "saas", label: "SaaS product" },
  { value: "ai_tool", label: "AI tool" },
  { value: "business_suite", label: "Business suite" },
];
export const BEST_FOR: { value: string; label: string }[] = [
  { value: "startups", label: "Startups" },
  { value: "small_business", label: "Small businesses" },
  { value: "agencies", label: "Agencies" },
  { value: "enterprises", label: "Enterprises" },
  { value: "freelancers", label: "Freelancers" },
  { value: "ecommerce", label: "E-commerce" },
  { value: "education", label: "Education" },
  { value: "healthcare", label: "Healthcare" },
  { value: "real_estate", label: "Real estate" },
];
const MAX_INTEGRATION = 32;

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

function same(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function ListField({ label, hint, value, onChange }: { label: string; hint: string; value: string; onChange: (v: string) => void }) {
  const id = useId();
  const hintId = useId();
  const count = lines(value).length;
  return (
    <div className={cp.field}>
      <label className="w360-label" htmlFor={id}>
        {label}
      </label>
      <textarea id={id} className="w360-input" rows={4} value={value} aria-describedby={hintId} onChange={(e) => onChange(e.target.value)} />
      <p id={hintId} className={count > MAX_ITEMS ? "w360-error-text" : "w360-hint"}>
        {hint} {count}/{MAX_ITEMS}
      </p>
    </div>
  );
}

/** ADR-056: what the store shows besides the listing text - a subcategory,
 * search tags, three bullet lists and the hand-set badges. Saves only what
 * changed, through the catalog's one product edit (validated and audited). */
export function MerchandisingForm({ product: p, busy, act }: { product: ConsoleProduct; busy: boolean; act: Act }) {
  const [subcategory, setSubcategory] = useState(p.subcategory ?? "");
  const [tags, setTags] = useState((p.tags ?? []).join(", "));
  const [lists, setLists] = useState<Record<ListKey, string>>(() => ({
    highlights: (p.highlights ?? []).join("\n"),
    benefits: (p.benefits ?? []).join("\n"),
    use_cases: (p.use_cases ?? []).join("\n"),
  }));
  const [productType, setProductType] = useState<ProductType>(p.product_type ?? "saas");
  const [bestFor, setBestFor] = useState<string[]>(p.best_for ?? []);
  const [integrations, setIntegrations] = useState((p.integrations ?? []).join(", "));
  const typeId = useId();
  const [flags, setFlags] = useState<Record<FlagKey, boolean>>(() => ({
    best_seller: !!p.best_seller,
    featured: !!p.featured,
    popular: !!p.popular,
    badge_new: !!p.badge_new,
  }));

  const tagList = tags
    .split(",")
    .map((t) => t.trim().toLowerCase())
    .filter(Boolean);
  const integrationList = integrations
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const chosenBestFor = BEST_FOR.map((b) => b.value).filter((v) => bestFor.includes(v));
  const changes: ProductChanges = {};
  if (productType !== (p.product_type ?? "saas")) changes.product_type = productType;
  if (!same(chosenBestFor, p.best_for ?? [])) changes.best_for = chosenBestFor;
  if (!same(integrationList, p.integrations ?? [])) changes.integrations = integrationList;
  if (subcategory.trim() !== (p.subcategory ?? "")) changes.subcategory = subcategory.trim();
  if (!same(tagList, p.tags ?? [])) changes.tags = tagList;
  for (const { key } of LISTS) {
    if (!same(lines(lists[key]), p[key] ?? [])) changes[key] = lines(lists[key]);
  }
  for (const { key } of FLAGS) {
    if (flags[key] !== !!p[key]) changes[key] = flags[key];
  }
  const changed = Object.keys(changes).length;
  const integrationProblem =
    integrationList.length > MAX_ITEMS
      ? `At most ${MAX_ITEMS} integrations.`
      : integrationList.some((i) => i.length > MAX_INTEGRATION)
        ? `Each integration name is at most ${MAX_INTEGRATION} characters.`
        : undefined;
  const tooMany = tagList.length > MAX_ITEMS || LISTS.some(({ key }) => lines(lists[key]).length > MAX_ITEMS) || !!integrationProblem;

  return (
    <form
      className={styles.card}
      aria-labelledby="merch-title"
      onSubmit={(e) => {
        e.preventDefault();
        if (changed && !tooMany) void act(() => adminApi.updateProduct(p.id, changes), "Store merchandising saved.");
      }}
    >
      <h2 id="merch-title" className={styles.cardTitle}>
        Store merchandising
      </h2>
      <p className={styles.cardLead}>How the product is found and presented in every Store - customer, affiliate, reseller and white-label.</p>
      <div className={styles.formGrid}>
        <TextField label="Subcategory" maxLength={64} value={subcategory} hint="Optional, e.g. Invoicing under Finance." onChange={setSubcategory} />
        <TextField label="Search tags" value={tags} hint={`Comma separated, up to ${MAX_ITEMS}. ${tagList.length}/${MAX_ITEMS}`} onChange={setTags} />
        <div className={cp.field}>
          <label className="w360-label" htmlFor={typeId}>
            Product type
          </label>
          <select id={typeId} className="w360-input" value={productType} onChange={(e) => setProductType(e.target.value as ProductType)}>
            {PRODUCT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <p className="w360-hint">The Store&apos;s Product Type filter and the header&apos;s Business Suite and AI Tools menus.</p>
        </div>
        <TextField
          label="Integrations"
          value={integrations}
          error={integrationProblem}
          hint={`The apps it connects to, comma separated (WhatsApp, Gmail, Razorpay). ${integrationList.length}/${MAX_ITEMS}`}
          onChange={setIntegrations}
        />
        <fieldset className={`${cp.field} ${styles.full}`}>
          <legend className="w360-label">Best for</legend>
          <div className={styles.choiceGrid}>
            {BEST_FOR.map((b) => (
              <label key={b.value} className="w360-label">
                <input
                  type="checkbox"
                  checked={bestFor.includes(b.value)}
                  onChange={(e) => setBestFor((cur) => (e.target.checked ? [...cur, b.value] : cur.filter((v) => v !== b.value)))}
                />{" "}
                {b.label}
              </label>
            ))}
          </div>
          <p className="w360-hint">The Store&apos;s Best for filter and the header&apos;s Industry Solutions menu.</p>
        </fieldset>
        {LISTS.map(({ key, label, hint }) => (
          <div key={key} className={styles.full}>
            <ListField label={label} hint={hint} value={lists[key]} onChange={(v) => setLists((cur) => ({ ...cur, [key]: v }))} />
          </div>
        ))}
        <fieldset className={`${cp.field} ${styles.full}`}>
          <legend className="w360-label">Badges</legend>
          {FLAGS.map(({ key, label, hint }) => (
            <label key={key} className="w360-label">
              <input type="checkbox" checked={flags[key]} onChange={(e) => setFlags((cur) => ({ ...cur, [key]: e.target.checked }))} /> {label}{" "}
              <span className={styles.muted}>- {hint}</span>
            </label>
          ))}
        </fieldset>
      </div>
      <div className={styles.toolbar}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || changed === 0 || tooMany}>
          Save merchandising
        </button>
        {changed > 0 && (
          <span className={styles.muted}>
            {changed} unsaved change{changed === 1 ? "" : "s"}
          </span>
        )}
      </div>
    </form>
  );
}
