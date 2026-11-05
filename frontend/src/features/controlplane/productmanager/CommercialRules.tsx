"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { adminApi } from "@/lib/api/controlPlane";
import { INTERVAL_LABELS } from "@/lib/api/adminConsole";
import {
  storeAdminApi,
  type Channel,
  type ChannelQuote,
  type CommercialPlan,
  type CommercialProduct,
  type CommercialRules as Rules,
  type PriceRange,
} from "@/lib/api/store";
import { percentToBp, toMajor, toMinor } from "../amounts";
import { dialog, FormDialog } from "../Modal";
import { LoadGate, money, useAction, useLoad } from "../ui";
import { SelectField, TextField } from "../widgets";
import { refusal } from "./model";
import { StoreTabs } from "./StoreTabs";
import styles from "./ProductManager.module.css";

/**
 * Super Admin → Store → Commercial rules (ADR-056): every product with what
 * each Store level gets - who may sell or promote it, the customer price, the
 * affiliate commission, the reseller and white-label margins and the price
 * range a partner may set. The figures per plan are the central engine's
 * (`channel_pricing`), the same functions checkout and the commission ledger
 * use; every edit goes to the service that versions and audits it.
 */

const CHANNEL_LABEL: Record<Channel, string> = {
  direct: "Customer",
  affiliate: "Affiliate",
  reseller: "Reseller",
  white_label: "White-label",
};

const FLAG: Record<keyof CommercialProduct["channels"], string> = {
  customer_store: "customer_store_enabled",
  affiliate: "affiliate_enabled",
  reseller: "reseller_enabled",
  white_label: "white_label_enabled",
};

function pct(bp: number | null | undefined): string {
  if (bp == null) return "—";
  return `${(bp / 100).toFixed(bp % 100 === 0 ? 0 : 2)}%`;
}

function QuoteCell({ q }: { q: ChannelQuote | undefined }) {
  if (!q) return <span className={styles.muted}>No price</span>;
  const c = q.currency;
  return (
    <span>
      <strong>{money(q.customer_minor, c)}</strong>
      {q.discount_bp > 0 && q.channel === "direct" && <span className={styles.muted}> ({pct(q.discount_bp)} off {money(q.list_minor, c)})</span>}
      {q.earning === "margin" && (
        <span className={styles.muted}>
          <br />
          cost {money(q.partner_cost_minor ?? 0, c)} · margin {money(q.margin_minor ?? 0, c)} ({pct(q.margin_bp)})
        </span>
      )}
      {q.earning === "commission" && (
        <span className={styles.muted}>
          <br />
          commission {money(q.commission_minor ?? 0, c)}
        </span>
      )}
      {q.channel !== "direct" && (
        <span className={styles.muted}>
          <br />
          platform {money(q.platform_share_minor, c)}
        </span>
      )}
    </span>
  );
}

