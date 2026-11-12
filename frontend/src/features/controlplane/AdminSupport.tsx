"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { POLICIES } from "@/features/store/links";
import { supportApi, type HelpArticleRow, type TicketDetail, type TicketRow } from "@/lib/api/adminPanel";
import { can, useAdmin } from "./ControlShell";
import { DateText } from "./AdminUsers";
import { OffsetPager } from "./AdminListParts";
import { DialogHead, FormDialog, FormPopup, Modal, dialog } from "./Modal";
import { LoadGate, useAction, useLoad } from "./ui";
import { TextAreaField, TextField } from "./widgets";
import {
  Badge,
  Card,
  Button,
  DataTable,
  DescriptionList,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  SearchInput,
  Select,
  Stack,
  StatusBadge,
  StatusChips,
  Trunc,
  EMPTY,
  formatDateTime,
  sentence,
  useConfirm,
  type Column,
  type MenuItem,
} from "./kit";
import local from "./AdminPeople.module.css";
import styles from "./AdminSupport.module.css";

/**
 * Admin → Support (2026-11-09): the desk where customers' and partners' tickets to
 * WorkEasy360 are answered, and the shared help library. Ticket text and help text are
 * plain text, shown as text with their line breaks kept. Reading needs
 * platform.staff_support.view; answering, assigning, resolving and editing help need
 * platform.staff_support.manage, and the server checks both again.
 */

const MANAGE = "platform.staff_support.manage";
const LIMIT = 25;

// --- the ticket list ----------------------------------------------------------------

const AUDIENCES = [
  { value: "all", label: "All" },
  { value: "customer", label: "Customers" },
  { value: "partner", label: "Partners" },
];

const ASSIGNED = [
  { value: "", label: "Anyone" },
  { value: "me", label: "Me" },
  { value: "none", label: "Unassigned" },
];

/** The server's "open" means every ticket still waiting for someone, so its chip counts
 * all three of those statuses: the number always matches the rows it opens. */
function statusChips(counts: Record<string, number> | null) {
  const n = (key: string) => (counts ? (counts[key] ?? 0) : null);
  const sum = (keys: string[]) => (counts ? keys.reduce((total, key) => total + (counts[key] ?? 0), 0) : null);
  return [
    { value: "open", label: "Open", count: sum(["open", "in_progress", "waiting"]) },
    { value: "in_progress", label: "In progress", count: n("in_progress") },
    { value: "waiting", label: "Waiting", count: n("waiting") },
    { value: "resolved", label: "Resolved", count: n("resolved") },
    { value: "closed", label: "Closed", count: n("closed") },
    { value: "all", label: "All", count: sum(["open", "in_progress", "waiting", "resolved", "closed"]) },
  ];
}

const askedBy = (t: TicketRow) => (t.audience === "customer" ? t.organization_name || t.partner_name : t.partner_name || t.organization_name);
const audienceWord = (t: TicketRow) => (t.audience === "customer" ? "Customer" : "Partner");

function askerHref(t: TicketRow): string | null {
  if (t.audience === "customer" && t.organization_id) return `/admin/customers/${t.organization_id}`;
  if (t.partner_id) return `/admin/partners/${t.partner_id}`;
  return t.organization_id ? `/admin/customers/${t.organization_id}` : null;
}

function Asker({ t }: { t: TicketRow }) {
  const name = askedBy(t);
  const href = askerHref(t);
  return (
    <Stack
      main={
        href && name ? (
          <Link href={href}>
            <Trunc>{name}</Trunc>
          </Link>
        ) : (
          <Trunc>{name}</Trunc>
        )
      }
      sub={audienceWord(t)}
    />
  );
}

