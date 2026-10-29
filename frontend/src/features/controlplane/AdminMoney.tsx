"use client";

import { useId, useState } from "react";
import { adminApi, type PayoutRow } from "@/lib/api/controlPlane";
import { ReasonAction, Status, money } from "./ui";
import styles from "./ControlPlane.module.css";

// The products console that lived here (Phase 11) is now the Product Manager:
// features/controlplane/productmanager (ADR-051).

// --- one payout row (Partner Detail -> Payouts) -------------------------------------

/** One payout with its lifecycle controls (approver is never the preparer, and
 * every step is enforced by the server).
 * Exported so a partner's own Payouts tab shows the very same controls rather
 * than a second copy of them. */
export function PayoutRowView({
  payout: p,
  manage,
  approve,
  busy,
  act,
}: {
  payout: PayoutRow;
  manage: boolean;
  approve: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const version = p.version ?? 0;
  const refId = useId();
  const [reference, setReference] = useState("");
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
          {manage && p.status === "draft" && (
            <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => adminApi.payoutAction(p.id, "ready", version, {}), "Marked ready for approval.")}>
              Mark ready
            </button>
          )}
          {approve && p.status === "ready" && (
            <ReasonAction label="Approve" busy={busy} onConfirm={(r) => act(() => adminApi.payoutAction(p.id, "approve", version, { reason: r }), "Payout approved. No payment was made.")} />
          )}
          {manage && p.status === "approved" && (
            <form
              className={styles.field}
              onSubmit={(e) => {
                e.preventDefault();
                void act(() => adminApi.payoutAction(p.id, "paid", version, { reference: reference.trim() }), "Recorded as paid.");
              }}
            >
              <label className="w360-label" htmlFor={refId}>
                Transfer reference (no bank details)
              </label>
              <input id={refId} className="w360-input" required minLength={3} maxLength={64} value={reference} onChange={(e) => setReference(e.target.value)} />
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || reference.trim().length < 3}>
                Record as paid
              </button>
            </form>
          )}
          {manage && p.status !== "paid" && p.status !== "cancelled" && (
            <ReasonAction label="Cancel payout" busy={busy} onConfirm={(r) => act(() => adminApi.payoutAction(p.id, "cancel", version, { reason: r }), "Payout cancelled; its entries are payable again.")} />
          )}
        </div>
      </td>
    </tr>
  );
}
