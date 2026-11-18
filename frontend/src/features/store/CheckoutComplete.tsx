"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Icon } from "@/components/sidebar/icons";
import { consoleApi, type InvoiceDetail, type Order, type ProductTrial } from "@/lib/api/console";
import { orgAccountApi, type OrgNotification } from "@/lib/api/orgAccount";
import { storeHostApi, type StorePaymentAttempt } from "@/lib/api/storeHost";
import { describeError } from "@/features/controlplane/ui";
import { PAYMENT_POLL } from "@/features/console/BillingScreen";
import { shelfPrice } from "@/features/storefront/StoreBrowser";
import { useMyAccount, useStoreAccount } from "./account";
import { CheckoutBand, TrustStrip } from "./Checkout";
import { StoreSignInForm } from "./StoreAuth";
import { takeReturnTo } from "./returnTo";
import { myAccountLinks, storePaths } from "./links";
import styles from "./Checkout.module.css";
import shell from "./Store.module.css";

/**
 * Order confirmation (ADR-065): what the **server** says happened - never the
 * provider's return itself, which proves nothing. The page follows the payment
 * until the provider's verified confirmation settles it (or says it is still on
 * its way), then shows the order and payment references, the invoice, the
 * product, whether the subscription is active and whether set-up is waiting -
 * with Open Product, Open User Admin and, for a purchase a product started, the
 * way back to it. A Recharge order says which balance it credits.
 */

type Outcome = "success" | "pending" | "failed";

