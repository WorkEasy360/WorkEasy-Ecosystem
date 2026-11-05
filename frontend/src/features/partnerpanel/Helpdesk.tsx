"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useId, useRef, useState } from "react";
import { LoadGate, Status, Table, useAction, useLoad, when } from "@/features/controlplane/ui";
import {
  panelApi,
  type CannedResponse,
  type NewTicket,
  type SlaState,
  type Ticket,
  type TicketDetail,
  type TicketPage,
} from "@/lib/api/partnerPanel";
import { dialog, FormDialog, FormPopup } from "@/features/controlplane/Modal";
import { EmptyState, PageHeader, usePanel } from "./PanelKit";
import { useNote } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/**
 * The partner helpdesk (2026-11-03): tickets to Workeasy360 for every level,
 * and - for a Reseller or White-label partner - a desk for its own customers'
 * tickets, with saved replies. The SLA (first answer and resolution deadlines
 * by priority) is the server's; this only shows where a ticket stands.
 *
 * The open ticket is named in the address (`?ticket=<id>`), so a link to a
 * ticket opens it, and Back closes it.
 */

const SLA_LABEL: Record<SlaState, string> = {
  on_track: "On track",
  at_risk: "Due soon",
  breached: "Overdue",
  met: "Met",
};

const SLA_BADGE: Record<SlaState, string> = {
  on_track: "w360-badge w360-badge-info",
  at_risk: "w360-badge w360-badge-warning",
  breached: "w360-badge w360-badge-danger",
  met: "w360-badge w360-badge-success",
};

const CATEGORY_LABEL: Record<string, string> = {
  general: "General",
  technical: "Technical",
  billing: "Billing",
  commission: "Commission dispute",
  payout: "Payout",
  account: "Account",
  product: "Product",
};

const STATUS_FILTERS = [
  { key: "open", label: "Open" },
  { key: "resolved", label: "Resolved" },
  { key: "closed", label: "Closed" },
  { key: "all", label: "All" },
];

export function Sla({ ticket }: { ticket: Ticket }) {
  const state = ticket.status === "resolved" || ticket.status === "closed" ? ticket.sla.resolution : ticket.sla.state;
  return <span className={SLA_BADGE[state]}>{SLA_LABEL[state]}</span>;
}

function useOpenTicket(): [string | null, (id: string | null) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const open = params?.get("ticket") ?? null;
  return [open, (id) => router.push(id ? `${pathname}?ticket=${encodeURIComponent(id)}` : pathname)];
}

function TicketTable({
  page,
  onOpen,
  desk,
}: {
  page: TicketPage;
  onOpen: (id: string) => void;
  desk?: boolean;
}) {
  return (
    <Table
      caption={desk ? "Tickets from your customers" : "Your tickets to Workeasy360"}
      head={["Ticket", ...(desk ? ["Customer"] : []), "Subject", "Priority", "Status", "SLA", "Updated"]}
      isEmpty={page.items.length === 0}
      empty="No tickets here."
    >
      {page.items.map((t) => (
        <tr key={t.id}>
          <th scope="row">
            <button type="button" className={styles.linkButton} onClick={() => onOpen(t.id)}>
              {t.reference}
            </button>
          </th>
          {desk && <td>{t.customer || "Former customer"}</td>}
          <td>
            {t.subject}
            <span className={styles.muted}> · {CATEGORY_LABEL[t.category] ?? t.category}</span>
          </td>
          <td>{t.priority}</td>
          <td>
            <Status value={t.status} />
          </td>
          <td>
            <Sla ticket={t} />
          </td>
          <td>{when(t.updated_at)}</td>
        </tr>
      ))}
    </Table>
  );
}

function Filters({ status, onStatus, counts }: { status: string; onStatus: (s: string) => void; counts: TicketPage["counts"] | null }) {
  const open = counts ? (counts.open ?? 0) + (counts.in_progress ?? 0) + (counts.waiting ?? 0) : null;
  return (
    <div className={styles.range}>
      <fieldset>
        <legend>Show</legend>
        {STATUS_FILTERS.map((f) => (
          <button key={f.key} type="button" className={styles.rangeButton} aria-pressed={status === f.key} onClick={() => onStatus(f.key)}>
            {f.label}
            {f.key === "open" && open !== null ? ` (${open})` : ""}
          </button>
        ))}
      </fieldset>
    </div>
  );
}

