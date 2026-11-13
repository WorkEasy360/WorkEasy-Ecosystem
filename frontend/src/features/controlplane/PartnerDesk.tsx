"use client";

import Link from "next/link";
import { useState } from "react";
import { adminDeskApi, type PendingMethod } from "@/lib/api/adminDesk";
import type { Ticket, TicketDetail } from "@/lib/api/partnerPanel";
import { Sla } from "@/features/partnerpanel/Helpdesk";
import { can, useAdmin } from "./ControlShell";
import { FormDialog, FormPopup } from "./Modal";
import { LoadGate, Status, Table, money, useAction, useLoad, when } from "./ui";
import { SelectField, TextAreaField, TextField } from "./widgets";
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  SearchInput,
  SectionHeader,
  StatusChips,
  Tooltip,
  exactTime,
  formatRelative,
  kit,
  titleCase,
  type Column,
} from "./kit";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";

/**
 * Super Admin's side of the unified partner dashboard (2026-11-03):
 *
 * - **Partner Tickets**: every partner's tickets to Workeasy360, with their SLA;
 *   staff answer (or keep an internal note) and set the status.
 * - **Withdrawals**: partners' withdrawal requests and payout details waiting
 *   for verification. "Start payout" prepares an ordinary payout - its approval
 *   by a second person and its "paid" confirmation stay on the partner's
 *   Payouts tab, unchanged.
 */

const FILTERS = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];

type StaffTicketRow = Ticket & { partner_name: string };

/** A ticket's priority: an urgent one carries its one-hour answer promise. */
function Priority({ value }: { value: string }) {
  if (value === "urgent")
    return (
      <Badge tone="danger" icon="clock">
        Urgent · 1 h SLA
      </Badge>
    );
  return <Badge tone={value === "high" ? "warning" : "neutral"}>{titleCase(value)}</Badge>;
}

export function PartnerTicketsScreen() {
  const [status, setStatus] = useState("open");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [load] = useLoad(() => adminDeskApi.tickets(status, search || undefined), [status, search]);
  const counts = load.status === "ready" ? load.data.counts : {};
  const columns: Column<StaffTicketRow>[] = [
    {
      key: "ticket",
      header: "Ticket",
      value: (t) => t.number,
      render: (t) => (
        <Button variant="link" onClick={() => setOpen(t.id)}>
          {t.reference}
        </Button>
      ),
    },
    { key: "partner", header: "Partner", value: (t) => t.partner_name, render: (t) => t.partner_name },
    { key: "subject", header: "Subject", value: (t) => t.subject, render: (t) => <span className={kit.trunc} title={t.subject}>{t.subject}</span> },
    { key: "priority", header: "Priority", value: (t) => t.priority, render: (t) => <Priority value={t.priority} /> },
    { key: "status", header: "Status", value: (t) => t.status, render: (t) => <Status value={t.status} /> },
    { key: "sla", header: "SLA", render: (t) => <Sla ticket={t} /> },
    {
      key: "updated",
      header: "Updated",
      value: (t) => t.updated_at,
      render: (t) => (
        <Tooltip content={exactTime(t.updated_at)}>
          <span tabIndex={0}>{formatRelative(t.updated_at)}</span>
        </Tooltip>
      ),
    },
  ];
  return (
    <Page>
      <PageHeader
        title="Partner tickets"
        subtitle="Tickets partners raise from their dashboard's Helpdesk."
        info="Urgent tickets must be answered within an hour; the SLA column says whether each is on track."
      />
      {open ? (
        <StaffTicket ticketId={open} onClose={() => setOpen(null)} />
      ) : (
        <>
          <StatusChips
            label="Ticket status"
            value={status}
            onChange={setStatus}
            chips={FILTERS.map((f) => ({ ...f, count: counts[f.value as keyof typeof counts] ?? null }))}
          />
          <FilterBar label="Filter tickets" onClear={search ? () => setSearch("") : undefined}>
            <SearchInput label="Search partner, subject or #number" value={search} onSearch={setSearch} />
          </FilterBar>
          <DataTable
            caption="Partner tickets, newest activity first"
            columns={columns}
            rows={load.status === "ready" ? (load.data.items as StaffTicketRow[]) : []}
            rowKey={(t) => t.id}
            loading={load.status === "loading"}
            error={load.status === "error" ? load.message : load.status === "forbidden" ? "You don't have access to partner tickets." : null}
            empty={<EmptyState icon="ticket" title="No tickets here." hint={search ? "Try another search." : "Tickets partners raise appear here."} />}
          />
        </>
      )}
    </Page>
  );
}