const TICKET_COLUMNS: Column<TicketRow>[] = [
  { key: "reference", header: "Reference", render: (t) => <Link href={`/admin/tickets/${t.id}`}>{t.reference}</Link> },
  { key: "subject", header: "Subject", render: (t) => <Trunc max="lg">{t.subject}</Trunc> },
  { key: "asker", header: "Who asked", render: (t) => <Asker t={t} /> },
  { key: "category", header: "Category", render: (t) => sentence(t.category) },
  { key: "priority", header: "Priority", render: (t) => <StatusBadge value={t.priority} /> },
  { key: "status", header: "Status", render: (t) => <StatusBadge value={t.status} /> },
  { key: "assignee", header: "Assignee", render: (t) => t.assigned_to_name || "Unassigned" },
  { key: "updated", header: "Updated", render: (t) => <DateText iso={t.updated_at} relative /> },
];

export function TicketsScreen() {
  const [audience, setAudience] = useState("all");
  const [status, setStatus] = useState("open");
  const [assigned, setAssigned] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [load] = useLoad(
    () =>
      supportApi.tickets({
        audience: audience === "all" ? undefined : audience,
        status,
        assigned: assigned || undefined,
        q: query || undefined,
        limit: LIMIT,
        offset,
      }),
    [audience, status, assigned, query, offset],
  );
  // The chips keep their numbers while the next page loads.
  if (load.status === "ready" && load.data.counts !== counts) setCounts(load.data.counts);

  /** A new filter starts again on the first page. */
  const first = <T,>(set: (value: T) => void) => (value: T) => {
    set(value);
    setOffset(0);
  };
  const filtered = Boolean(query || assigned || audience !== "all");
  const rows = load.status === "ready" ? load.data.items : [];

  return (
    <Page>
      <PageHeader
        title="Tickets"
        subtitle="What customers and partners have asked WorkEasy360, newest activity first."
        info="Open counts every ticket still waiting for someone: open, in progress or waiting."
      />
      <StatusChips label="Ticket status" value={status} onChange={first(setStatus)} chips={statusChips(counts)} />
      <FilterBar
        label="Filter tickets"
        onClear={
          filtered
            ? () => {
                setAudience("all");
                setAssigned("");
                setQuery("");
                setOffset(0);
              }
            : undefined
        }
      >
        <SearchInput label="Search subject, partner or #number" value={query} onSearch={first(setQuery)} />
        <Select label="Who asked" prefix="Who asked:" value={audience} onChange={first(setAudience)} options={AUDIENCES} />
        <Select label="Assigned" prefix="Assigned:" value={assigned} onChange={first(setAssigned)} options={ASSIGNED} />
      </FilterBar>
      <DataTable
        caption="Tickets, newest activity first"
        columns={TICKET_COLUMNS}
        rows={rows}
        rowKey={(t) => t.id}
        rowHref={(t) => `/admin/tickets/${t.id}`}
        loading={load.status === "loading"}
        error={load.status === "error" ? load.message : load.status === "forbidden" ? "You don't have access to tickets." : null}
        pageSize={0}
        hideEmptyColumns={false}
        empty={
          <EmptyState
            icon="ticket"
            title="No tickets here"
            hint={filtered || status !== "all" ? "Try another status or clear the filters." : "Tickets appear here when a customer or a partner asks for help."}
          />
        }
      />
      {load.status === "ready" && <OffsetPager total={load.data.total} limit={load.data.limit} offset={load.data.offset} shown={rows.length} onOffset={setOffset} />}
    </Page>
  );
}

// --- one ticket ------------------------------------------------------------------------

const TICKET_CRUMBS = [{ label: "Tickets", href: "/admin/tickets" }];

export function TicketScreen({ ticketId }: { ticketId: string }) {
  const [load, reload] = useLoad(() => supportApi.ticket(ticketId), [ticketId]);
  if (load.status === "ready") return <TicketView t={load.data} reload={reload} />;
  return (
    <Page>
      <PageHeader title="Ticket" crumbs={[...TICKET_CRUMBS, { label: "Ticket" }]} />
      <LoadGate load={load} what="this ticket">
        {() => null}
      </LoadGate>
    </Page>
  );
}

