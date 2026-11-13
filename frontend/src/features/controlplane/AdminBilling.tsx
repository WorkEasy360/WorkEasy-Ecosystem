"use client";

import { useRef, useState } from "react";
import { adminConsoleApi, INTERVAL_LABELS, type StaffAttempt, type StaffInvoice } from "@/lib/api/adminConsole";
import { walletsApi } from "@/lib/api/adminPanel";
import { newKey } from "@/lib/api/controlPlane";
import { ReasonField, reasonIsValid } from "./AdminWallets";
import { can, useAdmin } from "./ControlShell";
import { useToast } from "./kit";
import { FormPopup } from "./Modal";
import { LoadGate, ReasonAction, Status, Table, money, useAction, useLoad, when } from "./ui";
import { toMajor, toMinor } from "./amounts";
import { TextField } from "./widgets";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";

/**
 * A customer's invoices, payments and refunds on the Super Admin customer
 * page (Phase 8 extension; ``platform.staff_payment.view``). Changes need
 * ``platform.staff_payment.manage`` and each asks for the reason an
 * auditor will read: marking an invoice paid (a transfer made elsewhere),
 * voiding it, or refunding a provider payment. Every change names the invoice
 * version it was decided on, so a stale page cannot overwrite a newer state.
 */

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

/** Refund states that still hold part of a payment (the server's own list). */
const OPEN_REFUND = new Set(["requested", "submitting", "pending", "needs_review"]);

function day(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

export function CustomerBilling({ organizationId }: { organizationId: string }) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_payment.manage");
  const payFromWallet = can(permissions, "platform.staff_wallet.manage");
  const [load, reload] = useLoad(() => adminConsoleApi.customerBilling(organizationId), [organizationId]);
  const { act, busy, feedback } = useAction(reload);

  return (
    <section className={`w360-card ${cp.card}`} aria-labelledby="billing-h">
      <h2 id="billing-h" className={cp.cardTitle}>
        Billing
      </h2>
      {feedback}
      <LoadGate load={load} what="this customer's invoices">
        {({ invoices }) =>
          invoices.length === 0 ? (
            <p className={cp.muted}>No invoices yet.</p>
          ) : (
            <div className={cp.stack}>
              <Table
                caption="Invoices"
                head={["Number", "Period", "Total", "Paid", "Refunded", "Status", "Dunning stage"]}
              >
                {invoices.map((inv) => (
                  <tr key={inv.invoice_id}>
                    <th scope="row">{inv.number}</th>
                    <td>
                      {day(inv.period_start)} – {day(inv.period_end)}
                      <div className={cp.muted}>{INTERVAL_LABELS[inv.billing_interval] ?? inv.billing_interval}</div>
                    </td>
                    <td className={cp.num}>{money(inv.total_minor, inv.currency)}</td>
                    <td className={cp.num}>{money(inv.amount_paid_minor, inv.currency)}</td>
                    <td className={cp.num}>{money(inv.amount_refunded_minor, inv.currency)}</td>
                    <td>
                      <Status value={inv.status} />
                      {inv.status === "open" && inv.amount_due_minor > 0 && (
                        <div className={cp.muted}>
                          {money(inv.amount_due_minor, inv.currency)} due {day(inv.due_at)}
                        </div>
                      )}
                    </td>
                    <td>{inv.dunning_stage ? inv.dunning_stage.replace(/_/g, " ") : "—"}</td>
                  </tr>
                ))}
              </Table>
              {invoices
                .filter((inv) => inv.attempts.length > 0 || ((manage || payFromWallet) && inv.status === "open"))
                .map((inv) => (
                  <InvoiceDetail
                    key={inv.invoice_id}
                    invoice={inv}
                    organizationId={organizationId}
                    manage={manage}
                    payFromWallet={payFromWallet}
                    act={act}
                    busy={busy}
                    onPaid={reload}
                  />
                ))}
            </div>
          )
        }
      </LoadGate>
    </section>
  );
}

