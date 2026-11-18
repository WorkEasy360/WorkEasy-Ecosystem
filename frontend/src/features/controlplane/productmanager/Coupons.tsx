"use client";

import { useId, useState } from "react";
import { adminApi } from "@/lib/api/controlPlane";
import { storeAdminApi, type Coupon, type CouponInput } from "@/lib/api/store";
import { toBasisPoints, toMinor } from "../amounts";
import { dialog, FormPopup } from "../Modal";
import { LoadGate, Status, useAction, useLoad, when } from "../ui";
import cp from "../ControlPlane.module.css";
import { TextField } from "../widgets";
import { StoreHeader } from "./StoreTabs";
import {
  Button,
  DataTable,
  EmptyState,
  Page,
  SectionHeader,
  Stack,
  Trunc,
  useConfirm,
  type Column,
} from "../kit";
import { iconLabel, popupButton } from "../kit/PopupLabel";
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
  // Typed text to the integers the server stores, without floating point.
  const percentBp = toBasisPoints(value);
  const amountMinor = toMinor(value, currency.trim().toUpperCase());
  const codeProblem =
    clean && !CODE.test(clean) ? "3 to 32 letters, digits, - or _." : undefined;
  const valueProblem =
    value === ""
      ? undefined
      : kind === "percent"
        ? !(percentBp !== null && percentBp > 0 && percentBp <= 9900)
          ? "A percentage from 0.01 to 99."
          : undefined
        : !(amountMinor !== null && amountMinor > 0)
          ? "An amount above zero, with at most two decimals."
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
    if (kind === "percent") input.percent_bp = percentBp ?? undefined;
    else {
      input.amount_minor = amountMinor ?? undefined;
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
        label={iconLabel("New coupon")}
        buttonClassName={popupButton()}
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

/** The coupon list (admin-ux phase 3): the one table, with Enable / Disable in its
 * Actions column. Disabling asks first: the code stops working at checkout at once. */
function CouponTable({
  coupons,
  names,
  onChanged,
}: {
  coupons: Coupon[];
  names: Record<string, string>;
  onChanged: () => void;
}) {
  const { act, busy, feedback } = useAction(onChanged);
  const confirm = useConfirm();

  const enable = (c: Coupon) =>
    void act(
      () => storeAdminApi.updateCoupon(c.id, { status: "active" }),
      `Coupon ${c.code} enabled.`,
    );
  const disable = (c: Coupon) =>
    confirm.ask({
      title: `Disable coupon ${c.code}?`,
      message:
        "Customers can no longer use this code on a new order, from now on. Uses already recorded stay as they are. You can enable it again later.",
      confirmLabel: "Disable coupon",
      danger: true,
      onConfirm: () =>
        act(
          () => storeAdminApi.updateCoupon(c.id, { status: "disabled" }),
          `Coupon ${c.code} disabled.`,
        ),
    });

  const columns: Column<Coupon>[] = [
    {
      key: "code",
      header: "Code",
      value: (c) => c.code,
      render: (c) => (
        <Stack
          main={<code>{c.code}</code>}
          sub={c.description ? <Trunc max="md">{c.description}</Trunc> : undefined}
        />
      ),
    },
    { key: "discount", header: "Discount", value: (c) => c.label, render: (c) => c.label },
    {
      key: "products",
      header: "Products",
      priority: "medium",
      value: (c) => c.product_codes.length,
      render: (c) => (
        <Trunc max="md">
          {c.product_codes.length
            ? c.product_codes.map((p) => names[p] ?? p).join(", ")
            : "All products"}
        </Trunc>
      ),
    },
    {
      key: "valid",
      header: "Valid",
      priority: "low",
      value: (c) => c.starts_at ?? "",
      render: (c) => (
        <>
          {c.starts_at ? when(c.starts_at) : "Now"} –{" "}
          {c.ends_at ? when(c.ends_at) : "No end"}
        </>
      ),
    },
    {
      key: "used",
      header: "Used",
      align: "right",
      priority: "medium",
      value: (c) => c.redemptions,
      render: (c) => (
        <Stack
          main={`${c.redemptions}${c.max_redemptions ? ` / ${c.max_redemptions}` : ""}`}
          sub={c.once_per_organization ? "once per customer" : undefined}
        />
      ),
    },
    {
      key: "status",
      header: "Status",
      priority: "essential",
      value: (c) => c.status,
      render: (c) => <Status value={c.status} />,
    },
  ];

  return (
    <>
      {feedback}
      <DataTable
        caption="Coupons"
        columns={columns}
        rows={coupons}
        rowKey={(c) => c.id}
        rowLabel={(c) => `coupon ${c.code}`}
        hideEmptyColumns={false}
        actions={(c) =>
          c.status === "active" ? (
            <Button
              size="sm"
              icon="ban"
              disabled={busy}
              aria-label={`Disable coupon ${c.code}`}
              onClick={() => disable(c)}
            >
              Disable
            </Button>
          ) : (
            <Button
              size="sm"
              icon="play"
              disabled={busy}
              aria-label={`Enable coupon ${c.code}`}
              onClick={() => enable(c)}
            >
              Enable
            </Button>
          )
        }
        empty={
          <EmptyState
            icon="box"
            title="No coupons yet."
            hint="Create a code customers can type at checkout."
            compact
          />
        }
      />
      {confirm.dialog}
    </>
  );
}

export function Coupons() {
  const [load, reload] = useLoad(
    () => Promise.all([storeAdminApi.coupons(), adminApi.products()]),
    [],
  );
  return (
    <Page>
      <StoreHeader current="coupons" />
      <LoadGate load={load} what="the coupons">
        {([coupons, products]) => {
          const list = products.results.map((p) => ({
            code: p.code,
            name: p.name,
          }));
          const names = Object.fromEntries(list.map((p) => [p.code, p.name]));
          return (
            <>
              <SectionHeader
                title="All coupons"
                description="A coupon lowers the first payment only, before GST."
                actions={<CouponForm products={list} onCreated={reload} />}
              />
              <CouponTable coupons={coupons.results} names={names} onChanged={reload} />
            </>
          );
        }}
      </LoadGate>
    </Page>
  );
}