const SET_STATUS = [
  { value: "", label: "Keep as is" },
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In progress" },
  { value: "waiting", label: "Waiting on the asker" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
];

function TicketView({ t, reload }: { t: TicketDetail; reload: () => void }) {
  const closed = t.status === "closed";
  const answering = t.may_answer && !closed;
  const asker = askedBy(t);
  const href = askerHref(t);
  return (
    <Page>
      <PageHeader
        title={`${t.reference} ${t.subject}`}
        crumbs={[...TICKET_CRUMBS, { label: t.reference }]}
        badges={
          <>
            <StatusBadge value={t.status} />
            <StatusBadge value={t.priority} />
          </>
        }
        subtitle={`${audienceWord(t)} ticket, opened ${formatDateTime(t.opened_at)}.`}
        actions={
          answering ? (
            <>
              <AssignPopup t={t} onDone={reload} />
              {t.status !== "resolved" && <ResolvePopup t={t} onDone={reload} />}
            </>
          ) : undefined
        }
        primary={answering ? <ReplyPopup t={t} onDone={reload} /> : undefined}
      />
      {closed && <p className={styles.note}>This ticket is closed, so it takes no more replies.</p>}
      {!t.may_answer && <p className={styles.note}>You can read this ticket. Answering it needs support management access.</p>}
      <div className={styles.ticketGrid}>
        <Card title="Conversation">
          {t.messages.length === 0 ? (
            <EmptyState icon="ticket" title="No messages yet" compact />
          ) : (
            <ol className={styles.conversation} aria-label="Conversation">
              {t.messages.map((m) => (
                <li key={m.id} className={m.internal ? styles.internal : styles.message}>
                  <p className={styles.messageHead}>
                    <strong>{m.author || sentence(m.side)}</strong>
                    {m.internal && <Badge tone="warning">Internal note</Badge>}
                    <span className={styles.messageTime}>{formatDateTime(m.created_at)}</span>
                  </p>
                  {/* Plain text: React escapes it, and the stylesheet keeps its line breaks. */}
                  <p className={styles.messageBody}>{m.body}</p>
                </li>
              ))}
            </ol>
          )}
        </Card>
        <Card title="Details">
          <DescriptionList
            items={[
              { label: "Who asked", value: href && asker ? <Link href={href}>{asker}</Link> : asker || EMPTY },
              { label: "Asked as", value: audienceWord(t) },
              { label: "Category", value: sentence(t.category) },
              { label: "Assigned to", value: t.assigned_to_name || "Unassigned" },
              { label: "Promise to answer", value: t.sla?.state ? sentence(String(t.sla.state)) : EMPTY },
              { label: "Updated", value: formatDateTime(t.updated_at) },
              { label: "Resolved", value: formatDateTime(t.resolved_at), when: Boolean(t.resolved_at) },
              { label: "Resolution", value: <span className={styles.messageBody}>{t.resolution}</span>, when: Boolean(t.resolution) },
            ]}
          />
        </Card>
      </div>
    </Page>
  );
}

function ReplyPopup({ t, onDone }: { t: TicketDetail; onDone: () => void }) {
  const [message, setMessage] = useState("");
  const [internal, setInternal] = useState(false);
  const [status, setStatus] = useState("");
  const text = message.trim();
  return (
    <FormPopup
      label="Reply"
      title={`Reply to ${t.reference}`}
      size="md"
      columns={1}
      submitLabel={text ? (internal ? "Save note" : "Send reply") : "Save status"}
      canSubmit={Boolean(text || status)}
      onOpen={() => {
        setMessage("");
        setInternal(false);
        setStatus("");
      }}
      onSubmit={async () => {
        await supportApi.reply(t.id, { message: text || undefined, internal, status: status || undefined });
        if (!text) return "Status updated.";
        return internal ? "Internal note saved." : "Reply sent.";
      }}
      onDone={onDone}
    >
      <TextAreaField label={internal ? "Note" : "Reply"} value={message} onChange={setMessage} maxLength={5000} rows={6} />
      <label className={local.check}>
        <input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} />
        Internal note (only staff can read it)
      </label>
      <div className={local.field}>
        <Select label="Set status" showLabel fullWidth value={status} onChange={setStatus} options={SET_STATUS} />
        <p className={local.note}>A closed ticket takes no more replies.</p>
      </div>
    </FormPopup>
  );
}

