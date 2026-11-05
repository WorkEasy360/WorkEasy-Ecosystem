"use client";

import { useId, useState } from "react";
import { adminApi } from "@/lib/api/controlPlane";
import { storeAdminApi, type Coupon, type CouponInput } from "@/lib/api/store";
import { dialog, FormPopup } from "../Modal";
import { LoadGate, Status, Table, useAction, useLoad, when } from "../ui";
import cp from "../ControlPlane.module.css";
import { TextField } from "../widgets";
import { StoreTabs } from "./StoreTabs";
import styles from "./ProductManager.module.css";

/**
 * Super Admin → Store → Coupons (ADR-058): issue discount codes customers type
 * on a product page or at checkout. The server applies a code to the first
 * payment only (never a set-up fee or a renewal), at most 99% off, and counts
 * a use when an order is placed - giving it back if the order is cancelled or
 * expires. A code and its discount never change; disable it and issue another.
 */

const CODE = /^[A-Z0-9][A-Z0-9_-]{2,31}$/;

/** A `datetime-local` value as the ISO the server reads, or null. */
function moment(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function CouponForm({
  products,
  onCreated,
}: {
  products: { code: string; name: string }[];
  onCreated: () => void;
}) {
  const [code, setCode] = useState("");
  const [kind, setKind] = useState<"percent" | "fixed">("percent");
  const [value, setValue] = useState("");
  const [currency, setCurrency] = useState("INR");
  const [description, setDescription] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [starts, setStarts] = useState("");
  const [ends, setEnds] = useState("");
  const [limit, setLimit] = useState("");
  const [once, setOnce] = useState(true);
  const kindId = useId();
  const startsId = useId();
  const endsId = useId();

  const clean = code.trim().toUpperCase();
  const amount = Number(value);
  const codeProblem =
    clean && !CODE.test(clean) ? "3 to 32 letters, digits, - or _." : undefined;
  const valueProblem =
    value === ""
      ? undefined
      : kind === "percent"
        ? !(amount > 0 && amount <= 99)
          ? "A percentage from 0.01 to 99."
          : undefined
        : !(amount > 0)
          ? "An amount above zero."
          : undefined;
  const limitNumber = limit.trim() === "" ? null : Number(limit);
  const limitProblem =
    limitNumber !== null && !(Number.isInteger(limitNumber) && limitNumber >= 1)
      ? "A whole number, or empty for no limit."
      : undefined;
  const ready =
    CODE.test(clean) && value !== "" && !valueProblem && !limitProblem;

  function body(): CouponInput {
    const input: CouponInput = {
      code: clean,
      kind,
      description: description.trim(),
      product_codes: chosen,
      starts_at: moment(starts),
      ends_at: moment(ends),
      max_redemptions: limitNumber,
      once_per_organization: once,
    };
    if (kind === "percent") input.percent_bp = Math.round(amount * 100);
    else {
      input.amount_minor = Math.round(amount * 100);
      input.currency = currency.trim().toUpperCase();
    }
    return input;
  }

  function reset() {
    setCode("");
    setKind("percent");
    setValue("");
    setCurrency("INR");
    setDescription("");
    setChosen([]);
    setStarts("");
    setEnds("");
    setLimit("");
    setOnce(true);
  }

  return (
    <div className={styles.toolbar}>
      <FormPopup
        label="+ New coupon"
        title="New coupon"
        intro="Customers type the code on a product page or at checkout. It lowers the first payment only, before GST."
        submitLabel="Create coupon"
        canSubmit={ready}
        onOpen={reset}
        onSubmit={async () => {
          await storeAdminApi.createCoupon(body());
          return `Coupon ${clean} created.`;
        }}
        onDone={onCreated}
      >
        <TextField
          label="Code"
          required
          showRequired
          maxLength={32}
          value={code}
          error={codeProblem}
          hint="e.g. LAUNCH40"
          onChange={setCode}
        />
        <div className={cp.field}>
          <label className="w360-label" htmlFor={kindId}>
            Discount
          </label>
          <select
            id={kindId}
            className="w360-input"
            value={kind}
            onChange={(e) => setKind(e.target.value as "percent" | "fixed")}
          >
            <option value="percent">Percent off</option>
            <option value="fixed">Fixed amount off</option>
          </select>
        </div>
        <TextField
          label={kind === "percent" ? "Percent off" : "Amount off"}
          required
          showRequired
          inputMode="decimal"
          value={value}
          error={valueProblem}
          hint={
            kind === "percent"
              ? "Up to 99%."
              : "In whole currency units, e.g. 500."
          }
          onChange={setValue}
        />
        {kind === "fixed" && (
          <TextField
            label="Currency"
            maxLength={3}
            value={currency}
            hint="The orders it applies to, e.g. INR."
            onChange={setCurrency}
          />
        )}
        <TextField
          label="Use limit"
          inputMode="numeric"
          value={limit}
          error={limitProblem}
          hint="How many orders in all. Empty = no limit."
          onChange={setLimit}
        />
        <TextField
          label="Note (staff only)"
          maxLength={200}
          value={description}
          hint="What the code is for."
          onChange={setDescription}
        />
        <div className={cp.field}>
          <label className="w360-label" htmlFor={startsId}>
            Starts
          </label>
          <input
            id={startsId}
            type="datetime-local"
            className="w360-input"
            value={starts}
            onChange={(e) => setStarts(e.target.value)}
          />
          <p className="w360-hint">Empty = now.</p>
        </div>
        <div className={cp.field}>
          <label className="w360-label" htmlFor={endsId}>
            Ends
          </label>
          <input
            id={endsId}
            type="datetime-local"
            className="w360-input"
            value={ends}
            onChange={(e) => setEnds(e.target.value)}
          />
          <p className="w360-hint">Empty = no end date.</p>
        </div>
        <fieldset className={`${cp.field} ${dialog.span}`}>
          <legend className="w360-label">Products</legend>
          <div className={styles.choiceGrid}>
            {products.map((p) => (
              <label key={p.code} className="w360-label">
                <input
                  type="checkbox"
                  checked={chosen.includes(p.code)}
                  onChange={(e) =>
                    setChosen((cur) =>
                      e.target.checked
                        ? [...cur, p.code]
                        : cur.filter((c) => c !== p.code),
                    )
                  }
                />{" "}
                {p.name}
              </label>
            ))}
          </div>
          <p className="w360-hint">None ticked = every product.</p>
        </fieldset>
        <label className={`w360-label ${dialog.span}`}>
          <input
            type="checkbox"
            checked={once}
            onChange={(e) => setOnce(e.target.checked)}
          />{" "}
          Once per customer
        </label>
      </FormPopup>
    </div>
  );
}

function CouponRow({
  coupon: c,
  names,
  onChanged,
}: {
  coupon: Coupon;
  names: Record<string, string>;
  onChanged: () => void;
}) {
  const { act, busy, feedback } = useAction(onChanged);
  const next = c.status === "active" ? "disabled" : "active";
  return (
    <tr>
      <th scope="row">
        <code>{c.code}</code>
        {c.description && <div className={styles.muted}>{c.description}</div>}
      </th>
      <td>{c.label}</td>
      <td>
        {c.product_codes.length
          ? c.product_codes.map((p) => names[p] ?? p).join(", ")
          : "All products"}
      </td>
      <td>
        {c.starts_at ? when(c.starts_at) : "Now"} –{" "}
        {c.ends_at ? when(c.ends_at) : "No end"}
      </td>
      <td className={cp.num}>
        {c.redemptions}
        {c.max_redemptions ? ` / ${c.max_redemptions}` : ""}
        {c.once_per_organization && (
          <div className={styles.muted}>once per customer</div>
        )}
      </td>
      <td>
        <Status value={c.status} />
      </td>
      <td>
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          disabled={busy}
          aria-label={`${next === "disabled" ? "Disable" : "Enable"} coupon ${c.code}`}
          onClick={() =>
            void act(
              () => storeAdminApi.updateCoupon(c.id, { status: next }),
              `Coupon ${c.code} ${next === "disabled" ? "disabled" : "enabled"}.`,
            )
          }
        >
          {next === "disabled" ? "Disable" : "Enable"}
        </button>
        {feedback}
      </td>
    </tr>
  );
}

export function Coupons() {
  const [load, reload] = useLoad(
    () => Promise.all([storeAdminApi.coupons(), adminApi.products()]),
    [],
  );
  return (
    <div className={styles.page}>
      <h1>Coupons</h1>
      <StoreTabs current="coupons" />
      <LoadGate load={load} what="the coupons">
        {([coupons, products]) => {
          const list = products.results.map((p) => ({
            code: p.code,
            name: p.name,
          }));
          const names = Object.fromEntries(list.map((p) => [p.code, p.name]));
          return (
            <>
              <CouponForm products={list} onCreated={reload} />
              <Table
                caption="Coupons"
                head={[
                  "Code",
                  "Discount",
                  "Products",
                  "Valid",
                  "Used",
                  "Status",
                  "",
                ]}
                isEmpty={coupons.results.length === 0}
                empty="No coupons yet."
              >
                {coupons.results.map((c) => (
                  <CouponRow
                    key={c.id}
                    coupon={c}
                    names={names}
                    onChanged={reload}
                  />
                ))}
              </Table>
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}
