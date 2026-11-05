"use client";

import { useId, useState } from "react";
import { LoadGate, Status, Table, money, useAction, useLoad, when } from "@/features/controlplane/ui";
import { panelApi, type PayoutMethod, type PayoutMethodInput, type Wallet as WalletData } from "@/lib/api/partnerPanel";
import { FormDialog } from "@/features/controlplane/Modal";
import { Kpi, PageHeader, usePanel } from "./PanelKit";
import { useFieldChecks, useNote } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/**
 * Wallet & Payouts (2026-11-03): the balance, where payouts go, and asking for
 * one. Every amount is the server's - the browser sends none: a withdrawal asks
 * for the whole available balance, and the server works out the TDS. Payout
 * details are verified by Workeasy360 staff before the first withdrawal; the
 * account number and PAN are stored encrypted and shown back as their last
 * four characters only.
 */
export function Wallet() {
  const { id, active } = usePanel();
  const [load, reload] = useLoad(() => panelApi.wallet(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Wallet & payouts"
        lead="Your commission balance, where we pay it and your withdrawal requests. Amounts are worked out by Workeasy360 from your ledger."
      />
      <LoadGate load={load} what="your wallet">
        {(w) => <WalletBody w={w} reload={reload} active={active} />}
      </LoadGate>
    </div>
  );
}

const VERIFICATION: Record<PayoutMethod["verification"], string> = {
  pending: "Waiting for verification",
  verified: "Verified",
  rejected: "Not accepted",
};

function WalletBody({ w, reload, active }: { w: WalletData; reload: () => void; active: boolean }) {
  const { id } = usePanel();
  const [editing, setEditing] = useState(false);
  const { act, busy, feedback } = useAction(reload);
  const saved = useNote();
  const balance = w.balances.find((b) => b.currency === w.currency);
  return (
    <>
      <ul className={styles.kpis} aria-label="Balance">
        <Kpi icon="currency" label="Available to withdraw" value={money(w.preview.amount_minor, w.currency)} note="Cleared and in no payout" />
        <Kpi icon="history" label="Pending clearance" value={money(balance?.pending_minor ?? 0, w.currency)} note="Still in the holding period" />
        <Kpi icon="handshake" label="In payouts" value={money(balance?.in_payouts_minor ?? 0, w.currency)} note="Being paid" />
        <Kpi icon="receipt" label="Paid out" value={money(balance?.paid_minor ?? 0, w.currency)} />
      </ul>

      <section className={styles.panel} aria-labelledby="withdraw">
        <h2 id="withdraw">Withdraw</h2>
        <dl className={styles.earnings}>
          <div>
            <dt>Amount</dt>
            <dd>{money(w.preview.amount_minor, w.currency)}</dd>
          </div>
          {w.rules.tds_rate_bp > 0 && (
            <div>
              <dt>
                TDS {(w.rules.tds_rate_bp / 100).toFixed(2)}% (Section {w.rules.tds_section})
              </dt>
              <dd>− {money(w.preview.tds_minor, w.currency)}</dd>
            </div>
          )}
          <div data-strong>
            <dt>You receive</dt>
            <dd>{money(w.preview.net_minor, w.currency)}</dd>
          </div>
        </dl>
        <p className={styles.muted}>
          Minimum withdrawal: {money(w.rules.minimum_minor, w.currency)}. A withdrawal requests your whole available balance; Workeasy360 prepares
          the payout, a second person approves it, and it is sent to your verified account.
        </p>
        {w.can_manage ? (
          <>
            {w.blocker && <p role="note">{w.blocker}</p>}
            <p>
              <button
                type="button"
                className="w360-btn w360-btn-primary"
                disabled={!w.can_request || busy || !active}
                onClick={() => act(() => panelApi.requestWithdrawal(id), "Withdrawal requested. Workeasy360 will process it.")}
              >
                Request withdrawal
              </button>
            </p>
          </>
        ) : (
          <p className={styles.muted}>A partner owner or administrator can request withdrawals.</p>
        )}
        {feedback}
      </section>

      <section className={styles.panel} aria-labelledby="payout-details">
        <div className={styles.panelHead}>
          <h2 id="payout-details">Payout details</h2>
          {w.can_manage && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              aria-haspopup="dialog"
              onClick={() => {
                saved.clear();
                setEditing(true);
              }}
            >
              {w.method ? "Change details" : "Add payout details"}
            </button>
          )}
        </div>
        {saved.node}
        {editing && (
          <MethodForm
            current={w.method}
            panRequired={w.rules.pan_required}
            onDone={(m) => {
              saved.show(m);
              reload();
            }}
            onClose={() => setEditing(false)}
          />
        )}
        {w.method ? (
          <dl className={styles.facts}>
            <div>
              <dt>Paid to</dt>
              <dd>{w.method.destination}</dd>
            </div>
            <div>
              <dt>Account holder</dt>
              <dd>{w.method.account_holder}</dd>
            </div>
            {w.method.bank_name && (
              <div>
                <dt>Bank</dt>
                <dd>{w.method.bank_name}</dd>
              </div>
            )}
            <div>
              <dt>PAN</dt>
              <dd>{w.method.pan_on_file ? `******${w.method.pan_hint}` : "Not given"}</dd>
            </div>
            <div>
              <dt>Verification</dt>
              <dd>
                <Status value={w.method.verification} /> {VERIFICATION[w.method.verification]}
                {w.method.verification_note && <span className={styles.muted}> — {w.method.verification_note}</span>}
              </dd>
            </div>
          </dl>
        ) : (
          <p className={styles.muted}>No payout details yet. Add a bank account or UPI address{w.rules.pan_required ? " and your PAN" : ""}.</p>
        )}
      </section>

      <section className={styles.panel} aria-labelledby="withdrawals">
        <h2 id="withdrawals">Withdrawal requests</h2>
        <Table
          caption="Your withdrawal requests, newest first"
          head={["Requested", "Amount", "TDS", "You receive", "To", "Status"]}
          isEmpty={w.withdrawals.length === 0}
          empty="No withdrawal requests yet."
        >
          {w.withdrawals.map((r) => (
            <tr key={r.id}>
              <th scope="row">{when(r.requested_at)}</th>
              <td className={styles.num}>{money(r.amount_minor, r.currency)}</td>
              <td className={styles.num}>{r.tds_minor ? money(r.tds_minor, r.currency) : "—"}</td>
              <td className={styles.num}>{money(r.net_minor, r.currency)}</td>
              <td>{r.destination}</td>
              <td>
                <Status value={r.status} />
                {r.decision_note && <span className={styles.muted}> {r.decision_note}</span>}
                {r.status === "requested" && w.can_manage && (
                  <>
                    {" "}
                    <button
                      type="button"
                      className="w360-btn w360-btn-secondary"
                      disabled={busy}
                      onClick={() => act(() => panelApi.cancelWithdrawal(id, r.id), "Withdrawal request cancelled.")}
                    >
                      Cancel
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </section>
    </>
  );
}

function MethodForm({
  current,
  panRequired,
  onDone,
  onClose,
}: {
  current: PayoutMethod | null;
  panRequired: boolean;
  onDone: (message: string | undefined) => void;
  onClose: () => void;
}) {
  const { id } = usePanel();
  const ids = { method: useId(), holder: useId(), bank: useId(), ifsc: useId(), acc: useId(), acc2: useId(), vpa: useId(), pan: useId() };
  const [form, setForm] = useState<PayoutMethodInput>({
    method: current?.method ?? "bank",
    account_holder: current?.account_holder ?? "",
    bank_name: current?.bank_name ?? "",
    ifsc: current?.ifsc ?? "",
    account_number: "",
    account_number_confirm: "",
    upi_vpa: current?.upi_vpa ?? "",
    pan: "",
  });
  const checks = useFieldChecks();
  const set = (key: keyof PayoutMethodInput) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  const keepsAccount = current?.method === "bank" && form.method === "bank";
  return (
    <FormDialog
      title={current ? "Change payout details" : "Add payout details"}
      intro="The account number and PAN are stored encrypted. Saving sends the details to Workeasy360 for verification before the next withdrawal."
      submitLabel="Save payout details"
      onSubmit={async () => {
        checks.check();
        await panelApi.savePayoutMethod(id, form);
        return "Payout details saved. Workeasy360 will verify them.";
      }}
      onDone={onDone}
      onClose={onClose}
    >
      <div>
        <label className="w360-label" htmlFor={ids.method}>
          Payout method
        </label>
        <select id={ids.method} className="w360-input" value={form.method} onChange={set("method")}>
          <option value="bank">Bank transfer (NEFT / RTGS / IMPS)</option>
          <option value="upi">UPI</option>
        </select>
      </div>
      <div>
        <label className="w360-label" htmlFor={ids.holder}>
          Account holder name
        </label>
        <input id={ids.holder} className="w360-input" required maxLength={120} value={form.account_holder} onChange={set("account_holder")} />
      </div>
      {form.method === "bank" ? (
        <>
          <div>
            <label className="w360-label" htmlFor={ids.bank}>
              Bank name
            </label>
            <input id={ids.bank} className="w360-input" maxLength={120} value={form.bank_name} onChange={set("bank_name")} />
          </div>
          <div>
            <label className="w360-label" htmlFor={ids.ifsc}>
              IFSC
            </label>
            <input id={ids.ifsc} className="w360-input" required maxLength={11} autoComplete="off" value={form.ifsc} onChange={set("ifsc")} />
          </div>
          <div>
            <label className="w360-label" htmlFor={ids.acc}>
              Account number{keepsAccount ? ` (leave blank to keep ****${current?.account_hint})` : ""}
            </label>
            <input
              id={ids.acc}
              className="w360-input"
              required={!keepsAccount}
              inputMode="numeric"
              autoComplete="off"
              maxLength={18}
              value={form.account_number}
              onChange={set("account_number")}
            />
          </div>
          <div>
            <label className="w360-label" htmlFor={ids.acc2}>
              Confirm account number
            </label>
            <input
              id={ids.acc2}
              className="w360-input"
              required={!keepsAccount || !!form.account_number}
              inputMode="numeric"
              autoComplete="off"
              maxLength={18}
              value={form.account_number_confirm}
              onChange={set("account_number_confirm")}
            />
          </div>
        </>
      ) : (
        <div>
          <label className="w360-label" htmlFor={ids.vpa}>
            UPI address
          </label>
          <input id={ids.vpa} className="w360-input" required placeholder="name@bank" maxLength={100} value={form.upi_vpa} onChange={set("upi_vpa")} />
        </div>
      )}
      <div>
        <label className="w360-label" htmlFor={ids.pan}>
          PAN{current?.pan_on_file ? ` (leave blank to keep ******${current.pan_hint})` : panRequired ? "" : " (optional)"}
        </label>
        <input
          id={ids.pan}
          className="w360-input"
          required={panRequired && !current?.pan_on_file}
          maxLength={10}
          autoComplete="off"
          value={form.pan}
          onChange={set("pan")}
        />
      </div>
      {checks.probe}
    </FormDialog>
  );
}