function AssignPopup({ t, onDone }: { t: TicketDetail; onDone: () => void }) {
  const [people] = useLoad(() => supportApi.people(), []);
  const current = t.assigned_to ?? "";
  const [person, setPerson] = useState(current);
  const list = people.status === "ready" ? people.data.results : [];
  const options = [
    { value: "", label: "Unassigned" },
    ...list.map((p) => ({ value: p.id, label: p.name })),
    // Someone no longer on the staff list still shows who holds the ticket now.
    ...(current && !list.some((p) => p.id === current) ? [{ value: current, label: t.assigned_to_name || "Current assignee" }] : []),
  ];
  return (
    <FormPopup
      label="Assign"
      buttonClassName="w360-btn w360-btn-secondary"
      title={`Assign ${t.reference}`}
      size="sm"
      submitLabel="Assign"
      canSubmit={people.status === "ready" && person !== current}
      onOpen={() => setPerson(current)}
      onSubmit={async () => {
        await supportApi.assign(t.id, person || null);
        const name = options.find((o) => o.value === person)?.label;
        return person ? `Assigned to ${name}.` : "Now unassigned.";
      }}
      onDone={onDone}
    >
      <div className={local.field}>
        <Select label="Assign to" showLabel fullWidth value={person} onChange={setPerson} options={options} />
      </div>
      {(people.status === "error" || people.status === "forbidden") && (
        <p className="w360-error-text" role="alert">
          The staff list could not be loaded.
        </p>
      )}
    </FormPopup>
  );
}

function ResolvePopup({ t, onDone }: { t: TicketDetail; onDone: () => void }) {
  const [resolution, setResolution] = useState("");
  const text = resolution.trim();
  return (
    <FormPopup
      label="Resolve"
      buttonClassName="w360-btn w360-btn-secondary"
      title={`Resolve ${t.reference}`}
      size="sm"
      submitLabel="Resolve"
      canSubmit={text.length >= 5}
      onOpen={() => setResolution("")}
      onSubmit={async () => {
        await supportApi.resolve(t.id, text);
        return "Ticket resolved.";
      }}
      onDone={onDone}
    >
      <TextAreaField label="How it was resolved" hint="At least 5 characters." value={resolution} onChange={setResolution} maxLength={500} />
    </FormPopup>
  );
}

// --- help content ------------------------------------------------------------------------

const KIND_LABEL: Record<HelpArticleRow["kind"], string> = {
  faq: "FAQ",
  setup_guide: "Setup guide",
  sales_guide: "Sales guide (partners)",
  policy: "Store policy",
};
const AUDIENCE_LABEL: Record<HelpArticleRow["audience"], string> = { all: "All", customer: "Customers", partner: "Partners" };

const KIND_FILTER = [
  { value: "", label: "All kinds" },
  { value: "faq", label: "FAQ" },
  { value: "setup_guide", label: "Setup guide" },
  // ADR-063: a sales guide or product document, shown to partners under
  // Products & Referrals -> Sales Resources.
  { value: "sales_guide", label: "Sales guide (partners)" },
  // ADR-065: the Digital Store's footer policies - the Store shows only what is published here.
  { value: "policy", label: "Store policy" },
];

const STATUS_FILTER = [
  { value: "", label: "All" },
  { value: "draft", label: "Draft" },
  { value: "published", label: "Published" },
  { value: "archived", label: "Archived" },
];

const KIND_OPTIONS = KIND_FILTER.slice(1) as { value: HelpArticleRow["kind"]; label: string }[];
const AUDIENCE_OPTIONS = (Object.keys(AUDIENCE_LABEL) as HelpArticleRow["audience"][]).map((value) => ({ value, label: AUDIENCE_LABEL[value] }));

