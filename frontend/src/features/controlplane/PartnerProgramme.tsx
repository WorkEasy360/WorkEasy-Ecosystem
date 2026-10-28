"use client";

import { useState } from "react";
import { adminProgrammeApi, type MarginRule } from "@/lib/api/adminProgramme";
import { partnerConsoleApi } from "@/lib/api/partnerConsole";
import { LoadGate, Table, money, useAction, useLoad, when } from "./ui";
import { SelectField, TextField } from "./widgets";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";

/**
 * Super Admin → Currency & pricing → the partner programme (ADR-053).
 *
 * - **Partner margins**: a discount off list price - a default for Reseller and
 *   for White-label partners, and overrides for one partner, optionally for one
 *   product. A partner's cost is list less the discount; its margin is what its
 *   customer pays less that cost, earned on the ledger instead of a commission.
 *   Every change is a new version from now on; earlier sales keep theirs.
 * - **Set-up fees**: an optional one-time fee for becoming a Reseller or a
 *   White-label partner. Settled offline: approval records the payment reference.
 *
 * The browser sends a scope and whole basis points; the server validates both.
 */

const SCOPES = [
  { value: "reseller", label: "Default for Reseller partners" },
  { value: "white_label", label: "Default for White-label partners" },
  { value: "partner", label: "One partner (override)" },
];

const pct = (bp: number) => `${(bp / 100).toFixed(2)}%`;

/** "12.5" -> 1250 basis points; null when it is not a percentage from 0 to 100. */
function toBp(text: string): number | null {
  const value = text.trim();
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(value)) return null;
  const [whole, frac = ""] = value.split(".");
  const bp = Number(whole) * 100 + Number(frac.padEnd(2, "0"));
  return bp <= 10000 ? bp : null;
}

export function PartnerProgramme({
  manage,
  manageFees,
  products,
}: {
  /** ``platform.staff_commission.manage``: set and end margins. */
  manage: boolean;
  /** ``platform.staff_partner.manage``: set the set-up fees. */
  manageFees: boolean;
  products: { code: string; name: string }[];
}) {
  return (
    <>
      <MarginsSection manage={manage} products={products} />
      <FeesSection manage={manageFees} />
    </>
  );
}