function CommissionDialog({ product, currency, onDone, onCancel }: { product: CommercialProduct; currency: string; onDone: (m: string | undefined) => void; onCancel: () => void }) {
  const current = product.affiliate_commission;
  const [plan, setPlan] = useState("");
  const [mode, setMode] = useState<"rate" | "fixed">(current?.fixed_amount_minor ? "fixed" : "rate");
  const [rate, setRate] = useState(current && !current.fixed_amount_minor ? String(current.rate_bp / 100) : "");
  const [fixed, setFixed] = useState(current?.fixed_amount_minor ? toMajor(current.fixed_amount_minor, current.fixed_currency || currency) : "");
  const [recurring, setRecurring] = useState(!!current?.recurring);
  const [months, setMonths] = useState(current?.recurring_months ? String(current.recurring_months) : "");
  const [from, setFrom] = useState("");
  const modeName = useId();

  async function save(): Promise<string> {
    const rateBp = percentToBp(rate);
    const fixedMinor = mode === "fixed" ? toMinor(fixed, currency) : null;
    if (mode === "rate" && rateBp === null) throw refusal("Enter a commission from 0 to 100%, up to two decimals.");
    if (mode === "fixed" && (fixedMinor === null || fixedMinor <= 0)) throw refusal("Enter the fixed commission as an amount above zero.");
    const m = months.trim() ? Number(months) : null;
    if (m !== null && (!Number.isInteger(m) || m < 1 || m > 120)) throw refusal("Months is a whole number from 1 to 120.");
    await storeAdminApi.setCommission({
      product_code: product.code,
      plan_code: plan,
      rate_bp: mode === "rate" ? (rateBp ?? 0) : 0,
      fixed_amount_minor: fixedMinor,
      fixed_currency: mode === "fixed" ? currency : "",
      recurring,
      recurring_months: recurring ? m : null,
      effective_from: from ? `${from}T00:00:00+00:00` : null,
    });
    return `Commission for ${product.name} saved. Sales already made keep the commission they were made under.`;
  }

  return (
    <FormDialog
      title={`Commission: ${product.name}`}
      intro="Earned by affiliates (and by selling partners without a margin rule) only after a verified payment."
      submitLabel="Save commission"
      onSubmit={save}
      onDone={onDone}
      onClose={onCancel}
    >
      <SelectField
        label="Applies to"
        value={plan}
        onChange={setPlan}
        options={[{ value: "", label: `Every plan of ${product.name}` }, ...product.plans.map((p) => ({ value: p.code, label: `Only ${p.name}` }))]}
      />
      <TextField label="Effective from (UTC)" type="date" value={from} onChange={setFrom} hint="Empty = now. A commission change never applies to past sales." />
      <fieldset className={dialog.span}>
        <legend className="w360-label">Commission type</legend>
        <div className={dialog.grid}>
          <div className={styles.radioStack}>
            <label className="w360-label">
              <input type="radio" name={modeName} checked={mode === "rate"} onChange={() => setMode("rate")} /> Percentage of the sale
            </label>
            <label className="w360-label">
              <input type="radio" name={modeName} checked={mode === "fixed"} onChange={() => setMode("fixed")} /> Fixed amount per payment ({currency})
            </label>
          </div>
          {mode === "rate" ? (
            <TextField label="Commission %" inputMode="decimal" value={rate} onChange={setRate} />
          ) : (
            <TextField label={`Fixed commission (${currency})`} inputMode="decimal" value={fixed} onChange={setFixed} />
          )}
        </div>
      </fieldset>
      <label className={`w360-label ${dialog.span}`}>
        <input type="checkbox" checked={recurring} onChange={(e) => setRecurring(e.target.checked)} /> Also earn on renewals (recurring)
      </label>
      {recurring && (
        <TextField label="For how many months" hint="Empty = as long as the customer stays." inputMode="numeric" value={months} onChange={setMonths} />
      )}
    </FormDialog>
  );
}

function MarginDialog({ product, experience, onDone, onCancel }: { product: CommercialProduct; experience: "reseller" | "white_label"; onDone: (m: string | undefined) => void; onCancel: () => void }) {
  const current = product.margins?.[experience];
  const [value, setValue] = useState(current ? String(current.discount_bp / 100) : "");
  const who = CHANNEL_LABEL[experience];
  return (
    <FormDialog
      title={`${who} margin: ${product.name}`}
      intro={`A ${who.toLowerCase()} buys at the list price less this discount and keeps the difference to what its customer pays. Workeasy360 keeps the rest. Applies to new sales from now; a partner's own override still wins.`}
      size="sm"
      submitLabel="Save margin"
      onSubmit={async () => {
        const bp = percentToBp(value);
        if (bp === null) throw refusal("Enter a margin from 0 to 100%, up to two decimals.");
        await storeAdminApi.setMargin({ experience, product_code: product.code, discount_bp: bp });
        return `${who} margin for ${product.name} saved.`;
      }}
      onDone={onDone}
      onClose={onCancel}
    >
      <TextField label="Margin (discount off list) %" inputMode="decimal" value={value} onChange={setValue} />
    </FormDialog>
  );
}

