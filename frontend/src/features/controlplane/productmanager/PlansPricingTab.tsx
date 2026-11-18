"use client";

import { useState } from "react";
import Link from "next/link";
import { INTERVAL_LABELS } from "@/lib/api/adminConsole";
import { storeAdminApi, type BoardPlan, type PlanBoard, type PriceRow } from "@/lib/api/store";
import { percentToBp, toMajor, toMinor } from "../amounts";
import { dialog, FormDialog } from "../Modal";
import { LoadGate, money, useAction, useLoad } from "../ui";
import { TextField } from "../widgets";
import { refusal } from "./model";
import styles from "./ProductManager.module.css";

/**
 * Products → one product → Plans & Pricing (ADR-056). Every plan of the product
 * as a card: its price per billing cycle (with discount, set-up fee and
 * effective dates, current and scheduled), its seats and trial, and the
 * everyday actions - edit price, set recommended, enable, disable, archive,
 * duplicate, publish a draft version.
 *
 * Nothing here computes a price. "Edit price" publishes a new price row
 * through the pricing service, which supersedes the old one (never edits it),
 * refuses to backdate, and keeps every past order's price reproducible.
 */

const CYCLES = ["monthly", "annual", "quarterly", "semi_annual"] as const;

const PLAN_STATUS: Record<string, { label: string; badge: string }> = {
  draft: { label: "Draft", badge: "w360-badge w360-badge-neutral" },
  available: { label: "On sale", badge: "w360-badge w360-badge-success" },
  grandfathered: { label: "Disabled for new sales", badge: "w360-badge w360-badge-warning" },
  retired: { label: "Archived", badge: "w360-badge w360-badge-warning" },
};

function pct(bp: number): string {
  return `${(bp / 100).toFixed(bp % 100 === 0 ? 0 : 2)}%`;
}

function dateOf(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : "";
}

/** A date field's value as the start of that day, UTC; empty means "now". */
function dayStart(value: string): string | null {
  return value ? `${value}T00:00:00+00:00` : null;
}

function PriceCell({ row }: { row: PriceRow }) {
  if (row.amount_minor === null) return <span>Tiered</span>;
  const net = row.net_amount_minor ?? row.amount_minor;
  return (
    <span>
      {row.discount_bp > 0 && (
        <>
          <s aria-label={`was ${money(row.amount_minor, row.currency)}`}>{money(row.amount_minor, row.currency)}</s>{" "}
        </>
      )}
      <strong>{money(net, row.currency)}</strong>
      {row.discount_bp > 0 && <span className={styles.muted}> ({pct(row.discount_bp)} off)</span>}
    </span>
  );
}

