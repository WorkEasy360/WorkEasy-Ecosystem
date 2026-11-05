"use client";

import { useId, useRef, useState } from "react";
import { LoadGate, Status, Table, useAction, useLoad, when } from "@/features/controlplane/ui";
import { FormPopup, dialog } from "@/features/controlplane/Modal";
import cp from "@/features/controlplane/ControlPlane.module.css";
import { consoleApi } from "@/lib/api/console";
import type { NewTicket, TicketDetail } from "@/lib/api/partnerPanel";
import local from "./ConsoleScreens.module.css";
import { Panel } from "./consoleUi";

/**
 * Tickets to the organization's support provider (2026-11-03, the partner
 * helpdesk): when the organization is served by a Workeasy360 partner that runs
 * a desk, any member can ask it for help here and follow the answer. The
 * provider is the server's answer (the organization's live partner link) - an
 * organization with none sees nothing of this.
 */
export function ProviderSupport({ slug }: { slug: string }) {
  const [load, reload] = useLoad(() => consoleApi.supportTickets(slug), [slug]);
  const [open, setOpen] = useState<string | null>(null);
  if (load.status !== "ready" || !load.data.provider) return null;
  const page = load.data;
  return (
    <Panel title={`Ask ${page.provider!.name}`}>
      <p>
        {page.provider!.name} looks after your Workeasy360 subscription. Raise a ticket and follow the answer here.
      </p>
      {open ? (
        <TicketThread
          slug={slug}
          ticketId={open}
          onClose={() => {
            setOpen(null);
            reload();
          }}
        />
      ) : (
        <>
          <div className={local.actions}>
            <NewTicketPopup slug={slug} categories={page.categories} priorities={page.priorities} onCreated={(t) => setOpen(t.id)} />
          </div>
          <Table caption="Your tickets" head={["Ticket", "Subject", "Status", "Updated"]} isEmpty={page.items.length === 0} empty="No tickets yet.">
            {page.items.map((t) => (
              <tr key={t.id}>
                <th scope="row">
                  <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(t.id)}>
                    {t.reference}
                  </button>
                </th>
                <td>{t.subject}</td>
                <td>
                  <Status value={t.status} />
                </td>
                <td>{when(t.updated_at)}</td>
              </tr>
            ))}
          </Table>
        </>
      )}
    </Panel>
  );
}

const NEW_TICKET: NewTicket = { subject: "", message: "", category: "general", priority: "normal" };

/** A new ticket, written in a popup (2026-11-05, every form in a popup); once sent, its thread opens. */
function NewTicketPopup({
  slug,
  categories,
  priorities,
  onCreated,
}: {
  slug: string;
  categories: string[];
  priorities: string[];
  onCreated: (t: TicketDetail) => void;
}) {
  const ids = { subject: useId(), category: useId(), priority: useId(), message: useId() };
  const [form, setForm] = useState<NewTicket>(NEW_TICKET);
  const created = useRef<TicketDetail | null>(null);
  const set = (key: keyof NewTicket) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  return (
    <FormPopup
      label="Raise a ticket"
      title="Raise a ticket"
      submitLabel="Send ticket"
      canSubmit={form.subject !== "" && form.message !== ""}
      onOpen={() => {
        created.current = null;
        setForm(NEW_TICKET);
      }}
      onSubmit={async () => {
        created.current = await consoleApi.openSupportTicket(slug, form);
        return "Ticket sent.";
      }}
      onDone={() => {
        if (created.current) onCreated(created.current);
      }}
    >
      <div className={`${cp.field} ${dialog.span}`}>
        <label className="w360-label" htmlFor={ids.subject}>
          Subject
        </label>
        <input id={ids.subject} className="w360-input" required maxLength={200} value={form.subject} onChange={set("subject")} />
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.category}>
          Category
        </label>
        <select id={ids.category} className="w360-input" value={form.category} onChange={set("category")}>
          {categories
            .filter((c) => c !== "commission" && c !== "payout")
            .map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
        </select>
      </div>
      <div className={cp.field}>
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
      <div className={`${cp.field} ${dialog.span}`}>
        <label className="w360-label" htmlFor={ids.message}>
          What do you need?
        </label>
        <textarea id={ids.message} className="w360-input" required rows={5} maxLength={5000} value={form.message} onChange={set("message")} />
      </div>
    </FormPopup>
  );
}

function TicketThread({ slug, ticketId, onClose }: { slug: string; ticketId: string; onClose: () => void }) {
  const [load, reload] = useLoad(() => consoleApi.supportTicket(slug, ticketId), [slug, ticketId]);
  const [message, setMessage] = useState("");
  const { act, busy, feedback } = useAction(reload);
  const replyId = useId();
  return (
    <div>
      <p>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
          All tickets
        </button>
      </p>
      <LoadGate load={load} what="the ticket">
        {(t) => (
          <>
            <h3>
              {t.reference} {t.subject} <Status value={t.status} />
            </h3>
            <ol className={local.includes} aria-label="Conversation">
              {t.messages.map((m) => (
                <li key={m.id}>
                  <strong>{m.mine ? "You" : m.author}</strong> <span className={cp.muted}>{when(m.created_at)}</span>
                  <p className={local.messageBody}>{m.body}</p>
                </li>
              ))}
            </ol>
            {t.status === "closed" ? (
              <p className={cp.muted}>This ticket is closed.</p>
            ) : (
              <>
                <div className={local.actions}>
                  <FormPopup
                    label="Reply"
                    title={`Reply to ${t.reference}`}
                    size="sm"
                    submitLabel="Send"
                    disabled={busy}
                    canSubmit={message !== ""}
                    onOpen={() => setMessage("")}
                    onSubmit={async () => {
                      await consoleApi.replySupportTicket(slug, t.id, { message });
                      return "Reply sent.";
                    }}
                    onDone={() => {
                      setMessage("");
                      reload();
                    }}
                  >
                    <div className={cp.field}>
                      <label className="w360-label" htmlFor={replyId}>
                        Your reply
                      </label>
                      <textarea id={replyId} className="w360-input" rows={4} maxLength={5000} required value={message} onChange={(e) => setMessage(e.target.value)} />
                    </div>
                  </FormPopup>
                  <button
                    type="button"
                    className="w360-btn w360-btn-secondary"
                    disabled={busy}
                    onClick={() => act(() => consoleApi.replySupportTicket(slug, t.id, { status: "closed" }), "Ticket closed.")}
                  >
                    Close ticket
                  </button>
                </div>
                {feedback}
              </>
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}
