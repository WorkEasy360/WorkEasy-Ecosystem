"use client";

import { useState } from "react";
import { adminProgrammeApi, type MarginRule } from "@/lib/api/adminProgramme";
import { partnerConsoleApi } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { dialog, FormPopup } from "./Modal";
import { LoadGate, Table, money, useAction, useLoad, when } from "./ui";
import { SelectField, TextAreaField, TextField } from "./widgets";
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
      <AffiliatePolicySection manage={manageFees} />
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
      {manage && (
        <div>
          <FormPopup
            label="+ Set a margin"
            title="Set a margin"
            submitLabel="Save margin"
            disabled={busy}
            canSubmit={bp !== null && !(scope === "partner" && !partnerId)}
            onOpen={() => {
              setScope("reseller");
              setPartnerId("");
              setProduct("");
              setDiscount("");
              setRecurring(true);
            }}
            onSubmit={async () => {
              if (bp === null) return;
              await adminProgrammeApi.setMargin({
                discount_bp: bp,
                recurring,
                product_code: product || undefined,
                ...(scope === "partner" ? { partner_id: partnerId } : { experience: scope }),
              });
              return "Margin saved. It applies to sales from now on.";
            }}
            onDone={reload}
          >
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
            <label className={`w360-label ${dialog.span}`}>
              <input type="checkbox" checked={recurring} onChange={(e) => setRecurring(e.target.checked)} /> Also earn the margin on paid renewals
            </label>
          </FormPopup>
        </div>
      )}
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
    </section>
  );
}

/** Partners → Upgrade Fees (2026-11-03): the set-up fees alone, where staff look
 * for them. Super Admin's Partner Programme shows the same section. */
export function UpgradeFeesScreen() {
  const { permissions } = useAdmin();
  return (
    <div className={cp.stack}>
      <h1>Upgrade fees</h1>
      <p className={cp.lead}>
        What a partner pays to upgrade - Affiliate to Reseller, or to White-label - and how to pay it offline. Upgrade requests and their
        payments are on each partner&apos;s Upgrade panel.
      </p>
      <FeesSection manage={can(permissions, "platform.staff_partner.manage")} />
    </div>
  );
}

function FeesSection({ manage }: { manage: boolean }) {
  const [load, reload] = useLoad(() => adminProgrammeApi.fees(), []);
  const [target, setTarget] = useState("reseller");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("INR");
  const [instructions, setInstructions] = useState("");
  // The amount travels as the decimal text typed; the server converts it to
  // minor units for the currency (no money arithmetic in the browser).
  const valid = /^\d{1,10}(\.\d{1,2})?$/.test(amount.trim());
  return (
    <section className={styles.section} aria-labelledby="setup-fees">
      <h2 id="setup-fees" className={styles.sectionTitle}>
        Partner set-up fees
      </h2>
      <p className={cp.lead}>
        An optional one-time fee for becoming a Reseller or White-label partner. The partner pays it online (when a payment provider
        takes the currency) or offline, giving the transfer&apos;s reference, which you verify before approving. Zero removes the fee.
        Requests already open keep the fee they started with.
      </p>
      {manage && (
        <div>
          <FormPopup
            label="Set a fee"
            title="Set a fee"
            submitLabel="Save fee"
            canSubmit={valid}
            onOpen={() => {
              setTarget("reseller");
              setAmount("");
              setCurrency("INR");
              setInstructions("");
            }}
            onSubmit={async () => {
              await adminProgrammeApi.setFee({ experience: target, amount: amount.trim(), currency, instructions: instructions.trim() });
              return "Set-up fee saved.";
            }}
            onDone={reload}
          >
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
            <div className={dialog.span}>
              <TextAreaField
                label="How to pay offline"
                value={instructions}
                onChange={setInstructions}
                maxLength={1000}
                hint="Shown to the partner beside the fee: bank account, IFSC, UPI ID. The partner then gives the transfer's reference."
              />
            </div>
          </FormPopup>
        </div>
      )}
      <LoadGate load={load} what="set-up fees">
        {(d) => (
          <Table caption="Set-up fees" head={["Becoming", "Fee", "How to pay offline"]}>
            {(["reseller", "white_label"] as const).map((key) => (
              <tr key={key}>
                <th scope="row">{key === "reseller" ? "Reseller" : "White-label"}</th>
                <td className={cp.num}>{d.fees[key] ? money(d.fees[key]!.amount_minor, d.fees[key]!.currency) : "No fee"}</td>
                <td className={cp.preWrap}>{d.fees[key]?.instructions || "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
    </section>
  );
}

/** ADR-054: the affiliate programme's attribution window and what a product link
 * does once its affiliate may no longer promote the product. Super Admin only. */
function AffiliatePolicySection({ manage }: { manage: boolean }) {
  const [load, reload] = useLoad(() => adminProgrammeApi.affiliatePolicy(), []);
  const [days, setDays] = useState("");
  const [revoked, setRevoked] = useState("");
  const number = Number(days.trim());
  const valid = days.trim() !== "" && Number.isInteger(number) && number >= 1 && number <= 365;
  return (
    <section className={styles.section} aria-labelledby="affiliate-policy">
      <h2 id="affiliate-policy" className={styles.sectionTitle}>
        Affiliate attribution policy
      </h2>
      <p className={cp.lead}>
        A visitor is credited to the first affiliate link they follow, for the attribution window. A later click on another link is
        counted for that link but never takes the customer. Changes apply to new visits only. Revoking a product from an affiliate
        always stops new links; this decides whether its existing links keep taking visitors. Earned commission is never touched.
      </p>
      <LoadGate load={load} what="the affiliate policy">
        {(d) => (
          <>
            <dl className={cp.factsGrid}>
              <dt>Attribution window</dt>
              <dd>{d.attribution_days} days, first touch</dd>
              <dt>After a product is revoked</dt>
              <dd>{d.revoked_links === "stop" ? "Existing links stop taking new visitors" : "Existing links keep taking visitors"}</dd>
              <dt>Last changed</dt>
              <dd>{d.updated_at ? when(d.updated_at) : "Never - the platform default applies"}</dd>
            </dl>
            {manage && (
              <div>
                <FormPopup
                  label="Change the policy"
                  title="Change the affiliate policy"
                  size="sm"
                  submitLabel="Save policy"
                        canSubmit={valid}
                  onOpen={() => {
                    setDays(String(d.attribution_days));
                    setRevoked(d.revoked_links);
                  }}
                  onSubmit={async () => {
                    await adminProgrammeApi.setAffiliatePolicy({ attribution_days: number, revoked_links: revoked || d.revoked_links });
                    return "Affiliate policy saved.";
                  }}
                  onDone={reload}
                >
                  <TextField
                    label="Attribution window (days)"
                    value={days}
                    onChange={setDays}
                    inputMode="numeric"
                    maxLength={3}
                    hint={`1 to ${d.max_attribution_days}. Now ${d.attribution_days}.`}
                    error={days && !valid ? `Enter a whole number from 1 to ${d.max_attribution_days}.` : undefined}
                  />
                  <SelectField
                    label="Existing links after a revocation"
                    value={revoked || d.revoked_links}
                    onChange={setRevoked}
                    options={[
                      { value: "stop", label: "Stop taking new visitors" },
                      { value: "continue", label: "Keep taking visitors" },
                    ]}
                  />
                </FormPopup>
              </div>
            )}
          </>
        )}
      </LoadGate>
    </section>
  );
}
