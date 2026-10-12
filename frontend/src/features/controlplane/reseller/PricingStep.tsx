"use client";

import { useId } from "react";
import type { ReviewPricingRow } from "@/lib/api/partnerConsole";
import { formatBasisPoints } from "../amounts";
import { money } from "../ui";
import { Segmented } from "../widgets";
import styles from "../ControlPlane.module.css";
import local from "../PartnerConsole.module.css";
import { priceKey, type ReviewState, type StepProps } from "./draft";

export const billingLabel = (interval: string) => {
  const text = interval.replace(/_/g, " ");
  return text ? text[0].toUpperCase() + text.slice(1) : text;
};

function PriceRow({
  row,
  value,
  error,
  canPrice,
  onChange,
  onBlur,
}: {
  row: ReviewPricingRow;
  value: string;
  error?: string;
  canPrice: boolean;
  onChange: (value: string) => void;
  onBlur: () => void;
}) {
  const inputId = useId();
  const errorId = useId();
  return (
    <tr>
      <th scope="row">
        {row.product_name} — {row.plan_name}
      </th>
      <td>{billingLabel(row.interval)}</td>
      <td>{row.currency}</td>
      <td className={styles.num}>{row.list_price_minor === null ? "No list price" : money(row.list_price_minor, row.currency)}</td>
      <td>
        <label className="w360-visually-hidden" htmlFor={inputId}>
          Partner price for {row.plan_name} in {row.currency} ({row.interval})
        </label>
        <input
          id={inputId}
          className="w360-input"
          inputMode="decimal"
          value={value}
          placeholder={row.list_price_minor === null ? "" : "List price"}
          disabled={!canPrice}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
        />
        {error && (
          <p id={errorId} className="w360-error-text">
            {error}
          </p>
        )}
      </td>
      <td className={styles.num}>
        {row.discount_minor === null ? "—" : `${money(row.discount_minor, row.currency)} (${formatBasisPoints(row.discount_bp ?? 0)})`}
      </td>
      <td className={styles.num}>
        {row.commission_minor === null ? "No commission" : `${money(row.commission_minor, row.currency)} at ${formatBasisPoints(row.commission_bp ?? 0)}`}
      </td>
      <td className={styles.num}>{row.platform_net_minor === null ? "—" : money(row.platform_net_minor, row.currency)}</td>
    </tr>
  );
}

/** Step 5: standard (list) pricing or custom partner prices. Every figure is the
 * server's: the browser turns a typed amount into minor units and nothing else. */
export function PricingStep({
  draft,
  set,
  errors,
  review,
  canPrice,
  onRefresh,
}: Pick<StepProps, "draft" | "set" | "errors"> & {
  review: ReviewState;
  canPrice: boolean;
  onRefresh: (force: boolean) => void;
}) {
  const rows = review.data?.pricing ?? [];
  const custom = draft.pricingMode === "custom";
  return (
    <>
      <Segmented<"standard" | "custom">
        legend="Partner pricing"
        options={[
          { value: "standard", label: "Standard partner pricing (list price)" },
          { value: "custom", label: "Custom partner pricing", disabled: !canPrice },
        ]}
        value={draft.pricingMode}
        onChange={(v) => set("pricingMode", v)}
        hint={
          canPrice
            ? "A partner price applies to this partner only. It never changes the global list price, and sales already made keep the price they were made at."
            : "Custom partner prices need the platform.staff_pricing.manage permission. Every plan will use the list price."
        }
      />
      {draft.products.length === 0 ? (
        <p role="status">No products are allowed yet, so there is nothing to price. Go on to the next step.</p>
      ) : !custom ? (
        <p role="status">
          Every allowed plan is sold at its list price. No partner price is created, and the partner earns commission on the list
          price.
        </p>
      ) : (
        <>
          <p className={styles.muted}>
            The customer pays the partner price; the partner earns commission on it. Leave a price empty to use the list price.
            Every figure is calculated by the server. Other billing cycles use the list price until you set one on the
            reseller&apos;s page.
          </p>
          {review.load === "loading" && <p aria-live="polite">Calculating…</p>}
          {review.load === "error" && <p role="alert">The figures could not be calculated. Try again.</p>}
          {rows.length > 0 && (
            <div className={styles.tableWrap} role="region" aria-label="Partner pricing" tabIndex={0}>
              <table className={styles.table}>
                <caption>Prices per billing cycle, calculated by the server</caption>
                <thead>
                  <tr>
                    {["Product / plan", "Billing", "Currency", "List price", "Partner price", "Discount", "Partner margin", "Platform keeps"].map((h) => (
                      <th key={h} scope="col">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const key = priceKey(r.plan_version_id, r.interval);
                    return (
                      <PriceRow
                        key={key}
                        row={r}
                        value={draft.prices[key] ?? ""}
                        error={errors[`price.${key}`]}
                        canPrice={canPrice}
                        onChange={(v) => set("prices", { ...draft.prices, [key]: v })}
                        onBlur={() => onRefresh(false)}
                      />
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className={local.progressItem}>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onRefresh(true)} disabled={review.load === "loading"}>
              Update figures
            </button>
            {review.stale && review.load === "ready" && <span className={styles.muted}>The figures are from before your last change.</span>}
          </div>
        </>
      )}
    </>
  );
}