/** A new ticket, in a popup (opened by "Raise a ticket" or "Raise a dispute"). */
export function NewTicketForm({
  title,
  onCreated,
  onClose,
  categories,
  priorities,
  preset,
}: {
  title: string;
  onCreated: (t: TicketDetail, message: string | undefined) => void;
  onClose: () => void;
  categories: string[];
  priorities: string[];
  preset?: Partial<NewTicket>;
}) {
  const { id } = usePanel();
  const ids = { subject: useId(), category: useId(), priority: useId(), message: useId() };
  const [form, setForm] = useState<NewTicket>({ subject: "", message: "", category: "general", priority: "normal", ...preset });
  // The new ticket, kept from the save until the popup reports it (a ref: no redraw needed).
  const created = useRef<TicketDetail | null>(null);
  const set = (key: keyof NewTicket) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  return (
    <FormDialog
      title={title}
      submitLabel="Send ticket"
      canSubmit={Boolean(form.subject.trim() && form.message.trim())}
      onSubmit={async () => {
        created.current = await panelApi.openTicket(id, form);
        return "Ticket sent to Workeasy360.";
      }}
      onDone={(m) => {
        if (created.current) onCreated(created.current, m);
      }}
      onClose={onClose}
    >
      <div className={dialog.span}>
        <label className="w360-label" htmlFor={ids.subject}>
          Subject
        </label>
        <input id={ids.subject} className="w360-input" required maxLength={200} value={form.subject} onChange={set("subject")} />
      </div>
      <div>
        <label className="w360-label" htmlFor={ids.category}>
          Category
        </label>
        <select id={ids.category} className="w360-input" value={form.category} onChange={set("category")}>
          {categories.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABEL[c] ?? c}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="w360-label" htmlFor={ids.priority}>
          Priority
        </label>
        <select id={ids.priority} className="w360-input" value={form.priority} onChange={set("priority")}>
          {priorities.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>
      <div className={dialog.span}>
        <label className="w360-label" htmlFor={ids.message}>
          What do you need?
        </label>
        <textarea id={ids.message} className="w360-input" required rows={5} maxLength={5000} value={form.message} onChange={set("message")} />
      </div>
    </FormDialog>
  );
}

/** One ticket: its conversation and the reply form for this side. */
function TicketView({ ticketId, onClose, desk }: { ticketId: string; onClose: () => void; desk?: boolean }) {
  const { id } = usePanel();
  const [load, reload] = useLoad(() => panelApi.ticket(id, ticketId), [id, ticketId]);
  return (
    <section className={styles.panel} aria-labelledby="ticket-view">
      <p>
        <button type="button" className={styles.linkButton} onClick={onClose}>
          ← All tickets
        </button>
      </p>
      <LoadGate load={load} what="the ticket">
        {(t) => <Conversation t={t} reload={reload} desk={desk} />}
      </LoadGate>
    </section>
  );
}

function Conversation({ t, reload, desk }: { t: TicketDetail; reload: () => void; desk?: boolean }) {
  const { id } = usePanel();
  const answering = desk === true;
  const [canned] = useLoad(
    () => (answering ? panelApi.cannedResponses(id) : Promise.resolve({ items: [] as CannedResponse[], max: 0 })),
    [id, answering],
  );
  const [message, setMessage] = useState("");
  const [internal, setInternal] = useState(false);
  const [status, setStatus] = useState("");
  const ids = { message: useId(), internal: useId(), status: useId(), canned: useId() };
  const closed = t.status === "closed";
  const mayAnswer = !answering || t.may_answer;
  return (
    <>
      <h2 id="ticket-view">
        {t.reference} {t.subject}
      </h2>
      <p className={styles.ticketMeta}>
        <Status value={t.status} /> <Sla ticket={t} /> <span className={styles.muted}>Priority {t.priority}</span>
        {t.customer && <span className={styles.muted}> · {t.customer}</span>}
        <span className={styles.muted}> · Opened {when(t.opened_at)}</span>
      </p>
      <p className={styles.muted}>
        First answer due {when(t.sla.first_response_due_at)} · Resolution due {when(t.sla.resolve_due_at)}
      </p>
      <ol className={styles.thread} aria-label="Conversation">
        {t.messages.map((m) => (
          <li key={m.id} className={styles.message} data-mine={m.mine || undefined} data-internal={m.internal || undefined}>
            <p className={styles.messageHead}>
              <strong>{m.mine ? "You" : m.author}</strong>
              {m.internal && <span className="w360-badge w360-badge-warning">Internal note</span>}
              <span className={styles.muted}>{when(m.created_at)}</span>
            </p>
            <p className={styles.messageBody}>{m.body}</p>
          </li>
        ))}
      </ol>
      {closed ? (
        <p className={styles.muted}>This ticket is closed. Open a new one to continue.</p>
      ) : !mayAnswer ? (
        <p className={styles.muted}>Your role can read customer tickets but not answer them.</p>
      ) : (
        <div className={styles.actions}>
          <FormPopup
            label={answering ? "Answer" : "Reply"}
            buttonAriaLabel={`${answering ? "Answer" : "Reply to"} ${t.reference}`}
            title={`${answering ? "Answer" : "Reply to"} ${t.reference}`}
            size="sm"
            submitLabel="Send"
            canSubmit={Boolean(message.trim() || status)}
            onOpen={() => {
              setMessage("");
              setInternal(false);
              setStatus("");
            }}
            onSubmit={async () => {
              await panelApi.replyTicket(id, t.id, { message: message || undefined, internal, status: status || undefined });
              return status ? "Ticket updated." : "Reply sent.";
            }}
            onDone={reload}
          >
            {answering && canned.status === "ready" && canned.data.items.length > 0 && (
              <div>
                <label className="w360-label" htmlFor={ids.canned}>
                  Insert a saved reply
                </label>
                <select
                  id={ids.canned}
                  className="w360-input"
                  value=""
                  onChange={(e) => {
                    const chosen = canned.data.items.find((c) => c.id === e.target.value);
                    if (chosen) setMessage((m) => (m ? `${m}\n\n${chosen.body}` : chosen.body));
                  }}
                >
                  <option value="">Choose…</option>
                  {canned.data.items.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div>
              <label className="w360-label" htmlFor={ids.message}>
                {answering ? "Your answer" : "Your reply"}
              </label>
              <textarea id={ids.message} className="w360-input" rows={4} maxLength={5000} value={message} onChange={(e) => setMessage(e.target.value)} />
            </div>
            {answering && (
              <label className={styles.check} htmlFor={ids.internal}>
                <input id={ids.internal} type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} /> Internal note (the
                customer does not see it)
              </label>
            )}
            <div>
              <label className="w360-label" htmlFor={ids.status}>
                {answering ? "Set status" : "Status"}
              </label>
              <select id={ids.status} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">{answering ? "Keep as is" : "Keep open"}</option>
                {answering ? (
                  <>
                    <option value="in_progress">In progress</option>
                    <option value="waiting">Waiting on customer</option>
                    <option value="resolved">Resolved</option>
                    <option value="closed">Closed</option>
                  </>
                ) : (
                  <option value="closed">Close this ticket</option>
                )}
              </select>
            </div>
          </FormPopup>
        </div>
      )}
    </>
  );
}

/** My Tickets: the partner's tickets to Workeasy360 (every level). */
export function MyTickets() {
  const { id } = usePanel();
  const params = useSearchParams();
  const [open, setOpen] = useOpenTicket();
  const [status, setStatus] = useState("open");
  const [composing, setComposing] = useState(params?.get("new") === "1");
  const [load, reload] = useLoad(() => panelApi.tickets(id, "platform", status), [id, status]);
  const [support] = useLoad(() => panelApi.support(id), [id]);
  const sent = useNote();
  return (
    <div className={styles.page}>
      <PageHeader
        title="Helpdesk"
        lead="Ask Workeasy360 for help and follow every ticket to its answer. Urgent tickets are answered first."
        actions={
          !open ? (
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              aria-haspopup="dialog"
              onClick={() => {
                sent.clear();
                setComposing(true);
              }}
            >
              Raise a ticket
            </button>
          ) : undefined
        }
      />
      {sent.node}
      {composing && load.status === "ready" && (
        <NewTicketForm
          title="New ticket"
          categories={load.data.categories}
          priorities={load.data.priorities}
          onClose={() => setComposing(false)}
          onCreated={(t, m) => {
            sent.show(m);
            reload();
            setOpen(t.id);
          }}
        />
      )}
      {open ? (
        <TicketView ticketId={open} onClose={() => setOpen(null)} />
      ) : (
        <>
          <Filters status={status} onStatus={setStatus} counts={load.status === "ready" ? load.data.counts : null} />
          <LoadGate load={load} what="your tickets">
            {(page) =>
              page.items.length === 0 && status === "open" ? (
                <EmptyState title="No open tickets" icon="help">
                  <span>When you raise a ticket, it shows here with its deadline.</span>
                </EmptyState>
              ) : (
                <TicketTable page={page} onOpen={setOpen} />
              )
            }
          </LoadGate>
          {support.status === "ready" && (support.data.contact.available || support.data.help_desk.available) && (
            <section className={styles.panel} aria-labelledby="other-ways">
              <h2 id="other-ways">Other ways to reach us</h2>
              {support.data.contact.available && (
                <p>
                  Email: <a href={`mailto:${support.data.contact.email}`}>{support.data.contact.email}</a>
                </p>
              )}
              {support.data.help_desk.available && (
                <p>
                  <a href={support.data.help_desk.url} target="_blank" rel="noopener noreferrer">
                    Open {support.data.help_desk.name}
                    <span className="w360-visually-hidden"> (opens in a new tab)</span>
                  </a>
                </p>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}

/** Customer Desk: tickets from the partner's own customers (Reseller and
 * White-label, members with the desk permission). */
export function CustomerDesk() {
  const { id } = usePanel();
  const [open, setOpen] = useOpenTicket();
  const [status, setStatus] = useState("open");
  const [load] = useLoad(() => panelApi.tickets(id, "customer", status), [id, status]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Customer desk"
        lead="Tickets your customers raise from their Workeasy360 console. Answer them here; internal notes stay with your team."
      />
      {open ? (
        <TicketView ticketId={open} onClose={() => setOpen(null)} desk />
      ) : (
        <>
          <Filters status={status} onStatus={setStatus} counts={load.status === "ready" ? load.data.counts : null} />
          <LoadGate load={load} what="your customers' tickets">
            {(page) =>
              page.items.length === 0 && status === "open" ? (
                <EmptyState title="No open customer tickets" icon="help">
                  <span>Your customers raise tickets from Support in their console.</span>
                </EmptyState>
              ) : (
                <TicketTable page={page} onOpen={setOpen} desk />
              )
            }
          </LoadGate>
        </>
      )}
    </div>
  );
}

/** Saved replies for the customer desk. */
export function SavedReplies() {
  const { id, can } = usePanel();
  const [load, reload] = useLoad(() => panelApi.cannedResponses(id), [id]);
  const [editing, setEditing] = useState<CannedResponse | "new" | null>(null);
  const { act, busy, feedback } = useAction(reload);
  const stored = useNote();
  const may = can("desk.manage");
  const edit = (what: CannedResponse | "new") => {
    stored.clear();
    setEditing(what);
  };
  return (
    <div className={styles.page}>
      <PageHeader
        title="Saved replies"
        lead="Answers your support team inserts into a reply in one step."
        actions={
          may ? (
            <button type="button" className="w360-btn w360-btn-primary" aria-haspopup="dialog" onClick={() => edit("new")}>
              New saved reply
            </button>
          ) : undefined
        }
      />
      {stored.node}
      {editing !== null && (
        <CannedForm
          current={editing === "new" ? null : editing}
          onDone={(m) => {
            stored.show(m);
            reload();
          }}
          onClose={() => setEditing(null)}
        />
      )}
      <LoadGate load={load} what="saved replies">
        {(d) => (
          <Table caption="Saved replies" head={["Title", "Reply", "Updated", ""]} isEmpty={d.items.length === 0} empty="No saved replies yet.">
            {d.items.map((c) => (
              <tr key={c.id}>
                <th scope="row">{c.title}</th>
                <td className={styles.clip}>{c.body}</td>
                <td>{when(c.updated_at)}</td>
                <td>
                  {may && (
                    <span className={styles.actionsRow}>
                      <button type="button" className="w360-btn w360-btn-secondary" aria-haspopup="dialog" onClick={() => edit(c)}>
                        Edit<span className="w360-visually-hidden"> {c.title}</span>
                      </button>
                      <button
                        type="button"
                        className="w360-btn w360-btn-secondary"
                        disabled={busy}
                        onClick={() => act(() => panelApi.deleteCannedResponse(id, c.id), "Saved reply deleted.")}
                      >
                        Delete<span className="w360-visually-hidden"> {c.title}</span>
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
      {feedback}
    </div>
  );
}

/** A saved reply, new or changed, in a popup. */
function CannedForm({ current, onDone, onClose }: { current: CannedResponse | null; onDone: (m: string | undefined) => void; onClose: () => void }) {
  const { id } = usePanel();
  const [title, setTitle] = useState(current?.title ?? "");
  const [body, setBody] = useState(current?.body ?? "");
  const ids = { title: useId(), body: useId() };
  return (
    <FormDialog
      size="sm"
      title={current ? `Edit saved reply: ${current.title}` : "New saved reply"}
      submitLabel="Save"
      canSubmit={Boolean(title.trim() && body.trim())}
      onSubmit={async () => {
        await panelApi.saveCannedResponse(id, { title, body }, current?.id);
        return "Saved reply stored.";
      }}
      onDone={onDone}
      onClose={onClose}
    >
      <div>
        <label className="w360-label" htmlFor={ids.title}>
          Title
        </label>
        <input id={ids.title} className="w360-input" required maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div>
        <label className="w360-label" htmlFor={ids.body}>
          Reply
        </label>
        <textarea id={ids.body} className="w360-input" required rows={5} maxLength={5000} value={body} onChange={(e) => setBody(e.target.value)} />
      </div>
    </FormDialog>
  );
}

/** Commission disputes: a ticket in the commission category, optionally about
 * one ledger entry. The answer comes from Workeasy360's partner team; a
 * correction, when due, is a ledger adjustment staff record. */
export function Disputes() {
  const { id, base } = usePanel();
  const params = useSearchParams();
  const entry = params?.get("entry") ?? "";
  const [composing, setComposing] = useState(entry !== "");
  const [load, reload] = useLoad(() => panelApi.tickets(id, "platform", "all"), [id]);
  const sent = useNote();
  return (
    <div className={styles.page}>
      <PageHeader
        title="Commission disputes"
        lead="Think a commission is missing or wrong? Raise a dispute. Workeasy360 reviews it, and any correction appears in your ledger as an adjustment."
        actions={
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            aria-haspopup="dialog"
            onClick={() => {
              sent.clear();
              setComposing(true);
            }}
          >
            Raise a dispute
          </button>
        }
      />
      {sent.node}
      {composing && load.status === "ready" && (
        <NewTicketForm
          title="New dispute"
          categories={["commission"]}
          priorities={load.data.priorities}
          preset={{ category: "commission", subject: entry ? "Dispute about a commission entry" : "", related_ref: entry ? `commission:${entry}` : undefined }}
          onClose={() => setComposing(false)}
          onCreated={(_t, m) => {
            sent.show(m);
            reload();
          }}
        />
      )}
      <LoadGate load={load} what="your disputes">
        {(page) => {
          const disputes = { ...page, items: page.items.filter((t) => t.category === "commission") };
          return disputes.items.length === 0 ? (
            <EmptyState title="No disputes" icon="currency">
              <span>Disputes you raise appear here with their status.</span>
            </EmptyState>
          ) : (
            <Table caption="Your commission disputes" head={["Ticket", "Subject", "Status", "Updated"]}>
              {disputes.items.map((t) => (
                <tr key={t.id}>
                  <th scope="row">
                    <Link href={`${base}/support-manager?ticket=${encodeURIComponent(t.id)}`}>{t.reference}</Link>
                  </th>
                  <td>{t.subject}</td>
                  <td>
                    <Status value={t.status} />
                  </td>
                  <td>{when(t.updated_at)}</td>
                </tr>
              ))}
            </Table>
          );
        }}
      </LoadGate>
    </div>
  );
}
