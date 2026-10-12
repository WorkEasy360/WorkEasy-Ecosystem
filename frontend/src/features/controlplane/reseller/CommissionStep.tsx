"use client";

import { useId, useState } from "react";
import type { ConsoleProduct } from "@/lib/api/partnerConsole";
import { formatBasisPoints } from "../amounts";
import { PayoutPrefsForm } from "../ProfileForms";
import { money } from "../ui";
import { Segmented, TextField } from "../widgets";
import styles from "../ControlPlane.module.css";
import local from "../PartnerConsole.module.css";
import cls from "../AddReseller.module.css";
import { localNowInput, type ReviewState, type StepProps } from "./draft";

function ScopeChoice({
  draft,
  set,
  products,
  error,
}: Pick<StepProps, "draft" | "set"> & { products: ConsoleProduct[]; error?: string }) {
  const errorId = useId();
  const names = (code: string) => products.find((p) => p.code === code)?.name ?? code;
  return (
    <>
      <Segmented<"all" | "products">
        legend="Applies to"
        options={[
          { value: "all", label: "All products" },
          { value: "products", label: "Selected products", disabled: draft.products.length === 0 },
        ]}
        value={draft.commissionScope}
        onChange={(v) => {
          set("commissionScope", v);
          if (v === "products" && draft.commissionProducts.length === 0) set("commissionProducts", [...draft.products]);
        }}
        hint={
          draft.products.length === 0
            ? "Allow at least one product on the Products step to limit the rate to some of them."
            : "One rate for every product the partner may sell, or only the products you tick."
        }
      />
      {draft.commissionScope === "products" && (
        <fieldset className={local.checkGroup} aria-describedby={error ? errorId : undefined}>
          <legend>Products this rate applies to</legend>
          <ul className={local.checks}>
            {draft.products.map((code) => (
              <li key={code}>
                <label>
                  <input
                    type="checkbox"
                    checked={draft.commissionProducts.includes(code)}
                    onChange={(e) =>
                      set(
                        "commissionProducts",
                        e.target.checked ? [...draft.commissionProducts, code] : draft.commissionProducts.filter((c) => c !== code),
                      )
                    }
                  />{" "}
                  {names(code)}
                </label>
              </li>
            ))}
          </ul>
          {error && (
            <p id={errorId} className="w360-error-text">
              {error}
            </p>
          )}
        </fieldset>
      )}
    </>
  );
}

/** Step 6: the partner's commission, and how and when it is paid out. */
export function CommissionStep({
  draft,
  set,
  patchProfile,
  errors,
  review,
  canCommission,
  products,
  onRefresh,
}: StepProps & {
  review: ReviewState;
  canCommission: boolean;
  products: ConsoleProduct[];
  onRefresh: (force: boolean) => void;
}) {
  // The earliest moment the date picker offers; the server refuses a past one regardless.
  const [earliest] = useState(localNowInput);
  const commission = review.data?.commission;
  const programme = commission?.programme_default_bp;
  const example = commission?.example;
  const custom = draft.commissionMode === "custom";
  return (
    <>
      <h3 className={styles.cardTitle}>Commission</h3>
      <Segmented<"default" | "custom">
        legend="Commission rate"
        options={[
          { value: "default", label: "Programme default" },
          { value: "custom", label: "A rate for this partner", disabled: !canCommission },
        ]}
        value={draft.commissionMode}
        onChange={(v) => set("commissionMode", v)}
        hint={
          canCommission
            ? "A rate for this partner applies from the moment it is assigned. Sales already made keep the rate they were computed under: changing a rule never changes an old sale."
            : "Assigning a rate needs the platform.staff_commission.manage permission."
        }
      />
      <p>
        Method: <strong>a percentage of each sale</strong>. It is the only commission method the platform has.
      </p>
      {!custom ? (
        <p role="status">
          {programme === null || programme === undefined
            ? "The programme default is still being checked, or there is none: with no default rule this partner earns nothing until one exists or a rate is assigned."
            : `The partner will earn the programme default, currently ${formatBasisPoints(programme)}. If the default changes, so does this partner's rate for later sales.`}
        </p>
      ) : (
        <>
          <TextField
            label="Commission rate (%)"
            value={draft.ratePercent}
            onChange={(v) => set("ratePercent", v)}
            onBlur={() => onRefresh(false)}
            inputMode="decimal"
            required
            showRequired
            hint="A percentage from 0 to 100, for example 15 or 12.5."
            error={errors["commission.rate"]}
          />
          <ScopeChoice draft={draft} set={set} products={products} error={errors["commission.scope"]} />
          <TextField
            label="Effective from (optional)"
            type="datetime-local"
            value={draft.effectiveFrom}
            onChange={(v) => set("effectiveFrom", v)}
            onBlur={() => onRefresh(false)}
            min={earliest}
            hint="Empty means from the moment the rate is assigned. A rate never takes effect in the past."
            error={errors["commission.effective_from"]}
          />
        </>
      )}
      {errors.commission && (
        <p className="w360-error-text" role="alert">
          {errors.commission}
        </p>
      )}
      {commission && (
        <p>
          Hold period: <strong>{commission.hold_days} {commission.hold_days === 1 ? "day" : "days"}</strong> before a commission becomes
          payable. It is a platform setting, not chosen per partner.
        </p>
      )}
      {example && (
        <div className={cls.example} role="group" aria-label="Worked example">
          <p>
            <strong>Worked example, calculated by the server</strong> at {formatBasisPoints(example.rate_bp)}
            {review.stale ? " (from before your last change - use Update figures)" : ""}
          </p>
          <dl className={cls.facts}>
            <dt>Customer purchase</dt>
            <dd>{money(example.purchase_minor, example.currency)}</dd>
            <dt>Commission</dt>
            <dd>{money(example.commission_minor, example.currency)}</dd>
            <dt>Platform net</dt>
            <dd>{money(example.platform_net_minor, example.currency)}</dd>
          </dl>
        </div>
      )}
      <div>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onRefresh(true)} disabled={review.load === "loading"}>
          Update figures
        </button>
      </div>

      <h3 className={styles.cardTitle}>Payouts</h3>
      <p>
        A payout moves <strong>Draft → Ready → Approved → Paid</strong>, one step at a time, by staff. Nothing is transferred
        automatically, and no bank details are collected.
      </p>
      <PayoutPrefsForm value={draft.profile} onChange={patchProfile} errors={errors} currency={draft.currency} />
    </>
  );
}
