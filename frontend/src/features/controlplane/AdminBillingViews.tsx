"use client";

import { useState } from "react";
import Link from "next/link";
import { INTERVAL_LABELS } from "@/lib/api/adminConsole";
import {
  billingApi,
  jsonPost,
  type CreditNoteRow,
  type InvoiceRow,
  type PaymentRow,
  type RefundRow,
  type StuckOrderRow,
  type SubscriptionLine,
  type SubscriptionRow,
} from "@/lib/api/adminPanel";
import { apiFetch } from "@/lib/api/client";
import { adminApi, newKey, type AccrualRow, type PayoutRow } from "@/lib/api/controlPlane";
import { TableSection } from "./AdminCommerce";
import { PayoutActions } from "./AdminMoney";
import { DateText } from "./AdminUsers";
import { can, useAdmin } from "./ControlShell";
import {
  Alert,
  Badge,
  Button,
  ButtonLink,
  DataTable,
  EMPTY,
  EmptyState,
  FilterBar,
  IdCell,
  KeyText,
  Page,
  PageHeader,
  SearchInput,
  Select,
  Stack,
  StatusBadge,
  StatusChips,
  Trunc,
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  kit,
  productName,
  sentence,
  useConfirm,
  useToast,
  type Column,
  type Tone,
} from "./kit";
import { LoadGate, describeError, useAction, useLoad, usePaged } from "./ui";
import local from "./AdminBillingViews.module.css";

/**
 * Admin → Billing & Subscriptions (2026-11-09): invoices, failed and stuck payments,
 * refunds, credit notes, subscriptions, commission entries and payouts, across
 * customers and partners. Every filter goes to the server and every change is
 * authorized again by it. Money is integer minor units per currency, never added
 * across currencies.
 */

// --- shared pieces -----------------------------------------------------------------------

type Paged<R> = ReturnType<typeof usePaged<R>>;

/** A server-paged list. A page that fails after rows are on screen keeps them and says so. */
function PagedTable<R>({
  paged,
  what,
  ...table
}: { paged: Paged<R>; what: string } & Omit<React.ComponentProps<typeof DataTable<R>>, "rows" | "loading" | "error" | "onLoadMore" | "hasMore">) {
  const hasRows = paged.rows.length > 0;
  const failed =
    paged.state === "forbidden"
      ? `You don't have access to ${what}.`
      : paged.state === "error"
        ? `Couldn't load ${what}. Reload the page to try again.`
        : null;
  return (
    <>
      {failed && hasRows && <Alert tone="danger">{`Couldn't load more ${what}. Try Load more again.`}</Alert>}
      <DataTable<R>
        {...table}
        rows={paged.rows}
        loading={paged.state === "loading"}
        error={failed}
        onLoadMore={hasRows ? () => void paged.more() : undefined}
        hasMore={paged.hasMore}
      />
    </>
  );
}

const customerHref = (id: string) => `/admin/customers/${id}`;

function CustomerLink({ id, name }: { id: string; name: string }) {
  return (
    <Link href={customerHref(id)}>
      <Trunc>{name || id}</Trunc>
    </Link>
  );
}

function InvoiceLink({ number }: { number: string }) {
  if (!number) return <>{EMPTY}</>;
  return <Link href={`/admin/invoices?q=${encodeURIComponent(number)}`}>{number}</Link>;
}

/** A partner by name when the server sends one, by the start of its id otherwise. */
function PartnerLink({ id, name }: { id: string | undefined; name?: string }) {
  if (!id) return <>{EMPTY}</>;
  return name ? (
    <Link href={`/admin/partners/${id}`}>
      <Trunc>{name}</Trunc>
    </Link>
  ) : (
    <Link href={`/admin/partners/${id}`} aria-label={`Partner ${id}`} title={id}>
      <code className={kit.mono}>{id.slice(0, 8)}…</code>
    </Link>
  );
}

const moneyColumn = <R,>(key: string, header: string, minor: (row: R) => number | null, currency: (row: R) => string): Column<R> => ({
  key,
  header,
  align: "right",
  value: minor,
  render: (row) => formatMoney(minor(row), currency(row)),
});

