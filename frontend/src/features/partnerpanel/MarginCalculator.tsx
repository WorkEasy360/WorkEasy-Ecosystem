"use client";

import { useId, useState } from "react";
import { LoadGate, money, useLoad } from "@/features/controlplane/ui";
import { panelApi, type PlanRow } from "@/lib/api/partnerPanel";
import { EmptyState, PageHeader, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

/**
 * The margin calculator (2026-11-03, the specification's Reseller module): pick
 * a plan, a billing interval and a number of seats, and see what the customer
 * pays, your cost and your margin.
 *
 * **The per-seat figures are the server's** - the same pricing engine the
 * Pricing & Margins page shows (`/plans`); this page only multiplies them by
 * the seats chosen, as an estimate. What a customer is charged, and what you
 * earn, is worked out by Workeasy360 when the order is placed.
 */
export function MarginCalculator() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.plans(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Margin calculator"
        lead="Estimate what a sale earns you. Prices and margins are Workeasy360's figures for your account; the final amounts are calculated when the order is placed."
      />
      <LoadGate load={load} what="your plans">
        {(d) => (d.plans.length === 0 ? <EmptyState title="No plans to price yet" icon="tag" /> : <Calculator plans={d.plans} currency={d.currency} />)}
      </LoadGate>
    </div>
  );
}

function Calculator({ plans, currency }: { plans: PlanRow[]; currency: string }) {
  const ids = { plan: useId(), interval: useId(), seats: useId() };
  const [planId, setPlanId] = useState(plans[0].plan_version_id);
  const plan = plans.find((p) => p.plan_version_id === planId) ?? plans[0];
  const [interval, setInterval] = useState(plan.prices[0]?.interval ?? "");
  const price = plan.prices.find((p) => p.interval === interval) ?? plan.prices[0];
  const [seats, setSeats] = useState(1);
  const n = Number.isFinite(seats) && seats > 0 ? Math.min(Math.floor(seats), 100_000) : 1;
  const cur = plan.currency || currency;
  const customer = price?.customer_price_minor ?? price?.list_price_minor ?? null;
  const cost = price?.partner_cost_minor ?? price?.partner_price_minor ?? null;
  const margin = price?.margin_minor ?? (price?.expected_earning_minor || null);
  const times = (v: number | null | undefined) => (v == null ? "—" : money(v * n, cur));
  return (
    <section className={styles.panel} aria-labelledby="calc">
      <h2 id="calc">Estimate a sale</h2>
      <div className={styles.formRow}>
        <div>
          <label className="w360-label" htmlFor={ids.plan}>
            Plan
          </label>
          <select
            id={ids.plan}
            className="w360-input"
            value={planId}
            onChange={(e) => {
              setPlanId(e.target.value);
              const next = plans.find((p) => p.plan_version_id === e.target.value);
              setInterval(next?.prices[0]?.interval ?? "");
            }}
          >
            {plans.map((p) => (
              <option key={p.plan_version_id} value={p.plan_version_id}>
                {p.product_name} - {p.plan_name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={ids.interval}>
            Billing
          </label>
          <select id={ids.interval} className="w360-input" value={price?.interval ?? ""} onChange={(e) => setInterval(e.target.value)}>
            {plan.prices.map((p) => (
              <option key={p.interval} value={p.interval}>
                {p.interval}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={ids.seats}>
            Seats
          </label>
          <input id={ids.seats} className="w360-input" type="number" min={1} max={100000} value={seats} onChange={(e) => setSeats(Number(e.target.value))} />
        </div>
      </div>
      {!price ? (
        <p className={styles.muted}>This plan has no price for your account yet.</p>
      ) : (
        <dl className={styles.earnings} aria-live="polite">
          <div>
            <dt>Customer pays (per seat)</dt>
            <dd>{customer == null ? "—" : money(customer, cur)}</dd>
          </div>
          <div>
            <dt>Customer pays ({n} seats)</dt>
            <dd>{times(customer)}</dd>
          </div>
          <div>
            <dt>Your cost ({n} seats)</dt>
            <dd>{times(cost)}</dd>
          </div>
          <div data-strong>
            <dt>{plan.earning?.model === "commission" ? "Your commission" : "Your margin"} ({n} seats)</dt>
            <dd>{times(margin)}</dd>
          </div>
          {price.margin_bp != null && (
            <div>
              <dt>Margin rate</dt>
              <dd>{(price.margin_bp / 100).toFixed(2)}%</dd>
            </div>
          )}
        </dl>
      )}
    </section>
  );
}