function PriceTable({ plan, prices, onClose, onCancel, manage, busy }: {
  plan: BoardPlan;
  prices: PriceRow[];
  onClose: (row: PriceRow) => void;
  onCancel: (row: PriceRow) => void;
  manage: boolean;
  busy: boolean;
}) {
  if (prices.length === 0) return <p className={styles.muted}>No price in this book yet.</p>;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className="w360-visually-hidden">Prices of {plan.name}</caption>
        <thead>
          <tr>
            <th scope="col">Billing cycle</th>
            <th scope="col">Price</th>
            <th scope="col">Set-up fee</th>
            <th scope="col">Effective</th>
            {manage && <th scope="col">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {prices.map((row) => (
            <tr key={row.price_id}>
              <th scope="row">
                {INTERVAL_LABELS[row.interval] ?? row.interval}
                {row.state === "scheduled" && <span className="w360-badge w360-badge-info"> Scheduled</span>}
              </th>
              <td>
                <PriceCell row={row} />
              </td>
              <td>{row.setup_fee_minor ? money(row.setup_fee_minor, row.currency) : "—"}</td>
              <td>
                {row.state === "scheduled" ? `From ${dateOf(row.valid_from)}` : `Since ${dateOf(row.valid_from)}`}
                {row.valid_to && ` until ${dateOf(row.valid_to)}`}
              </td>
              {manage && (
                <td>
                  {row.state === "scheduled" ? (
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => onCancel(row)}>
                      Cancel change
                    </button>
                  ) : (
                    !row.valid_to && (
                      <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => onClose(row)}>
                        End price
                      </button>
                    )
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface CycleDraft {
  amount: string;
  discount: string;
  setup: string;
}

/** Monthly and yearly (and any other cycle the plan has) in one form. Only the
 * cycles whose values changed are published, each as a new price row. */
function EditPriceDialog({ plan, board, bookId, onDone, onCancel }: {
  plan: BoardPlan;
  board: PlanBoard;
  bookId: string;
  onDone: (message: string | undefined) => void;
  onCancel: () => void;
}) {
  const book = board.books.find((b) => b.id === bookId);
  const currency = book?.currency ?? "";
  const target = plan.version ?? plan.draft_version;
  const current = (interval: string) => plan.prices.find((p) => p.price_book_id === bookId && p.interval === interval && p.state === "current");
  const cycles = CYCLES.filter((c) => c === "monthly" || c === "annual" || current(c));
  const [drafts, setDrafts] = useState<Record<string, CycleDraft>>(() =>
    Object.fromEntries(
      cycles.map((c) => {
        const row = current(c);
        return [
          c,
          {
            amount: row?.amount_minor != null ? toMajor(row.amount_minor, currency) : "",
            discount: row ? String(row.discount_bp / 100) : "0",
            setup: row?.setup_fee_minor ? toMajor(row.setup_fee_minor, currency) : "",
          },
        ];
      }),
    ),
  );
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");

  function parse(c: string): { amount: number; discount: number; setup: number | null } | string | null {
    const d = drafts[c];
    if (!d.amount.trim()) return null;
    const amount = toMinor(d.amount, currency);
    if (amount === null) return `${INTERVAL_LABELS[c]}: enter a price like 499 or 499.00.`;
    const discount = percentToBp(d.discount.trim() || "0");
    if (discount === null) return `${INTERVAL_LABELS[c]}: the discount is 0 to 100%, up to two decimals.`;
    const setup = d.setup.trim() ? toMinor(d.setup, currency) : null;
    if (d.setup.trim() && setup === null) return `${INTERVAL_LABELS[c]}: enter the set-up fee as an amount.`;
    return { amount, discount, setup };
  }

  async function save(): Promise<string> {
    if (!target) throw refusal("This plan has no version to price yet.");
    const work: { interval: string; amount: number; discount: number; setup: number | null }[] = [];
    for (const c of cycles) {
      const parsed = parse(c);
      if (typeof parsed === "string") throw refusal(parsed);
      if (parsed === null) continue;
      const row = current(c);
      const same = row && row.amount_minor === parsed.amount && row.discount_bp === parsed.discount && (row.setup_fee_minor ?? null) === parsed.setup;
      if (!same || from || until) work.push({ interval: c, ...parsed });
    }
    if (work.length === 0) throw refusal("Nothing changed.");
    for (const w of work) {
      await storeAdminApi.publishPrice({
        price_book_id: bookId,
        plan_version_id: target.id,
        interval: w.interval,
        amount_minor: w.amount,
        discount_bp: w.discount,
        setup_fee_minor: w.setup,
        valid_from: dayStart(from),
        valid_to: dayStart(until),
      });
    }
    return from ? `New price for ${plan.name} scheduled from ${from}.` : `Price of ${plan.name} updated. Existing subscriptions keep the price they bought at.`;
  }

  return (
    <FormDialog
      title={`Edit price: ${plan.name}`}
      intro={`${book?.name ?? "Price book"} (${currency}). A change applies to new purchases from its start date; existing orders and subscriptions keep their price.`}
      size="lg"
      submitLabel="Save price"
      canSubmit={!!target}
      onSubmit={save}
      onDone={onDone}
      onClose={onCancel}
    >
      {cycles.map((c) => (
        <fieldset key={c} className={dialog.span}>
          <legend className="w360-label">{INTERVAL_LABELS[c] ?? c}</legend>
          <div className={styles.formGrid3}>
            {(
              [
                ["amount", `Price (${currency})`, "Empty = no price for this cycle"],
                ["discount", "Discount %", "A promotional discount off the price"],
                ["setup", `Set-up fee (${currency})`, "One-time, with the first purchase"],
              ] as const
            ).map(([key, label, hint]) => (
              <TextField
                key={key}
                label={label}
                hint={hint}
                inputMode="decimal"
                value={drafts[c][key]}
                onChange={(v) => setDrafts((cur) => ({ ...cur, [c]: { ...cur[c], [key]: v } }))}
              />
            ))}
          </div>
        </fieldset>
      ))}
      <TextField label="Effective from (UTC)" type="date" value={from} onChange={setFrom} hint="Empty = now. A price change cannot start in the past." />
      <TextField label="Effective until (UTC, optional)" type="date" value={until} onChange={setUntil} hint="E.g. the end of a promotion." />
    </FormDialog>
  );
}

function DuplicateDialog({ plan, onDone, onCancel }: { plan: BoardPlan; onDone: (message: string | undefined) => void; onCancel: () => void }) {
  const [code, setCode] = useState(`${plan.code}-copy`);
  const [name, setName] = useState(`${plan.name} (copy)`);
  return (
    <FormDialog
      title={`Duplicate ${plan.name}`}
      intro="Copies the plan, its seats, trial, features and current prices as a new draft."
      size="sm"
      submitLabel="Duplicate"
      onSubmit={async () => {
        const made = await storeAdminApi.duplicatePlan(plan.id, code.trim(), name.trim());
        return `${name.trim()} created as a draft with ${made.prices_copied} price${made.prices_copied === 1 ? "" : "s"}. Publish its version and enable it to sell.`;
      }}
      onDone={onDone}
      onClose={onCancel}
    >
      <TextField label="Plan name" value={name} maxLength={128} onChange={setName} />
      <TextField label="Plan code" value={code} maxLength={64} onChange={(v) => setCode(v.toLowerCase())} hint="Permanent; lowercase letters, digits, hyphens or underscores." />
    </FormDialog>
  );
}

function EndPriceDialog({ row, onDone, onCancel }: { row: PriceRow; onDone: (message: string | undefined) => void; onCancel: () => void }) {
  const [until, setUntil] = useState("");
  return (
    <FormDialog
      title="End this price"
      intro="Without a replacement, the plan cannot be bought in this cycle after the end date. Existing subscriptions are not affected."
      size="sm"
      submitLabel="End price"
      onSubmit={async () => {
        await storeAdminApi.closePrice(row.price_id, dayStart(until));
        return until ? `The price ends on ${until}.` : "The price has ended.";
      }}
      onDone={onDone}
      onClose={onCancel}
    >
      <TextField label="End on (UTC)" type="date" value={until} onChange={setUntil} hint="Empty = now." />
    </FormDialog>
  );
}

type Dialog =
  | { kind: "price"; plan: BoardPlan }
  | { kind: "duplicate"; plan: BoardPlan }
  | { kind: "end"; row: PriceRow }
  | null;

function PlanCard({ plan, board, bookId, act, busy, open }: {
  plan: BoardPlan;
  board: PlanBoard;
  bookId: string;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
  open: (d: Dialog) => void;
}) {
  const status = PLAN_STATUS[plan.status] ?? { label: plan.status, badge: "w360-badge w360-badge-neutral" };
  const terms = plan.version ?? plan.draft_version;
  const prices = plan.prices.filter((p) => p.price_book_id === bookId);
  const { catalog, pricing } = board.can_manage;
  return (
    <section className={styles.card} aria-labelledby={`plan-${plan.id}`}>
      <div className={styles.toolbar}>
        <h3 id={`plan-${plan.id}`} className={styles.cardTitle}>
          {plan.name}
        </h3>
        <span className={status.badge}>{status.label}</span>
        {plan.recommended && <span className="w360-badge w360-badge-info">Recommended</span>}
        {plan.kind === "add_on" && <span className="w360-badge w360-badge-neutral">Add-on</span>}
        {plan.billing_type === "free_trial" && <span className="w360-badge w360-badge-neutral">Free trial plan</span>}
      </div>
      <p className={styles.muted}>
        <span className={styles.code}>{plan.code}</span>
        {terms && (
          <>
            {" "}
            · {terms.included_seats} user{terms.included_seats === 1 ? "" : "s"} included
            {terms.max_seats ? `, up to ${terms.max_seats}` : ""} · {terms.trial_days ? `${terms.trial_days}-day trial` : "no plan trial"} ·{" "}
            {plan.version ? `version ${plan.version.version}` : "not published"}
            {plan.draft_version && ` (draft v${plan.draft_version.version} waiting)`}
          </>
        )}
      </p>
      <PriceTable
        plan={plan}
        prices={prices}
        manage={pricing}
        busy={busy}
        onClose={(row) => open({ kind: "end", row })}
        onCancel={(row) => void act(() => storeAdminApi.cancelPrice(row.price_id), "The scheduled price change was withdrawn.")}
      />
      {(catalog || pricing) && (
        <div className={styles.toolbar}>
          {pricing && terms && (
            <button type="button" className="w360-btn w360-btn-primary" disabled={busy || !bookId} onClick={() => open({ kind: "price", plan })}>
              Edit price
            </button>
          )}
          {catalog && plan.draft_version && (
            <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => storeAdminApi.publishVersion(plan.draft_version!.id), `Version ${plan.draft_version!.version} of ${plan.name} is published.`)}>
              Publish draft version
            </button>
          )}
          {catalog && plan.status !== "retired" && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={busy}
              onClick={() => void act(() => storeAdminApi.updatePlan(plan.id, { recommended: !plan.recommended }), plan.recommended ? `${plan.name} is no longer recommended.` : `${plan.name} is the recommended plan.`)}
            >
              {plan.recommended ? "Unset recommended" : "Set recommended"}
            </button>
          )}
          {catalog && (plan.status === "draft" || plan.status === "grandfathered") && (
            <button type="button" className="w360-btn w360-btn-secondary" disabled={busy || !plan.version} onClick={() => void act(() => storeAdminApi.setPlanStatus(plan.id, "available"), `${plan.name} is on sale.`)}>
              Enable
            </button>
          )}
          {catalog && plan.status === "available" && (
            <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => storeAdminApi.setPlanStatus(plan.id, "grandfathered"), `${plan.name} is closed to new sales; existing customers keep it.`)}>
              Disable
            </button>
          )}
          {catalog && pricing && (
            <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => open({ kind: "duplicate", plan })}>
              Duplicate
            </button>
          )}
          {catalog && plan.status !== "retired" && plan.status !== "available" && (
            <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => storeAdminApi.setPlanStatus(plan.id, "retired"), `${plan.name} is archived.`)}>
              Archive
            </button>
          )}
          {catalog && (
            <Link className="w360-btn w360-btn-secondary" href={`/admin/plans?product=${encodeURIComponent(board.product.code)}`}>
              Edit features &amp; trial
            </Link>
          )}
        </div>
      )}
    </section>
  );
}

