"use client";

import { useId, useMemo, useRef, useState } from "react";
import { apiFetch } from "@/lib/api/client";
import { consoleApi } from "@/lib/api/console";
import type { NewTicket, TicketDetail, TicketMessage } from "@/lib/api/partnerPanel";
import { LoadGate, describeError, useLoad, type Load } from "@/features/controlplane/ui";
import { FormDialog, FormPopup, dialog } from "@/features/controlplane/Modal";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  FilterBar,
  SearchInput,
  StatusBadge,
  StatusChips,
  formatDate,
  formatDateTime,
  sentence,
  type Column,
} from "@/features/controlplane/kit";
import cp from "@/features/controlplane/ControlPlane.module.css";
import local from "./ConsoleScreens.module.css";
import { Panel } from "./consoleUi";

/**
 * The customer's support center (2026-11-11, the User Admin brief Part 2 section 06):
 * tickets, the conversation, who answers, and the published guides - plus the setup a
 * product asks for (handled in My Products).
 *
 * One desk, whoever answers it. An organization a partner looks after asks that partner
 * (`/orgs/<slug>/provider-tickets`); any other asks Workeasy360 (`/support-tickets`).
 * The server says which: it knows the organization's live partner link. The
 * organization comes from the address and is verified by the server; there is nothing
 * here that could name another.
 */

// --- the desk ------------------------------------------------------------------------

export interface DeskTicket {
  id: string;
  reference: string;
  subject: string;
  status: string;
  category?: string;
  priority?: string;
  opened_at?: string;
  updated_at: string;
  resolution?: string;
  messages?: (Pick<TicketMessage, "id" | "side" | "author" | "body" | "created_at"> & { mine?: boolean })[];
}

/** Who answers this organization's tickets, and its tickets. */
export interface SupportDesk {
  /** "partner": the organization's partner runs its desk; "platform": Workeasy360 does. */
  kind: "partner" | "platform";
  /** Who answers, as the customer reads it. */
  provider: string;
  items: DeskTicket[];
  counts: Record<string, number>;
  categories: string[];
  priorities: string[];
}

const org = (slug: string) => `/api/v1/orgs/${encodeURIComponent(slug)}`;
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

/** Categories a customer files under (a partner's commission and payout are its own). */
const CUSTOMER_CATEGORIES = (all: string[]) => all.filter((c) => c !== "commission" && c !== "payout");
const DEFAULT_CATEGORIES = ["general", "technical", "billing", "account", "product"];
const DEFAULT_PRIORITIES = ["low", "normal", "high", "urgent"];

function countsOf(items: DeskTicket[], counts: Record<string, number> | undefined): Record<string, number> {
  if (counts && Object.keys(counts).length > 0) return counts;
  const out: Record<string, number> = {};
  for (const t of items) out[t.status] = (out[t.status] ?? 0) + 1;
  return out;
}

/** The organization's desk: its partner's when it has one, else Workeasy360's. */
export function useSupportDesk(slug: string): [Load<SupportDesk>, () => void] {
  return useLoad(async (): Promise<SupportDesk> => {
    const partner = await consoleApi.supportTickets(slug);
    const categories = partner.categories?.length ? partner.categories : DEFAULT_CATEGORIES;
    const priorities = partner.priorities?.length ? partner.priorities : DEFAULT_PRIORITIES;
    if (partner.provider) {
      return {
        kind: "partner",
        provider: partner.provider.name,
        items: partner.items,
        counts: countsOf(partner.items, partner.counts as Record<string, number>),
        categories: CUSTOMER_CATEGORIES(categories),
        priorities,
      };
    }
    const direct = await apiFetch<{ items: DeskTicket[]; counts?: Record<string, number> }>(`${org(slug)}/support-tickets`);
    return {
      kind: "platform",
      provider: "WorkEasy360",
      items: direct.items,
      counts: countsOf(direct.items, direct.counts),
      categories: CUSTOMER_CATEGORIES(categories),
      priorities,
    };
  }, [slug]);
}

