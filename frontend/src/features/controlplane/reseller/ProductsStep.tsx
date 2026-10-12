"use client";

import { useId, useState } from "react";
import type { ConsoleProduct } from "@/lib/api/partnerConsole";
import { Status } from "../ui";
import styles from "../ControlPlane.module.css";
import cls from "../AddReseller.module.css";
import type { StepProps } from "./draft";

/** What the platform configured for a product's trial, in a sentence. The
 * numbers are the server's; this only words them. */
export function trialSummary(product: ConsoleProduct): string {
  const trial = product.trial;
  if (!trial || !trial.enabled) return "No trial configured";
  const plans = trial.eligible_plan_codes
    .map((code) => product.plans.find((p) => p.plan_code === code)?.plan_name ?? code)
    .join(", ");
  const seats =
    trial.seat_limit === null
      ? "with the plan's included seats"
      : `up to ${trial.seat_limit} ${trial.seat_limit === 1 ? "seat" : "seats"}`;
  return `${trial.duration_days}-day trial on ${plans}, ${seats}`;
}

function ProductRow({
  product,
  allowed,
  trialAllowed,
  onAllow,
  onTrial,
}: {
  product: ConsoleProduct;
  allowed: boolean;
  trialAllowed: boolean;
  onAllow: (on: boolean) => void;
  onTrial: (on: boolean) => void;
}) {
  const detailId = useId();
  const trialHelpId = useId();
  const offerable = product.status === "available";
  const platformTrial = Boolean(product.trial?.enabled);
  const planNames = product.plans.map((p) => p.plan_name);
  return (
    <li className={cls.product}>
      <div className={cls.productHead}>
        <label className={cls.productName}>
          <input
            type="checkbox"
            checked={allowed}
            disabled={!offerable}
            onChange={(e) => onAllow(e.target.checked)}
            aria-describedby={detailId}
          />{" "}
          {product.name}
        </label>
        <Status value={product.status} />
      </div>
      <p id={detailId} className={styles.muted}>
        {offerable ? "Allow this partner to sell it. " : "Not available to sell yet, so it cannot be allowed. "}
        Plans: {planNames.length > 0 ? planNames.join(", ") : "none yet"}. Platform trial: {trialSummary(product)}.
      </p>
      {allowed && (
        <div className={cls.trialBox}>
          <label>
            <input
              type="checkbox"
              checked={platformTrial && trialAllowed}
              disabled={!platformTrial}
              onChange={(e) => onTrial(e.target.checked)}
              aria-describedby={trialHelpId}
            />{" "}
            Allow free trial for this partner&apos;s customers{" "}
            <span className="w360-visually-hidden">({product.name})</span>
          </label>
          <p id={trialHelpId} className={styles.muted}>
            {platformTrial
              ? "Untick to stop this partner's customers starting the platform's trial of this product. A trial that has already started is not affected."
              : "The platform has no trial configured for this product, so there is nothing to allow. A partner can only narrow the platform's trial, never add one."}
          </p>
        </div>
      )}
    </li>
  );
}

/** Step 4: which catalog products the partner may sell, and whether its customers may trial them. */
export function ProductsStep({ products, draft, set, errors }: Pick<StepProps, "draft" | "set" | "errors"> & { products: ConsoleProduct[] }) {
  const searchId = useId();
  const [search, setSearch] = useState("");
  const needle = search.trim().toLowerCase();
  const shown = products.filter((p) => `${p.name} ${p.code}`.toLowerCase().includes(needle));
  const offered = products.filter((p) => p.status === "available");
  const allowed = new Set(draft.products);

  function allow(code: string, on: boolean) {
    set("products", on ? [...draft.products, code] : draft.products.filter((c) => c !== code));
  }

  return (
    <>
      <p className={styles.muted}>
        Allowing a product is what gives a partner the right to sell it. With no product allowed the partner can sell nothing - the
        server enforces this - and you can allow products later from the partner&apos;s page.
      </p>
      {products.length > 6 && (
        <div className={styles.field}>
          <label className="w360-label" htmlFor={searchId}>
            Search products
          </label>
          <input id={searchId} className="w360-input" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={80} />
        </div>
      )}
      {products.length === 0 ? (
        <p role="status">No product is in the catalog yet.</p>
      ) : shown.length === 0 ? (
        <p role="status">No product matches “{search}”.</p>
      ) : (
        <ul className={cls.productList} aria-label="Products">
          {shown.map((p) => (
            <ProductRow
              key={p.code}
              product={p}
              allowed={allowed.has(p.code)}
              trialAllowed={draft.trialAllowed[p.code] ?? true}
              onAllow={(on) => allow(p.code, on)}
              onTrial={(on) => set("trialAllowed", { ...draft.trialAllowed, [p.code]: on })}
            />
          ))}
        </ul>
      )}
      <p className={styles.muted}>
        {draft.products.length} of {offered.length} available {offered.length === 1 ? "product" : "products"} allowed.
      </p>
      {(errors.products || errors.product_options) && (
        <p className="w360-error-text" role="alert">
          {errors.products ?? errors.product_options}
        </p>
      )}
    </>
  );
}