function RangeDialog({ plan, experience, currency, existing, onDone, onCancel }: {
  plan: CommercialPlan;
  experience: "reseller" | "white_label";
  currency: string;
  existing: PriceRange | undefined;
  onDone: (m: string | undefined) => void;
  onCancel: () => void;
}) {
  const [interval, setInterval] = useState(existing?.interval ?? "monthly");
  const [min, setMin] = useState(existing ? toMajor(existing.min_minor, currency) : "");
  const [max, setMax] = useState(existing?.max_minor != null ? toMajor(existing.max_minor, currency) : "");
  const [allowed, setAllowed] = useState(existing?.partner_may_set ?? true);
  return (
    <FormDialog
      title={`${CHANNEL_LABEL[experience]} price range: ${plan.name}`}
      intro="Partners may set their own customer price only inside this range, and only while it is allowed. Prices they already set stay until they change them."
      submitLabel="Save range"
      onSubmit={async () => {
        const low = toMinor(min, currency);
        const high = max.trim() ? toMinor(max, currency) : null;
        if (low === null) throw refusal("Enter the lowest allowed price.");
        if (max.trim() && (high === null || high < low)) throw refusal("The highest price must be at least the lowest.");
        await storeAdminApi.setRange({ experience, plan_id: plan.plan_id, interval, currency, min_minor: low, max_minor: high, partner_may_set: allowed });
        return `Price range for ${plan.name} saved.`;
      }}
      onDone={onDone}
      onClose={onCancel}
    >
      <div className={dialog.span}>
        <SelectField
          label="Billing cycle"
          value={interval}
          onChange={setInterval}
          options={["monthly", "quarterly", "semi_annual", "annual"].map((i) => ({ value: i, label: INTERVAL_LABELS[i] }))}
        />
      </div>
      <TextField label={`Lowest price (${currency})`} inputMode="decimal" value={min} onChange={setMin} />
      <TextField label={`Highest price (${currency})`} hint="Empty = no limit." inputMode="decimal" value={max} onChange={setMax} />
      <label className={`w360-label ${dialog.span}`}>
        <input type="checkbox" checked={allowed} onChange={(e) => setAllowed(e.target.checked)} /> Partners may set their own price
      </label>
    </FormDialog>
  );
}

type Dialog =
  | { kind: "commission"; product: CommercialProduct }
  | { kind: "margin"; product: CommercialProduct; experience: "reseller" | "white_label" }
  | { kind: "range"; plan: CommercialPlan; experience: "reseller" | "white_label"; existing?: PriceRange }
  | null;

function RangeList({ plan, experience, currency, manage, busy, open, act }: {
  plan: CommercialPlan;
  experience: "reseller" | "white_label";
  currency: string;
  manage: boolean;
  busy: boolean;
  open: (d: Dialog) => void;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const mine = plan.ranges.filter((r) => r.experience === experience && r.currency === currency);
  return (
    <div>
      {mine.length === 0 && <span className={styles.muted}>Set by Workeasy360</span>}
      {mine.map((r) => (
        <div key={r.id}>
          {INTERVAL_LABELS[r.interval]?.split(" ")[0] ?? r.interval}: {money(r.min_minor, r.currency)}–{r.max_minor != null ? money(r.max_minor, r.currency) : "no limit"}
          {!r.partner_may_set && <span className={styles.muted}> (locked)</span>}
          {manage && (
            <>
              {" "}
              <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => open({ kind: "range", plan, experience, existing: r })}>
                Edit
              </button>{" "}
              <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => storeAdminApi.removeRange(r.id), "Price range removed.")}>
                Remove
              </button>
            </>
          )}
        </div>
      ))}
      {manage && (
        <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => open({ kind: "range", plan, experience })}>
          Add range
        </button>
      )}
    </div>
  );
}