function StaffTicket({ ticketId, onClose }: { ticketId: string; onClose: () => void }) {
  const [load, reload] = useLoad(() => adminDeskApi.ticket(ticketId), [ticketId]);
  return (
    <Card>
      <p>
        <Button icon="chevronLeft" onClick={onClose}>
          All tickets
        </Button>
      </p>
      <LoadGate load={load} what="the ticket">
        {(t) => <StaffConversation t={t} reload={reload} />}
      </LoadGate>
    </Card>
  );
}

function StaffConversation({ t, reload }: { t: TicketDetail; reload: () => void }) {
  const [message, setMessage] = useState("");
  const [internal, setInternal] = useState(false);
  const [status, setStatus] = useState("");
  function reset() {
    setMessage("");
    setInternal(false);
    setStatus("");
  }
  return (
    <>
      <h2 className={styles.sectionTitle}>
        {t.reference} {t.subject}
      </h2>
      <p>
        <Status value={t.status} /> <Sla ticket={t} /> <Priority value={t.priority} /> ·{" "}
        {t.partner && <Link href={`/admin/partners/${t.partner.id}`}>{t.partner.name}</Link>} · opened {when(t.opened_at)}
        {t.related_ref && <> · about {t.related_ref}</>}
      </p>
      <ol className={cp.stack} aria-label="Conversation">
        {t.messages.map((m) => (
          <li key={m.id}>
            <strong>{m.mine ? "You" : m.author}</strong>
            {m.internal && <span className="w360-badge w360-badge-warning"> Internal note</span>} <span className={cp.muted}>{when(m.created_at)}</span>
            <p className={styles.messageBody}>{m.body}</p>
          </li>
        ))}
      </ol>
      {t.status === "closed" ? (
        <p className={cp.muted}>This ticket is closed.</p>
      ) : t.may_answer ? (
        <p>
          <FormPopup
            label="Reply"
            title={`Reply to ${t.reference}`}
            size="sm"
            submitLabel="Send"
            canSubmit={!!message.trim() || !!status}
            onOpen={reset}
            onSubmit={async () => {
              await adminDeskApi.reply(t.id, { message: message || undefined, internal, status: status || undefined });
              return "Saved.";
            }}
            onDone={reload}
          >
            <TextAreaField label="Answer" value={message} onChange={setMessage} maxLength={5000} />
            <label className="w360-label">
              <input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} /> Internal note (the partner does not see
              it)
            </label>
            <SelectField
              label="Set status"
              value={status}
              onChange={setStatus}
              options={[
                { value: "", label: "Keep as is" },
                { value: "in_progress", label: "In progress" },
                { value: "waiting", label: "Waiting on partner" },
                { value: "resolved", label: "Resolved" },
                { value: "closed", label: "Closed" },
              ]}
            />
          </FormPopup>
        </p>
      ) : (
        <p className={cp.muted}>You can read partner tickets; answering needs the partner management permission.</p>
      )}
    </>
  );
}

const WITHDRAWAL_FILTERS = [
  { value: "requested", label: "Waiting" },
  { value: "processing", label: "Processing" },
  { value: "rejected", label: "Rejected" },
  { value: "cancelled", label: "Cancelled" },
  { value: "all", label: "All" },
];

