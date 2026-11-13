"use client";

import { useState } from "react";
import { adminApi, type PayoutRow } from "@/lib/api/controlPlane";
import { FormPopup } from "./Modal";
import { ReasonAction, Status, money } from "./ui";
import { TextField } from "./widgets";
import styles from "./ControlPlane.module.css";

// The products console that lived here (Phase 11) is now the Product Manager:
// features/controlplane/productmanager (ADR-051).

// --- one payout's controls (Partner Detail -> Payouts, and the Payouts list) ---------

interface PayoutControlsProps {
  payout: PayoutRow;
  manage: boolean;
  approve: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
}

/** A payout's lifecycle controls (approver is never the preparer, and every step
 * is enforced by the server). One set, used by a partner's own Payouts tab and by
 * the Payouts list across partners, rather than a second copy of them. */
export function PayoutActions({ payout: p, manage, approve, busy, act }: PayoutControlsProps) {
  const version = p.version ?? 0;
  const [reference, setReference] = useState("");
  return (
    <>
      {manage && p.status === "draft" && (
        <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => adminApi.payoutAction(p.id, "ready", version, {}), "Marked ready for approval.")}>
          Mark ready
        </button>
      )}
      {approve && p.status === "ready" && (
        <ReasonAction label="Approve" busy={busy} onConfirm={(r) => act(() => adminApi.payoutAction(p.id, "approve", version, { reason: r }), "Payout approved. No payment was made.")} />
      )}
      {manage && p.status === "approved" && (
        <FormPopup
          label="Record as paid"
          buttonAriaLabel={`Record the payout for ${p.period_end} as paid`}
          title="Record a payout as paid"
          intro={`${money(p.payable_minor, p.currency)} for the period ending ${p.period_end}. Record the transfer made outside WorkEasy360.`}
          size="sm"
          submitLabel="Record as paid"
          canSubmit={reference.trim().length >= 3}
          disabled={busy}
          onOpen={() => setReference("")}
          onSubmit={async () => {
            await adminApi.payoutAction(p.id, "paid", version, { reference: reference.trim() });
          }}
          // The row changes once it is paid, so the outcome is the payouts
          // list's own (and so is the reload that follows it).
          onDone={() => void act(async () => {}, "Recorded as paid.")}
        >
          <TextField label="Transfer reference (no bank details)" required minLength={3} maxLength={64} value={reference} onChange={setReference} />
        </FormPopup>
      )}
      {manage && p.status !== "paid" && p.status !== "cancelled" && (
        <ReasonAction
          label="Cancel payout"
          danger
          busy={busy}
          message="Its entries become payable again. No money moves."
          onConfirm={(r) => act(() => adminApi.payoutAction(p.id, "cancel", version, { reason: r }), "Payout cancelled; its entries are payable again.")}
        />
      )}
    </>
  );
}

/** One payout as a row of a partner's Payouts tab. */
export function PayoutRowView(props: PayoutControlsProps) {
  const { payout: p } = props;
  return (
    <tr>
      <th scope="row">{p.period_end}</th>
      <td>
        <Status value={p.status} />
      </td>
      <td className={styles.num}>{money(p.gross_minor, p.currency)}</td>
      <td className={styles.num}>{money(p.adjustments_minor, p.currency)}</td>
      <td className={styles.num}>{money(p.payable_minor, p.currency)}</td>
      <td>{p.line_count}</td>
      <td>
        <div className={styles.stack}>
          <PayoutActions {...props} />
        </div>
      </td>
    </tr>
  );
}
