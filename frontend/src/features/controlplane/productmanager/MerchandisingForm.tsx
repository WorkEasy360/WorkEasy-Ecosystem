"use client";

import { useId, useState } from "react";
import { adminApi, type ConsoleProduct, type ProductChanges } from "@/lib/api/controlPlane";
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
  { key: "featured", label: "Featured", hint: "Shown in the store's Featured collection." },
  { key: "popular", label: "Popular", hint: "Shown in the Popular collection." },
  { key: "badge_new", label: "New", hint: "Marked New now (a product is also New for 30 days after launch)." },
] as const;
type FlagKey = (typeof FLAGS)[number]["key"];

const MAX_ITEMS = 12;

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
  const [flags, setFlags] = useState<Record<FlagKey, boolean>>(() => ({
    featured: !!p.featured,
    popular: !!p.popular,
    badge_new: !!p.badge_new,
  }));

  const tagList = tags
    .split(",")
    .map((t) => t.trim().toLowerCase())
    .filter(Boolean);
  const changes: ProductChanges = {};
  if (subcategory.trim() !== (p.subcategory ?? "")) changes.subcategory = subcategory.trim();
  if (!same(tagList, p.tags ?? [])) changes.tags = tagList;
  for (const { key } of LISTS) {
    if (!same(lines(lists[key]), p[key] ?? [])) changes[key] = lines(lists[key]);
  }
  for (const { key } of FLAGS) {
    if (flags[key] !== !!p[key]) changes[key] = flags[key];
  }
  const changed = Object.keys(changes).length;
  const tooMany = tagList.length > MAX_ITEMS || LISTS.some(({ key }) => lines(lists[key]).length > MAX_ITEMS);

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