function InvoiceDetail({
  invoice: inv,
  organizationId,
  manage,
  payFromWallet,
  act,
  busy,
  onPaid,
}: {
  invoice: StaffInvoice;
  organizationId: string;
  manage: boolean;
  payFromWallet: boolean;
  act: Act;
  busy: boolean;
  onPaid: () => void;
}) {
  const [reference, setReference] = useState("");
  const canMarkPaid = manage && inv.status === "open" && inv.amount_due_minor > 0;
  const canVoid = manage && inv.status === "open" && inv.amount_paid_minor === 0;
  const canPayFromWallet = payFromWallet && inv.status === "open" && inv.amount_due_minor > 0;

  return (
    <div className={styles.section}>
      <h3 className={styles.subTitle}>Invoice {inv.number}</h3>
      {(canMarkPaid || canVoid || canPayFromWallet) && (
        <div className={styles.actions}>
          {canPayFromWallet && <PayFromWallet invoice={inv} organizationId={organizationId} busy={busy} onPaid={onPaid} />}
          {canMarkPaid && (
            <div className={cp.field}>
              <TextField
                label={`Payment reference for ${inv.number}`}
                value={reference}
                onChange={setReference}
                maxLength={255}
                autoComplete="off"
                hint={`A bank transfer id. Records ${money(inv.amount_due_minor, inv.currency)} received outside the platform.`}
              />
              <ReasonAction
                label={`Mark ${inv.number} paid`}
                busy={busy || !reference.trim()}
                onConfirm={(reason) =>
                  act(async () => {
                    await adminConsoleApi.markInvoicePaid(organizationId, inv.invoice_id, {
                      reference: reference.trim(),
                      reason,
                      version: inv.version,
                    });
                    setReference("");
                  }, `Invoice ${inv.number} is marked paid.`)
                }
              />
            </div>
          )}
          {canVoid && (
            <ReasonAction
              label={`Void ${inv.number}`}
              busy={busy}
              onConfirm={(reason) =>
                act(
                  () => adminConsoleApi.voidInvoice(organizationId, inv.invoice_id, { reason, version: inv.version }),
                  `Invoice ${inv.number} is void.`,
                )
              }
            />
          )}
        </div>
      )}
      <Table
        caption={`Payments for invoice ${inv.number}`}
        head={["Started", "Provider", "Status", "Amount", "Refunded", "Refunds", ...(manage ? ["Refund"] : [])]}
        isEmpty={inv.attempts.length === 0}
        empty="No payment attempts."
      >
        {inv.attempts.map((attempt) => (
          <AttemptRow
            key={attempt.attempt_id}
            attempt={attempt}
            invoiceNumber={inv.number}
            organizationId={organizationId}
            manage={manage}
            act={act}
            busy={busy}
          />
        ))}
      </Table>
    </div>
  );
}

/** Pay what an open invoice owes from the customer's Platform Wallet. The server refuses
 * it when the wallet holds too little, and that refusal stays in the popup. */
function PayFromWallet({
  invoice: inv,
  organizationId,
  busy,
  onPaid,
}: {
  invoice: StaffInvoice;
  organizationId: string;
  busy: boolean;
  onPaid: () => void;
}) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  return (
    <FormPopup
      label="Pay from wallet"
      buttonAriaLabel={`Pay ${inv.number} from wallet`}
      buttonClassName="w360-btn w360-btn-secondary"
      title={`Pay ${inv.number} from wallet`}
      intro={`Takes ${money(inv.amount_due_minor, inv.currency)} from this customer's Platform Wallet and settles the invoice.`}
      size="sm"
      submitLabel="Pay from wallet"
      disabled={busy}
      canSubmit={reasonIsValid(reason)}
      onOpen={() => setReason("")}
      onSubmit={async () => {
        await walletsApi.payInvoiceFromWallet(organizationId, inv.invoice_id, { reason: reason.trim(), expected_version: inv.version });
      }}
      onDone={() => {
        toast.success(`Invoice ${inv.number} is paid from the Platform Wallet.`);
        onPaid();
      }}
    >
      <ReasonField value={reason} onChange={setReason} />
    </FormPopup>
  );
}

