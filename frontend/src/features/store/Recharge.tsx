"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/sidebar/icons";
import { ApiError } from "@/lib/api/client";
import type { Order } from "@/lib/api/console";
import { orgAccountApi, type CreditPack, type OrgWallets, type WalletEntry } from "@/lib/api/orgAccount";
import { storeHostApi, type StorePaymentAttempt, type StoreRecharge } from "@/lib/api/storeHost";
import { describeError } from "@/features/controlplane/ui";
import { toMinor } from "@/features/controlplane/amounts";
import { PAYMENT_POLL, checkoutNavigation, safeCheckoutUrl } from "@/features/console/BillingScreen";
import { shelfPrice } from "@/features/storefront/StoreBrowser";
import { useMyAccount, useStoreAccount } from "./account";
import { rateText } from "./ProductDetails";
import { myAccountLinks, storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * Recharge (ADR-065): three separate balances, each recharged its own way -
 *
 * - **Platform Wallet**: money for eligible subscription payments (a free amount).
 * - **AI Credits**: credit packs, paid online only - never from the Platform Wallet.
 * - **Consumption Wallet**: money for messaging, API calls and webhooks (a free amount).
 *
 * Select -> confirm the amount -> pay online -> the provider confirms -> the server
 * credits the right balance -> the transaction is in My Account. A recharge is an
 * order like any other (`wallets/top-up`, `wallets/credit-packs`): the balance moves
 * only when its payment is verified, once, in the wallet it was for. The three are
 * never added together. Rates and packs are exactly what an Admin entered; a meter
 * with no rate is not listed (and not charged).
 */

const MAX_TOP_UP_MINOR = 100_000_000_00;

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

type Choice = { kind: "platform" | "consumption"; amount: number } | { kind: "ai_credits"; pack: CreditPack };

function Recent({ items, currency }: { items: WalletEntry[]; currency: string }) {
  if (items.length === 0) return <p className={styles.boxHint}>No transactions yet.</p>;
  return (
    <ul className={styles.recent}>
      {items.slice(0, 3).map((e) => (
        <li key={e.id}>
          <span>{e.entry_type_label}</span>
          <span className={e.amount < 0 ? styles.minus : styles.plus}>
            {e.amount < 0 ? "−" : "+"}
            {currency ? shelfPrice(Math.abs(e.amount), currency) : `${Math.abs(e.amount).toLocaleString()} credits`}
          </span>
        </li>
      ))}
    </ul>
  );
}

function AmountForm({ label, currency, disabled, onChoose }: { label: string; currency: string; disabled: boolean; onChoose: (minor: number) => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      className={styles.amountForm}
      onSubmit={(e) => {
        e.preventDefault();
        const minor = toMinor(text, currency || "INR");
        if (minor === null || minor <= 0 || minor > MAX_TOP_UP_MINOR) {
          setProblem("Enter an amount above zero, with at most two decimals.");
          return;
        }
        setProblem(null);
        onChoose(minor);
      }}
    >
      <label htmlFor={id} className={styles.boxLabel}>
        Recharge amount
      </label>
      <span className={styles.moneyInput}>
        <span aria-hidden="true">{currency === "INR" ? "₹" : currency}</span>
        <input id={id} inputMode="decimal" value={text} disabled={disabled} onChange={(e) => setText(e.target.value.replace(/[^0-9.]/g, ""))} aria-invalid={problem ? true : undefined} />
      </span>
      {problem && (
        <p role="alert" className="w360-error-text">
          {problem}
        </p>
      )}
      <button type="submit" className={`${shell.primary} ${styles.wide}`} disabled={disabled}>
        <Icon name="wallet" size={18} /> {label}
      </button>
    </form>
  );
}

export function RechargeScreen() {
  const router = useRouter();
  const account = useStoreAccount();
  const myAccount = useMyAccount();
  const slug = account.organization?.slug ?? null;
  const [terms, setTerms] = useState<StoreRecharge | null>(null);
  const [wallets, setWallets] = useState<{ slug: string; data?: OrgWallets; error?: string } | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const orderKey = useRef(newKey());
  const payKey = useRef(newKey());
  const orgSelect = useId();

  useEffect(() => {
    let live = true;
    storeHostApi.recharge(wallets?.data?.currency ?? undefined).then(
      (t) => live && setTerms(t),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [wallets?.data?.currency]);

  useEffect(() => {
    if (!slug) return;
    let live = true;
    orgAccountApi.wallets(slug).then(
      (data) => live && setWallets({ slug, data }),
      (err: unknown) => live && setWallets({ slug, error: err instanceof ApiError && err.status === 403 ? "Wallet balances are for the owner and Billing Managers of this organization." : describeError(err) }),
    );
    return () => {
      live = false;
    };
  }, [slug]);

  const data = wallets?.slug === slug ? wallets.data : undefined;
  const walletError = wallets?.slug === slug ? wallets.error : undefined;
  const canSpend = Boolean(data?.can_spend);
  const currency = data?.currency ?? terms?.currency ?? "INR";
  const signedIn = account.status === "signed_in";
  const packs = data?.credit_packs?.length ? data.credit_packs : (terms?.credit_packs ?? []);
  const aiRate = terms?.rates.find((r) => r.meter === "ai_tokens");
  const usageRates = (terms?.rates ?? []).filter((r) => r.meter !== "ai_tokens");
  const online = terms?.payment;
  const disabled = !signedIn || !slug || !canSpend || busy || !online?.available;

  async function follow(attempt: StorePaymentAttempt): Promise<void> {
    const started = Date.now();
    let current = attempt;
    while (true) {
      if (current.status === "succeeded") {
        router.push(`${storePaths.complete}?payment=${current.attempt_id}&org=${encodeURIComponent(slug ?? "")}`);
        return;
      }
      if (current.status === "awaiting_payment" && current.checkout_url) {
        const url = safeCheckoutUrl(current.checkout_url);
        if (!url) throw new Error("The payment page's address could not be verified, so it was not opened.");
        setStatus("Opening the secure payment page…");
        checkoutNavigation.assign(url);
        return;
      }
      if (["failed", "expired", "cancelled"].includes(current.status)) {
        payKey.current = newKey();
        throw new Error("The payment could not be started. Nothing was charged. Try again.");
      }
      if (Date.now() - started > PAYMENT_POLL.timeoutMs) throw new Error("The payment page is taking longer than usual. Try again in a minute.");
      await new Promise((r) => setTimeout(r, PAYMENT_POLL.intervalMs));
      current = await storeHostApi.payment(slug ?? "", current.attempt_id);
    }
  }

  async function confirm() {
    if (!slug || !choice) return;
    setBusy(true);
    setProblem(null);
    try {
      const order: Order =
        choice.kind === "ai_credits"
          ? await orgAccountApi.buyCreditPack(slug, choice.pack.code, orderKey.current)
          : await orgAccountApi.topUp(slug, { wallet_kind: choice.kind, amount_minor: choice.amount, currency }, orderKey.current);
      if (!order.invoice_id) throw new Error("This recharge has no invoice to pay. Contact support.");
      setStatus(`Order ${order.number} placed for ${shelfPrice(order.amount_due_minor, order.currency)}. Opening the payment page…`);
      // A recharge is paid online only (wallet amount 0): money for a wallet never comes from a wallet.
      const attempt = await storeHostApi.payInvoice(slug, order.invoice_id, payKey.current, 0);
      await follow(attempt);
    } catch (err) {
      setStatus(null);
      setProblem(err instanceof ApiError ? describeError(err) : err instanceof Error ? err.message : "Something went wrong. Please try again.");
      orderKey.current = newKey();
    } finally {
      setBusy(false);
    }
  }

  const choiceText =
    choice?.kind === "ai_credits"
      ? `${choice.pack.name}: ${choice.pack.credits.toLocaleString()} AI credits for ${shelfPrice(choice.pack.price_minor, choice.pack.currency)}`
      : choice
        ? `${shelfPrice(choice.amount, currency)} into your ${choice.kind === "platform" ? "Platform Wallet" : "Consumption Wallet"}`
        : "";

  return (
    <div className={shell.container}>
      <nav aria-label="Breadcrumb" className={shell.crumbs}>
        <ol>
          <li>
            <Link href={storePaths.home}>Store</Link>
          </li>
          <li aria-current="page">Recharge</li>
        </ol>
      </nav>
      <header className={shell.pageHead}>
        <h1 className={shell.pageTitle}>Recharge</h1>
        <p className={shell.pageLead}>
          Three separate balances, each for its own purpose. They are never added together, and money reaches a balance only after its payment is confirmed.
        </p>
      </header>

      <div className={`${shell.card} ${styles.rechargeContext}`}>
        {account.status === "loading" ? (
          <p aria-live="polite">Checking your sign-in…</p>
        ) : !signedIn ? (
          <p>
            <Icon name="info" size={18} /> <Link href={`/login?next=${encodeURIComponent(storePaths.recharge)}`}>Sign in</Link> to see your balances and recharge for your organization.
          </p>
        ) : account.organizations.length === 0 ? (
          <p>Your account has no organization yet. Recharging is for an organization.</p>
        ) : (
          <div className={styles.rechargeOrg}>
            <label htmlFor={orgSelect}>
              <Icon name="building" size={18} /> Organization
            </label>
            {account.organizations.length === 1 ? (
              <strong>{account.organizations[0].display_name}</strong>
            ) : (
              <select id={orgSelect} className="w360-input" value={slug ?? ""} onChange={(e) => account.chooseOrganization(e.target.value)}>
                {account.organizations.map((o) => (
                  <option key={o.slug} value={o.slug}>
                    {o.display_name}
                  </option>
                ))}
              </select>
            )}
            {walletError && <span className={styles.boxHint}>{walletError}</span>}
            {data && !canSpend && <span className={styles.boxHint}>Only the owner and Billing Managers can recharge.</span>}
            {online && !online.available && <span className={styles.boxHint}>Online payment is not available yet. Contact support to recharge.</span>}
            {online?.available && (
              <span className={styles.boxHint}>
                Paid online with {online.label}
                {online.methods.length ? ` (${online.methods.join(", ")})` : ""}.
              </span>
            )}
          </div>
        )}
      </div>

      {choice && (
        <section className={`${shell.card} ${styles.confirmBox}`} aria-labelledby="confirm-recharge">
          <h2 id="confirm-recharge" className={styles.detailHeading}>
            Confirm your recharge
          </h2>
          <p>{choiceText}.</p>
          <p className={styles.boxHint}>You pay on the payment provider&apos;s secure page. The balance is credited once the provider confirms the payment.</p>
          {problem && (
            <p role="alert" className="w360-error-text">
              {problem}
            </p>
          )}
          {status && (
            <p role="status" className={styles.boxHint}>
              {status}
            </p>
          )}
          <div className={styles.boxActions}>
            <button type="button" className={shell.primary} disabled={busy} onClick={() => void confirm()}>
              <Icon name="lock" size={18} /> {busy ? "Working…" : "Confirm & Pay"}
            </button>
            <button type="button" className={shell.secondary} disabled={busy} onClick={() => setChoice(null)}>
              Change
            </button>
          </div>
        </section>
      )}

      <div className={styles.rechargeGrid}>
        <section id="platform" className={`${shell.card} ${styles.rechargeCard}`} aria-labelledby="platform-title">
          <h2 id="platform-title" className={styles.rechargeTitle}>
            <span className={styles.rechargeIcon}>
              <Icon name="wallet" size={24} />
            </span>
            Platform Wallet
          </h2>
          <p className={styles.boxHint}>For eligible subscription payments: pay all or part of a Store order from it at checkout.</p>
          {data && (
            <p className={styles.balance}>
              <span>Available balance</span>
              <strong>{shelfPrice(data.platform.balance_minor, data.platform.currency || currency)}</strong>
            </p>
          )}
          <AmountForm label="Recharge Platform Wallet" currency={currency} disabled={disabled} onChoose={(amount) => setChoice({ kind: "platform", amount })} />
          {data && (
            <>
              <h3 className={styles.boxLabel}>Recent transactions</h3>
              <Recent items={data.platform.recent} currency={data.platform.currency || currency} />
              <button type="button" className={styles.linkButton} onClick={() => void myAccount.open(`/o/${encodeURIComponent(slug ?? "")}/wallets/platform`)}>
                All transactions in My Account
              </button>
            </>
          )}
          <Link href={storePaths.policy("platform-wallet-terms")} className={styles.termsLink}>
            Platform Wallet Terms
          </Link>
        </section>

        <section id="ai-credits" className={`${shell.card} ${styles.rechargeCard}`} aria-labelledby="ai-title">
          <h2 id="ai-title" className={styles.rechargeTitle}>
            <span className={styles.rechargeIcon}>
              <Icon name="sparkles" size={24} />
            </span>
            AI Credits
          </h2>
          <p className={styles.boxHint}>For AI features in WorkEasy360 products. Bought in packs and paid online - not from the Platform Wallet.</p>
          {data && (
            <p className={styles.balance}>
              <span>Available credits</span>
              <strong>{data.ai_credits.balance.toLocaleString()}</strong>
            </p>
          )}
          {aiRate && (
            <p className={styles.boxHint}>
              Conversion: {aiRate.unit_amount.toLocaleString()} AI credit{aiRate.unit_amount === 1 ? "" : "s"} per {aiRate.per_units.toLocaleString()} token
              {aiRate.per_units === 1 ? "" : "s"} used.
              {aiRate.included_units > 0 ? ` ${aiRate.included_units.toLocaleString()} tokens are free each month.` : ""}
            </p>
          )}
          {data?.ai_credits.included_per_month ? (
            <p className={styles.boxHint}>
              Your plans include {data.ai_credits.included_per_month.toLocaleString()} AI tokens a month
              {data.ai_credits.resets_at ? `, starting again on ${new Date(data.ai_credits.resets_at).toLocaleDateString()}` : ""}. Purchased credits have no expiry date in
              your account.
            </p>
          ) : null}
          {packs.length === 0 ? (
            <p className={`${shell.empty} ${styles.packEmpty}`}>No credit packs are on sale right now.</p>
          ) : (
            <ul className={styles.packs}>
              {packs.map((p) => (
                <li key={p.code} className={styles.pack}>
                  <span>
                    <strong>{p.name}</strong>
                    <span>{p.credits.toLocaleString()} credits</span>
                  </span>
                  <span className={styles.packPrice}>{shelfPrice(p.price_minor, p.currency)}</span>
                  <button type="button" className={shell.primary} disabled={disabled || p.currency !== currency} onClick={() => setChoice({ kind: "ai_credits", pack: p })}>
                    Buy Credits
                  </button>
                </li>
              ))}
            </ul>
          )}
          <Link href={storePaths.policy("ai-credits-terms")} className={styles.termsLink}>
            AI Credits Terms
          </Link>
        </section>

        <section id="consumption" className={`${shell.card} ${styles.rechargeCard}`} aria-labelledby="consumption-title">
          <h2 id="consumption-title" className={styles.rechargeTitle}>
            <span className={styles.rechargeIcon}>
              <Icon name="coins" size={24} />
            </span>
            Consumption Wallet
          </h2>
          <p className={styles.boxHint}>For messaging, API calls and webhooks beyond what your plans include.</p>
          {data && (
            <p className={styles.balance}>
              <span>Current balance</span>
              <strong>{shelfPrice(data.consumption.balance_minor, data.consumption.currency || currency)}</strong>
            </p>
          )}
          {usageRates.length > 0 ? (
            <table className={styles.ratesTable}>
              <caption className={styles.boxLabel}>Rates</caption>
              <thead>
                <tr>
                  <th scope="col">Service</th>
                  <th scope="col">Rate</th>
                  <th scope="col">Free / month</th>
                </tr>
              </thead>
              <tbody>
                {usageRates.map((r) => (
                  <tr key={r.meter}>
                    <th scope="row">{r.label}</th>
                    <td>{rateText(r)}</td>
                    <td>{r.included_units > 0 ? r.included_units.toLocaleString() : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className={styles.boxHint}>No usage rates are set in {currency} yet, so nothing is charged for usage.</p>
          )}
          <AmountForm label="Recharge Consumption Wallet" currency={currency} disabled={disabled} onChoose={(amount) => setChoice({ kind: "consumption", amount })} />
          {data && (
            <button type="button" className={styles.linkButton} onClick={() => void myAccount.open(`/o/${encodeURIComponent(slug ?? "")}/wallets/consumption`)}>
              Usage and transactions in My Account
            </button>
          )}
          <Link href={storePaths.policy("consumption-wallet-terms")} className={styles.termsLink}>
            Consumption Wallet Terms
          </Link>
        </section>
      </div>
      {signedIn && slug && (
        <p className={styles.boxHint}>
          Every recharge appears in{" "}
          <button type="button" className={styles.linkButton} onClick={() => void myAccount.open(myAccountLinks(slug)[2].path)}>
            My Account → Wallets &amp; Credits
          </button>
          .
        </p>
      )}
    </div>
  );
}