export function HelpContentScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, MANAGE);
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [load, reload] = useLoad(() => supportApi.articles({ kind: kind || undefined, status: status || undefined, q: query || undefined }), [kind, status, query]);
  const { act, busy, feedback } = useAction(reload);
  const { ask, dialog: confirm } = useConfirm();
  const [editing, setEditing] = useState<HelpArticleRow | "new" | null>(null);
  const [reading, setReading] = useState<HelpArticleRow | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const move = (row: HelpArticleRow, next: string, success: string) => {
    setNotice(null);
    void act(() => supportApi.setArticleStatus(row.id, next), success);
  };

  const columns: Column<HelpArticleRow>[] = [
    { key: "title", header: "Title", value: (a) => a.title, render: (a) => <Trunc max="lg">{a.title}</Trunc> },
    { key: "kind", header: "Kind", value: (a) => a.kind, render: (a) => KIND_LABEL[a.kind] },
    { key: "audience", header: "For", value: (a) => a.audience, render: (a) => AUDIENCE_LABEL[a.audience] },
    { key: "product", header: "Product", render: (a) => a.product_code || "All products" },
    { key: "status", header: "Status", value: (a) => a.status, render: (a) => <StatusBadge value={a.status} /> },
    { key: "order", header: "Order", align: "right", value: (a) => a.display_order, render: (a) => a.display_order },
    { key: "updated", header: "Updated", value: (a) => a.updated_at, render: (a) => <DateText iso={a.updated_at} relative /> },
  ];

  const menu = (a: HelpArticleRow): MenuItem[] => [
    { label: "Read", icon: "eye", onSelect: () => setReading(a) },
    { label: "Edit", icon: "edit", when: manage, onSelect: () => setEditing(a) },
    { label: "Publish", icon: "send", when: manage && a.status === "draft", disabled: busy, onSelect: () => move(a, "published", "Published. Readers can see it now.") },
    { label: "Unpublish to draft", icon: "history", when: manage && a.status === "published", disabled: busy, onSelect: () => move(a, "draft", "Back to draft. Readers no longer see it.") },
    { label: "Back to draft", icon: "history", when: manage && a.status === "archived", disabled: busy, onSelect: () => move(a, "draft", "Back to draft.") },
    {
      label: "Archive",
      icon: "ban",
      danger: true,
      separator: true,
      when: manage && a.status !== "archived",
      onSelect: () =>
        ask({
          title: `Archive "${a.title}"?`,
          message: "Readers stop seeing it at once. It stays in this list and can go back to draft.",
          confirmLabel: "Archive",
          danger: true,
          onConfirm: async () => {
            await supportApi.setArticleStatus(a.id, "archived");
            setNotice("Archived.");
            reload();
          },
        }),
    },
  ];

  const filtered = Boolean(kind || query || status);
  const rows = load.status === "ready" ? load.data.results : [];
  return (
    <Page>
      <PageHeader
        title="Help Content"
        subtitle="FAQs and setup guides. Only published articles reach readers."
        primary={
          manage ? (
            <Button
              variant="primary"
              icon="plus"
              aria-haspopup="dialog"
              onClick={() => {
                setNotice(null);
                setEditing("new");
              }}
            >
              New article
            </Button>
          ) : undefined
        }
      />
      {notice && <p role="status">{notice}</p>}
      {feedback}
      <StatusChips label="Article status" value={status} onChange={setStatus} chips={STATUS_FILTER} />
      <FilterBar
        label="Filter articles"
        onClear={
          kind || query
            ? () => {
                setKind("");
                setQuery("");
              }
            : undefined
        }
      >
        <SearchInput label="Search title or text" value={query} onSearch={setQuery} />
        <Select label="Kind" prefix="Kind:" value={kind} onChange={setKind} options={KIND_FILTER} />
      </FilterBar>
      <DataTable
        caption="Help articles"
        columns={columns}
        rows={rows}
        rowKey={(a) => a.id}
        rowLabel={(a) => a.title}
        rowMenu={menu}
        loading={load.status === "loading"}
        error={load.status === "error" ? load.message : load.status === "forbidden" ? "You don't have access to help content." : null}
        hideEmptyColumns={false}
        empty={<EmptyState icon="book" title="No articles here" hint={filtered ? "Try another status or clear the filters." : "FAQs and setup guides you write appear here, as drafts first."} />}
      />
      {confirm}
      {reading && <ArticleReader article={reading} onClose={() => setReading(null)} />}
      {editing && (
        <ArticleEditor
          article={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(message) => {
            setNotice(message);
            reload();
          }}
        />
      )}
    </Page>
  );
}