/** Paid and refunded read better as "—" than "₹0.00" beside a total. */
const moneyOrDash = <R,>(key: string, header: string, minor: (row: R) => number, currency: (row: R) => string): Column<R> => ({
  key,
  header,
  align: "right",
  value: minor,
  render: (row) => (minor(row) ? formatMoney(minor(row), currency(row)) : EMPTY),
});

// --- invoices ----------------------------------------------------------------------------

const INVOICE_CHIPS = [
  { value: "", label: "All" },
  { value: "open", label: "Open" },
  { value: "paid", label: "Paid" },
  { value: "void", label: "Void" },
];

const INVOICE_COLUMNS: Column<InvoiceRow>[] = [
  { key: "number", header: "Number", value: (i) => i.number, render: (i) => i.number },
  { key: "customer", header: "Customer", value: (i) => i.organization_name, render: (i) => <CustomerLink id={i.organization_id} name={i.organization_name} /> },
  { key: "issued", header: "Issued", value: (i) => i.issued_at, render: (i) => <DateText iso={i.issued_at} /> },
  { key: "status", header: "Status", value: (i) => i.status, render: (i) => <StatusBadge value={i.status} /> },
  moneyColumn<InvoiceRow>("total", "Total", (i) => i.total_minor, (i) => i.currency),
  moneyOrDash<InvoiceRow>("paid", "Paid", (i) => i.amount_paid_minor, (i) => i.currency),
  moneyOrDash<InvoiceRow>("refunded", "Refunded", (i) => i.amount_refunded_minor, (i) => i.currency),
  { key: "due", header: "Due", value: (i) => i.due_at, render: (i) => <DateText iso={i.due_at} /> },
];

/** `initialQuery`: an invoice number from the address (the global search). */
export function InvoicesScreen({ initialQuery = "" }: { initialQuery?: string } = {}) {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState(initialQuery);
  const paged = usePaged((cursor) => billingApi.invoices({ status: status || undefined, q: query || undefined, cursor }), [status, query]);
  return (
    <Page>
      <PageHeader title="Invoices" subtitle="Every invoice across customers, newest first. Open a customer to mark one paid, void it or refund it." />
      <StatusChips label="Invoice status" value={status} onChange={setStatus} chips={INVOICE_CHIPS} />
      <FilterBar onClear={query ? () => setQuery("") : undefined}>
        <SearchInput label="Search by invoice number" value={query} onSearch={setQuery} maxLength={40} />
      </FilterBar>
      <PagedTable
        paged={paged}
        what="invoices"
        caption="Invoices"
        columns={INVOICE_COLUMNS}
        rowKey={(i) => i.invoice_id}
        rowHref={(i) => customerHref(i.organization_id)}
        empty={
          <EmptyState
            icon="receipt"
            title="No invoices"
            hint={status || query ? "Try another status or clear the search." : "Invoices appear here when customers are billed."}
          />
        }
      />
    </Page>
  );
}

// --- failed payments and stuck orders ---------------------------------------------------------

/** The most the stuck-orders list asks for (`billingApi.stuckOrders`). */
const STUCK_SHOWN = 100;

const FAILED_COLUMNS: Column<PaymentRow>[] = [
  { key: "customer", header: "Customer", value: (p) => p.organization_name, render: (p) => <CustomerLink id={p.organization_id} name={p.organization_name} /> },
  { key: "invoice", header: "Invoice", value: (p) => p.invoice_number, render: (p) => <InvoiceLink number={p.invoice_number} /> },
  { key: "provider", header: "Provider", value: (p) => p.provider, render: (p) => sentence(p.provider) },
  moneyColumn<PaymentRow>("amount", "Amount", (p) => p.amount_minor, (p) => p.currency),
  {
    key: "failure",
    header: "Failure code",
    value: (p) => p.failure_code,
    render: (p) => (p.failure_code ? <KeyText>{p.failure_code}</KeyText> : EMPTY),
  },
  { key: "when", header: "When", value: (p) => p.completed_at ?? p.created_at, render: (p) => formatDateTime(p.completed_at ?? p.created_at) },
  {
    key: "resolve",
    header: "Resolve",
    render: (p) => (
      <ButtonLink size="sm" href={`${customerHref(p.organization_id)}#billing-h`} aria-label={`Open billing for ${p.organization_name || "this customer"}`}>
        Open billing
      </ButtonLink>
    ),
  },
];