export function PlansPricingTab({ productId }: { productId: string }) {
  const [load, reload] = useLoad(() => storeAdminApi.planBoard(productId), [productId]);
  const { act, busy, feedback } = useAction(reload);
  const [chosenBook, setChosenBook] = useState<string>("");
  const [opened, setOpened] = useState<Dialog>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const done = (message: string | undefined) => {
    setNotice(message ?? null);
    reload();
  };

  return (
    <LoadGate load={load} what="the plans and prices">
      {(board: PlanBoard) => {
        const bookId = chosenBook || board.books.find((b) => !b.region)?.id || board.books[0]?.id || "";
        return (
          <div className={styles.page}>
            <section className={styles.card} aria-labelledby="board-title">
              <h2 id="board-title" className={styles.cardTitle}>
                Plans &amp; Pricing
              </h2>
              <p className={styles.cardLead}>
                What {board.product.name} costs in every Store - customer, affiliate, reseller and white-label all read these prices. Partner margins and commissions are on Store → Commercial rules.
              </p>
              <div className={styles.toolbar}>
                {board.books.length > 0 ? (
                  <label className="w360-label">
                    Price book{" "}
                    <select className="w360-input" value={bookId} onChange={(e) => setChosenBook(e.target.value)}>
                      {board.books.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.name} ({b.currency}
                          {b.region ? `, ${b.region}` : ""})
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <p className={styles.muted}>
                    No price book yet. <Link href="/admin/pricing">Create one on Currency &amp; pricing.</Link>
                  </p>
                )}
                {board.can_manage.catalog && (
                  <Link className="w360-btn w360-btn-primary" href={`/admin/plans?product=${encodeURIComponent(board.product.code)}`}>
                    Add a plan
                  </Link>
                )}
              </div>
              {notice && <p role="status">{notice}</p>}
              {feedback}
            </section>
            {board.plans.length === 0 && <p className={styles.muted}>No plans yet. Add a plan to put {board.product.name} on sale.</p>}
            {board.plans.map((plan) => (
              <PlanCard key={plan.id} plan={plan} board={board} bookId={bookId} act={act} busy={busy} open={setOpened} />
            ))}
            {opened?.kind === "price" && <EditPriceDialog plan={opened.plan} board={board} bookId={bookId} onDone={done} onCancel={() => setOpened(null)} />}
            {opened?.kind === "duplicate" && <DuplicateDialog plan={opened.plan} onDone={done} onCancel={() => setOpened(null)} />}
            {opened?.kind === "end" && <EndPriceDialog row={opened.row} onDone={done} onCancel={() => setOpened(null)} />}
          </div>
        );
      }}
    </LoadGate>
  );
}