/** One article as a reader would read it: plain text, line breaks kept. */
function ArticleReader({ article, onClose }: { article: HelpArticleRow; onClose: () => void }) {
  const titleId = useId();
  return (
    <Modal labelledBy={titleId} onClose={onClose} size="lg">
      <DialogHead titleId={titleId} title={article.title} onClose={onClose} />
      <div className={dialog.body}>
        <p className={styles.messageBody}>{article.body}</p>
      </div>
      <div className={dialog.foot}>
        <Button onClick={onClose}>Close</Button>
      </div>
    </Modal>
  );
}

function ArticleEditor({ article, onClose, onSaved }: { article: HelpArticleRow | null; onClose: () => void; onSaved: (message: string) => void }) {
  const [title, setTitle] = useState(article?.title ?? "");
  const [body, setBody] = useState(article?.body ?? "");
  const [kind, setKind] = useState<HelpArticleRow["kind"]>(article?.kind ?? "faq");
  const [audience, setAudience] = useState<HelpArticleRow["audience"]>(article?.audience ?? "all");
  const [product, setProduct] = useState(article?.product_code ?? "");
  const [order, setOrder] = useState(String(article?.display_order ?? 0));
  const [policyKey, setPolicyKey] = useState<string>(article?.kind === "policy" ? article.slug : POLICIES[0].key);
  const orderOk = /^\d{1,4}$/.test(order.trim());
  const policy = kind === "policy";
  return (
    <FormDialog
      title={article ? "Edit article" : "New article"}
      intro="Plain text only. Only published articles reach readers; a new article starts as a draft."
      size="lg"
      submitLabel={article ? "Save" : "Create draft"}
      canSubmit={Boolean(title.trim() && body.trim() && orderOk)}
      onSubmit={async () => {
        const fields = {
          title: title.trim(),
          body: body.trim(),
          kind,
          audience: policy ? ("all" as const) : audience,
          product_code: policy ? "" : product.trim(),
          display_order: Number(order.trim()),
          ...(policy ? { policy_key: policyKey } : {}),
        };
        if (article) await supportApi.updateArticle(article.id, fields);
        else await supportApi.createArticle(fields);
        return article ? "Article saved." : "Draft created. Publish it when it is ready.";
      }}
      onDone={(message) => onSaved(message ?? "Saved.")}
      onClose={onClose}
    >
      <div className={dialog.span}>
        <TextField label="Title" required value={title} onChange={setTitle} maxLength={200} />
      </div>
      <div className={dialog.span}>
        <TextAreaField label="Body" value={body} onChange={setBody} maxLength={20000} rows={10} />
      </div>
      <div className={local.field}>
        <Select label="Kind" showLabel fullWidth value={kind} onChange={setKind} options={KIND_OPTIONS} />
      </div>
      {policy ? (
        <div className={local.field}>
          <Select
            label="Policy"
            showLabel
            fullWidth
            value={policyKey}
            onChange={setPolicyKey}
            options={POLICIES.map((p) => ({ value: p.key, label: p.label }))}
          />
        </div>
      ) : (
        <>
          <div className={local.field}>
            <Select label="Audience" showLabel fullWidth value={audience} onChange={setAudience} options={AUDIENCE_OPTIONS} />
          </div>
          <TextField label="Product code (optional)" hint="Empty means every product." value={product} onChange={setProduct} maxLength={64} autoComplete="off" />
        </>
      )}
      <TextField
        label="Order"
        hint="Lower numbers come first."
        inputMode="numeric"
        value={order}
        onChange={setOrder}
        maxLength={4}
        error={orderOk ? undefined : "Use a whole number from 0 to 9999."}
      />
    </FormDialog>
  );
}