const deskApi = {
  open: (slug: string, desk: SupportDesk, body: NewTicket): Promise<DeskTicket | TicketDetail> =>
    desk.kind === "partner"
      ? consoleApi.openSupportTicket(slug, body)
      : apiFetch<DeskTicket>(`${org(slug)}/support-tickets`, post(body)),
  detail: (slug: string, desk: SupportDesk, id: string): Promise<DeskTicket> =>
    desk.kind === "partner"
      ? (consoleApi.supportTicket(slug, id) as Promise<DeskTicket>)
      : apiFetch<DeskTicket>(`${org(slug)}/support-tickets/${encodeURIComponent(id)}`),
  reply: (slug: string, desk: SupportDesk, id: string, message: string) =>
    desk.kind === "partner"
      ? consoleApi.replySupportTicket(slug, id, { message })
      : apiFetch(`${org(slug)}/support-tickets/${encodeURIComponent(id)}`, post({ message })),
};

// --- a new ticket ------------------------------------------------------------------------

export interface TicketDraft {
  subject: string;
  message: string;
  category: string;
  priority: string;
}

const BLANK: TicketDraft = { subject: "", message: "", category: "general", priority: "normal" };

/** "Create Support Ticket": written in a popup; `initial` prefills it (a refund
 * request from Orders & Payments). Resolves with the new ticket. */
