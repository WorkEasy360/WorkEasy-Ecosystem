"use client";

import { useId, useState } from "react";
import { adminApi, type ConsoleProduct, type ProductChanges, type ProductType } from "@/lib/api/controlPlane";
import cp from "../ControlPlane.module.css";
import { dialog, FormPopup } from "../Modal";
import { TextField } from "../widgets";
import styles from "./ProductManager.module.css";

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
/** The longest refund promise (`catalog.domain.MAX_REFUND_DAYS`). */
const MAX_REFUND_DAYS = 365;

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

function same(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

const listsOf = (p: ConsoleProduct): Record<ListKey, string> => ({
  highlights: (p.highlights ?? []).join("\n"),
  benefits: (p.benefits ?? []).join("\n"),
  use_cases: (p.use_cases ?? []).join("\n"),
});

const flagsOf = (p: ConsoleProduct): Record<FlagKey, boolean> => ({
  best_seller: !!p.best_seller,
  featured: !!p.featured,
  popular: !!p.popular,
  badge_new: !!p.badge_new,
});

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
 * search tags, three bullet lists and the hand-set badges. The stored values
 * read on the page; the Edit popup saves only what changed, through the
 * catalog's one product edit (validated and audited). */
export function MerchandisingForm({ product: p, busy, reload }: { product: ConsoleProduct; busy: boolean; reload: () => void }) {
  const [subcategory, setSubcategory] = useState(p.subcategory ?? "");
  const [tags, setTags] = useState((p.tags ?? []).join(", "));
  const [lists, setLists] = useState<Record<ListKey, string>>(() => listsOf(p));
  const [productType, setProductType] = useState<ProductType>(p.product_type ?? "saas");
  const [bestFor, setBestFor] = useState<string[]>(p.best_for ?? []);
  const [integrations, setIntegrations] = useState((p.integrations ?? []).join(", "));
  // ADR-058: the refund promise the product page shows; empty = none.
  const [refundDays, setRefundDays] = useState(p.refund_days ? String(p.refund_days) : "");
  const typeId = useId();
  const [flags, setFlags] = useState<Record<FlagKey, boolean>>(() => flagsOf(p));

  function prefill() {
    setSubcategory(p.subcategory ?? "");
    setTags((p.tags ?? []).join(", "));
    setLists(listsOf(p));
    setProductType(p.product_type ?? "saas");
    setBestFor(p.best_for ?? []);
    setIntegrations((p.integrations ?? []).join(", "));
    setRefundDays(p.refund_days ? String(p.refund_days) : "");
    setFlags(flagsOf(p));
  }

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
  const refundText = refundDays.trim();
  const refundValue = refundText === "" ? null : Number(refundText);
  const refundProblem =
    refundValue !== null && (!Number.isInteger(refundValue) || refundValue < 1 || refundValue > MAX_REFUND_DAYS)
      ? `A whole number of days from 1 to ${MAX_REFUND_DAYS}, or empty for no refund promise.`
      : undefined;
  if (!refundProblem && (refundValue || null) !== (p.refund_days || null)) changes.refund_days = refundValue;
  const changed = Object.keys(changes).length;
  const integrationProblem =
    integrationList.length > MAX_ITEMS
      ? `At most ${MAX_ITEMS} integrations.`
      : integrationList.some((i) => i.length > MAX_INTEGRATION)
        ? `Each integration name is at most ${MAX_INTEGRATION} characters.`
        : undefined;
  const tooMany =
    tagList.length > MAX_ITEMS || LISTS.some(({ key }) => lines(lists[key]).length > MAX_ITEMS) || !!integrationProblem || !!refundProblem;

  const typeLabel = PRODUCT_TYPES.find((t) => t.value === (p.product_type ?? "saas"))?.label ?? p.product_type;
  const bestForLabels = BEST_FOR.filter((b) => (p.best_for ?? []).includes(b.value)).map((b) => b.label);
  const badges = FLAGS.filter(({ key }) => p[key]).map(({ label }) => label);
  const facts: [string, string][] = [
    ["Subcategory", p.subcategory || "—"],
    ["Search tags", (p.tags ?? []).join(", ") || "—"],
    ["Product type", typeLabel ?? "—"],
    ["Integrations", (p.integrations ?? []).join(", ") || "—"],
    ["Refund policy", p.refund_days ? `${p.refund_days} days` : "No refund promise"],
    ["Best for", bestForLabels.join(", ") || "—"],
    ...LISTS.map(({ key, label }): [string, string] => {
      const n = (p[key] ?? []).length;
      return [label, n ? `${n} item${n === 1 ? "" : "s"}` : "—"];
    }),
    ["Badges", badges.join(", ") || "None"],
  ];

  return (
    <section className={styles.card} aria-labelledby="merch-title">
      <div className={styles.sectionHead}>
        <h2 id="merch-title" className={styles.cardTitle}>
          Store merchandising
        </h2>
        <div className={styles.toolbar}>
          <FormPopup
            label="Edit merchandising"
            title="Store merchandising"
            intro="How the product is found and presented in every Store - customer, affiliate, reseller and white-label."
            size="lg"
            submitLabel="Save merchandising"
            disabled={busy}
            canSubmit={changed > 0 && !tooMany}
            onOpen={prefill}
            onSubmit={async () => {
              await adminApi.updateProduct(p.id, changes);
              return "Store merchandising saved.";
            }}
            onDone={reload}
          >
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
            <TextField
              label="Refund policy (days)"
              inputMode="numeric"
              value={refundDays}
              error={refundProblem}
              hint='Shown on the product page as "30-Day Refund Policy". Leave empty to show no refund promise.'
              onChange={setRefundDays}
            />
            <fieldset className={`${cp.field} ${dialog.span}`}>
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
              <div key={key} className={dialog.span}>
                <ListField label={label} hint={hint} value={lists[key]} onChange={(v) => setLists((cur) => ({ ...cur, [key]: v }))} />
              </div>
            ))}
            <fieldset className={`${cp.field} ${dialog.span}`}>
              <legend className="w360-label">Badges</legend>
              <div className={styles.radioStack}>
                {FLAGS.map(({ key, label, hint }) => (
                  <label key={key} className="w360-label">
                    <input type="checkbox" checked={flags[key]} onChange={(e) => setFlags((cur) => ({ ...cur, [key]: e.target.checked }))} /> {label}{" "}
                    <span className={styles.muted}>- {hint}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            {changed > 0 && (
              <p className={`${styles.muted} ${dialog.span}`}>
                {changed} unsaved change{changed === 1 ? "" : "s"}
              </p>
            )}
          </FormPopup>
        </div>
      </div>
      <p className={styles.cardLead}>How the product is found and presented in every Store - customer, affiliate, reseller and white-label.</p>
      <dl className={styles.facts}>
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