function MarginsSection({ manage, products }: { manage: boolean; products: { code: string; name: string }[] }) {
  const [load, reload] = useLoad(() => adminProgrammeApi.margins(), []);
  const [partners] = useLoad(() => partnerConsoleApi.resellers({ status: "active" }), []);
  const { act, busy, feedback } = useAction(reload);
  const [scope, setScope] = useState("reseller");
  const [partnerId, setPartnerId] = useState("");
  const [product, setProduct] = useState("");
  const [discount, setDiscount] = useState("");
  const [recurring, setRecurring] = useState(true);
  const bp = toBp(discount);
  const names = new Map(partners.status === "ready" ? partners.data.results.map((p) => [p.id, p.display_name]) : []);
  const scopeLabel = (r: MarginRule) =>
    r.partner_id ? `Override: ${names.get(r.partner_id) ?? r.partner_id}` : SCOPES.find((s) => s.value === r.experience)?.label ?? r.experience ?? "";

  return (
    <section className={styles.section} aria-labelledby="partner-margins">
      <h2 id="partner-margins" className={styles.sectionTitle}>
        Partner margins
      </h2>
      <p className={cp.lead}>
        A partner&apos;s cost is the list price less its discount; its margin (customer price less cost) is earned instead of a
        commission once a sale is paid. A change applies to sales from now on.
      </p>
      {feedback}
      <LoadGate load={load} what="partner margins">
        {(d) => (
          <Table
            caption="Margin rules in force"
            head={["Applies to", "Product", "Discount off list", "On renewals", "Since", ...(manage ? ["Actions"] : [])]}
            isEmpty={d.live.length === 0}
            empty="No margin rules: partners earn commission on every sale."
          >
            {d.live.map((r) => (
              <tr key={r.id}>
                <th scope="row">{scopeLabel(r)}</th>
                <td>{r.product_code || "All products"}</td>
                <td className={cp.num}>{pct(r.discount_bp)}</td>
                <td>{r.recurring ? "Yes" : "First sale only"}</td>
                <td>{when(r.effective_from)}</td>
                {manage && (
                  <td>
                    <button
                      type="button"
                      className="w360-btn w360-btn-secondary"
                      disabled={busy}
                      onClick={() => void act(() => adminProgrammeApi.endMargin(r.code), "Margin ended. Later sales earn commission again.")}
                    >
                      End<span className="w360-visually-hidden"> {scopeLabel(r)} margin</span>
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
      {manage && (
        <form
          className={cp.stack}
          onSubmit={(e) => {
            e.preventDefault();
            if (bp === null) return;
            void act(
              () =>
                adminProgrammeApi.setMargin({
                  discount_bp: bp,
                  recurring,
                  product_code: product || undefined,
                  ...(scope === "partner" ? { partner_id: partnerId } : { experience: scope }),
                }),
              "Margin saved. It applies to sales from now on.",
            );
          }}
        >
          <h3>Set a margin</h3>
          <SelectField label="Applies to" value={scope} onChange={setScope} options={SCOPES} />
          {scope === "partner" && (
            <SelectField
              label="Partner"
              value={partnerId}
              onChange={setPartnerId}
              options={[
                { value: "", label: "Choose a partner…" },
                ...(partners.status === "ready" ? partners.data.results.map((p) => ({ value: p.id, label: `${p.display_name} (${p.code})` })) : []),
              ]}
            />
          )}
          <SelectField
            label="Product"
            value={product}
            onChange={setProduct}
            options={[{ value: "", label: "All products" }, ...products.map((p) => ({ value: p.code, label: p.name }))]}
          />
          <TextField
            label="Discount off list price (%)"
            value={discount}
            onChange={setDiscount}
            inputMode="decimal"
            maxLength={6}
            hint="For example 20 or 12.5. The partner's cost is the list price less this."
            error={discount && bp === null ? "Enter a percentage from 0 to 100, with at most two decimals." : undefined}
          />
          <label>
            <input type="checkbox" checked={recurring} onChange={(e) => setRecurring(e.target.checked)} /> Also earn the margin on paid renewals
          </label>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || bp === null || (scope === "partner" && !partnerId)}>
              Save margin
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function FeesSection({ manage }: { manage: boolean }) {
  const [load, reload] = useLoad(() => adminProgrammeApi.fees(), []);
  const { act, busy, feedback } = useAction(reload);
  const [target, setTarget] = useState("reseller");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("INR");
  // The amount travels as the decimal text typed; the server converts it to
  // minor units for the currency (no money arithmetic in the browser).
  const valid = /^\d{1,10}(\.\d{1,2})?$/.test(amount.trim());
  return (
    <section className={styles.section} aria-labelledby="setup-fees">
      <h2 id="setup-fees" className={styles.sectionTitle}>
        Partner set-up fees
      </h2>
      <p className={cp.lead}>
        An optional one-time fee for becoming a Reseller or White-label partner. Workeasy360 invoices it; an upgrade with a fee is
        approved only with the payment reference. Zero removes the fee. Requests already open keep the fee they started with.
      </p>
      {feedback}
      <LoadGate load={load} what="set-up fees">
        {(d) => (
          <Table caption="Set-up fees" head={["Becoming", "Fee"]}>
            {(["reseller", "white_label"] as const).map((key) => (
              <tr key={key}>
                <th scope="row">{key === "reseller" ? "Reseller" : "White-label"}</th>
                <td className={cp.num}>{d.fees[key] ? money(d.fees[key]!.amount_minor, d.fees[key]!.currency) : "No fee"}</td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
      {manage && (
        <form
          className={cp.stack}
          onSubmit={(e) => {
            e.preventDefault();
            if (!valid) return;
            void act(() => adminProgrammeApi.setFee({ experience: target, amount: amount.trim(), currency }), "Set-up fee saved.");
          }}
        >
          <h3>Set a fee</h3>
          <SelectField
            label="Becoming"
            value={target}
            onChange={setTarget}
            options={[
              { value: "reseller", label: "Reseller" },
              { value: "white_label", label: "White-label" },
            ]}
          />
          <SelectField
            label="Currency"
            value={currency}
            onChange={setCurrency}
            options={["INR", "USD", "GBP", "EUR", "AED", "AUD", "CAD"].map((c) => ({ value: c, label: c }))}
          />
          <TextField
            label="Amount"
            value={amount}
            onChange={setAmount}
            inputMode="decimal"
            maxLength={13}
            hint="For example 25000 or 25000.00. Zero removes the fee."
            error={amount && !valid ? "Enter an amount with at most two decimals." : undefined}
          />
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !valid}>
              Save fee
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
