"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  consoleApi,
  type BillingAccount,
  type Invoice,
  type OrgSubscription,
  type PaymentAttempt,
} from "@/lib/api/console";
import { newKey } from "@/lib/api/controlPlane";
import { LoadGate, Status, Table, describeError, money, useAction, useLoad } from "@/features/controlplane/ui";
import { SUPPORTED_CURRENCIES } from "@/features/controlplane/amounts";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { ConfirmAction, PageHeader, Panel, UUID_RE, day, intervalLabel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/** How the page waits for a payment: a hosted page is usually ready within a
 * few seconds; the provider's confirmation after a return can take longer.
 * Mutable only so the tests need not wait in real time. */
export const PAYMENT_POLL = { intervalMs: 1500, timeoutMs: 40_000 };

/** The one place the page leaves for a payment provider (replaceable in tests,
 * where jsdom cannot navigate). */
export const checkoutNavigation = {
  assign: (url: string) => window.location.assign(url),
};

/**
 * The address to send the payer to, or `null`. A checkout URL comes from the
 * server, but it is still only followed when it is HTTPS or this app's own
 * origin (the development test page): never `javascript:`, `data:` or plain
 * HTTP to somewhere else.
 */
export function safeCheckoutUrl(raw: string | null | undefined, here?: string): string | null {
  if (!raw) return null;
  try {
    const base = here ?? window.location.href;
    const url = new URL(raw, base);
    if (url.protocol === "https:" || url.origin === new URL(base).origin) return url.href;
  } catch {
    // An unparseable address is not followed.
  }
  return null;
}


const COLLECTION_LABELS: Record<string, string> = {
  automatic: "Pay online by card",
  invoice: "Pay by invoice",
  partner_billed: "Billed by your partner",
};

const ENDED = new Set(["failed", "expired", "cancelled"]);

/**
 * Subscription & billing: what the organization subscribes to, its billing
 * account, and its invoices - each shown only to those allowed to see it.
 *
 * Paying an invoice starts a payment attempt with an idempotency key
 * minted once per click, waits for the provider's hosted page, and goes there.
 * Coming back (`?payment=<attempt>&outcome=...`) the page asks the server
 * whether the payment arrived: the browser's return proves nothing - only the
 * provider's signed confirmation marks an invoice paid.
 */
export function BillingScreen() {
  const { slug, can } = useOrgAccess();
  const [paidTick, setPaidTick] = useState(0);
  const onPaid = useCallback(() => setPaidTick((n) => n + 1), []);
  const canSubs = can("commercial.subscription.view");
  const canView = can("platform.billing.view");
  const canPay = can("platform.billing.manage");
  const canOpen = can("commercial.subscription.manage");

  return (
    <div className={styles.page}>
      <PageHeader
        title="Subscription & billing"
        lead="What your organization subscribes to, how it pays, and its invoices."
        actions={
          <Link className="w360-btn w360-btn-secondary" href={`/o/${slug}/explore`}>
            Explore products
          </Link>
        }
      />
      {canView && <PaymentReturn slug={slug} onPaid={onPaid} />}
      {!canSubs && !canView && !canOpen && (
        <p role="alert">You don&apos;t have access to this organization&apos;s subscription or billing.</p>
      )}
      {canSubs && <SubscriptionPanel slug={slug} canManage={canOpen} />}
      {(canView || canOpen) && <BillingAccountPanel slug={slug} canView={canView} canOpen={canOpen} />}
      {canView && <InvoicesPanel slug={slug} canPay={canPay} refresh={paidTick} />}
    </div>
  );
}

/** `/o/<slug>/billing` - where a payment provider sends the payer back - opens
 * this page, keeping only the two parameters it understands. */
export function BillingReturnRedirect() {
  const { slug } = useOrgAccess();
  const router = useRouter();
  const params = useSearchParams();
  useEffect(() => {
    const next = new URLSearchParams();
    const payment = params.get("payment");
    const outcome = params.get("outcome");
    if (payment && UUID_RE.test(payment)) next.set("payment", payment);
    if (outcome === "success" || outcome === "cancelled") next.set("outcome", outcome);
    const qs = next.toString();
    router.replace(`/o/${slug}/settings/billing${qs ? `?${qs}` : ""}`);
  }, [params, router, slug]);
  return (
    <>
      <h1 className="w360-visually-hidden">Subscription &amp; billing</h1>
      <p aria-live="polite">Opening billing…</p>
    </>
  );
}

// --- the return from a payment page ----------------------------------------------------

type ReturnState = { id: string; kind: "paid" | "processing" | "failed" | "error" };

function PaymentReturn({ slug, onPaid }: { slug: string; onPaid: () => void }) {
  const params = useSearchParams();
  const attemptId = params.get("payment");
  const outcome = params.get("outcome");
  const valid = attemptId !== null && UUID_RE.test(attemptId);
  const [state, setState] = useState<ReturnState | null>(null);

  useEffect(() => {
    if (!valid || !attemptId || outcome !== "success") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    const tick = async () => {
      try {
        const attempt = await consoleApi.payment(slug, attemptId);
        if (cancelled) return;
        if (attempt.status === "succeeded") {
          setState({ id: attemptId, kind: "paid" });
          onPaid();
          return;
        }
        if (ENDED.has(attempt.status)) {
          setState({ id: attemptId, kind: "failed" });
          return;
        }
      } catch {
        if (!cancelled) setState({ id: attemptId, kind: "error" });
        return;
      }
      if (Date.now() - started >= PAYMENT_POLL.timeoutMs) {
        setState({ id: attemptId, kind: "processing" });
        return;
      }
      timer = setTimeout(tick, PAYMENT_POLL.intervalMs);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [slug, attemptId, outcome, valid, onPaid]);

  if (!attemptId || !valid) return null;
  if (outcome !== "success") {
    return (
      <p role="status" className={local.notice}>
        Payment cancelled. You can pay the invoice whenever you&apos;re ready.
      </p>
    );
  }
  const kind = state && state.id === attemptId ? state.kind : null;
  if (kind === "failed" || kind === "error") {
    return (
      <p role="alert" className={local.notice}>
        {kind === "failed"
          ? "The payment didn't go through. You can try again from the invoice below."
          : "We couldn't check on this payment just now. Reload the page to check again."}
      </p>
    );
  }
  return (
    <p role="status" className={local.notice}>
      {kind === "paid"
        ? "Payment received. Thank you - the invoice is now marked as paid."
        : kind === "processing"
          ? "Your payment is still processing. The invoice shows as paid once the payment provider confirms it, which can take a few minutes."
          : "Checking your payment…"}
    </p>
  );
}

// --- subscription -------------------------------------------------------------------------

function SubscriptionPanel({ slug, canManage }: { slug: string; canManage: boolean }) {
  const [load, reload] = useLoad(() => consoleApi.subscriptions(slug), [slug]);
  const { act, busy, feedback } = useAction(reload);
  // Always at the end of the paid period: the organization keeps what it paid
  // for, and nothing renews (`cancel_subscription`, never `immediate` here).
  const cancel = (s: OrgSubscription) =>
    act(
      () => consoleApi.cancelSubscription(slug, s.id, s.version, newKey()),
      `Subscription cancelled. Your products stay available until ${day(s.current_period_end)}, and it won't renew.`,
    );
  return (
    <Panel title="Subscription">
      <LoadGate load={load} what="your subscription">
        {({ results }) => {
          const live = results.filter((s) => s.status !== "expired");
          if (live.length === 0) {
            return (
              <p className={local.textBlock}>
                No active subscription. <Link href={`/o/${slug}/explore`}>Choose a plan in Explore products</Link>
              </p>
            );
          }
          return (
            <>
              {live.map((s) => (
                <SubscriptionSummary
                  key={s.id}
                  subscription={s}
                  cancelControl={
                    canManage && renewing(s) ? (
                      <ConfirmAction
                        label="Cancel subscription"
                        prompt={`Cancel this subscription? Your products stay available until ${day(s.current_period_end)}, then end. Nothing more is charged.`}
                        confirmLabel="Yes, cancel at the end of the period"
                        busy={busy}
                        onConfirm={() => void cancel(s)}
                      />
                    ) : null
                  }
                />
              ))}
              {feedback}
            </>
          );
        }}
      </LoadGate>
    </Panel>
  );
}

/** Active and set to renew: the only state a customer can cancel from. */
function renewing(s: OrgSubscription): boolean {
  return !s.cancel_at && (s.status === "active" || s.status === "trialing" || s.status === "past_due");
}

function SubscriptionSummary({
  subscription: s,
  cancelControl,
}: {
  subscription: OrgSubscription;
  cancelControl?: React.ReactNode;
}) {
  const items = s.items.filter((i) => !i.removed_at);
  const renews = renewing(s);
  return (
    <div className={local.section}>
      <dl className={local.details}>
        <div>
          <dt>Status</dt>
          <dd>
            <Status value={s.status} />
          </dd>
        </div>
        <div>
          <dt>Billing</dt>
          <dd>
            {intervalLabel(s.billing_interval)}, in {s.currency}
          </dd>
        </div>
        <div>
          <dt>Current period</dt>
          <dd>
            {day(s.current_period_start)} – {day(s.current_period_end)}
          </dd>
        </div>
        {s.trial_ends_at && (
          <div>
            <dt>Trial ends</dt>
            <dd>{day(s.trial_ends_at)}</dd>
          </div>
        )}
        {s.cancel_at ? (
          <div>
            <dt>Ends on</dt>
            <dd>{day(s.cancel_at)}</dd>
          </div>
        ) : (
          renews && (
            <div>
              <dt>Renews on</dt>
              <dd>{day(s.current_period_end)}</dd>
            </div>
          )
        )}
      </dl>
      <Table
        caption="Products in this subscription"
        head={["Product", "Plan", "Quantity"]}
        isEmpty={items.length === 0}
        empty="No products in this subscription."
      >
        {items.map((i) => (
          <tr key={i.id}>
            <th scope="row">{i.product_code}</th>
            <td>
              {i.plan_code || "—"}
              {i.bundle_code && <div className={cp.muted}>Part of {i.bundle_code}</div>}
            </td>
            <td className={cp.num}>{i.quantity}</td>
          </tr>
        ))}
      </Table>
      {cancelControl}
    </div>
  );
}

// --- billing account ----------------------------------------------------------------------

function BillingAccountPanel({ slug, canView, canOpen }: { slug: string; canView: boolean; canOpen: boolean }) {
  const [load, reload] = useLoad(
    () => (canView ? consoleApi.billingAccount(slug) : Promise.resolve({ billing_account: null })),
    [slug, canView],
  );
  // Without the billing view permission the account cannot be read back, so
  // an account opened here is remembered for this visit instead.
  const [openedIn, setOpenedIn] = useState<string | null>(null);
  const { act, busy, feedback } = useAction(reload);

  return (
    <Panel title="Billing account">
      {feedback}
      <LoadGate load={load} what="the billing account">
        {({ billing_account: account }) => {
          if (account) return <AccountDetails account={account} />;
          if (openedIn) return <p className={local.textBlock}>This organization is billed in {openedIn}.</p>;
          if (!canOpen) {
            return (
              <p className={local.textBlock}>
                No billing account yet. The organization owner opens one, which sets the currency you&apos;re billed in.
              </p>
            );
          }
          return (
            <OpenAccountForm
              busy={busy}
              onOpen={(body) =>
                void act(async () => {
                  const opened = await consoleApi.openBillingAccount(slug, body);
                  if (!canView) setOpenedIn(opened.currency);
                }, `Billing account opened. Your organization is billed in ${body.currency}.`)
              }
            />
          );
        }}
      </LoadGate>
    </Panel>
  );
}

function AccountDetails({ account }: { account: BillingAccount }) {
  return (
    <dl className={local.details}>
      <div>
        <dt>Currency</dt>
        <dd>{account.currency}</dd>
      </div>
      <div>
        <dt>How you pay</dt>
        <dd>{COLLECTION_LABELS[account.collection_mode] ?? account.collection_mode.replace(/_/g, " ")}</dd>
      </div>
      <div>
        <dt>Billing email</dt>
        <dd>{account.billing_email || "—"}</dd>
      </div>
      <div>
        <dt>Billing region</dt>
        <dd>{account.billing_region || "—"}</dd>
      </div>
    </dl>
  );
}

/** Phase 9: the currency and a billing address - collection mode and region
 * are Workeasy360's to set (a sales-led agreement), never the customer's. */
export function OpenAccountForm({
  busy,
  onOpen,
}: {
  busy: boolean;
  onOpen: (body: { currency: string; billing_email: string }) => void;
}) {
  const currencyId = useId();
  const emailId = useId();
  const [currency, setCurrency] = useState("");
  const [email, setEmail] = useState("");

  return (
    <form
      className={cp.form}
      onSubmit={(e) => {
        e.preventDefault();
        onOpen({ currency, billing_email: email.trim() });
      }}
    >
      <p className={local.textBlock}>
        Open a billing account to buy plans. It sets the currency your organization is billed in, which can&apos;t be
        changed later.
      </p>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={currencyId}>
          Currency
        </label>
        <select
          id={currencyId}
          className="w360-input"
          required
          value={currency}
          onChange={(e) => setCurrency(e.target.value)}
        >
          <option value="">Choose a currency</option>
          {SUPPORTED_CURRENCIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={emailId}>
          Billing email (optional)
        </label>
        <input
          id={emailId}
          className="w360-input"
          type="email"
          autoComplete="email"
          maxLength={254}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !currency}>
          Open billing account
        </button>
      </div>
    </form>
  );
}

// --- invoices and paying them ----------------------------------------------------------------

function InvoicesPanel({ slug, canPay, refresh }: { slug: string; canPay: boolean; refresh: number }) {
  const [load, reload] = useLoad(() => consoleApi.invoices(slug), [slug, refresh]);
  const pay = usePayInvoice(slug, reload);
  return (
    <Panel title="Invoices">
      {pay.feedback}
      <LoadGate load={load} what="invoices">
        {({ results }) => (
          <Table
            caption="Your invoices, newest first"
            head={["Invoice", "Period", "Amount", "Status", "Due", "Paid", "Actions"]}
            isEmpty={results.length === 0}
            empty="No invoices yet."
          >
            {results.map((inv) => (
              <InvoiceRow
                key={inv.invoice_id}
                slug={slug}
                invoice={inv}
                canPay={canPay}
                busy={pay.busy}
                onPay={pay.start}
              />
            ))}
          </Table>
        )}
      </LoadGate>
    </Panel>
  );
}

function InvoiceRow({
  slug,
  invoice,
  canPay,
  busy,
  onPay,
}: {
  slug: string;
  invoice: Invoice;
  canPay: boolean;
  busy: boolean;
  onPay: (invoice: Invoice) => void;
}) {
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const payable = canPay && invoice.status === "open" && invoice.amount_due_minor > 0;
  return (
    <>
      <tr>
        <th scope="row" className={cp.mono}>
          {invoice.number}
        </th>
        <td>
          {day(invoice.period_start)} – {day(invoice.period_end)}
        </td>
        <td className={cp.num}>
          {money(invoice.total_minor, invoice.currency)}
          {invoice.status === "open" && invoice.amount_due_minor !== invoice.total_minor && (
            <div className={cp.muted}>{money(invoice.amount_due_minor, invoice.currency)} due</div>
          )}
        </td>
        <td>
          <Status value={invoice.status} />
          {invoice.dunning_stage && <div className={cp.muted}>Reminder: {invoice.dunning_stage.replace(/_/g, " ")}</div>}
        </td>
        <td>{day(invoice.due_at)}</td>
        <td>{day(invoice.paid_at)}</td>
        <td>
          <div className={local.actions}>
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              aria-expanded={open}
              aria-controls={open ? detailId : undefined}
              aria-label={`Details of invoice ${invoice.number}`}
              onClick={() => setOpen((v) => !v)}
            >
              Details
            </button>
            {payable && (
              <button
                type="button"
                className="w360-btn w360-btn-primary"
                aria-label={`Pay invoice ${invoice.number}`}
                disabled={busy}
                onClick={() => onPay(invoice)}
              >
                Pay
              </button>
            )}
          </div>
        </td>
      </tr>
      {open && (
        <tr id={detailId}>
          <td colSpan={7} className={local.detailCell}>
            <InvoiceLines slug={slug} invoiceId={invoice.invoice_id} />
          </td>
        </tr>
      )}
    </>
  );
}

function InvoiceLines({ slug, invoiceId }: { slug: string; invoiceId: string }) {
  const [load] = useLoad(() => consoleApi.invoice(slug, invoiceId), [slug, invoiceId]);
  return (
    <LoadGate load={load} what="this invoice">
      {(d) => (
        <div className={local.section}>
          <Table
            caption={`Lines on invoice ${d.number}`}
            head={["Description", "Product", "Quantity", "Amount"]}
            isEmpty={d.lines.length === 0}
            empty="This invoice has no lines."
          >
            {d.lines.map((line, i) => (
              <tr key={`${line.product_code}-${line.plan_code}-${i}`}>
                <th scope="row">{line.description}</th>
                <td>
                  {line.product_code || "—"}
                  {(line.plan_code || line.bundle_code) && (
                    <div className={cp.muted}>{line.plan_code || line.bundle_code}</div>
                  )}
                </td>
                <td className={cp.num}>{line.quantity}</td>
                <td className={cp.num}>{money(line.amount_minor, d.currency)}</td>
              </tr>
            ))}
          </Table>
          <dl className={local.details}>
            <div>
              <dt>Total</dt>
              <dd>{money(d.total_minor, d.currency)}</dd>
            </div>
            <div>
              <dt>Paid</dt>
              <dd>{money(d.amount_paid_minor, d.currency)}</dd>
            </div>
            {d.amount_refunded_minor > 0 && (
              <div>
                <dt>Refunded</dt>
                <dd>{money(d.amount_refunded_minor, d.currency)}</dd>
              </div>
            )}
            <div>
              <dt>Amount due</dt>
              <dd>{money(d.amount_due_minor, d.currency)}</dd>
            </div>
          </dl>
        </div>
      )}
    </LoadGate>
  );
}

type Note = { tone: "status" | "alert"; text: string };

function attemptProblem(a: PaymentAttempt): string {
  if (a.status === "expired") return "The payment session expired before it was completed. Choose Pay to start again.";
  if (a.status === "cancelled") return "The payment was cancelled. Choose Pay to start again.";
  const reason = a.failure_code ? ` (${a.failure_code.replace(/_/g, " ")})` : "";
  return `The payment couldn't be completed${reason}. Choose Pay to try again, or contact support.`;
}

/**
 * Start paying an invoice and take the payer to the provider's page.
 *
 * One idempotency key per invoice and user intent: a retry after a network
 * failure replays the same payment (the server hands back
 * the attempt already in flight). Only once an attempt has ended - failed,
 * expired, cancelled - does the next click get a new key.
 */
export function usePayInvoice(slug: string, onSettled: () => void) {
  const { act, busy, feedback } = useAction();
  const keys = useRef(new Map<string, string>());
  const [polling, setPolling] = useState<{ invoiceId: string; attemptId: string } | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  // The latest callback, without making the poll below restart whenever the
  // parent re-renders with a new function.
  const settledCallback = useRef(onSettled);
  useEffect(() => {
    settledCallback.current = onSettled;
  });

  /** Acts on an attempt's state; true when there is nothing left to wait for. */
  const settle = useCallback(
    (invoiceId: string, attempt: PaymentAttempt): boolean => {
      if (attempt.status === "awaiting_payment" && attempt.checkout_url) {
        const url = safeCheckoutUrl(attempt.checkout_url);
        if (url) {
          setNote({ tone: "status", text: "Opening the secure payment page…" });
          checkoutNavigation.assign(url);
        } else {
          setNote({
            tone: "alert",
            text: "The payment page's address could not be verified, so it was not opened. Contact support to pay this invoice.",
          });
        }
        return true;
      }
      if (attempt.status === "succeeded") {
        setNote({ tone: "status", text: "This invoice has been paid." });
        settledCallback.current();
        return true;
      }
      if (ENDED.has(attempt.status)) {
        keys.current.delete(invoiceId);
        setNote({ tone: "alert", text: attemptProblem(attempt) });
        return true;
      }
      return false;
    },
    [],
  );

  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    const tick = async () => {
      try {
        const attempt = await consoleApi.payment(slug, polling.attemptId);
        if (cancelled) return;
        if (settle(polling.invoiceId, attempt)) {
          setPolling(null);
          return;
        }
      } catch (err) {
        if (!cancelled) {
          setNote({ tone: "alert", text: describeError(err) });
          setPolling(null);
        }
        return;
      }
      if (Date.now() - started >= PAYMENT_POLL.timeoutMs) {
        setNote({
          tone: "alert",
          text: "The payment page is taking longer than usual to open. Choose Pay again in a minute - it continues the same payment.",
        });
        setPolling(null);
        return;
      }
      timer = setTimeout(tick, PAYMENT_POLL.intervalMs);
    };
    timer = setTimeout(tick, PAYMENT_POLL.intervalMs);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [polling, slug, settle]);

  function start(invoice: Pick<Invoice, "invoice_id" | "number">) {
    let key = keys.current.get(invoice.invoice_id);
    if (!key) {
      key = newKey();
      keys.current.set(invoice.invoice_id, key);
    }
    const commandKey = key;
    setNote(null);
    setPolling(null);
    void act(async () => {
      const attempt = await consoleApi.payInvoice(slug, invoice.invoice_id, commandKey);
      if (!settle(invoice.invoice_id, attempt)) {
        setNote({ tone: "status", text: `Preparing the secure payment page for invoice ${invoice.number}…` });
        setPolling({ invoiceId: invoice.invoice_id, attemptId: attempt.attempt_id });
      }
    }, `Payment started for invoice ${invoice.number}.`);
  }

  return {
    start,
    busy: busy || polling !== null,
    feedback: (
      <>
        {feedback}
        {note &&
          (note.tone === "alert" ? (
            <p role="alert" className="w360-error-text">
              {note.text}
            </p>
          ) : (
            <p role="status">{note.text}</p>
          ))}
      </>
    ),
  };
}