export function WithdrawalsScreen() {
  const { permissions } = useAdmin();
  const [status, setStatus] = useState("requested");
  const [load, reload] = useLoad(() => adminDeskApi.withdrawals(status), [status]);
  const { act, busy, feedback } = useAction(reload);
  const [rejecting, setRejecting] = useState<{ id: string; partner: string } | null>(null);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const manage = can(permissions, "platform.staff_payout.manage");
  return (
    <Page>
      <PageHeader
        title="Withdrawals"
        subtitle="Partners' withdrawal requests and payout details to verify."
        info="Start payout prepares an ordinary payout of the partner's cleared commission; a second person approves it and confirms it paid on the partner's Payouts tab. TDS shown is withheld from INR withdrawals."
      />
      {feedback}
      {notice && <p role="status">{notice}</p>}
      {rejecting && (
        <FormDialog
          title={`Reject the withdrawal of ${rejecting.partner}`}
          size="sm"
          submitLabel="Reject"
          danger
          canSubmit={!!note.trim()}
          onSubmit={async () => {
            await adminDeskApi.rejectWithdrawal(rejecting.id, note);
            return "Request rejected.";
          }}
          onDone={(m) => {
            setNotice(m ?? null);
            setNote("");
            reload();
          }}
          onClose={() => setRejecting(null)}
        >
          <TextField label="Why" value={note} onChange={setNote} maxLength={500} />
        </FormDialog>
      )}
      <LoadGate load={load} what="withdrawals">
        {(d) => (
          <>
            <section aria-labelledby="pending-methods" className={cp.stack}>
              <SectionHeader
                id="pending-methods"
                title={`Payout details to verify (${d.pending_methods.length})`}
                description="Bank or UPI details partners saved, waiting for a check before money is sent."
              />
              <Table
                caption="Payout details waiting for verification"
                head={["Partner", "Paid to", "Holder", "PAN", "Saved", "Actions"]}
                isEmpty={d.pending_methods.length === 0}
                empty="Nothing to verify."
              >
                {d.pending_methods.map((m) => (
                  <MethodRow key={m.partner.id} m={m} manage={manage} onDone={reload} />
                ))}
              </Table>
            </section>
            <section aria-labelledby="withdrawal-queue" className={cp.stack}>
              <h2 id="withdrawal-queue" className="w360-visually-hidden">
                Withdrawal requests
              </h2>
              <StatusChips
                label="Withdrawal status"
                value={status}
                onChange={setStatus}
                chips={WITHDRAWAL_FILTERS.map((f) => ({ ...f, count: f.value === "requested" || f.value === "processing" ? (d.counts[f.value] ?? 0) : null }))}
              />
              <Table
                caption="Withdrawal requests"
                head={["Partner", "Requested", "Amount", "TDS", "Net", "To", "Status", "Actions"]}
                numeric={["Amount", "TDS", "Net"]}
                isEmpty={d.items.length === 0}
                empty="No requests here."
              >
                {d.items.map((w) => (
                  <tr key={w.id}>
                    <th scope="row">
                      <Link href={`/admin/partners/${w.partner.id}`}>{w.partner.name}</Link>
                    </th>
                    <td>{when(w.requested_at)}</td>
                    <td className={cp.num}>{money(w.amount_minor, w.currency)}</td>
                    <td className={cp.num}>{w.tds_minor ? money(w.tds_minor, w.currency) : "—"}</td>
                    <td className={cp.num}>{money(w.net_minor, w.currency)}</td>
                    <td>{w.destination}</td>
                    <td>
                      <Status value={w.status} />
                      {w.decision_note && <span className={cp.muted}> {w.decision_note}</span>}
                    </td>
                    <td>
                      {manage && w.status === "requested" && (
                        <span className={styles.rowActions}>
                          <Button
                            size="sm"
                            variant="primary"
                            disabled={busy}
                            onClick={() => {
                              setNotice(null);
                              void act(() => adminDeskApi.startPayout(w.id), "Payout prepared. Approve and pay it on the partner's Payouts tab.");
                            }}
                          >
                            Start payout
                          </Button>
                          <Button
                            size="sm"
                            aria-haspopup="dialog"
                            onClick={() => {
                              setNotice(null);
                              setNote("");
                              setRejecting({ id: w.id, partner: w.partner.name });
                            }}
                          >
                            Reject…
                          </Button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
            </section>
          </>
        )}
      </LoadGate>
    </Page>
  );
}

function MethodRow({ m, manage, onDone }: { m: PendingMethod; manage: boolean; onDone: () => void }) {
  const { act, busy, feedback } = useAction(onDone);
  const [revealed, setRevealed] = useState<{ account_number?: string; pan?: string } | null>(null);
  const [note, setNote] = useState("");
  return (
    <tr>
      <th scope="row">
        <Link href={`/admin/partners/${m.partner.id}`}>{m.partner.name}</Link>
      </th>
      <td>
        {m.destination}
        {revealed?.account_number && <div>Account: {revealed.account_number}</div>}
        {m.bank_name && <div className={cp.muted}>{m.bank_name}</div>}
      </td>
      <td>{m.account_holder}</td>
      <td>{revealed?.pan ?? (m.pan_on_file ? `******${m.pan_hint}` : "—")}</td>
      <td>{when(m.updated_at)}</td>
      <td>
        {manage && (
          <span className={styles.rowActions}>
            {!revealed && (
              <Button
                size="sm"
                disabled={busy}
                onClick={() => act(async () => setRevealed(await adminDeskApi.payoutMethod(m.partner.id, true)), "Details shown (audited).")}
              >
                Show full details
              </Button>
            )}
            <Button size="sm" variant="primary" disabled={busy} onClick={() => act(() => adminDeskApi.verifyMethod(m.partner.id, "verified"), "Verified.")}>
              Verify
            </Button>
            <FormPopup
              label="Reject…"
              buttonClassName="w360-btn w360-btn-secondary"
              buttonAriaLabel={`Reject the payout details of ${m.partner.name}`}
              title={`Reject the payout details of ${m.partner.name}`}
              size="sm"
              submitLabel="Reject"
              danger
              disabled={busy}
              canSubmit={!!note.trim()}
              onOpen={() => setNote("")}
              onSubmit={async () => {
                await adminDeskApi.verifyMethod(m.partner.id, "rejected", note);
                return "Rejected.";
              }}
              onDone={onDone}
            >
              <TextField label="Reason if rejecting" value={note} onChange={setNote} maxLength={500} />
            </FormPopup>
          </span>
        )}
        {feedback}
      </td>
    </tr>
  );
}