const RETRY_SAID: Record<string, { tone: "success" | "info" | "error"; text: (number: string) => string }> = {
  fulfilled: { tone: "success", text: (n) => `Order ${n} is activated.` },
  failed: { tone: "error", text: (n) => `Order ${n} could not be activated, so its payment is being refunded automatically.` },
  noop: { tone: "info", text: (n) => `Order ${n} was already dealt with. Nothing changed.` },
  gone: { tone: "error", text: (n) => `The customer behind order ${n} no longer exists. Nothing was activated.` },
};

export function FailedAndStuckScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_payment.manage");
  const toast = useToast();
  const failed = usePaged((cursor) => billingApi.payments({ failed: "1", cursor }), []);
  const [stuck, reloadStuck] = useLoad(() => billingApi.stuckOrders(), []);
  const refunds = usePaged((cursor) => billingApi.refunds({ waiting: "1", cursor }), []);
  const [retrying, setRetrying] = useState<string | null>(null);

  const retry = async (order: StuckOrderRow) => {
    if (retrying) return;
    setRetrying(order.order_id);
    try {
      const { outcome } = await billingApi.retryActivation(order.order_id);
      const said = RETRY_SAID[outcome];
      if (!said) toast.info(`Retry finished for order ${order.number}: ${sentence(outcome)}.`);
      else if (said.tone === "success") toast.success(said.text(order.number));
      else if (said.tone === "error") toast.error(said.text(order.number));
      else toast.info(said.text(order.number));
    } catch (err) {
      toast.error(describeError(err));
    } finally {
      setRetrying(null);
      reloadStuck();
    }
  };

  const stuckColumns: Column<StuckOrderRow>[] = [
    { key: "customer", header: "Customer", value: (o) => o.organization_name, render: (o) => <CustomerLink id={o.organization_id} name={o.organization_name} /> },
    {
      key: "order",
      header: "Order",
      value: (o) => o.number,
      render: (o) => <Stack main={o.number} sub={o.description ? <Trunc max="md">{o.description}</Trunc> : undefined} />,
    },
    moneyColumn<StuckOrderRow>("amount", "Amount", (o) => o.amount_due_minor, (o) => o.currency),
    { key: "paid", header: "Paid", value: (o) => o.paid_at, render: (o) => formatDateTime(o.paid_at) },
    {
      key: "state",
      header: "Where it stands",
      wrap: true,
      value: (o) => o.status,
      render: () => <Stack main={<Badge tone="warning">Paid</Badge>} sub="Payment received, but the subscription or wallet credit has not started." />,
    },
    ...(manage
      ? [
          {
            key: "retry",
            header: "Retry",
            render: (o: StuckOrderRow) => (
              <Button
                size="sm"
                icon="refresh"
                loading={retrying === o.order_id}
                disabled={retrying !== null}
                aria-label={`Retry activation for order ${o.number}`}
                onClick={() => void retry(o)}
              >
                Retry activation
              </Button>
            ),
          },
        ]
      : []),
  ];

  return (
    <Page>
      <PageHeader
        title="Failed & stuck"
        subtitle="Failed payments, paid orders that never started, and refunds that didn't go through."
        info="A failed payment stays here while its invoice is still open. A failure later paid by a retry leaves this list on its own. An order that can't be fulfilled is refunded automatically, so it appears here only if that refund fails."
      />
      <TableSection title="Failed payments" description="Open the customer's billing to resolve one.">
        <PagedTable
          paged={failed}
          what="failed payments"
          caption="Failed payments"
          columns={FAILED_COLUMNS}
          rowKey={(p) => p.attempt_id}
          empty={<EmptyState icon="checkCircle" title="No failed payments are waiting" hint="Nothing needs anyone's attention here." />}
        />
      </TableSection>
      <TableSection title="Paid, not activated" description="Paid orders that have not become subscriptions or wallet credit, most recently changed first.">
        <LoadGate load={stuck} what="orders waiting for activation">
          {({ results }) => (
            <>
              <DataTable
                caption="Paid orders not activated"
                columns={stuckColumns}
                rows={results}
                rowKey={(o) => o.order_id}
                pageSize={0}
                maxHeight={false}
                empty={<EmptyState icon="checkCircle" title="No paid orders are waiting for activation" hint="Nothing needs anyone's attention here." />}
              />
              {results.length >= STUCK_SHOWN && <p className={local.note}>Showing the {STUCK_SHOWN} most recently changed. Older orders appear as these leave the list.</p>}
            </>
          )}
        </LoadGate>
      </TableSection>
      <TableSection title="Refunds that need attention" description="Refunds that failed or need a person to check them. A later refund of the same payment clears the older one.">
        <PagedTable
          paged={refunds}
          what="refunds that need attention"
          caption="Refunds that need attention"
          columns={REFUND_COLUMNS}
          rowKey={(r) => r.refund_id}
          empty={<EmptyState icon="checkCircle" title="No refunds need attention" hint="Nothing needs anyone's attention here." />}
        />
      </TableSection>
    </Page>
  );
}