function AttemptRow({
  attempt: a,
  invoiceNumber,
  organizationId,
  manage,
  act,
  busy,
}: {
  attempt: StaffAttempt;
  invoiceNumber: string;
  organizationId: string;
  manage: boolean;
  act: Act;
  busy: boolean;
}) {
  const inFlight = a.refunds.filter((r) => OPEN_REFUND.has(r.status)).reduce((sum, r) => sum + r.amount_minor, 0);
  const remaining = Math.max(0, a.amount_minor - a.amount_refunded_minor - inFlight);
  const refundable = manage && a.status === "succeeded" && a.provider !== "manual" && remaining > 0;
  return (
    <tr>
      <td>{when(a.created_at)}</td>
      <td>{a.provider}</td>
      <td>
        <Status value={a.status} />
        {a.failure_code && <div className={cp.muted}>{a.failure_code}</div>}
      </td>
      <td className={cp.num}>{money(a.amount_minor, a.currency)}</td>
      <td className={cp.num}>{money(a.amount_refunded_minor, a.currency)}</td>
      <td>
        {a.refunds.length === 0 ? (
          "—"
        ) : (
          <ul className={styles.plainList}>
            {a.refunds.map((r) => (
              <li key={r.refund_id}>
                {money(r.amount_minor, r.currency)} <Status value={r.status} />
                <div className={cp.muted}>{r.reason}</div>
              </li>
            ))}
          </ul>
        )}
      </td>
      {manage && (
        <td>
          {refundable ? (
            <RefundAction
              attempt={a}
              remaining={remaining}
              invoiceNumber={invoiceNumber}
              organizationId={organizationId}
              act={act}
              busy={busy}
            />
          ) : (
            <span className={cp.muted}>{a.provider === "manual" ? "Recorded manually" : "Not refundable"}</span>
          )}
        </td>
      )}
    </tr>
  );
}

function RefundAction({
  attempt,
  remaining,
  invoiceNumber,
  organizationId,
  act,
  busy,
}: {
  attempt: StaffAttempt;
  remaining: number;
  invoiceNumber: string;
  organizationId: string;
  act: Act;
  busy: boolean;
}) {
  const currency = attempt.currency;
  const [amount, setAmount] = useState(() => toMajor(remaining, currency));
  // One command id per refund decision: a retry of the same refund (after a
  // network error) replays it; a new amount is a new decision.
  const key = useRef<string | null>(null);
  const minor = toMinor(amount, currency);
  const invalid = minor === null || minor <= 0 || minor > remaining;

  return (
    <div className={cp.field}>
      <TextField
        label={`Refund amount (${currency}) on ${invoiceNumber}`}
        value={amount}
        onChange={(v) => {
          setAmount(v);
          key.current = null;
        }}
        inputMode={currency === "JPY" ? "numeric" : "decimal"}
        autoComplete="off"
        hint={`Up to ${money(remaining, currency)}.`}
        error={invalid ? `Enter an amount above zero and no more than ${money(remaining, currency)}.` : undefined}
      />
      <ReasonAction
        label={`Refund on ${invoiceNumber}`}
        busy={busy || invalid}
        onConfirm={(reason) => {
          if (minor === null || invalid) return;
          if (!key.current) key.current = newKey();
          const command = key.current;
          void act(async () => {
            await adminConsoleApi.refund(organizationId, attempt.attempt_id, { amount_minor: minor, reason }, command);
            key.current = null;
          }, `Refund of ${money(minor, currency)} requested. The provider confirms it shortly.`);
        }}
      />
    </div>
  );
}