export function NewTicketDialog({
  slug,
  desk,
  initial,
  title = "Create support ticket",
  onClose,
  onCreated,
}: {
  slug: string;
  desk: SupportDesk;
  initial?: Partial<TicketDraft>;
  title?: string;
  onClose: () => void;
  onCreated: (ticket: DeskTicket | TicketDetail) => void;
}) {
  const ids = { subject: useId(), category: useId(), priority: useId(), message: useId() };
  const [form, setForm] = useState<TicketDraft>({ ...BLANK, ...initial });
  const created = useRef<DeskTicket | TicketDetail | null>(null);
  const set = (key: keyof TicketDraft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  const categories = desk.categories.includes(form.category) ? desk.categories : [form.category, ...desk.categories];
  return (
    <FormDialog
      title={title}
      intro={`${desk.provider} answers here. You can follow the reply under My tickets.`}
      submitLabel="Send ticket"
      canSubmit={form.subject.trim() !== "" && form.message.trim() !== ""}
      onClose={onClose}
      onSubmit={async () => {
        created.current = await deskApi.open(slug, desk, {
          subject: form.subject.trim(),
          message: form.message.trim(),
          category: form.category,
          priority: form.priority,
        });
        return "Your ticket was sent.";
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
          {categories.map((c) => (
            <option key={c} value={c}>
              {sentence(c)}
            </option>
          ))}
        </select>
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.priority}>
          Priority
        </label>
        <select id={ids.priority} className="w360-input" value={form.priority} onChange={set("priority")}>
          {desk.priorities.map((p) => (
            <option key={p} value={p}>
              {sentence(p)}
            </option>
          ))}
        </select>
      </div>
      <div className={`${cp.field} ${dialog.span}`}>
        <label className="w360-label" htmlFor={ids.message}>
          How can we help?
        </label>
        <textarea id={ids.message} className="w360-input" required rows={5} maxLength={5000} value={form.message} onChange={set("message")} />
      </div>
    </FormDialog>
  );
}

// --- the conversation ------------------------------------------------------------------

function TicketThread({ slug, desk, id, onClose }: { slug: string; desk: SupportDesk; id: string; onClose: () => void }) {
  const [load, reload] = useLoad(() => deskApi.detail(slug, desk, id), [slug, desk.kind, id]);
  const [message, setMessage] = useState("");
  const [closing, setClosing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const replyId = useId();
  return (
    <LoadGate load={load} what="this ticket">
      {(ticket) => (
        <div className={local.section}>
          <div className={local.actions}>
            <Button icon="chevronLeft" onClick={onClose}>
              Back to my tickets
            </Button>
          </div>
          <h3 className={local.subTitle}>
            {ticket.reference}: {ticket.subject}
          </h3>
          <p className={local.contactLine}>
            <StatusBadge value={ticket.status} />
            {ticket.category && <span className={local.muted}>{sentence(ticket.category)}</span>}
            {ticket.opened_at && <span className={local.muted}>Opened {formatDate(ticket.opened_at)}</span>}
          </p>
          {ticket.resolution && <p>Resolution: {ticket.resolution}</p>}
          <ol className={local.thread} aria-label="Conversation">
            {(ticket.messages ?? []).map((m) => {
              const mine = m.mine ?? m.side === "customer";
              return (
                <li key={m.id} className={local.message} data-mine={mine || undefined}>
                  <p className={local.messageMeta}>
                    <strong>{mine ? "You" : m.author}</strong> · {formatDateTime(m.created_at)}
                  </p>
                  <p className={local.messageBody}>{m.body}</p>
                </li>
              );
            })}
          </ol>
          {ticket.status === "closed" ? (
            <p className={local.muted}>This ticket is closed. Create a new ticket if you need more help.</p>
          ) : (
            <form
              className={local.section}
              onSubmit={(e) => {
                e.preventDefault();
                if (!message.trim() || busy) return;
                setBusy(true);
                setClosing(null);
                deskApi.reply(slug, desk, ticket.id, message.trim()).then(
                  () => {
                    setMessage("");
                    setBusy(false);
                    reload();
                  },
                  (err: unknown) => {
                    setClosing(describeError(err));
                    setBusy(false);
                  },
                );
              }}
            >
              <div className={cp.field}>
                <label className="w360-label" htmlFor={replyId}>
                  Your reply
                </label>
                <textarea id={replyId} className="w360-input" rows={3} maxLength={5000} value={message} onChange={(e) => setMessage(e.target.value)} />
              </div>
              {closing && (
                <p role="alert" className="w360-error-text">
                  {closing}
                </p>
              )}
              <div className={local.actions}>
                <Button type="submit" variant="primary" icon="send" disabled={busy || !message.trim()}>
                  Send reply
                </Button>
                {desk.kind === "partner" && (
                  <Button
                    disabled={busy}
                    onClick={() => {
                      setBusy(true);
                      consoleApi.replySupportTicket(slug, ticket.id, { status: "closed" }).then(
                        () => {
                          setBusy(false);
                          reload();
                        },
                        (err: unknown) => {
                          setClosing(describeError(err));
                          setBusy(false);
                        },
                      );
                    }}
                  >
                    Close ticket
                  </Button>
                )}
              </div>
            </form>
          )}
        </div>
      )}
    </LoadGate>
  );
}

// --- my tickets --------------------------------------------------------------------------

const STATUS_CHIPS = [
  { value: "", label: "All" },
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In progress" },
  { value: "waiting", label: "Waiting on you" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
] as const;

/** My tickets: status chips (labels, never menus), search, the table and the open conversation. */
export function SupportTickets({
  slug,
  desk,
  reload,
  initialOpen = null,
}: {
  slug: string;
  desk: SupportDesk;
  reload: () => void;
  /** A ticket to open on arrival (one just created from the page header). */
  initialOpen?: string | null;
}) {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(initialOpen);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const total = Object.values(desk.counts).reduce((a, b) => a + b, 0) || desk.items.length;
  const rows = useMemo(
    () =>
      desk.items.filter(
        (t) =>
          (!status || t.status === status) &&
          (!q || `${t.reference} ${t.subject}`.toLowerCase().includes(q.toLowerCase())),
      ),
    [desk.items, status, q],
  );
  const columns: Column<DeskTicket>[] = [
    {
      key: "ticket",
      header: "Ticket",
      value: (t) => t.reference,
      render: (t) => (
        <Button variant="link" onClick={() => setOpen(t.id)} aria-label={`Open ticket ${t.reference}: ${t.subject}`}>
          {t.reference}
        </Button>
      ),
    },
    { key: "subject", header: "Subject", value: (t) => t.subject, render: (t) => t.subject },
    { key: "category", header: "Category", value: (t) => t.category ?? "", render: (t) => sentence(t.category ?? "") },
    { key: "status", header: "Status", value: (t) => t.status, render: (t) => <StatusBadge value={t.status} /> },
    { key: "updated", header: "Updated", value: (t) => t.updated_at, render: (t) => formatDateTime(t.updated_at) },
  ];
  return (
    <Card
      title="My tickets"
      description={`Tickets to ${desk.provider}, newest first.`}
      actions={
        !open && (
          <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>
            Create support ticket
          </Button>
        )
      }
    >
      <div className={local.section}>
        {notice && <p role="status">{notice}</p>}
        {open ? (
          <TicketThread
            slug={slug}
            desk={desk}
            id={open}
            onClose={() => {
              setOpen(null);
              reload();
            }}
          />
        ) : (
          <>
            <StatusChips
              label="Ticket status"
              value={status}
              onChange={setStatus}
              chips={STATUS_CHIPS.map((c) => ({
                value: c.value,
                label: c.label,
                count: c.value ? (desk.counts[c.value] ?? 0) : total,
              }))}
            />
            <FilterBar label="Find a ticket">
              <SearchInput label="Search tickets" value={q} onSearch={setQ} />
            </FilterBar>
            <DataTable
              caption="Your tickets"
              columns={columns}
              rows={rows}
              rowKey={(t) => t.id}
              rowLabel={(t) => t.subject}
              maxHeight={false}
              stickyFirst={false}
              empty={
                <EmptyState
                  icon="headset"
                  title={status || q ? "No tickets match." : "No tickets yet."}
                  hint={status || q ? "Try another status or search." : `Create a ticket and ${desk.provider} answers here.`}
                />
              }
            />
          </>
        )}
      </div>
      {creating && (
        <NewTicketDialog
          slug={slug}
          desk={desk}
          onClose={() => setCreating(false)}
          onCreated={(t) => {
            setNotice("Your ticket was sent.");
            reload();
            setOpen(t.id);
          }}
        />
      )}
    </Card>
  );
}

/** Tickets to Workeasy360 or to the organization's partner - whichever answers it. */
export function SupportDeskPanel({ slug }: { slug: string }) {
  const [load, reload] = useSupportDesk(slug);
  return (
    <LoadGate load={load} what="your support tickets">
      {(desk) => <SupportTickets slug={slug} desk={desk} reload={reload} />}
    </LoadGate>
  );
}

/** Tickets to Workeasy360 (kept for callers that name the platform desk). */
export function PlatformSupport({ slug }: { slug: string }) {
  return <SupportDeskPanel slug={slug} />;
}

// --- product setup ---------------------------------------------------------------------------

export interface SetupRecord {
  id: string;
  product_code: string;
  status: "pending" | "submitted" | "correction_required" | "approved";
  answers: Record<string, string>;
  decision_reason: string;
  requirements: { key: string; label: string; help_text: string; is_required: boolean }[];
  editable: boolean;
}

export const SETUP_STATUS_WORDS: Record<SetupRecord["status"], string> = {
  pending: "Waiting for your details",
  submitted: "Sent, being reviewed",
  correction_required: "Please correct this",
  approved: "Approved",
};

/** Setup still in progress: a product waits for details, a correction or a review. */
export const setupOpen = (r: SetupRecord) => r.status !== "approved";

/** What each product still needs from this organization (`GET /orgs/<slug>/setup`). */
export function useSetupRecords(slug: string): [Load<SetupRecord[]>, () => void] {
  return useLoad(() => apiFetch<{ results: SetupRecord[] }>(`${org(slug)}/setup`).then((r) => r.results), [slug]);
}

/** "Complete setup" / "Submit corrections": the product's questions, in a popup. */
export function SetupForm({ slug, record, onDone, productName }: { slug: string; record: SetupRecord; onDone: () => void; productName?: string }) {
  const [answers, setAnswers] = useState<Record<string, string>>(record.answers ?? {});
  const missing = record.requirements.some((r) => r.is_required && !(answers[r.key] ?? "").trim());
  const label = record.status === "correction_required" ? "Correct and send" : "Send details";
  return (
    <FormPopup
      label={label}
      buttonAriaLabel={productName ? `${label}: ${productName}` : undefined}
      title={productName ? `Setup details · ${productName}` : "Setup details"}
      submitLabel="Send"
      canSubmit={!missing}
      onOpen={() => setAnswers(record.answers ?? {})}
      onSubmit={async () => {
        await apiFetch(`${org(slug)}/setup/${record.id}/submit`, post({ answers }));
        onDone();
        return "Thanks. We'll review these.";
      }}
    >
      {record.requirements.map((r) => (
        <div key={r.key} className={dialog.span}>
          <label className="w360-label" htmlFor={`setup-${record.id}-${r.key}`}>
            {r.label}
            {r.is_required ? " (required)" : ""}
          </label>
          <input
            id={`setup-${record.id}-${r.key}`}
            className="w360-input"
            maxLength={2000}
            value={answers[r.key] ?? ""}
            onChange={(e) => setAnswers((a) => ({ ...a, [r.key]: e.target.value }))}
          />
          {r.help_text && <p className="w360-hint">{r.help_text}</p>}
        </div>
      ))}
    </FormPopup>
  );
}

/** One product's setup: where it stands, what the reviewer said, and the form. */
export function SetupStatus({
  slug,
  record,
  onDone,
  productName,
}: {
  slug: string;
  record: SetupRecord;
  onDone: () => void;
  productName?: string;
}) {
  return (
    <div className={local.section}>
      <p className={local.contactLine}>
        <StatusBadge value={record.status} label={SETUP_STATUS_WORDS[record.status]} />
      </p>
      {record.decision_reason && <p role="status">{record.decision_reason}</p>}
      {record.requirements.length > 0 && (
        <ul className={local.featureList} aria-label="Setup requirements">
          {record.requirements.map((r) => (
            <li key={r.key}>
              {r.label}
              {r.is_required ? " (required)" : ""}
              {answers(record, r.key) && <span className={local.muted}> · answered</span>}
            </li>
          ))}
        </ul>
      )}
      {record.editable && (
        <div className={local.actions}>
          <SetupForm slug={slug} record={record} onDone={onDone} productName={productName} />
        </div>
      )}
    </div>
  );
}

const answers = (record: SetupRecord, key: string) => Boolean((record.answers?.[key] ?? "").trim());

/** What a product still needs from this organization. Nothing shows when nothing does. */
export function CustomerSetupPanel({ slug }: { slug: string }) {
  const [load, reload] = useSetupRecords(slug);
  if (load.status !== "ready" || load.data.length === 0) return null;
  return (
    <Panel title="Product setup">
      <ul className={local.includes}>
        {load.data.map((record) => (
          <li key={record.id}>
            <strong>{record.product_code}</strong>: {SETUP_STATUS_WORDS[record.status]}
            {record.decision_reason && <p role="status">{record.decision_reason}</p>}
            {record.editable && (
              <div className={local.actions}>
                <SetupForm slug={slug} record={record} onDone={reload} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

// --- guides ----------------------------------------------------------------------------------

interface HelpArticle {
  slug: string;
  kind: "faq" | "setup_guide";
  title: string;
  body: string;
}

/** A published answer about paying, invoices, plans or wallets - the Billing guides. */
const BILLING_WORDS = /\b(bill|billing|invoice|invoices|payment|payments|pay|refund|refunds|subscription|subscriptions|plan|plans|renew|renewal|wallet|wallets|credit|credits|gst|tax)\b/i;

function GuideList({ title, description, articles }: { title: string; description: string; articles: HelpArticle[] }) {
  return (
    <Card title={title} description={description}>
      {articles.length === 0 ? (
        <EmptyState icon="book" title="Nothing published here yet." compact />
      ) : (
        <div className={local.section}>
          {articles.map((article) => (
            <details key={article.slug}>
              <summary>{article.title}</summary>
              <p className={`${local.preWrap} ${local.muted}`}>{article.body}</p>
            </details>
          ))}
        </div>
      )}
    </Card>
  );
}

/** The published library, for customers: Setup guides, Billing guides and the other
 * answers - every article the platform's own, shown as plain text, never markup. */
export function HelpLibrary() {
  const [load] = useLoad(
    () => apiFetch<{ results: HelpArticle[] }>("/api/v1/public/help", { query: { audience: "customer" } }),
    [],
  );
  if (load.status !== "ready" || load.data.results.length === 0) return null;
  const all = load.data.results;
  const setup = all.filter((a) => a.kind === "setup_guide");
  const faqs = all.filter((a) => a.kind !== "setup_guide");
  const billing = faqs.filter((a) => BILLING_WORDS.test(`${a.title} ${a.body}`));
  const other = faqs.filter((a) => !billing.includes(a));
  return (
    <div className={local.cardRow}>
      <GuideList title="Setup guides" description="Getting a product ready to use." articles={setup} />
      <GuideList title="Billing guides" description="Plans, payments, invoices and renewals." articles={billing} />
      {other.length > 0 && <GuideList title="Common questions" description="Answers to what customers ask most." articles={other} />}
    </div>
  );
}