// --- refunds -----------------------------------------------------------------------------

const REFUND_CHIPS = [
  { value: "", label: "All" },
  { value: "requested", label: "Requested" },
  { value: "submitting", label: "Submitting" },
  { value: "pending", label: "Pending" },
  { value: "succeeded", label: "Succeeded" },
  { value: "failed", label: "Failed" },
  { value: "needs_review", label: "Needs review" },
];

const REVIEW_HINT = "Confirm in the provider's dashboard";

function RefundStatus({ refund }: { refund: RefundRow }) {
  if (refund.status === "needs_review") {
    return (
      <Stack
        main={
          <Badge tone="danger" icon="alert">
            Needs review
          </Badge>
        }
        sub={REVIEW_HINT}
      />
    );
  }
  return <Stack main={<StatusBadge value={refund.status} />} sub={refund.failure_code ? sentence(refund.failure_code) : undefined} />;
}

const REFUND_COLUMNS: Column<RefundRow>[] = [
  { key: "customer", header: "Customer", value: (r) => r.organization_name, render: (r) => <CustomerLink id={r.organization_id} name={r.organization_name} /> },
  { key: "invoice", header: "Invoice", value: (r) => r.invoice_number, render: (r) => <InvoiceLink number={r.invoice_number} /> },
  { key: "provider", header: "Provider", value: (r) => r.provider, render: (r) => sentence(r.provider) },
  moneyColumn<RefundRow>("amount", "Amount", (r) => r.amount_minor, (r) => r.currency),
  moneyColumn<RefundRow>("original", "Original payment", (r) => r.original_amount_minor, (r) => r.currency),
  { key: "reason", header: "Reason", value: (r) => r.reason, render: (r) => <Trunc max="md">{r.reason}</Trunc> },
  { key: "status", header: "Status", value: (r) => r.status, render: (r) => <RefundStatus refund={r} /> },
  { key: "note", header: "Credit note", value: (r) => r.credit_note, render: (r) => r.credit_note || EMPTY },
  { key: "created", header: "Created", value: (r) => r.created_at, render: (r) => <DateText iso={r.created_at} /> },
];

export function RefundsScreen() {
  const [status, setStatus] = useState("");
  const paged = usePaged((cursor) => billingApi.refunds({ status: status || undefined, cursor }), [status]);
  const toReview = paged.rows.some((r) => r.status === "needs_review");
  return (
    <Page>
      <PageHeader
        title="Refunds"
        subtitle="Refunds across customers, each with the payment and invoice it answers."
        info="Needs review means nobody can tell whether the provider returned the money. It is never sent again automatically, because that could return it twice. Confirm it in the provider's dashboard."
      />
      <StatusChips label="Refund status" value={status} onChange={setStatus} chips={REFUND_CHIPS} />
      {toReview && (
        <Alert tone="warning" title="Refunds below need review">
          We can&apos;t tell whether the provider returned this money, and it won&apos;t be sent again automatically because that could return it twice. Confirm each one in the provider&apos;s dashboard.
        </Alert>
      )}
      <PagedTable
        paged={paged}
        what="refunds"
        caption="Refunds"
        columns={REFUND_COLUMNS}
        rowKey={(r) => r.refund_id}
        rowHref={(r) => customerHref(r.organization_id)}
        empty={
          <EmptyState
            icon="receipt"
            title={status === "needs_review" ? "No refunds need review" : "No refunds"}
            hint={status ? "Try another status." : "Refunds appear here when staff refund a payment from a customer's billing."}
          />
        }
      />
    </Page>
  );
}

// --- credit notes --------------------------------------------------------------------------

