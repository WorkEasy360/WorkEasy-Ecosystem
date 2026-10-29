"use client";

import { useId, useState } from "react";
import { adminApi, type ConsoleProduct, type ProductChanges, type TrialConfig } from "@/lib/api/controlPlane";
import { ReasonAction } from "../ui";
import { Segmented } from "../widgets";
import { STATUS_BADGE, STATUS_LABEL, VISIBILITY_BADGE, VISIBILITY_LABEL, visibilityOf } from "./model";
import cp from "../ControlPlane.module.css";
import styles from "./ProductManager.module.css";

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;
type Allowed = "allowed" | "not_allowed";

const ALLOWED: { value: Allowed; label: string }[] = [
  { value: "allowed", label: "Allowed" },
  { value: "not_allowed", label: "Not allowed" },
];

const CHANNELS: { key: keyof Pick<ProductChanges, "customer_store_enabled" | "reseller_enabled" | "affiliate_enabled" | "white_label_enabled">; label: string }[] = [
  { key: "customer_store_enabled", label: "Customer Store" },
  { key: "reseller_enabled", label: "Reseller" },
  { key: "affiliate_enabled", label: "Affiliate" },
  { key: "white_label_enabled", label: "White-label" },
];

/** How a product may be trialled: on or off, its length, and the plans a trial
 * may start on (moved from the Phase 11 console; a trial may now be switched
 * on before the product has a plan - nobody can start it until one is chosen). */
export function TrialConfigEditor({
  product,
  busy,
  onSave,
}: {
  product: ConsoleProduct;
  busy: boolean;
  onSave: (config: TrialConfig, message: string) => void;
}) {
  const current = product.channels.trial;
  const [enabled, setEnabled] = useState(current?.enabled ?? false);
  const [days, setDays] = useState(String(current?.duration_days ?? 14));
  const [seatLimit, setSeatLimit] = useState(current?.seat_limit != null ? String(current.seat_limit) : "");
  const [plans, setPlans] = useState<string[]>(current?.eligible_plan_codes ?? []);
  const standardPlans = product.plans.filter((p) => p.kind === "standard");
  const ids = { days: useId(), seats: useId() };
  const badDays = !/^\d+$/.test(days.trim()) || Number(days) < 1 || Number(days) > 365;

  return (
    <form
      className={cp.form}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(
          {
            enabled,
            duration_days: Number(days),
            seat_limit: seatLimit.trim() ? Number(seatLimit) : null,
            payment_method_required: false,
            eligible_plan_codes: plans,
          },
          `Trial configuration for ${product.name} saved.`,
        );
      }}
    >
      <Segmented
        legend="Free trial"
        options={[
          { value: "enabled", label: "Enabled" },
          { value: "disabled", label: "Disabled" },
        ]}
        value={enabled ? "enabled" : "disabled"}
        onChange={(v) => setEnabled(v === "enabled")}
      />
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.days}>
          Trial duration (days)
        </label>
        <input id={ids.days} className="w360-input" type="number" min={1} max={365} required value={days} onChange={(e) => setDays(e.target.value)} />
        {badDays && <p className="w360-error-text">Enter a trial length from 1 to 365 days.</p>}
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.seats}>
          Seat limit (empty = the plan&apos;s own included seats)
        </label>
        <input id={ids.seats} className="w360-input" type="number" min={1} value={seatLimit} onChange={(e) => setSeatLimit(e.target.value)} />
      </div>
      <fieldset className={cp.field}>
        <legend className="w360-label">Eligible plans</legend>
        {standardPlans.length === 0 && <p className={styles.muted}>This product has no standard plan yet. Nobody can start a trial until one is chosen here.</p>}
        {standardPlans.map((plan) => (
          <label key={plan.code} className="w360-label">
            <input
              type="checkbox"
              checked={plans.includes(plan.code)}
              onChange={(e) => setPlans((cur) => (e.target.checked ? [...cur, plan.code] : cur.filter((c) => c !== plan.code)))}
            />{" "}
            {plan.name}
            {(!plan.is_public || plan.status !== "available" || !plan.priced) && <span className={styles.muted}> (not yet on sale)</span>}
          </label>
        ))}
      </fieldset>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || badDays}>
          Save trial configuration
        </button>
      </div>
    </form>
  );
}

