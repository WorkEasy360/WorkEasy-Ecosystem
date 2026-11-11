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
  type ProductTrial,
} from "@/lib/api/console";
import { getAppDirectory } from "@/lib/api/launcher";
import { newKey } from "@/lib/api/controlPlane";
import { LoadGate, Status, Table, describeError, money, useAction, useLoad } from "@/features/controlplane/ui";
import { SUPPORTED_CURRENCIES } from "@/features/controlplane/amounts";
import { FormPopup } from "@/features/controlplane/Modal";
import {
  Alert,
  Button,
  ButtonLink,
  Card,
  DataTable,
  DescriptionList,
  EmptyState,
  Page,
  Stack,
  StatusBadge,
  StatusChips,
  formatNumber,
  productName,
  type Column,
} from "@/features/controlplane/kit";
import cp from "@/features/controlplane/ControlPlane.module.css";
import local from "./ConsoleScreens.module.css";
import { ConfirmAction, ContactOrgAdmin, PageHeader, Panel, UUID_RE, day, intervalLabel } from "./consoleUi";
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
  partner_wallet: "Paid by your partner",
};

const ENDED = new Set(["failed", "expired", "cancelled"]);

/** A subscription as the customer API returns it, with the fields a newer server
 * adds (2026-11-11): how it is collected, where a trial came from, and - for a
 * subscription bought in a mobile app store - which store manages it. */
export type SubscriptionWithSource = OrgSubscription & {
  collection_mode?: string;
  trial_source?: string;
  source?: string;
};

export interface PurchaseSource {
  key: "online" | "invoice" | "partner" | "trial" | "app_store" | "play_store";
  label: string;
}

/** Where a subscription was bought, from what the server says about it - never
 * guessed from the browser. The original channel decides renewals, cancellations and
 * refunds (the brief, Part 2 section 03). */
export function purchaseSource(s: SubscriptionWithSource, accountMode?: string | null): PurchaseSource {
  if (s.source === "app_store") return { key: "app_store", label: "Apple App Store" };
  if (s.source === "play_store") return { key: "play_store", label: "Google Play" };
  const mode = s.collection_mode ?? accountMode ?? "";
  if (mode === "partner_wallet" || mode === "partner_billed") return { key: "partner", label: "Through your partner" };
  if (s.status === "trialing") return { key: "trial", label: s.trial_source === "partner" ? "Free trial (partner)" : "Free trial" };
  if (mode === "invoice") return { key: "invoice", label: "By invoice (WorkEasy360 sales)" };
  return { key: "online", label: "Online (WorkEasy360)" };
}

/** Where a mobile store's subscriber manages it - the store's own page, since the
 * web cannot cancel a subscription the store bills. */
const STORE_MANAGEMENT: Record<"app_store" | "play_store", { label: string; href: string }> = {
  app_store: { label: "Manage in the App Store", href: "https://apps.apple.com/account/subscriptions" },
  play_store: { label: "Manage in Google Play", href: "https://play.google.com/store/account/subscriptions" },
};

/**
 * Subscriptions (2026-11-11, the User Admin brief Part 2 section 03): what the
 * organization subscribes to - product, plan, purchase source, start, renewal or
 * expiry, status - with the actions the purchase channel supports, and the
 * organization's product trials. Orders, payments and invoices are on Orders &
 * Payments; the billing account on Billing Profile.
 *
 * Coming back from a payment page (`?payment=<attempt>&outcome=...`) the page asks
 * the server whether the payment arrived: the browser's return proves nothing - only
 * the provider's signed confirmation marks an invoice paid.
 */