const CREDIT_NOTE_COLUMNS: Column<CreditNoteRow>[] = [
  { key: "number", header: "Number", value: (n) => n.number, render: (n) => n.number },
  { key: "customer", header: "Customer", value: (n) => n.organization_name, render: (n) => <CustomerLink id={n.organization_id} name={n.organization_name} /> },
  { key: "invoice", header: "Invoice", value: (n) => n.invoice_number, render: (n) => <InvoiceLink number={n.invoice_number} /> },
  { key: "refund", header: "Refund ID", render: (n) => <IdCell value={n.refund_id} label="Copy refund ID" /> },
  moneyColumn<CreditNoteRow>("amount", "Amount", (n) => n.amount_minor, (n) => n.currency),
  { key: "reason", header: "Reason", value: (n) => n.reason, render: (n) => <Trunc max="md">{n.reason}</Trunc> },
  { key: "issued", header: "Issued", value: (n) => n.issued_at, render: (n) => <DateText iso={n.issued_at} /> },
];

export function CreditNotesScreen() {
  const [query, setQuery] = useState("");
  const paged = usePaged((cursor) => billingApi.creditNotes({ q: query || undefined, cursor }), [query]);
  return (
    <Page>
      <PageHeader title="Credit notes" subtitle="The credit note issued for each confirmed refund, newest first." />
      <FilterBar onClear={query ? () => setQuery("") : undefined}>
        <SearchInput label="Search by credit note or invoice number" value={query} onSearch={setQuery} maxLength={40} />
      </FilterBar>
      <PagedTable
        paged={paged}
        what="credit notes"
        caption="Credit notes"
        columns={CREDIT_NOTE_COLUMNS}
        rowKey={(n) => n.id}
        rowHref={(n) => customerHref(n.organization_id)}
        empty={<EmptyState icon="receipt" title="No credit notes" hint={query ? "Try another number or clear the search." : "A credit note is issued when a refund is confirmed."} />}
      />
    </Page>
  );
}

// --- subscriptions ---------------------------------------------------------------------------

const SUBSCRIPTION_CHIPS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "trialing", label: "Trial" },
  { value: "past_due", label: "Past due" },
  { value: "grace", label: "Grace" },
  { value: "suspended", label: "Suspended" },
  { value: "cancelled", label: "Cancelled" },
  { value: "expired", label: "Expired" },
];

