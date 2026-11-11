"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { useSearchParams } from "next/navigation";
import { StatusChips } from "@/features/controlplane/kit";
import { FormDialog } from "@/features/controlplane/Modal";
import { LoadGate, Status, Table, money, useLoad, when } from "@/features/controlplane/ui";
import { walletApi, type PlatformWallet as WalletData, type WalletEntry } from "@/lib/api/partnerWallet";
import { EmptyState, Kpi, PageHeader, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

/**
 * Platform Wallet (ADR-063, the specification's 8): the reseller's prepaid balance,
 * Add Money and every movement. It pays for customers' plans at the approved
 * partner purchase price (Customers → Plan Activation). It is not the partner's
 * earnings - those are under Earnings & Payouts and are never added here.
 *
 * Every amount is the server's. Add Money sends what the person typed; the
 * provider's hosted page takes the payment, and only its verified callback
 * credits the wallet.
 */
export function PlatformWalletPage() {
  const { id } = usePanel();
  const [load, reload] = useLoad(() => walletApi.wallet(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Platform Wallet"
        lead="Your prepaid balance with Workeasy360. It pays for your customers' plans at your approved purchase price. Your earnings are kept apart, under Earnings & Payouts."
      />
      <ReturnNotice onSeen={reload} />
      <LoadGate load={load} what="your Platform Wallet">
        {(w) => <WalletBody w={w} reload={reload} />}
      </LoadGate>
    </div>
  );
}

/** Back from the provider's page: what it said, until its callback is confirmed. */
function ReturnNotice({ onSeen }: { onSeen: () => void }) {
  const params = useSearchParams();
  const outcome = params?.get("outcome");
  useEffect(() => {
    if (outcome) onSeen();
    // Re-read once when the person comes back from the payment page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outcome]);
  if (outcome === "success") {
    return (
      <p role="status" className={styles.notice}>
        Payment sent. Your balance goes up as soon as the payment provider confirms it - usually within a minute. Its status is listed under Top-ups.
      </p>
    );
  }
  if (outcome === "cancelled") {
    return (
      <p role="status" className={styles.notice}>
        The payment was cancelled. Nothing was charged.
      </p>
    );
  }
  return null;
}

const TOP_UP_STATUS: Record<string, string> = {
  pending: "Starting",
  awaiting_payment: "Awaiting payment",
  succeeded: "Paid",
  failed: "Failed",
  expired: "Expired",
};

function WalletBody({ w, reload }: { w: WalletData; reload: () => void }) {
  const { base } = usePanel();
  const [adding, setAdding] = useState(false);
  return (
    <>
      <ul className={styles.kpis} aria-label="Balance">
        {w.wallets.map((wallet) => (
          <Kpi
            key={wallet.currency}
            icon="card"
            label={`Available balance (${wallet.currency})`}
            value={money(wallet.balance_minor, wallet.currency)}
            note={wallet.is_low ? "Below the low-balance level set for this wallet" : "Ready to pay for customer plans"}
          />
        ))}
      </ul>

      <section className={styles.panel} aria-labelledby="add-money">
        <div className={styles.panelHead}>
          <h2 id="add-money">Add Money</h2>
          {w.top_up.allowed && (
            <button type="button" className="w360-btn w360-btn-primary" aria-haspopup="dialog" onClick={() => setAdding(true)}>
              Add Money
            </button>
          )}
        </div>
        {w.top_up.allowed ? (
          <p className={styles.muted}>
            Pay online through Workeasy360&apos;s payment provider. {w.top_up.offline_note}
          </p>
        ) : (
          <p className={styles.muted}>
            Adding money needs an active, approved reseller account and a role that sells (Partner Owner, Partner Admin or Sales). {w.top_up.offline_note}
          </p>
        )}
        {adding && <AddMoneyDialog w={w} onClose={() => setAdding(false)} onDone={reload} />}
        <h3>Top-ups</h3>
        <Table
          caption="Your recent top-ups"
          head={["Started", "Amount", "Status", "Completed"]}
          numeric={["Amount"]}
          isEmpty={w.top_ups.length === 0}
          empty="No online top-ups yet."
        >
          {w.top_ups.map((t) => (
            <tr key={t.id}>
              <th scope="row">{when(t.created_at)}</th>
              <td className={styles.num}>{money(t.amount_minor, t.currency)}</td>
              <td>
                <Status value={t.status} label={TOP_UP_STATUS[t.status] ?? t.status} />
                {t.status === "awaiting_payment" && t.checkout_url && (
                  <>
                    {" "}
                    <a href={t.checkout_url}>Continue payment</a>
                  </>
                )}
              </td>
              <td>{when(t.completed_at)}</td>
            </tr>
          ))}
        </Table>
      </section>

      <section className={styles.panel} aria-labelledby="recent">
        <div className={styles.panelHead}>
          <h2 id="recent">Recent transactions</h2>
          <Link href={`${base}/platform-wallet/transactions`} className="w360-btn w360-btn-secondary">
            All transactions
          </Link>
        </div>
        <EntriesTable entries={w.recent} caption="Your latest wallet movements" />
      </section>
    </>
  );
}

function AddMoneyDialog({ w, onClose, onDone }: { w: WalletData; onClose: () => void; onDone: () => void }) {
  const { id } = usePanel();
  const amountId = useId();
  const currencyId = useId();
  const [currency, setCurrency] = useState(w.currency || w.wallets[0]?.currency || "");
  const [amount, setAmount] = useState("");
  const online = w.top_up.online[currency] ?? false;
  return (
    <FormDialog
      title="Add Money"
      intro={
        online
          ? `You will be taken to the payment page. Between ${money(w.top_up.min_minor, currency)} and ${money(w.top_up.max_minor, currency)}.`
          : `Online payment is not available in ${currency} yet. ${w.top_up.offline_note}`
      }
      submitLabel="Continue to payment"
      columns={1}
      canSubmit={online && /^\d+(\.\d{1,2})?$/.test(amount.trim())}
      onClose={onClose}
      onDone={onDone}
      onSubmit={async () => {
        const payment = await walletApi.startTopUp(id, amount.trim(), currency);
        if (payment.checkout_url) window.location.assign(payment.checkout_url);
        return "Opening the payment page…";
      }}
    >
      {w.wallets.length > 1 && (
        <div className="w360-field">
          <label htmlFor={currencyId}>Currency</label>
          <select id={currencyId} className="w360-input" value={currency} onChange={(e) => setCurrency(e.target.value)}>
            {w.wallets.map((wallet) => (
              <option key={wallet.currency} value={wallet.currency}>
                {wallet.currency}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="w360-field">
        <label htmlFor={amountId}>Amount ({currency})</label>
        <input
          id={amountId}
          className="w360-input"
          inputMode="decimal"
          autoComplete="off"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="5000.00"
        />
      </div>
    </FormDialog>
  );
}

const TYPE_CHIPS = [
  { value: "", label: "All" },
  { value: "top_up", label: "Top-ups" },
  { value: "subscription", label: "Plan charges & renewals" },
  { value: "refund", label: "Refunds" },
  { value: "adjustment", label: "Adjustments" },
  { value: "reversal", label: "Reversals" },
];

/** Transactions (the specification's 8.2): every movement, with what caused it. */
export function WalletTransactions() {
  const { id } = usePanel();
  const [type, setType] = useState("");
  const [limit, setLimit] = useState(25);
  const [load] = useLoad(() => walletApi.transactions(id, { type: type || undefined, limit }), [id, type, limit]);
  return (
    <div className={styles.page}>
      <PageHeader title="Wallet transactions" lead="Every movement of your Platform Wallet, newest first: top-ups, plan charges, renewals, refunds and adjustments." />
      <StatusChips label="Transaction type" chips={TYPE_CHIPS} value={type} onChange={(v) => setType(v)} />
      <LoadGate load={load} what="your wallet transactions">
        {(page) => (
          <>
            <EntriesTable entries={page.items} caption="Platform Wallet transactions" />
            {page.total > page.items.length && (
              <p>
                <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setLimit((n) => Math.min(n + 25, 100))}>
                  Show more
                </button>{" "}
                <span className={styles.muted}>
                  Showing {page.items.length} of {page.total}.
                </span>
              </p>
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}

function EntriesTable({ entries, caption }: { entries: WalletEntry[]; caption: string }) {
  if (entries.length === 0) {
    return (
      <EmptyState title="No transactions yet" icon="card">
        <p>Money you add and plans you pay for will be listed here.</p>
      </EmptyState>
    );
  }
  return (
    <Table caption={caption} head={["Date", "Type", "Amount", "Balance after", "Order", "Customer", "Details"]} numeric={["Amount", "Balance after"]}>
      {entries.map((e) => (
        <tr key={e.id}>
          <th scope="row">{when(e.created_at)}</th>
          <td>{e.entry_type_label}</td>
          <td className={styles.num}>{money(e.amount_minor, e.currency)}</td>
          <td className={styles.num}>{money(e.balance_after_minor, e.currency)}</td>
          <td>{e.order?.number ?? "—"}</td>
          <td>{e.customer ? e.customer.name ?? "Former customer" : "—"}</td>
          <td>
            {e.reason}
            {e.reverses_id && <span className={styles.muted}> (reverses an earlier transaction)</span>}
          </td>
        </tr>
      ))}
    </Table>
  );
}