export function BillingScreen() {
  const { slug, can } = useOrgAccess();
  const [paidTick, setPaidTick] = useState(0);
  const onPaid = useCallback(() => setPaidTick((n) => n + 1), []);
  const canSubs = can("commercial.subscription.view");
  const canView = can("platform.billing.view");
  const canManage = can("commercial.subscription.manage");

  return (
    <Page>
      <PageHeader
        title="Subscriptions"
        lead="What your organization subscribes to, where it was bought, and when it renews."
        actions={
          canView ? (
            <ButtonLink href={`/o/${slug}/orders`} icon="receipt">
              Orders &amp; Payments
            </ButtonLink>
          ) : undefined
        }
        primary={
          <ButtonLink variant="primary" icon="store" href={`/o/${slug}/store`}>
            Explore Products
          </ButtonLink>
        }
      />
      {canView && <PaymentReturn slug={slug} onPaid={onPaid} />}
      {!canSubs ? (
        <ContactOrgAdmin what="subscriptions and billing" />
      ) : (
        <>
          <SubscriptionsSection key={paidTick} slug={slug} canManage={canManage} canView={canView} />
          <TrialsSection slug={slug} canBuy={canManage} />
        </>
      )}
    </Page>
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
          ? "The payment didn't go through. You can try again from the invoice on Orders & Payments."
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

// --- subscriptions -------------------------------------------------------------------------

/** Product names by code, for the tables (the directory a member already sees). */
export function useProductNames(slug: string) {
  const [load] = useLoad(
    () => getAppDirectory(slug).then(({ apps }) => new Map(apps.map((a) => [a.product_code, a.display_name] as const))),
    [slug],
  );
  return (code: string) => (load.status === "ready" ? load.data.get(code) : undefined) ?? productName(code);
}

function SubscriptionsSection({ slug, canManage, canView }: { slug: string; canManage: boolean; canView: boolean }) {
  const [load, reload] = useLoad(() => consoleApi.subscriptions(slug), [slug]);
  // The account's collection mode answers "where was it bought" for a server that
  // does not yet say so per subscription.
  const [account] = useLoad(
    () => (canView ? consoleApi.billingAccount(slug).then((r) => r.billing_account) : Promise.resolve(null)),
    [slug, canView],
  );
  const accountMode = account.status === "ready" ? (account.data?.collection_mode ?? null) : null;
  const nameOf = useProductNames(slug);
  const { act, busy, feedback } = useAction(reload);
  // Always at the end of the paid period: the organization keeps what it paid
  // for, and nothing renews (`cancel_subscription`, never `immediate` here).
  const cancel = (s: OrgSubscription) =>
    act(
      () => consoleApi.cancelSubscription(slug, s.id, s.version, newKey()),
      `Subscription cancelled. Your products stay available until ${day(s.current_period_end)}, and it won't renew.`,
    );
  return (
    <LoadGate load={load} what="your subscriptions">
      {({ results }) => {
        const live = (results as SubscriptionWithSource[]).filter((s) => s.status !== "expired");
        if (live.length === 0) {
          return (
            <Card>
              <EmptyState
                icon="layers"
                title="No active subscription."
                hint="Choose a plan in the Store to subscribe, or start a free trial."
                action={{ label: "Explore Products", href: `/o/${slug}/store`, icon: "store" }}
              />
            </Card>
          );
        }
        return (
          <>
            {feedback}
            {live.map((s) => (
              <SubscriptionCard
                key={s.id}
                slug={slug}
                subscription={s}
                source={purchaseSource(s, accountMode)}
                nameOf={nameOf}
                canManage={canManage}
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
          </>
        );
      }}
    </LoadGate>
  );
}

/** Active and set to renew: the only state a customer can cancel from. */
function renewing(s: OrgSubscription): boolean {
  return !s.cancel_at && (s.status === "active" || s.status === "trialing" || s.status === "past_due");
}

/** "Renews 1 Oct 2026", "Ends 1 Oct 2026", "Trial ends ...": what happens next. */
function nextEvent(s: OrgSubscription): string {
  if (s.cancel_at) return `Ends ${day(s.cancel_at)}`;
  if (s.status === "trialing" && s.trial_ends_at) return `Trial ends ${day(s.trial_ends_at)}`;
  if (renewing(s)) {
    return !s.renewal_policy || s.renewal_policy === "auto_renew"
      ? `Renews ${day(s.current_period_end)}`
      : `Expires ${day(s.current_period_end)}`;
  }
  return `Ended ${day(s.current_period_end)}`;
}

type SubscriptionItem = OrgSubscription["items"][number];

function SubscriptionCard({
  slug,
  subscription: s,
  source,
  nameOf,
  canManage,
  cancelControl,
}: {
  slug: string;
  subscription: SubscriptionWithSource;
  source: PurchaseSource;
  nameOf: (code: string) => string;
  canManage: boolean;
  cancelControl?: React.ReactNode;
}) {
  const items = s.items.filter((i) => !i.removed_at);
  const store = source.key === "app_store" || source.key === "play_store" ? STORE_MANAGEMENT[source.key] : null;
  const columns: Column<SubscriptionItem>[] = [
    { key: "product", header: "Product", value: (i) => nameOf(i.product_code), render: (i) => nameOf(i.product_code) },
    {
      key: "plan",
      header: "Plan",
      value: (i) => i.plan_name || i.plan_code,
      render: (i) => <Stack main={i.plan_name || i.plan_code || "—"} sub={i.bundle_code ? `Part of ${i.bundle_code}` : undefined} />,
    },
    { key: "source", header: "Purchase source", render: () => source.label },
    { key: "start", header: "Start date", value: (i) => i.added_at, render: (i) => day(i.added_at) },
    { key: "renewal", header: "Renewal / expiry", render: () => nextEvent(s) },
    { key: "status", header: "Status", render: () => <StatusBadge value={s.cancel_at ? "cancelled" : s.status} /> },
    { key: "quantity", header: "Quantity", align: "right", value: (i) => i.quantity, render: (i) => formatNumber(i.quantity) },
    ...(canManage && !store && source.key !== "partner"
      ? [
          {
            key: "action",
            header: "Actions",
            render: (i: SubscriptionItem) =>
              i.product_code ? (
                <ButtonLink
                  size="sm"
                  href={`/o/${slug}/store/${encodeURIComponent(i.product_code)}`}
                  aria-label={`Change plan: ${nameOf(i.product_code)}`}
                >
                  Change plan
                </ButtonLink>
              ) : null,
          } satisfies Column<SubscriptionItem>,
        ]
      : []),
  ];
  return (
    <Card title="Subscription" description={`${intervalLabel(s.billing_interval)}, billed in ${s.currency}`}>
      <div className={local.section}>
        <DescriptionList
          columns={2}
          items={[
            { label: "Status", value: <StatusBadge value={s.status} /> },
            { label: "Purchase source", value: source.label },
            { label: "Current period", value: `${day(s.current_period_start)} – ${day(s.current_period_end)}` },
            { label: "Trial ends", value: s.trial_ends_at ? day(s.trial_ends_at) : null, when: Boolean(s.trial_ends_at) },
            s.cancel_at
              ? { label: "Ends on", value: day(s.cancel_at) }
              : { label: "Renews on", value: day(s.current_period_end), when: renewing(s) },
          ]}
        />
        <DataTable
          caption="Products in this subscription"
          columns={columns}
          rows={items}
          rowKey={(i) => i.id}
          rowLabel={(i) => nameOf(i.product_code)}
          maxHeight={false}
          stickyFirst={false}
          hideEmptyColumns={false}
          pageSize={0}
          empty={<EmptyState icon="box" title="No products in this subscription." compact />}
        />
        {s.status === "past_due" && canManage && (
          <Alert
            tone="warning"
            title="Payment is overdue."
            actions={
              <ButtonLink size="sm" variant="primary" href={`/o/${slug}/orders`}>
                Renew - pay the open invoice
              </ButtonLink>
            }
          >
            Pay the open invoice to keep this subscription renewing.
          </Alert>
        )}
        {store ? (
          // A subscription a mobile store bills is cancelled, renewed and refunded there.
          <p className={local.contactLine}>
            <span className={local.muted}>Bought in {source.label}: renewals, cancellation and refunds are managed there.</span>
            <a className="w360-btn w360-btn-secondary" href={store.href} target="_blank" rel="noopener noreferrer">
              {store.label}
            </a>
          </p>
        ) : source.key === "partner" ? (
          <p className={local.contactLine}>
            <span className={local.muted}>Your partner manages this subscription&apos;s renewals, changes and cancellation.</span>
            <Link href={`/o/${slug}/help`}>Ask your partner in Help</Link>
          </p>
        ) : (
          cancelControl
        )}
      </div>
    </Card>
  );
}

// --- product trials ---------------------------------------------------------------------------

function TrialsSection({ slug, canBuy }: { slug: string; canBuy: boolean }) {
  const [load] = useLoad(() => consoleApi.trials(slug).then((r) => r.results), [slug]);
  const nameOf = useProductNames(slug);
  // Supplementary: a trial list that cannot load leaves the subscriptions above.
  if (load.status !== "ready" || load.data.length === 0) return null;
  const columns: Column<ProductTrial>[] = [
    { key: "product", header: "Product", value: (t) => nameOf(t.product_code), render: (t) => nameOf(t.product_code) },
    { key: "status", header: "Status", value: (t) => t.status, render: (t) => <StatusBadge value={t.status} /> },
    { key: "started", header: "Started", value: (t) => t.started_at, render: (t) => day(t.started_at) },
    { key: "ends", header: "Ends", value: (t) => t.ends_at, render: (t) => day(t.ended_at ?? t.ends_at) },
    { key: "seats", header: "Seats", align: "right", value: (t) => t.seats, render: (t) => formatNumber(t.seats) },
    ...(canBuy
      ? [
          {
            key: "buy",
            header: "Actions",
            render: (t: ProductTrial) =>
              t.status === "active" || t.status === "expired" ? (
                <ButtonLink
                  size="sm"
                  href={`/o/${slug}/store/${encodeURIComponent(t.product_code)}`}
                  aria-label={`Upgrade: ${nameOf(t.product_code)}`}
                >
                  Upgrade
                </ButtonLink>
              ) : null,
          } satisfies Column<ProductTrial>,
        ]
      : []),
  ];
  return (
    <Card title="Free trials" description="Each product's own trial, newest first.">
      <DataTable
        caption="Product trials"
        columns={columns}
        rows={load.data}
        rowKey={(t) => t.id}
        rowLabel={(t) => nameOf(t.product_code)}
        maxHeight={false}
        stickyFirst={false}
        pageSize={0}
      />
    </Card>
  );
}

// --- billing account ----------------------------------------------------------------------

export function BillingAccountPanel({ slug, canView, canOpen }: { slug: string; canView: boolean; canOpen: boolean }) {
  const [load, reload] = useLoad(
    () => (canView ? consoleApi.billingAccount(slug) : Promise.resolve({ billing_account: null })),
    [slug, canView],
  );
  // Without the billing view permission the account cannot be read back, so
  // an account opened here is remembered for this visit instead.
  const [openedIn, setOpenedIn] = useState<string | null>(null);
  // The popup's button is gone once the account exists, so the outcome is said here.
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <Panel title="Billing account">
      {notice && <p role="status">{notice}</p>}
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
            <>
              <p className={local.textBlock}>No billing account yet.</p>
              <div className={local.actions}>
                <OpenAccountPopup
                  onOpen={async (body) => {
                    const opened = await consoleApi.openBillingAccount(slug, body);
                    if (!canView) setOpenedIn(opened.currency);
                    setNotice(`Billing account opened. Your organization is billed in ${body.currency}.`);
                  }}
                  onDone={reload}
                />
              </div>
            </>
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

const OPEN_ACCOUNT_LEAD =
  "Open a billing account to buy plans. It sets the currency your organization is billed in, which can't be changed later.";

/** Phase 9: the currency and a billing address - collection mode and region
 * are Workeasy360's to set (a sales-led agreement), never the customer's. */
function AccountFields({
  currency,
  email,
  onCurrency,
  onEmail,
}: {
  currency: string;
  email: string;
  onCurrency: (value: string) => void;
  onEmail: (value: string) => void;
}) {
  const currencyId = useId();
  const emailId = useId();
  return (
    <>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={currencyId}>
          Currency
        </label>
        <select
          id={currencyId}
          className="w360-input"
          required
          value={currency}
          onChange={(e) => onCurrency(e.target.value)}
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
          onChange={(e) => onEmail(e.target.value)}
        />
      </div>
    </>
  );
}

/** The billing details on the page: a step of the checkout, which stays on the page. */
export function OpenAccountForm({
  busy,
  onOpen,
}: {
  busy: boolean;
  onOpen: (body: { currency: string; billing_email: string }) => void;
}) {
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
      <p className={local.textBlock}>{OPEN_ACCOUNT_LEAD}</p>
      <AccountFields currency={currency} email={email} onCurrency={setCurrency} onEmail={setEmail} />
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !currency}>
          Open billing account
        </button>
      </div>
    </form>
  );
}

/** The same fields in a popup, on the billing page (2026-11-05, every form in a popup). */
function OpenAccountPopup({
  onOpen,
  onDone,
}: {
  onOpen: (body: { currency: string; billing_email: string }) => Promise<void>;
  onDone: () => void;
}) {
  const [currency, setCurrency] = useState("");
  const [email, setEmail] = useState("");
  return (
    <FormPopup
      label="Open billing account"
      title="Open a billing account"
      intro={OPEN_ACCOUNT_LEAD}
      submitLabel="Open billing account"
      canSubmit={!!currency}
      onOpen={() => {
        setCurrency("");
        setEmail("");
      }}
      onSubmit={() => onOpen({ currency, billing_email: email.trim() })}
      onDone={onDone}
    >
      <AccountFields currency={currency} email={email} onCurrency={setCurrency} onEmail={setEmail} />
    </FormPopup>
  );
}

// --- invoices and paying them ----------------------------------------------------------------

type InvoiceFilter = "" | "open" | "paid" | "void";

/** Invoices on Orders & Payments (2026-11-11): what each member may see of them. */
export function InvoicesSection({ refresh = 0 }: { refresh?: number }) {
  const { slug, can } = useOrgAccess();
  if (!can("platform.billing.view")) return null;
  return <InvoicesPanel slug={slug} canPay={can("platform.billing.manage")} refresh={refresh} />;
}

export function InvoicesPanel({ slug, canPay, refresh }: { slug: string; canPay: boolean; refresh: number }) {
  const [load, reload] = useLoad(() => consoleApi.invoices(slug), [slug, refresh]);
  const pay = usePayInvoice(slug, reload);
  const [filter, setFilter] = useState<InvoiceFilter>("");
  return (
    <Panel title="Invoices" description="Every invoice, newest first. Open one for its lines, or print it.">
      {pay.feedback}
      <LoadGate load={load} what="invoices">
        {({ results }) => {
          const shown = results.filter((inv) => !filter || inv.status === filter);
          return (
            <>
              {results.length > 0 && (
                <StatusChips
                  label="Invoice status"
                  value={filter}
                  onChange={setFilter}
                  chips={(
                    [
                      ["", "All"],
                      ["open", "Open"],
                      ["paid", "Paid"],
                      ["void", "Void"],
                    ] as [InvoiceFilter, string][]
                  ).map(([value, label]) => ({
                    value,
                    label,
                    count: value ? results.filter((inv) => inv.status === value).length : results.length,
                  }))}
                />
              )}
              <Table
                caption="Your invoices, newest first"
                head={["Invoice", "Period", "Amount", "Status", "Due", "Paid", "Actions"]}
                numeric={["Amount"]}
                isEmpty={shown.length === 0}
                empty={filter ? "No invoices with this status." : "No invoices yet."}
              >
                {shown.map((inv) => (
                  <InvoiceRow key={inv.invoice_id} slug={slug} invoice={inv} canPay={canPay} busy={pay.busy} onPay={pay.start} />
                ))}
              </Table>
              {!canPay && results.some((inv) => inv.status === "open") && <ContactOrgAdmin what="paying invoices" />}
            </>
          );
        }}
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
            {payable && (
              <Button
                size="sm"
                variant="primary"
                aria-label={`Pay invoice ${invoice.number}`}
                disabled={busy}
                onClick={() => onPay(invoice)}
              >
                Pay
              </Button>
            )}
            <Button
              size="sm"
              aria-expanded={open}
              aria-controls={open ? detailId : undefined}
              aria-label={`Details of invoice ${invoice.number}`}
              onClick={() => setOpen((v) => !v)}
            >
              Details
            </Button>
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

/** An invoice's lines and totals, laid out to print (Print uses the browser's own
 * "Save as PDF"; the platform has no invoice download of its own yet). */
function InvoiceLines({ slug, invoiceId }: { slug: string; invoiceId: string }) {
  const { access } = useOrgAccess();
  const [load] = useLoad(() => consoleApi.invoice(slug, invoiceId), [slug, invoiceId]);
  return (
    <LoadGate load={load} what="this invoice">
      {(d) => (
        <div className={`${local.section} ${local.invoiceSheet}`}>
          <div className={local.invoiceHead}>
            <div>
              <h3>Invoice {d.number}</h3>
              <p className={local.muted}>
                {access?.organization.display_name ?? ""} · {day(d.period_start)} – {day(d.period_end)}
              </p>
            </div>
            <div className={local.actions}>
              <StatusBadge value={d.status} />
              <Button size="sm" icon="download" className={local.noPrint} onClick={() => window.print()} aria-label={`Print invoice ${d.number}`}>
                Print / save as PDF
              </Button>
            </div>
          </div>
          <Table
            caption={`Lines on invoice ${d.number}`}
            head={["Description", "Product", "Quantity", "Amount"]}
            numeric={["Quantity", "Amount"]}
            isEmpty={d.lines.length === 0}
            empty="This invoice has no lines."
          >
            {d.lines.map((line, i) => (
              <tr key={`${line.product_code}-${line.plan_code}-${i}`}>
                <th scope="row">{line.description}</th>
                <td>
                  {line.product_code || "—"}
                  {(line.plan_code || line.bundle_code) && <div className={cp.muted}>{line.plan_code || line.bundle_code}</div>}
                </td>
                <td className={cp.num}>{line.quantity}</td>
                <td className={cp.num}>{money(line.amount_minor, d.currency)}</td>
              </tr>
            ))}
          </Table>
          <DescriptionList
            columns={2}
            items={[
              { label: "Issued", value: day(d.issued_at) },
              { label: "Due", value: day(d.due_at) },
              { label: "Total", value: money(d.total_minor, d.currency) },
              { label: "Paid", value: money(d.amount_paid_minor, d.currency) },
              { label: "Refunded", value: money(d.amount_refunded_minor, d.currency), when: d.amount_refunded_minor > 0 },
              { label: "Amount due", value: money(d.amount_due_minor, d.currency) },
            ]}
          />
          {(d.attempts ?? []).length > 0 && (
            <Table caption={`Payments for invoice ${d.number}`} head={["Payment", "Status", "Amount", "Provider"]} numeric={["Amount"]}>
              {(d.attempts ?? []).map((a) => (
                <tr key={a.attempt_id}>
                  <th scope="row" className={cp.mono}>
                    {a.attempt_id.slice(0, 8)}
                  </th>
                  <td>
                    <Status value={a.status} />
                    {a.failure_code && <div className={cp.muted}>{a.failure_code.replace(/_/g, " ")}</div>}
                  </td>
                  <td className={cp.num}>{money(a.amount_minor, a.currency)}</td>
                  <td>{a.provider}</td>
                </tr>
              ))}
            </Table>
          )}
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