const ATTEMPT_ENDED = new Set(["failed", "expired", "cancelled"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;

const WALLET_NAME: Record<string, string> = { platform: "Platform Wallet", ai_credits: "AI Credits", consumption: "Consumption Wallet" };

type Found = {
  outcome: Outcome;
  attempt?: StorePaymentAttempt;
  invoice?: InvoiceDetail | null;
  order?: Order | null;
  trial?: ProductTrial | null;
  setup: OrgNotification[];
  note?: string;
};

async function settle(slug: string, attemptId: string): Promise<StorePaymentAttempt> {
  const started = Date.now();
  let attempt = await storeHostApi.payment(slug, attemptId);
  // The provider's confirmation (its webhook) can arrive a little after the payer.
  while (!["succeeded", ...ATTEMPT_ENDED].includes(attempt.status) && Date.now() - started < PAYMENT_POLL.timeoutMs) {
    await new Promise((r) => setTimeout(r, PAYMENT_POLL.intervalMs));
    attempt = await storeHostApi.payment(slug, attemptId);
  }
  return attempt;
}

async function orderAndInvoice(slug: string, invoiceId: string | null, orderId: string | null) {
  const invoice = invoiceId ? await consoleApi.invoice(slug, invoiceId).catch(() => null) : null;
  const id = orderId ?? invoice?.order_id ?? null;
  const order = id ? await consoleApi.order(slug, id).catch(() => null) : null;
  return { invoice, order };
}

async function find(slug: string, params: URLSearchParams): Promise<Found> {
  const payment = params.get("payment");
  const orderId = params.get("order");
  const trialId = params.get("trial");
  const setup = await orgAccountApi.notifications(slug).then(
    (r) => r.items.filter((n) => n.category === "setup"),
    () => [] as OrgNotification[],
  );
  if (trialId && UUID.test(trialId)) {
    const trials = await consoleApi.trials(slug);
    const trial = trials.results.find((t) => t.id === trialId) ?? null;
    return { outcome: trial ? "success" : "pending", trial, setup };
  }
  if (payment && UUID.test(payment)) {
    const attempt = await settle(slug, payment);
    const { invoice, order } = await orderAndInvoice(slug, attempt.invoice_id, null);
    const outcome: Outcome = attempt.status === "succeeded" ? "success" : ATTEMPT_ENDED.has(attempt.status) ? "failed" : "pending";
    return { outcome, attempt, invoice, order, setup };
  }
  if (orderId && UUID.test(orderId)) {
    const { invoice, order } = await orderAndInvoice(slug, null, orderId);
    const done = order && ["paid", "fulfilled"].includes(order.status);
    return { outcome: done ? "success" : order?.status === "pending_payment" ? "pending" : "failed", invoice, order, setup };
  }
  return { outcome: "failed", setup, note: "This confirmation link is incomplete." };
}

function subscriptionState(order: Order | null | undefined, trial: ProductTrial | null | undefined): { label: string; tone: "ok" | "wait" | "bad" } {
  if (trial) return trial.status === "active" ? { label: "Trial active", tone: "ok" } : { label: trial.status.replace(/_/g, " "), tone: "wait" };
  if (!order) return { label: "Checking…", tone: "wait" };
  if (order.status === "fulfilled") return { label: order.kind === "wallet" ? "Credited" : "Active", tone: "ok" };
  if (order.status === "paid") return { label: order.kind === "wallet" ? "Crediting" : "Activating", tone: "wait" };
  if (order.status === "pending_payment") return { label: "Waiting for payment", tone: "wait" };
  if (order.status === "failed") return { label: "Not activated - refunded", tone: "bad" };
  return { label: order.status === "expired" ? "Expired" : "Cancelled", tone: "bad" };
}

export function CheckoutCompleteScreen() {
  const params = useSearchParams();
  const account = useStoreAccount();
  const myAccount = useMyAccount();
  const rawSlug = params.get("org") ?? "";
  const slug = SLUG.test(rawSlug) ? rawSlug : null;
  const [found, setFound] = useState<Found | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [back] = useState(() => (typeof window === "undefined" ? null : takeReturnTo()));
  const key = params.toString();

  useEffect(() => {
    if (account.status !== "signed_in" || !slug) return;
    let live = true;
    find(slug, new URLSearchParams(key)).then(
      (f) => live && setFound(f),
      (err: unknown) => live && setError(describeError(err)),
    );
    return () => {
      live = false;
    };
  }, [account.status, slug, key]);

  const step = found?.outcome === "success" ? "confirmation" : "payment";
  return (
    <div className={shell.container}>
      <CheckoutBand step={step} />
      {account.status === "loading" ? (
        <p aria-live="polite">Checking your sign-in…</p>
      ) : account.status === "signed_out" ? (
        <section className={`${shell.card} ${styles.card}`} aria-labelledby="complete-sign-in">
          <h2 id="complete-sign-in" className={styles.cardTitle}>
            Sign in to see your order
          </h2>
          <p className={styles.muted}>Your payment is safe either way: it is confirmed with the provider, not by this page.</p>
          <StoreSignInForm compact onSignedIn={account.refresh} />
        </section>
      ) : !slug ? (
        <p role="alert" className={`${shell.card} ${shell.empty}`}>
          This confirmation link is incomplete. Your orders are in My Account.
        </p>
      ) : error ? (
        <p role="alert" className={`${shell.card} ${shell.empty}`}>
          {error}
        </p>
      ) : !found ? (
        <section className={`${shell.card} ${styles.card} ${styles.confirm}`} aria-busy="true">
          <p className={styles.confirmTitle} aria-live="polite">
            Confirming your payment with the provider…
          </p>
          <p className={styles.muted}>This usually takes a few seconds. Please keep this page open.</p>
        </section>
      ) : (
        <Confirmation found={found} slug={slug} back={back} open={(path) => void myAccount.open(path)} />
      )}
      <TrustStrip />
    </div>
  );
}

function Confirmation({ found, slug, back, open }: { found: Found; slug: string; back: { url: string; label: string } | null; open: (path: string) => void }) {
  const { outcome, attempt, invoice, order, trial, setup } = found;
  const state = subscriptionState(order, trial);
  const links = myAccountLinks(slug);
  const wallet = order?.kind === "wallet" ? (order.lines?.[0] as { wallet_kind?: string } | undefined)?.wallet_kind : undefined;
  const currency = order?.currency ?? attempt?.currency ?? "INR";
  const headline =
    outcome === "success"
      ? trial
        ? "Your free trial has started"
        : order?.kind === "wallet"
          ? "Recharge successful"
          : "Payment successful - thank you!"
      : outcome === "pending"
        ? "Your payment is being confirmed"
        : "The payment did not go through";
  return (
    <section className={`${shell.card} ${styles.card} ${styles.confirm}`} aria-labelledby="confirm-title">
      <p className={`${styles.confirmIcon} ${outcome === "success" ? styles.ok : outcome === "pending" ? styles.wait : styles.bad}`} aria-hidden="true">
        <Icon name={outcome === "success" ? "checkCircle" : outcome === "pending" ? "clock" : "xCircle"} size={44} />
      </p>
      <h2 id="confirm-title" className={styles.confirmTitle}>
        {headline}
      </h2>
      <p className={styles.muted} role="status">
        {outcome === "success"
          ? trial
            ? "Nothing was charged. Open the product from My Products."
            : order?.kind === "wallet"
              ? `The payment is confirmed${wallet ? ` and your ${WALLET_NAME[wallet] ?? "wallet"} is credited once the order completes` : ""}.`
              : "The payment is confirmed by the provider. Your subscription activates automatically."
          : outcome === "pending"
            ? "We have not received the provider's confirmation yet. Nothing activates until it arrives; this page and My Account update when it does."
            : found.note ?? "Nothing was charged, and any Platform Wallet amount that was reserved has been released. You can try again."}
      </p>
      <dl className={styles.confirmFacts}>
        {order && (
          <div>
            <dt>Order reference</dt>
            <dd>{order.number}</dd>
          </div>
        )}
        {attempt && (
          <div>
            <dt>Payment reference</dt>
            <dd className={styles.mono}>{attempt.attempt_id.slice(0, 8).toUpperCase()}</dd>
          </div>
        )}
        {invoice && (
          <div>
            <dt>Invoice</dt>
            <dd>
              {invoice.number}{" "}
              <button type="button" className={styles.linkButton} onClick={() => open(`/o/${encodeURIComponent(slug)}/orders`)}>
                View in My Account
              </button>
            </dd>
          </div>
        )}
        {(order || trial) && (
          <div>
            <dt>{order?.kind === "wallet" ? "Recharge" : "Purchased product"}</dt>
            <dd>{order?.description ?? trial?.product_code}</dd>
          </div>
        )}
        {attempt && (attempt.wallet_amount_minor ?? 0) > 0 && (
          <div>
            <dt>Paid from Platform Wallet</dt>
            <dd>
              {shelfPrice(attempt.wallet_amount_minor ?? 0, currency)}
              {attempt.wallet_status === "held" ? " (reserved)" : attempt.wallet_status === "released" ? " (released)" : ""}
            </dd>
          </div>
        )}
        {attempt && attempt.provider !== "wallet" && (
          <div>
            <dt>Paid online</dt>
            <dd>{shelfPrice(attempt.amount_minor, currency)}</dd>
          </div>
        )}
        {(order || trial) && (
          <div>
            <dt>{order?.kind === "wallet" ? "Status" : "Subscription status"}</dt>
            <dd>
              <span className={`${styles.stateChip} ${styles[state.tone]}`}>{state.label}</span>
            </dd>
          </div>
        )}
        {order?.kind !== "wallet" && (
          <div>
            <dt>Setup status</dt>
            <dd>{setup.length > 0 ? `${setup[0].title}${setup.length > 1 ? ` (+${setup.length - 1} more)` : ""}` : "Nothing waiting"}</dd>
          </div>
        )}
      </dl>
      <div className={styles.confirmActions}>
        {order?.kind === "wallet" ? (
          <button type="button" className={shell.primary} onClick={() => open(links[2].path)}>
            <Icon name="wallet" size={18} /> View Transaction
          </button>
        ) : (
          <button type="button" className={shell.primary} onClick={() => open(setup.length > 0 ? `/o/${encodeURIComponent(slug)}/apps?filter=setup` : links[0].path)}>
            <Icon name="apps" size={18} /> {setup.length > 0 ? "Finish Setup in My Products" : "Open Product"}
          </button>
        )}
        <button type="button" className={shell.secondary} onClick={() => open(`/o/${encodeURIComponent(slug)}/dashboard`)}>
          <Icon name="dashboard" size={18} /> Open User Admin
        </button>
        {back && (
          <a href={back.url} className={shell.secondary}>
            <Icon name="chevronLeft" size={18} /> Back to {back.label}
          </a>
        )}
        {outcome !== "success" && (
          <Link href={order?.kind === "wallet" ? storePaths.recharge : storePaths.products} className={shell.secondary}>
            Try again
          </Link>
        )}
      </div>
    </section>
  );
}
