/**
 * Super Admin: the partner helpdesk and the withdrawal queue (2026-11-03, the
 * unified partner dashboard). Staff answer partners' tickets to Workeasy360,
 * verify payout details and turn withdrawal requests into ordinary payouts.
 * Every call is authorized again by the server (staff_partner.* for tickets,
 * staff_payout.manage for money).
 */

import { apiFetch } from "./client";
import type { PayoutMethod, TicketDetail, TicketPage, Withdrawal } from "./partnerPanel";

const admin = "/api/v1/admin";
const json = (body: unknown): string => JSON.stringify(body);

export type StaffWithdrawal = Withdrawal & { partner: { id: string; name: string; code: string } };
export type PendingMethod = PayoutMethod & { partner: { id: string; name: string; code: string } };

export const adminDeskApi = {
  tickets: (status: string, q?: string) => apiFetch<TicketPage>(`${admin}/partner-tickets`, { query: { status, q } }),
  ticket: (ticketId: string) => apiFetch<TicketDetail>(`${admin}/partner-tickets/${encodeURIComponent(ticketId)}`),
  reply: (ticketId: string, body: { message?: string; internal?: boolean; status?: string }) =>
    apiFetch<TicketDetail>(`${admin}/partner-tickets/${encodeURIComponent(ticketId)}`, { method: "POST", body: json(body) }),
  withdrawals: (status: string) =>
    apiFetch<{ items: StaffWithdrawal[]; counts: Record<string, number>; pending_methods: PendingMethod[] }>(
      `${admin}/partner-withdrawals`,
      { query: { status } },
    ),
  startPayout: (withdrawalId: string) =>
    apiFetch<Withdrawal>(`${admin}/partner-withdrawals/${encodeURIComponent(withdrawalId)}`, {
      method: "POST",
      body: json({ action: "start_payout" }),
    }),
  rejectWithdrawal: (withdrawalId: string, note: string) =>
    apiFetch<Withdrawal>(`${admin}/partner-withdrawals/${encodeURIComponent(withdrawalId)}`, {
      method: "POST",
      body: json({ action: "reject", note }),
    }),
  payoutMethod: (partnerId: string, reveal = false) =>
    apiFetch<PayoutMethod & { account_number?: string; pan?: string }>(
      `${admin}/partners/${encodeURIComponent(partnerId)}/payout-method`,
      { query: { reveal: reveal ? "1" : undefined } },
    ),
  verifyMethod: (partnerId: string, decision: "verified" | "rejected", note = "") =>
    apiFetch<PayoutMethod>(`${admin}/partners/${encodeURIComponent(partnerId)}/payout-method`, {
      method: "POST",
      body: json({ decision, note }),
    }),
};
