"use client";

import Link from "next/link";
import { useId, useRef, useState } from "react";
import { StatusChips } from "@/features/controlplane/kit";
import { FormDialog } from "@/features/controlplane/Modal";
import { LoadGate, Status, Table, describeError, money, useLoad, when } from "@/features/controlplane/ui";
import { newKey } from "@/lib/api/controlPlane";
import { panelApi } from "@/lib/api/partnerPanel";
import {
  walletApi,
  type PartnerSubscriptionRow,
  type PlanChangeQuote,
  type RenewalQuote,
  type SubscriptionFilter,
} from "@/lib/api/partnerWallet";
import { EmptyState, PageHeader, usePanel } from "./PanelKit";
import { useNote } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";


const FILTERS: { value: SubscriptionFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "pending_setup", label: "Pending Setup" },
  { value: "expiring", label: "Expiring" },
  { value: "expired", label: "Expired" },
];

/**
 * Customer Subscriptions (ADR-063, the specification's 7.1): your customers'
 * subscriptions, filtered by Active, Pending Setup, Expiring or Expired. The
 * subscription, its setup, its payment and who pays are separate columns, never
 * one merged status. A plan you pay for from your Platform Wallet is renewed
 * and changed here; one your customer pays for is managed from its customer row.
 */
export function CustomerSubscriptions() {
  const { id } = usePanel();
  const [filter, setFilter] = useState<SubscriptionFilter>("all");
  const [q, setQ] = useState("");
  const searchId = useId();
  const [load, reload] = useLoad(() => walletApi.subscriptions(id, filter, q), [id, filter, q]);
  const note = useNote();
  return (
    <div className={styles.page}>
      <PageHeader title="Customer Subscriptions" lead="Every subscription of your assigned customers. Statuses are filters; actions are on each row." />
      <div className={styles.filters}>
        <div className="w360-field">
          <label htmlFor={searchId}>Search customers</label>
          <input id={searchId} className="w360-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <LoadGate load={load} what="your customers' subscriptions">
        {(data) => (
          <>
            <StatusChips
              label="Subscription status"
              chips={FILTERS.map((f) => ({ ...f, count: data.counts[f.value] }))}
              value={filter}
              onChange={setFilter}
            />
            {note.node}
            {data.truncated && <p className={styles.muted}>Showing the first 300 customers; search to narrow the list.</p>}
            {data.items.length === 0 ? (
              <EmptyState title="No subscriptions here" icon="receipt">
                <p>{filter === "all" ? "Your customers hold no subscription yet." : "No subscription matches this filter."}</p>
              </EmptyState>
            ) : (
              <Table
                caption="Customer subscriptions"
                head={["Customer", "Product and plan", "Validity", "Renewal", "Status", "Setup", "Paid by", "Order", ""]}
              >
                {data.items.map((row) => (
                  <SubscriptionRow key={row.subscription_id} row={row} onChanged={(m) => { note.show(m); reload(); }} />
                ))}
              </Table>
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}

function SubscriptionRow({ row, onChanged }: { row: PartnerSubscriptionRow; onChanged: (message: string) => void }) {
  const { can } = usePanel();
  const [open, setOpen] = useState<"history" | "renew" | "change" | null>(null);
  const live = row.lines.filter((l) => !l.removed_at);
  const first = live[0] ?? row.lines[0];
  const walletPaid = row.paid_by === "partner_wallet";
  return (
    <tr>
      <th scope="row">{row.customer ?? "Former customer"}</th>
      <td>
        {live.length === 0
          ? "No live plan"
          : live.map((l) => (
              <div key={l.subscription_item_id}>
                {l.product_name} · {l.plan_name}
                {l.quantity > 1 ? ` × ${l.quantity}` : ""}
              </div>
            ))}
      </td>
      <td>
        {when(row.current_period_start)} – {when(row.state.ends_at ?? row.current_period_end)}
      </td>
      <td>{row.renewal_date ? when(row.renewal_date) : row.state.expired ? "—" : "Does not renew"}</td>
      <td>
        <Status value={row.state.expired ? "expired" : row.state.expiring ? "expiring" : row.status} />
      </td>
      <td>{row.state.pending_setup ? <Status value="pending" label="Pending setup" /> : "—"}</td>
      <td>
        {walletPaid ? "Your Platform Wallet" : "Customer"}
        {walletPaid && row.renewal_prepaid && <div className={styles.muted}>Next period paid</div>}
      </td>
      <td>
        {first?.order ? (
          <>
            {first.order.number}
            <div className={styles.muted}>{first.order.paid ? "Paid" : first.order.status === "pending_payment" ? "Awaiting payment" : "Nothing due"}</div>
          </>
        ) : (
          "—"
        )}
      </td>
      <td>
        <div className={styles.actionsRow}>
          <button type="button" className="w360-btn w360-btn-secondary" aria-haspopup="dialog" onClick={() => setOpen("history")}>
            View
          </button>
          {walletPaid && can("subscription.sell") && !row.state.expired && (
            <button type="button" className="w360-btn w360-btn-secondary" aria-haspopup="dialog" onClick={() => setOpen("renew")}>
              Renew
            </button>
          )}
          {walletPaid && can("subscription.manage") && live.length > 0 && (
            <button type="button" className="w360-btn w360-btn-secondary" aria-haspopup="dialog" onClick={() => setOpen("change")}>
              Change Plan
            </button>
          )}
        </div>
        {open === "history" && <HistoryDialog row={row} onClose={() => setOpen(null)} />}
        {open === "renew" && <RenewDialog row={row} onClose={() => setOpen(null)} onDone={onChanged} />}
        {open === "change" && <ChangePlanDialog row={row} onClose={() => setOpen(null)} onDone={onChanged} />}
      </td>
    </tr>
  );
}

function HistoryDialog({ row, onClose }: { row: PartnerSubscriptionRow; onClose: () => void }) {
  return (
    <FormDialog title={`${row.customer ?? "Customer"} - subscription history`} submitLabel="Close" cancelLabel="Close" columns={1} onSubmit={() => undefined} onClose={onClose}>
      <p>
        Billing cycle: {row.billing_interval}. Current period {when(row.current_period_start)} to {when(row.current_period_end)}.
        {row.cancel_at ? ` Ends ${when(row.cancel_at)}.` : ""}
      </p>
      <Table caption="Plan lines, oldest first" head={["Product and plan", "Seats", "From", "Until", "Order"]} numeric={["Seats"]}>
        {row.lines.map((l) => (
          <tr key={l.subscription_item_id}>
            <th scope="row">
              {l.product_name} · {l.plan_name}
            </th>
            <td className={styles.num}>{l.quantity}</td>
            <td>{when(l.added_at)}</td>
            <td>{l.removed_at ? when(l.removed_at) : "Live"}</td>
            <td>{l.order?.number ?? "—"}</td>
          </tr>
        ))}
      </Table>
    </FormDialog>
  );
}

function RenewDialog({ row, onClose, onDone }: { row: PartnerSubscriptionRow; onClose: () => void; onDone: (m: string) => void }) {
  const { id } = usePanel();
  const [load] = useLoad(() => walletApi.renewalQuote(id, row.organization_id, row.subscription_id), [id, row]);
  const [quote, setQuote] = useState<RenewalQuote | null>(null);
  const shown = quote ?? (load.status === "ready" ? load.data : null);
  const payable = shown && !shown.already_paid && !shown.blocked && shown.wallet.sufficient;
  return (
    <FormDialog
      title="Renew from your Platform Wallet"
      submitLabel={shown && shown.charge_minor > 0 ? `Pay ${money(shown.charge_minor, shown.wallet.currency)}` : "Renew"}
      columns={1}
      canSubmit={Boolean(payable)}
      onClose={onClose}
      onDone={(m) => onDone(m ?? "Renewed.")}
      onSubmit={async () => {
        if (!shown) return;
        const done = await walletApi.renew(id, row.organization_id, row.subscription_id, shown.charge_minor);
        setQuote(done);
        return `The period from ${when(done.period_end)} is paid. It renews then without another charge.`;
      }}
    >
      <LoadGate load={load} what="the renewal">
        {() =>
          shown ? (
            shown.already_paid ? (
              <p>The next period, from {when(shown.period_end)}, is already paid.</p>
            ) : shown.blocked ? (
              <p role="note">{shown.message}</p>
            ) : (
              <>
                <p>Pay the next period now, from {when(shown.period_end)}, at your purchase price:</p>
                <ul className={styles.list}>
                  {shown.lines.map((l) => (
                    <li key={`${l.product_code}-${l.plan_code}`}>
                      {l.plan_code} × {l.quantity}: {money(l.partner_cost_minor, shown.wallet.currency)}
                    </li>
                  ))}
                </ul>
                <p>
                  Wallet: {money(shown.wallet.balance_minor, shown.wallet.currency)} now,{" "}
                  {shown.wallet.sufficient ? money(shown.wallet.balance_after_minor, shown.wallet.currency) : "not enough"} after.
                </p>
              </>
            )
          ) : null
        }
      </LoadGate>
    </FormDialog>
  );
}

function ChangePlanDialog({ row, onClose, onDone }: { row: PartnerSubscriptionRow; onClose: () => void; onDone: (m: string) => void }) {
  const { id } = usePanel();
  const live = row.lines.filter((l) => !l.removed_at && l.plan_version_id);
  const [itemId, setItemId] = useState(live[0]?.subscription_item_id ?? "");
  const [target, setTarget] = useState("");
  const [quote, setQuote] = useState<PlanChangeQuote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = useRef(newKey());
  const [plans] = useLoad(() => panelApi.plans(id), [id]);
  const line = live.find((l) => l.subscription_item_id === itemId);
  async function price(to: string) {
    setTarget(to);
    setQuote(null);
    setError(null);
    key.current = newKey();
    if (!to || !itemId) return;
    try {
      setQuote(await walletApi.planChangeQuote(id, row.organization_id, { subscription_item_id: itemId, to_plan_version_id: to }));
    } catch (err) {
      setError(describeError(err));
    }
  }
  return (
    <FormDialog
      title="Change plan"
      intro="The plan rules Workeasy360 set apply: an upgrade starts now and your wallet pays the difference for the rest of the period; a downgrade starts at the period end and costs nothing now."
      submitLabel={quote && quote.charge_minor > 0 ? `Pay ${money(quote.charge_minor, quote.wallet.currency)} and change` : "Change plan"}
      columns={1}
      canSubmit={Boolean(quote && quote.wallet.sufficient)}
      onClose={onClose}
      onDone={(m) => onDone(m ?? "Plan changed.")}
      onSubmit={async () => {
        if (!quote) return;
        const done = await walletApi.changePlan(
          id,
          row.organization_id,
          { subscription_item_id: itemId, to_plan_version_id: target, expected_charge_minor: quote.charge_minor, expected_version: row.version },
          key.current,
        );
        return done.timing === "immediate" ? "The new plan is active now." : "The new plan starts at the end of the current period.";
      }}
    >
      {live.length > 1 && (
        <div className="w360-field">
          <label htmlFor="change-line">Line</label>
          <select id="change-line" className="w360-input" value={itemId} onChange={(e) => setItemId(e.target.value)}>
            {live.map((l) => (
              <option key={l.subscription_item_id} value={l.subscription_item_id}>
                {l.product_name} · {l.plan_name}
              </option>
            ))}
          </select>
        </div>
      )}
      <LoadGate load={plans} what="the plans you may sell">
        {(data) => (
          <div className="w360-field">
            <label htmlFor="change-target">New plan</label>
            <select id="change-target" className="w360-input" value={target} onChange={(e) => void price(e.target.value)}>
              <option value="">Choose a plan</option>
              {data.plans
                .filter((p) => p.enabled_for_sale && p.product_code === line?.product_code && p.plan_version_id !== line?.plan_version_id)
                .map((p) => (
                  <option key={p.plan_version_id} value={p.plan_version_id}>
                    {p.plan_name}
                  </option>
                ))}
            </select>
          </div>
        )}
      </LoadGate>
      {quote && (
        <p>
          {quote.timing === "immediate" ? "Starts now." : "Starts at the period end."} Your purchase price goes from{" "}
          {money(quote.from_cost_minor, quote.wallet.currency)} to {money(quote.to_cost_minor, quote.wallet.currency)}. Charged now:{" "}
          <strong>{money(quote.charge_minor, quote.wallet.currency)}</strong>
          {!quote.wallet.sufficient && " - your wallet does not hold enough."}
        </p>
      )}
      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}
    </FormDialog>
  );
}

const PAYMENT_STATUS: Record<string, string> = {
  pending: "Starting",
  awaiting_payment: "Awaiting payment",
  succeeded: "Paid",
  failed: "Failed",
  expired: "Expired",
};

/** Billing Records (the specification's 7.2): what you paid Workeasy360, and what
 * your Platform Wallet paid for your customers. Customer orders are the next tab. */
export function BillingRecords() {
  const { id, base, can } = usePanel();
  const [load] = useLoad(() => walletApi.billingRecords(id), [id]);
  // A partner without a Platform Wallet (White Label alone, an Affiliate) has no
  // wallet payments to show: the section is for those that hold one.
  const wallet = can("wallet.view");
  return (
    <div className={styles.page}>
      <PageHeader
        title="Billing Records"
        lead={
          wallet
            ? "Your payments to Workeasy360 and your Platform Wallet's payments for customer plans, with their status."
            : "Your payments to Workeasy360, with their status."
        }
      />
      <LoadGate load={load} what="your billing records">
        {(r) => (
          <>
            <section className={styles.panel} aria-labelledby="partner-payments">
              <h2 id="partner-payments">Your payments</h2>
              <Table
                caption="Your payments to Workeasy360"
                head={["Date", "For", "Amount", "Payment status", "Refund status"]}
                numeric={["Amount"]}
                isEmpty={r.partner_payments.length === 0}
                empty="No payments yet."
              >
                {r.partner_payments.map((p) => (
                  <tr key={p.id}>
                    <th scope="row">{when(p.created_at)}</th>
                    <td>{p.label}</td>
                    <td className={styles.num}>{money(p.amount_minor, p.currency)}</td>
                    <td>
                      <Status value={p.status} label={PAYMENT_STATUS[p.status] ?? p.status} />
                    </td>
                    <td>{p.refund_status === "none" ? "Not refunded" : p.refund_status}</td>
                  </tr>
                ))}
              </Table>
            </section>
            {wallet && (
              <section className={styles.panel} aria-labelledby="wallet-payments">
                <h2 id="wallet-payments">Wallet payments for customer plans</h2>
                <Table
                  caption="Plans paid from your Platform Wallet"
                  head={["Date", "Customer", "Order", "Amount", "Details"]}
                  numeric={["Amount"]}
                  isEmpty={r.wallet_charges.length === 0}
                  empty="Your Platform Wallet has paid for no plan yet."
                >
                  {r.wallet_charges.map((e) => (
                    <tr key={e.id}>
                      <th scope="row">{when(e.created_at)}</th>
                      <td>{e.customer?.name ?? "—"}</td>
                      <td>{e.order?.number ?? "Renewal"}</td>
                      <td className={styles.num}>{money(e.amount_minor, e.currency)}</td>
                      <td>{e.reason}</td>
                    </tr>
                  ))}
                </Table>
              </section>
            )}
            {r.orders_visible && (
              <p>
                <Link href={`${base}/payments`} className="w360-btn w360-btn-secondary">
                  Customer orders and invoice references
                </Link>
              </p>
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}