export function AvailabilityTab({
  product: p,
  manage,
  busy,
  act,
}: {
  product: ConsoleProduct;
  manage: boolean;
  busy: boolean;
  act: Act;
}) {
  const [flags, setFlags] = useState<Record<string, Allowed>>(() =>
    Object.fromEntries(CHANNELS.map((c) => [c.key, p[c.key] ? "allowed" : "not_allowed"])),
  );
  const visibility = visibilityOf(p.listing);
  const changed = CHANNELS.filter((c) => (flags[c.key] === "allowed") !== p[c.key]);

  return (
    <div className={styles.page}>
      <section className={styles.card} aria-labelledby="lifecycle-title">
        <h2 id="lifecycle-title" className={styles.cardTitle}>
          Status and store visibility
        </h2>
        <dl className={styles.facts}>
          <div>
            <dt>Product status</dt>
            <dd>
              <span className={STATUS_BADGE[p.manager_status]}>{STATUS_LABEL[p.manager_status]}</span>
            </dd>
          </div>
          <div>
            <dt>Store visibility</dt>
            <dd>
              <span className={VISIBILITY_BADGE[visibility]}>{VISIBILITY_LABEL[visibility]}</span>
            </dd>
          </div>
        </dl>
        <p className={styles.muted}>Published means visible in the store. It never gives anyone access: that needs a trial or a purchase.</p>
        {manage && (
          <div className={styles.toolbar}>
            {p.manager_status !== "archived" && visibility !== "published" && (
              <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={() => void act(() => adminApi.productLifecycle(p.id, "publish"), `${p.name} is published to the store.`)}>
                Publish
              </button>
            )}
            {visibility === "published" && (
              <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => adminApi.productLifecycle(p.id, "unpublish"), `${p.name} is hidden from the store.`)}>
                Unpublish
              </button>
            )}
            {p.manager_status === "archived" && p.status === "deprecated" && (
              <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => adminApi.productLifecycle(p.id, "restore"), `${p.name} is active again (hidden).`)}>
                Restore
              </button>
            )}
            {p.manager_status !== "archived" && (
              <ReasonAction
                label="Archive"
                danger
                busy={busy}
                onConfirm={() => act(() => adminApi.productLifecycle(p.id, "archive"), `${p.name} is archived. Its history is kept.`)}
              />
            )}
          </div>
        )}
      </section>

      <section className={styles.card} aria-labelledby="channels-title">
        <h2 id="channels-title" className={styles.cardTitle}>
          Where it may be offered
        </h2>
        <p className={styles.cardLead}>Eligibility only: partners still need the product granted, and customers still need an entitlement.</p>
        <form
          className={styles.page}
          onSubmit={(e) => {
            e.preventDefault();
            const changes: ProductChanges = Object.fromEntries(changed.map((c) => [c.key, flags[c.key] === "allowed"]));
            void act(() => adminApi.updateProduct(p.id, changes), "Availability saved.");
          }}
        >
          <div className={styles.channelGrid}>
            {CHANNELS.map((c) =>
              manage ? (
                <Segmented key={c.key} legend={c.label} options={ALLOWED} value={flags[c.key]} onChange={(v) => setFlags((cur) => ({ ...cur, [c.key]: v }))} />
              ) : (
                <p key={c.key}>
                  {c.label}: <strong>{p[c.key] ? "Allowed" : "Not allowed"}</strong>
                </p>
              ),
            )}
          </div>
          {manage && (
            <div>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || changed.length === 0}>
                Save availability
              </button>
            </div>
          )}
        </form>
      </section>

      <section className={styles.card} aria-labelledby="trial-title">
        <h2 id="trial-title" className={styles.cardTitle}>
          Trial
        </h2>
        {manage ? (
          <TrialConfigEditor product={p} busy={busy} onSave={(config, message) => void act(() => adminApi.setTrialConfig(p.id, config), message)} />
        ) : (
          <p>{p.channels.trial?.enabled ? `On, ${p.channels.trial.duration_days} days` : "Off"}</p>
        )}
      </section>
    </div>
  );
}