function ProductRules({ product, data, act, busy, open }: {
  product: CommercialProduct;
  data: Rules;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
  open: (d: Dialog) => void;
}) {
  const currency = data.currency ?? "";
  const { catalog, pricing, commission } = data.can_manage;
  const rule = product.affiliate_commission;
  return (
    <section className={styles.card} aria-labelledby={`cr-${product.code}`}>
      <div className={styles.toolbar}>
        <h2 id={`cr-${product.code}`} className={styles.cardTitle}>
          {product.name}
        </h2>
        <span className="w360-badge w360-badge-neutral">{product.status}</span>
        <Link href={`/admin/products/${product.id}?tab=plans`}>Plans &amp; Pricing</Link>
      </div>
      <fieldset className={styles.toolbar}>
        <legend className="w360-label">Who may sell or promote it</legend>
        {(Object.keys(FLAG) as (keyof CommercialProduct["channels"])[]).map((key) => (
          <label key={key} className="w360-label">
            <input
              type="checkbox"
              checked={product.channels[key]}
              disabled={!catalog || busy}
              onChange={(e) =>
                void act(
                  () => adminApi.updateProduct(product.id, { [FLAG[key]]: e.target.checked }),
                  `${product.name}: ${key.replace("_", " ")} ${e.target.checked ? "allowed" : "not allowed"}.`,
                )
              }
            />{" "}
            {key === "customer_store" ? "Customer store" : CHANNEL_LABEL[key as Channel]}
          </label>
        ))}
      </fieldset>
      {data.shows_money && (
        <div className={styles.formGrid}>
          <div>
            <p className="w360-label">Affiliate commission</p>
            <p>
              {rule ? (rule.fixed_amount_minor ? money(rule.fixed_amount_minor, rule.fixed_currency) : pct(rule.rate_bp)) : "None"}
              {rule && (
                <span className={styles.muted}>
                  {" "}
                  ({rule.scope === "all" ? "programme default" : "this product"}
                  {rule.recurring ? `, recurring${rule.recurring_months ? ` for ${rule.recurring_months} months` : ""}` : ", first payment only"})
                </span>
              )}
            </p>
            {commission && (
              <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => open({ kind: "commission", product })}>
                Edit commission
              </button>
            )}
          </div>
          {(["reseller", "white_label"] as const).map((exp) => {
            const m = product.margins?.[exp];
            return (
              <div key={exp}>
                <p className="w360-label">{CHANNEL_LABEL[exp]} margin</p>
                <p>
                  {m ? pct(m.discount_bp) : "None (earns commission)"}
                  {m && <span className={styles.muted}> ({m.scope === "all" ? "programme default" : "this product"})</span>}
                </p>
                {commission && (
                  <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => open({ kind: "margin", product, experience: exp })}>
                    Edit margin
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
      {product.plans.length === 0 ? (
        <p className={styles.muted}>No plans yet.</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="w360-visually-hidden">Plans of {product.name} by Store level</caption>
            <thead>
              <tr>
                <th scope="col">Plan</th>
                {data.shows_money && (["direct", "affiliate", "reseller", "white_label"] as Channel[]).map((c) => <th key={c} scope="col">{CHANNEL_LABEL[c]} (monthly)</th>)}
                <th scope="col">Reseller price range</th>
                <th scope="col">White-label price range</th>
              </tr>
            </thead>
            <tbody>
              {product.plans.map((plan) => (
                <tr key={plan.plan_id}>
                  <th scope="row">
                    {plan.name}
                    {plan.recommended && <span className="w360-badge w360-badge-info"> Recommended</span>}
                    <br />
                    <span className={styles.muted}>{plan.status}</span>
                  </th>
                  {data.shows_money &&
                    (["direct", "affiliate", "reseller", "white_label"] as Channel[]).map((c) => {
                      const byCycle = plan.channels[c] ?? {};
                      return (
                        <td key={c}>
                          <QuoteCell q={byCycle.monthly ?? Object.values(byCycle)[0]} />
                        </td>
                      );
                    })}
                  {(["reseller", "white_label"] as const).map((exp) => (
                    <td key={exp}>
                      <RangeList plan={plan} experience={exp} currency={currency} manage={pricing} busy={busy} open={open} act={act} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function CommercialRules() {
  const [currency, setCurrency] = useState<string | undefined>(undefined);
  const [load, reload] = useLoad(() => storeAdminApi.commercialRules(currency), [currency]);
  const { act, busy, feedback } = useAction(reload);
  const [opened, setOpened] = useState<Dialog>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const done = (m: string | undefined) => {
    setNotice(m ?? null);
    reload();
  };
  return (
    <div className={styles.page}>
      <h1>Commercial rules</h1>
      <StoreTabs current="rules" />
      <p className={styles.muted}>
        One central catalog, priced once. Each Store level sees the same prices; partners earn a margin or a commission computed on the server, only after verified payment.
      </p>
      {notice && <p role="status">{notice}</p>}
      {feedback}
      <LoadGate load={load} what="the commercial rules">
        {(data: Rules) => (
          <>
            {data.currencies.length > 1 && (
              <label className="w360-label">
                Currency{" "}
                <select className="w360-input" value={data.currency ?? ""} onChange={(e) => setCurrency(e.target.value)}>
                  {data.currencies.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
            )}
            {!data.currency && <p className={styles.muted}>No price book yet: create one on Currency &amp; pricing.</p>}
            {data.products.map((p) => (
              <ProductRules key={p.code} product={p} data={data} act={act} busy={busy} open={setOpened} />
            ))}
            {opened?.kind === "commission" && <CommissionDialog product={opened.product} currency={data.currency ?? ""} onDone={done} onCancel={() => setOpened(null)} />}
            {opened?.kind === "margin" && <MarginDialog product={opened.product} experience={opened.experience} onDone={done} onCancel={() => setOpened(null)} />}
            {opened?.kind === "range" && (
              <RangeDialog plan={opened.plan} experience={opened.experience} currency={data.currency ?? ""} existing={opened.existing} onDone={done} onCancel={() => setOpened(null)} />
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}
