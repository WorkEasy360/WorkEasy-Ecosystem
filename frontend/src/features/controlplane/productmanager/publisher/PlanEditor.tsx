"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { PERIODS, PERIOD_NAME, blankPlan, type PlanDraft, type PriceBook, type Period, type Problems } from "./model";
import styles from "./Publisher.module.css";

/**
 * The page's Plans & Pricing (ADR-068): one card per plan, saved into the very
 * plan records Products & Pricing > Plans lists - there is no second copy.
 * Add Another Plan adds a card at once; an unsaved card can be removed; cards
 * reorder by drag or by the Move buttons (the Store shows them in this order);
 * one plan may be Recommended. Seats, trial, set-up fee, discount and
 * visibility are under Advanced plan settings, closed by default.
 */
export function PlanEditor({
  plans,
  books,
  problems,
  maxPlans,
  productCode,
  canPrice,
  busy,
  onChange,
}: {
  plans: PlanDraft[];
  books: PriceBook[];
  problems: Problems;
  maxPlans: number;
  productCode: string | null;
  canPrice: boolean;
  busy: boolean;
  onChange: (plans: PlanDraft[]) => void;
}) {
  const [dragging, setDragging] = useState<number | null>(null);
  const update = (index: number, patch: Partial<PlanDraft>) => onChange(plans.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  const move = (from: number, to: number) => {
    if (to < 0 || to >= plans.length || from === to) return;
    const next = [...plans];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
  };
  const recommend = (index: number, on: boolean) => onChange(plans.map((p, i) => ({ ...p, recommended: i === index ? on : on ? false : p.recommended })));
  const add = () => {
    const last = plans[plans.length - 1];
    onChange([...plans, { ...blankPlan(last?.bookId ?? books[0]?.id ?? ""), advancedOpen: false }]);
  };

  return (
    <div className={styles.plans}>
      {!canPrice && (
        <p className={styles.notice} role="note">
          You can name and describe plans; setting prices needs the pricing permission.
        </p>
      )}
      {problems.plans && (
        <p className="w360-error-text" role="alert">
          {problems.plans}
        </p>
      )}
      <ol className={styles.planList} aria-label="Plans">
        {plans.map((plan, index) => (
          <li
            key={plan.key}
            className={`${styles.planCard} ${plan.recommended ? styles.planCardRecommended : ""} ${dragging === index ? styles.planCardDragging : ""}`}
            draggable={!busy}
            onDragStart={(e) => {
              setDragging(index);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragOver={(e) => {
              if (dragging === null) return;
              e.preventDefault();
            }}
            onDrop={(e) => {
              e.preventDefault();
              if (dragging !== null) move(dragging, index);
              setDragging(null);
            }}
            onDragEnd={() => setDragging(null)}
          >
            <PlanCard
              plan={plan}
              index={index}
              count={plans.length}
              books={books}
              problems={problems}
              productCode={productCode}
              canPrice={canPrice}
              busy={busy}
              onUpdate={(patch) => update(index, patch)}
              onMove={(to) => move(index, to)}
              onRecommend={(on) => recommend(index, on)}
              onRemove={() => onChange(plans.filter((_, i) => i !== index))}
            />
          </li>
        ))}
      </ol>
      <button type="button" className={`w360-btn w360-btn-secondary ${styles.addPlan}`} disabled={busy || plans.length >= maxPlans} onClick={add}>
        <span aria-hidden="true">+</span> Add Another Plan
      </button>
      {plans.length >= maxPlans && <p className="w360-hint">A product page carries at most {maxPlans} plans.</p>}
    </div>
  );
}

function PlanCard({
  plan,
  index,
  count,
  books,
  problems,
  productCode,
  canPrice,
  busy,
  onUpdate,
  onMove,
  onRecommend,
  onRemove,
}: {
  plan: PlanDraft;
  index: number;
  count: number;
  books: PriceBook[];
  problems: Problems;
  productCode: string | null;
  canPrice: boolean;
  busy: boolean;
  onUpdate: (patch: Partial<PlanDraft>) => void;
  onMove: (to: number) => void;
  onRecommend: (on: boolean) => void;
  onRemove: () => void;
}) {
  const base = useId();
  const id = (name: string) => `${base}-${name}`;
  const err = (name: string) => problems[`plans.${index}.${name}`];
  const n = index + 1;
  const book = books.find((b) => b.id === plan.bookId);
  const label = plan.name.trim() || `Plan ${n}`;
  const priceLocked = !canPrice;

  return (
    <fieldset className={styles.planFieldset} disabled={busy}>
      <legend className="w360-visually-hidden">
        Plan {n}: {label}
      </legend>
      <div className={styles.planHead}>
        <span className={styles.dragHandle} aria-hidden="true" title="Drag to reorder">
          ⋮⋮
        </span>
        <div className={styles.planNameField}>
          <label className="w360-label" htmlFor={id("name")}>
            Plan name
          </label>
          <input
            id={id("name")}
            className="w360-input"
            value={plan.name}
            maxLength={128}
            placeholder="e.g. Starter"
            aria-invalid={err("name") ? true : undefined}
            onChange={(e) => onUpdate({ name: e.target.value })}
          />
          {err("name") && <p className="w360-error-text">{err("name")}</p>}
        </div>
        <div className={styles.segment} role="radiogroup" aria-label={`Price type for ${label}`}>
          {[
            { value: false, text: "Paid" },
            { value: true, text: "Free" },
          ].map((option) => (
            <button
              key={option.text}
              type="button"
              role="radio"
              aria-checked={plan.free === option.value}
              className={plan.free === option.value ? styles.segmentOn : ""}
              disabled={priceLocked}
              onClick={() => onUpdate({ free: option.value })}
            >
              {option.text}
            </button>
          ))}
        </div>
        <label className={styles.recommend}>
          <input type="checkbox" checked={plan.recommended} onChange={(e) => onRecommend(e.target.checked)} />
          <span>Recommended</span>
        </label>
        <div className={styles.planTools}>
          <button type="button" className={styles.iconButton} aria-label={`Move ${label} up`} disabled={index === 0} onClick={() => onMove(index - 1)}>
            ↑
          </button>
          <button type="button" className={styles.iconButton} aria-label={`Move ${label} down`} disabled={index === count - 1} onClick={() => onMove(index + 1)}>
            ↓
          </button>
          {plan.id === null ? (
            <button type="button" className={styles.iconButton} aria-label={`Remove ${label}`} disabled={count <= 1} onClick={onRemove}>
              ✕
            </button>
          ) : (
            <span className={`w360-badge ${plan.status === "available" ? "w360-badge-success" : "w360-badge-neutral"}`}>
              {plan.status === "available" ? "On sale" : plan.status === "draft" ? "Draft" : plan.status}
            </span>
          )}
        </div>
      </div>

      <div className={styles.planGrid}>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={id("book")}>
            Currency
          </label>
          <select
            id={id("book")}
            className="w360-input"
            value={plan.bookId}
            disabled={priceLocked}
            aria-invalid={err("bookId") ? true : undefined}
            onChange={(e) => onUpdate({ bookId: e.target.value })}
          >
            {books.length === 0 && <option value="">No price book yet</option>}
            {books.map((b) => (
              <option key={b.id} value={b.id}>
                {b.currency} - {b.name}
              </option>
            ))}
          </select>
          {err("bookId") && <p className="w360-error-text">{err("bookId")}</p>}
        </div>

        <div className={`${styles.field} ${styles.span2}`}>
          <span className="w360-label" id={id("periods")}>
            {plan.free ? "Billing period" : "Billing periods and prices"}
          </span>
          <div className={styles.periodRow} role="group" aria-labelledby={id("periods")}>
            {PERIODS.map((period) => (
              <PeriodPrice
                key={period}
                period={period}
                plan={plan}
                currency={book?.currency ?? ""}
                disabled={priceLocked}
                error={err(`prices.${period}`)}
                onToggle={(on) => {
                  const offered = plan.free
                    ? { monthly: false, quarterly: false, semi_annual: false, annual: false, [period]: on || true }
                    : { ...plan.offered, [period]: on };
                  onUpdate({ offered });
                }}
                onPrice={(value) => onUpdate({ prices: { ...plan.prices, [period]: value } })}
              />
            ))}
          </div>
          {err("prices") && <p className="w360-error-text">{err("prices")}</p>}
        </div>

        <div className={`${styles.field} ${styles.span2}`}>
          <label className="w360-label" htmlFor={id("caption")}>
            Short plan description
          </label>
          <input
            id={id("caption")}
            className="w360-input"
            value={plan.caption}
            maxLength={160}
            placeholder="e.g. For small teams getting started"
            aria-invalid={err("caption") ? true : undefined}
            onChange={(e) => onUpdate({ caption: e.target.value })}
          />
          {err("caption") && <p className="w360-error-text">{err("caption")}</p>}
        </div>

        <div className={`${styles.field} ${styles.span2}`}>
          <label className="w360-label" htmlFor={id("highlights")}>
            Features / highlights
          </label>
          <textarea
            id={id("highlights")}
            className={`w360-input ${styles.shortArea}`}
            rows={3}
            value={plan.highlights}
            placeholder={"One per line, e.g.\n5 users\nEmail support"}
            aria-describedby={id("highlights-hint")}
            onChange={(e) => onUpdate({ highlights: e.target.value })}
          />
          <p id={id("highlights-hint")} className="w360-hint">
            One per line. Shown as ticks on the plan&apos;s card in the Store.
          </p>
        </div>
      </div>

      <details className={styles.advanced} open={plan.advancedOpen} onToggle={(e) => onUpdate({ advancedOpen: (e.target as HTMLDetailsElement).open })}>
        <summary>Advanced plan settings</summary>
        <div className={styles.planGrid}>
          <NumberField id={id("seats")} label="Included seats" value={plan.seats} error={err("seats")} min={1} onChange={(seats) => onUpdate({ seats })} />
          <NumberField
            id={id("max")}
            label="Seat limit"
            hint="Empty for no limit."
            value={plan.maxSeats}
            error={err("maxSeats")}
            min={1}
            onChange={(maxSeats) => onUpdate({ maxSeats })}
          />
          <NumberField id={id("trial")} label="Trial days" hint="0 for none." value={plan.trialDays} error={err("trialDays")} min={0} onChange={(trialDays) => onUpdate({ trialDays })} />
          {!plan.free && (
            <>
              <div className={styles.field}>
                <label className="w360-label" htmlFor={id("setup")}>
                  One-time set-up fee{book ? ` (${book.currency})` : ""}
                </label>
                <input
                  id={id("setup")}
                  className="w360-input"
                  inputMode="decimal"
                  value={plan.setupFee}
                  disabled={priceLocked}
                  placeholder="None"
                  aria-invalid={err("setupFee") ? true : undefined}
                  onChange={(e) => onUpdate({ setupFee: e.target.value })}
                />
                {err("setupFee") && <p className="w360-error-text">{err("setupFee")}</p>}
              </div>
              <div className={styles.field}>
                <label className="w360-label" htmlFor={id("discount")}>
                  Launch discount (%)
                </label>
                <input
                  id={id("discount")}
                  className="w360-input"
                  inputMode="decimal"
                  value={plan.discount}
                  disabled={priceLocked}
                  placeholder="None"
                  aria-invalid={err("discount") ? true : undefined}
                  onChange={(e) => onUpdate({ discount: e.target.value })}
                />
                {err("discount") && <p className="w360-error-text">{err("discount")}</p>}
              </div>
              <label className={styles.check}>
                <input type="checkbox" checked={plan.perUser} disabled={priceLocked} onChange={(e) => onUpdate({ perUser: e.target.checked })} />
                <span>Price is per user (seat)</span>
              </label>
            </>
          )}
          <label className={styles.check}>
            <input type="checkbox" checked={plan.visible} onChange={(e) => onUpdate({ visible: e.target.checked })} />
            <span>Show this plan in the Store</span>
          </label>
        </div>
        <p className="w360-hint">
          Feature entitlements, included usage, renewal and upgrade rules, and partner terms are set on the plan in{" "}
          {productCode && plan.id ? <Link href={`/admin/plans?product=${encodeURIComponent(productCode)}`}>Products &amp; Pricing &gt; Plans</Link> : "Products & Pricing > Plans once the plan is saved"}
          . A price change applies to new purchases; current subscribers keep the terms they bought.
        </p>
      </details>
    </fieldset>
  );
}

function PeriodPrice({
  period,
  plan,
  currency,
  disabled,
  error,
  onToggle,
  onPrice,
}: {
  period: Period;
  plan: PlanDraft;
  currency: string;
  disabled: boolean;
  error?: string;
  onToggle: (on: boolean) => void;
  onPrice: (value: string) => void;
}) {
  const id = useId();
  const on = plan.offered[period];
  return (
    <div className={`${styles.periodCell} ${on ? styles.periodOn : ""}`}>
      <label className={styles.periodToggle}>
        <input type={plan.free ? "radio" : "checkbox"} name={plan.free ? `${plan.key}-free-period` : undefined} checked={on} disabled={disabled} onChange={(e) => onToggle(e.target.checked)} />
        <span>{PERIOD_NAME[period]}</span>
      </label>
      {on && !plan.free && (
        <>
          <label className="w360-visually-hidden" htmlFor={id}>
            {PERIOD_NAME[period]} price for {plan.name || "this plan"}
            {currency ? ` (${currency})` : ""}
          </label>
          <div className={styles.money}>
            <span aria-hidden="true">{currency}</span>
            <input
              id={id}
              className="w360-input"
              inputMode="decimal"
              value={plan.prices[period]}
              placeholder="0"
              disabled={disabled}
              aria-invalid={error ? true : undefined}
              onChange={(e) => onPrice(e.target.value)}
            />
          </div>
          {error && <p className="w360-error-text">{error}</p>}
        </>
      )}
      {on && plan.free && <span className={styles.freeTag}>Free</span>}
    </div>
  );
}

function NumberField({
  id,
  label,
  hint,
  value,
  error,
  min,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  value: string;
  error?: string;
  min: number;
  onChange: (value: string) => void;
}) {
  return (
    <div className={styles.field}>
      <label className="w360-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="w360-input"
        type="number"
        inputMode="numeric"
        min={min}
        value={value}
        aria-invalid={error ? true : undefined}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {hint && (
        <p id={`${id}-hint`} className="w360-hint">
          {hint}
        </p>
      )}
      {error && <p className="w360-error-text">{error}</p>}
    </div>
  );
}