const RENEWING = [
  { value: "", label: "Any time" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
];

/** A line is a product and plan, or a bundle; its quantity is seats. */
function SubscriptionLines({ lines, names }: { lines: SubscriptionLine[]; names: Record<string, string> }) {
  if (lines.length === 0) return <>{EMPTY}</>;
  return (
    <ul className={local.lines}>
      {lines.map((line, i) => {
        const plan = line.plan_code ? sentence(line.plan_code) : "";
        const seats = `${formatNumber(line.quantity)} ${line.quantity === 1 ? "seat" : "seats"}`;
        return (
          <li key={`${line.product_code}-${line.plan_code}-${line.bundle_code}-${i}`}>
            {line.product_code ? (names[line.product_code] ?? sentence(line.product_code)) : sentence(line.bundle_code)}{" "}
            <span className={local.lineMeta}>{[plan, seats].filter(Boolean).join(" · ")}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function BillingSubscriptionsScreen() {
  const [status, setStatus] = useState("");
  const [product, setProduct] = useState("");
  const [renewing, setRenewing] = useState("");
  const [query, setQuery] = useState("");
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  // The catalog needs its own grant: without it the list still works, unfiltered by product.
  const [products] = useLoad(() => adminApi.products(), []);
  const catalog = products.status === "ready" ? products.data.results : [];
  const names = Object.fromEntries(catalog.map((p) => [p.code, productName(p.name)]));

  const paged = usePaged(
    async (cursor) => {
      const page = await billingApi.subscriptions({
        status: status || undefined,
        product: product || undefined,
        renewing: renewing ? Number(renewing) : undefined,
        q: query || undefined,
        cursor,
      });
      setCounts(page.counts);
      return page;
    },
    [status, product, renewing, query],
  );

  const columns: Column<SubscriptionRow>[] = [
    { key: "customer", header: "Customer", value: (s) => s.organization_name, render: (s) => <CustomerLink id={s.organization_id} name={s.organization_name} /> },
    {
      key: "lines",
      header: "Products and plans",
      wrap: true,
      value: (s) => s.lines.map((l) => l.product_code || l.bundle_code),
      render: (s) => <SubscriptionLines lines={s.lines} names={names} />,
    },
    { key: "status", header: "Status", value: (s) => s.status, render: (s) => <StatusBadge value={s.status} /> },
    { key: "billing", header: "Billing", value: (s) => s.billing_interval, render: (s) => INTERVAL_LABELS[s.billing_interval] ?? sentence(s.billing_interval) },
    { key: "renewal", header: "Renewal policy", value: (s) => s.renewal_policy, render: (s) => sentence(s.renewal_policy) },
    { key: "period", header: "Period end", value: (s) => s.current_period_end, render: (s) => <DateText iso={s.current_period_end} /> },
    { key: "cancel", header: "Cancel at", value: (s) => s.cancel_at, render: (s) => <DateText iso={s.cancel_at} /> },
  ];

  const chips = SUBSCRIPTION_CHIPS.map((chip) => ({
    ...chip,
    count: counts ? (chip.value ? (counts[chip.value] ?? 0) : Object.values(counts).reduce((sum, n) => sum + n, 0)) : undefined,
  }));
  const filtered = Boolean(product || renewing || query);

  return (
    <Page>
      <PageHeader
        title="Subscriptions"
        subtitle="Every subscription across customers, with what it holds. Open a customer to act on one."
        info="The counts on the status chips cover every subscription, whatever the other filters. Renewing within covers running subscriptions that are not set to cancel."
      />
      <p className={local.note}>
        Pending setup is not a subscription status, because setup is a separate state. It lives on{" "}
        <Link href="/admin/customers/setup">Customers &gt; Products &amp; Setup</Link>.
      </p>
      <StatusChips label="Subscription status" value={status} onChange={setStatus} chips={chips} />
      <FilterBar
        onClear={
          filtered
            ? () => {
                setProduct("");
                setRenewing("");
                setQuery("");
              }
            : undefined
        }
      >
        <SearchInput label="Search customers" value={query} onSearch={setQuery} />
        {catalog.length > 0 && (
          <Select
            label="Product"
            prefix="Product:"
            value={product}
            onChange={setProduct}
            options={[{ value: "", label: "All" }, ...catalog.map((p) => ({ value: p.code, label: productName(p.name) }))]}
          />
        )}
        <Select label="Renewing within" prefix="Renewing within:" value={renewing} onChange={setRenewing} options={RENEWING} />
      </FilterBar>
      <PagedTable
        paged={paged}
        what="subscriptions"
        caption="Subscriptions"
        columns={columns}
        rowKey={(s) => s.subscription_id}
        rowHref={(s) => customerHref(s.organization_id)}
        empty={
          <EmptyState
            icon="card"
            title="No subscriptions"
            hint={status || filtered ? "Try another status or clear the filters." : "Subscriptions appear here when customers buy or start a trial."}
          />
        }
      />
    </Page>
  );
}

// --- commission entries --------------------------------------------------------------------

/** What the staff ledger sends beyond the shared row (commissions/selectors.py).
 * `earning` and `partner_name` are read if the server sends them; today's response
 * has neither, so the Earning column stays hidden and a partner shows by its id. */
type LedgerRow = AccrualRow & {
  reverses?: string | null;
  status?: string;
  reversed_minor?: number;
  earning?: string;
  partner_name?: string;
};

const reverseEntry = (id: string, body: { reason_code: string; reason: string; amount_minor: number }, commandId: string) =>
  apiFetch<{ reversed: boolean }>(`/api/v1/admin/commission/accruals/${id}/reverse`, {
    ...jsonPost(body),
    headers: { "Idempotency-Key": commandId },
  });

const PARTNER_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const ENTRY_CHIPS = [
  { value: "", label: "All entries" },
  { value: "1", label: "Not in a payout" },
];

const KIND_TONE: Record<string, Tone> = { accrual: "success", reversal: "warning", adjustment: "info" };

const REVERSAL_REASONS = [
  { value: "correction", label: "Correction" },
  { value: "refund", label: "Refund" },
  { value: "cancellation", label: "Cancellation" },
  { value: "chargeback", label: "Chargeback" },
  { value: "downgrade_correction", label: "Downgrade correction" },
];

/** What is left to reverse of an earning; a reversal or adjustment has none. */
const reversibleMinor = (e: LedgerRow) => (e.kind === "accrual" && e.status !== "reversed" ? e.amount_minor - (e.reversed_minor ?? 0) : 0);

function ReversalReason({ onChange }: { onChange: (code: string) => void }) {
  const [code, setCode] = useState(REVERSAL_REASONS[0].value);
  return (
    <Select
      label="Why it is being reversed"
      showLabel
      fullWidth
      value={code}
      options={REVERSAL_REASONS}
      onChange={(next) => {
        setCode(next);
        onChange(next);
      }}
    />
  );
}

/** `partnerId`: one partner's id from the address; anything else is ignored. */
export function CommissionEntriesScreen({ partnerId = "" }: { partnerId?: string } = {}) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_commission.manage");
  const { ask, dialog: confirm } = useConfirm();
  const partner = PARTNER_ID.test(partnerId) ? partnerId : "";
  const [unpaid, setUnpaid] = useState("");
  const paged = usePaged<LedgerRow>(
    (cursor) => adminApi.accruals({ partner_id: partner || undefined, unpaid: unpaid || undefined, cursor }),
    [partner, unpaid],
  );

  const reverse = (entry: LedgerRow) => {
    // One command per decision: a retry from this dialog is the same reversal.
    const commandId = newKey();
    const amount = reversibleMinor(entry);
    const said = formatMoney(amount, entry.currency);
    const choice = { code: REVERSAL_REASONS[0].value };
    ask({
      title: "Reverse this commission entry?",
      message: (
        <>
          <p>
            This adds a reversal of {said} to the ledger. The original entry stays, and the reversal offsets it.
          </p>
          <ReversalReason
            onChange={(code) => {
              choice.code = code;
            }}
          />
        </>
      ),
      confirmLabel: "Reverse entry",
      danger: true,
      reason: true,
      actionName: "Reverse commission entry",
      onConfirm: async (reason) => {
        await reverseEntry(entry.id, { reason_code: choice.code, reason, amount_minor: amount }, commandId);
        paged.reload();
        return `Reversal of ${said} recorded.`;
      },
    });
  };

  const columns: Column<LedgerRow>[] = [
    { key: "when", header: "When", value: (e) => e.event_at, render: (e) => formatDateTime(e.event_at) },
    { key: "partner", header: "Partner", value: (e) => e.partner_name || e.partner_id, render: (e) => <PartnerLink id={e.partner_id} name={e.partner_name} /> },
    {
      key: "customer",
      header: "Customer",
      value: (e) => e.organization_name,
      render: (e) => (e.organization_id ? <CustomerLink id={e.organization_id} name={e.organization_name ?? ""} /> : EMPTY),
    },
    {
      key: "kind",
      header: "Kind",
      value: (e) => e.kind,
      render: (e) => <Stack main={<Badge tone={KIND_TONE[e.kind] ?? "neutral"}>{sentence(e.kind)}</Badge>} sub={e.reason_code ? sentence(e.reason_code) : undefined} />,
    },
    { key: "earning", header: "Earning", value: (e) => e.earning, render: (e) => sentence(e.earning) },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      value: (e) => e.amount_minor,
      render: (e) => (
        <Stack
          main={formatMoney(e.amount_minor, e.currency)}
          sub={e.reversed_minor ? `${formatMoney(e.reversed_minor, e.currency)} reversed` : undefined}
        />
      ),
    },
    { key: "clears", header: "Clears at", value: (e) => e.clears_at, render: (e) => formatDateTime(e.clears_at) },
    { key: "reverses", header: "Reverses", value: (e) => e.reverses, render: (e) => <IdCell value={e.reverses} label="Copy the ID of the entry it reverses" /> },
    ...(manage
      ? [
          {
            key: "action",
            header: "Action",
            render: (e: LedgerRow) =>
              reversibleMinor(e) > 0 ? (
                <Button size="sm" aria-label={`Reverse ${formatMoney(reversibleMinor(e), e.currency)} earned ${formatDate(e.event_at)}`} onClick={() => reverse(e)}>
                  Reverse
                </Button>
              ) : (
                EMPTY
              ),
          },
        ]
      : []),
  ];

  return (
    <Page>
      <PageHeader
        title="Commission entries"
        subtitle="Earnings records, not a wallet: nothing here can be spent or topped up."
        info="The ledger only grows. An accrual is earned on a sale, a reversal offsets an earlier entry, and an adjustment is a staff correction. Whether an entry has been paid depends on the payout that includes it."
      />
      {partner && (
        <p className={local.filterNote}>
          Showing one partner: <PartnerLink id={partner} />
          <ButtonLink variant="link" size="sm" href="/admin/commissions">
            Show every partner
          </ButtonLink>
        </p>
      )}
      <StatusChips label="Payout state" value={unpaid} onChange={setUnpaid} chips={ENTRY_CHIPS} />
      <PagedTable
        paged={paged}
        what="commission entries"
        caption="Commission entries"
        columns={columns}
        rowKey={(e) => e.id}
        empty={
          <EmptyState
            icon="receipt"
            title={unpaid ? "Every entry is in a payout" : "No commission entries"}
            hint={unpaid ? undefined : "Entries appear here when a partner's sale earns commission."}
          />
        }
      />
      {confirm}
    </Page>
  );
}

// --- payouts -----------------------------------------------------------------------------------

/** `partner_name` is read if the server sends it; today a payout names its partner by id only. */
type PayoutListRow = PayoutRow & { partner_name?: string };

const PAYOUT_CHIPS = [
  { value: "", label: "All" },
  { value: "draft", label: "Draft" },
  { value: "ready", label: "Ready" },
  { value: "approved", label: "Approved" },
  { value: "paid", label: "Paid" },
  { value: "cancelled", label: "Cancelled" },
];

export function PayoutsScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_payout.manage");
  const approve = can(permissions, "platform.staff_payout.approve");
  const [status, setStatus] = useState("");
  const paged = usePaged<PayoutListRow>((cursor) => adminApi.payouts({ status: status || undefined, cursor }), [status]);
  const { act, busy, feedback } = useAction(paged.reload);

  const columns: Column<PayoutListRow>[] = [
    { key: "partner", header: "Partner", value: (p) => p.partner_name || p.partner_id, render: (p) => <PartnerLink id={p.partner_id} name={p.partner_name} /> },
    // A date with no time: read as local midnight, so it never shows the day before.
    { key: "period", header: "Period end", value: (p) => p.period_end, render: (p) => formatDate(`${p.period_end}T00:00:00`) },
    { key: "status", header: "Status", value: (p) => p.status, render: (p) => <StatusBadge value={p.status} /> },
    moneyColumn<PayoutListRow>("gross", "Gross", (p) => p.gross_minor, (p) => p.currency),
    moneyColumn<PayoutListRow>("adjustments", "Adjustments", (p) => p.adjustments_minor, (p) => p.currency),
    moneyColumn<PayoutListRow>("payable", "Payable", (p) => p.payable_minor, (p) => p.currency),
    { key: "entries", header: "Entries", align: "right", value: (p) => p.line_count, render: (p) => formatNumber(p.line_count) },
    { key: "reference", header: "Transfer reference", value: (p) => p.paid_reference, render: (p) => <Trunc max="sm">{p.paid_reference}</Trunc> },
    ...(manage || approve
      ? [
          {
            key: "actions",
            header: "Actions",
            render: (p: PayoutListRow) => (
              <div className={local.rowActions}>
                <PayoutActions payout={p} manage={manage} approve={approve} busy={busy} act={act} />
              </div>
            ),
          },
        ]
      : []),
  ];

  return (
    <Page>
      <PageHeader
        title="Payouts"
        subtitle="Reviewed batches of cleared commission, across partners."
        info="This platform moves no money: paid records that a person confirmed a transfer made elsewhere. The person who prepares a payout cannot approve it. Prepare one from a partner's Payouts tab."
        actions={manage ? <ButtonLink href="/admin/partners/withdrawals">Withdrawal requests</ButtonLink> : undefined}
      />
      {feedback}
      <StatusChips label="Payout status" value={status} onChange={setStatus} chips={PAYOUT_CHIPS} />
      <PagedTable
        paged={paged}
        what="payouts"
        caption="Payouts"
        columns={columns}
        rowKey={(p) => p.id}
        empty={
          <EmptyState
            icon="wallet"
            title="No payouts"
            hint={status ? "Try another status." : "A payout appears here once someone prepares one from a partner's Payouts tab."}
          />
        }
      />
    </Page>
  );
}
