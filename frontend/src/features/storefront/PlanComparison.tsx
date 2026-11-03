"use client";

import { useState, type ReactNode } from "react";
import { money } from "@/features/controlplane/ui";
import { PER } from "./StoreBrowser";
import styles from "./StoreBrowser.module.css";

/**
 * A product's plans side by side (ADR-056), shared by every Store level: price
 * per billing cycle (with the struck-through list price when a promotional
 * discount runs, and any one-time set-up fee), seats, features, trial and the
 * recommended plan - every value the server's. What each column's button does
 * is the portal's (`renderCta`).
 */

export interface ComparablePrice {
  interval: string;
  currency: string;
  amount_minor: number | null;
  list_amount_minor?: number | null;
  discount_bp?: number;
  setup_fee_minor?: number | null;
}

export interface ComparablePlan {
  plan_version_id: string;
  plan_code: string;
  name: string;
  caption?: string;
  billing_type: "paid" | "free_trial";
  kind: "standard" | "add_on";
  trial_days?: number;
  recommended?: boolean;
  prices: ComparablePrice[];
}

export interface ComparableRow {
  key: string;
  label: string;
  kind: string;
  unit: string;
  values: Record<string, unknown>;
}

const CYCLE_LABEL: Record<string, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  semi_annual: "Every 6 months",
  annual: "Yearly",
};

function cell(row: ComparableRow, value: unknown): string {
  if (row.kind === "seats") {
    const v = value as { included: number; max: number | null } | undefined;
    if (!v) return "—";
    return v.max && v.max !== v.included ? `${v.included} (up to ${v.max})` : String(v.included);
  }
  if (value === null || value === undefined || value === false) return "—";
  if (value === true) return "Included";
  if (typeof value === "number") return value < 0 ? "Unlimited" : `${value.toLocaleString()}${row.unit ? ` ${row.unit}` : ""}`;
  return String(value);
}

export function PlanComparison({
  plans,
  rows,
  renderCta,
  currentPlanCode,
}: {
  plans: ComparablePlan[];
  rows: ComparableRow[];
  renderCta?: (plan: ComparablePlan, interval: string) => ReactNode;
  currentPlanCode?: string | null;
}) {
  const standard = plans.filter((p) => p.kind === "standard");
  const cycles = [...new Set(standard.flatMap((p) => p.prices.map((x) => x.interval)))].filter((c) => c !== "one_time");
  const [cycle, setCycle] = useState<string>(cycles.includes("monthly") ? "monthly" : (cycles[0] ?? "monthly"));
  if (standard.length === 0) return <p>No plans are on sale yet.</p>;

  return (
    <section aria-labelledby="compare-title" className={styles.compareSection}>
      <h2 id="compare-title">Compare plans</h2>
      {cycles.length > 1 && (
        <div role="group" aria-label="Billing cycle">
          {cycles.map((c) => (
            <button
              key={c}
              type="button"
              aria-pressed={cycle === c}
              className={cycle === c ? "w360-btn w360-btn-primary" : "w360-btn w360-btn-secondary"}
              onClick={() => setCycle(c)}
            >
              {CYCLE_LABEL[c] ?? c}
            </button>
          ))}
        </div>
      )}
      <div className={styles.compareWrap}>
        <table className={styles.compare}>
          <caption className="w360-visually-hidden">Plans compared, {CYCLE_LABEL[cycle] ?? cycle} prices</caption>
          <thead>
            <tr>
              <td />
              {standard.map((p) => (
                <th key={p.plan_version_id} scope="col" className={p.recommended ? styles.compareRecommended : undefined}>
                  {p.recommended && <span className={styles.badgeNew}>Recommended</span>}
                  <span className={styles.compareName}>{p.name}</span>
                  {p.plan_code === currentPlanCode && <span className={styles.chip}>Your plan</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Price</th>
              {standard.map((p) => {
                const price = p.prices.find((x) => x.interval === cycle);
                return (
                  <td key={p.plan_version_id}>
                    {p.billing_type === "free_trial" ? (
                      <strong>Free trial</strong>
                    ) : price && price.amount_minor !== null ? (
                      <>
                        <strong className={styles.amount}>{price.amount_minor === 0 ? "Free" : money(price.amount_minor, price.currency)}</strong>
                        {price.amount_minor > 0 && <span className={styles.per}>{PER[price.interval] ?? ""}</span>}
                        {price.list_amount_minor != null && price.list_amount_minor > price.amount_minor && (
                          <>
                            <br />
                            <s aria-label={`instead of ${money(price.list_amount_minor, price.currency)}`}>{money(price.list_amount_minor, price.currency)}</s>{" "}
                            <span className={styles.save}>{Math.round((price.discount_bp ?? 0) / 100)}% off</span>
                          </>
                        )}
                        {!!price.setup_fee_minor && (
                          <>
                            <br />
                            <span className={styles.hint}>+ {money(price.setup_fee_minor, price.currency)} one-time set-up</span>
                          </>
                        )}
                      </>
                    ) : (
                      <span className={styles.hint}>{price ? "Priced by usage" : "Contact sales"}</span>
                    )}
                  </td>
                );
              })}
            </tr>
            {rows.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.label}</th>
                {standard.map((p) => (
                  <td key={p.plan_version_id}>{cell(row, row.values[p.plan_version_id])}</td>
                ))}
              </tr>
            ))}
            <tr>
              <th scope="row">Free trial</th>
              {standard.map((p) => (
                <td key={p.plan_version_id}>{p.trial_days ? `${p.trial_days} days` : "—"}</td>
              ))}
            </tr>
            {renderCta && (
              <tr>
                <td />
                {standard.map((p) => (
                  <td key={p.plan_version_id}>{renderCta(p, cycle)}</td>
                ))}
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
