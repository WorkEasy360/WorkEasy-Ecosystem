"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { newKey } from "@/lib/api/controlPlane";
import { orgAccountApi, type CreditPack, type OrgWallets } from "@/lib/api/orgAccount";
import { formatMoney, formatNumber } from "@/features/controlplane/kit";
import { FormDialog } from "@/features/controlplane/Modal";
import { usePayInvoice } from "@/features/console/BillingScreen";
import styles from "./Wallets.module.css";

/**
 * Adding money and buying credits (User Admin Panel, 2026-11-11). Both are orders:
 * the server prices them, opens an invoice, and the member pays it on the same
 * secure payment page as any other invoice. The balance changes only once the
 * payment is confirmed, so an abandoned or failed payment changes nothing.
 */

export type SpendKind = "platform" | "consumption" | "ai_credits";

const TITLES: Record<SpendKind, string> = {
  platform: "Add money to the Platform Wallet",
  consumption: "Recharge the Consumption Wallet",
  ai_credits: "Buy AI credits",
};

/** Major units typed by a person ("1,500.50") to integer minor units, or null. */
export function toMinor(text: string): number | null {
  const cleaned = text.replace(/[,\s]/g, "");
  if (!/^\d{1,9}(\.\d{1,2})?$/.test(cleaned)) return null;
  const [whole, fraction = ""] = cleaned.split(".");
  const minor = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return minor > 0 ? minor : null;
}

/** The spend flow: open the dialog, place the order, go to the payment page. */
export function useWalletSpend(slug: string, onSettled: () => void) {
  const pay = usePayInvoice(slug, onSettled);
  const [open, setOpen] = useState<SpendKind | null>(null);
  const [problem, setProblem] = useState<React.ReactNode>(null);

  async function placeAndPay(place: (key: string) => Promise<{ invoice_id: string | null; invoice_number: string | null; number: string }>) {
    setProblem(null);
    // A refusal (another order waiting, no billing account...) is thrown on: the
    // dialog shows the server's words and stays open with what was typed.
    const order = await place(newKey());
    if (!order.invoice_id) {
      setProblem(
        <>
          Order {order.number} was placed. Pay it from <Link href={`/o/${slug}/orders`}>Orders &amp; Payments</Link>.
        </>,
      );
      return;
    }
    pay.start({ invoice_id: order.invoice_id, number: order.invoice_number ?? order.number });
  }

  return {
    open,
    ask: (kind: SpendKind) => setOpen(kind),
    close: () => setOpen(null),
    busy: pay.busy,
    feedback: (
      <>
        {pay.feedback}
        {problem && <p role="status">{problem}</p>}
      </>
    ),
    placeAndPay,
  };
}

export function SpendDialog({
  kind,
  slug,
  wallets,
  onClose,
  placeAndPay,
}: {
  kind: SpendKind;
  slug: string;
  wallets: OrgWallets;
  onClose: () => void;
  placeAndPay: ReturnType<typeof useWalletSpend>["placeAndPay"];
}) {
  if (kind === "ai_credits") {
    return <BuyCreditsDialog slug={slug} packs={wallets.credit_packs} onClose={onClose} placeAndPay={placeAndPay} />;
  }
  return (
    <TopUpDialog
      kind={kind}
      slug={slug}
      currency={wallets.currency ?? ""}
      balance={kind === "platform" ? wallets.platform.balance_minor : wallets.consumption.balance_minor}
      onClose={onClose}
      placeAndPay={placeAndPay}
    />
  );
}

const QUICK = [50_000, 100_000, 500_000];

function TopUpDialog({
  kind,
  slug,
  currency,
  balance,
  onClose,
  placeAndPay,
}: {
  kind: "platform" | "consumption";
  slug: string;
  currency: string;
  balance: number;
  onClose: () => void;
  placeAndPay: ReturnType<typeof useWalletSpend>["placeAndPay"];
}) {
  const amountId = useId();
  const hintId = useId();
  const [text, setText] = useState("");
  const minor = toMinor(text);
  return (
    <FormDialog
      title={TITLES[kind]}
      intro={
        <>
          Current balance {formatMoney(balance, currency)}. You pay on the secure payment page next; the money
          reaches the wallet once the payment is confirmed.
        </>
      }
      size="sm"
      columns={1}
      submitLabel={minor ? `Continue to pay ${formatMoney(minor, currency)}` : "Continue to payment"}
      canSubmit={minor !== null}
      onClose={onClose}
      onSubmit={async () => {
        await placeAndPay((key) =>
          orgAccountApi.topUp(slug, { wallet_kind: kind, amount_minor: minor ?? 0, currency }, key),
        );
        return "Opening the secure payment page…";
      }}
    >
      <div className="w360-field">
        <label htmlFor={amountId}>Amount ({currency})</label>
        <input
          id={amountId}
          className="w360-input"
          inputMode="decimal"
          autoComplete="off"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="1000.00"
          aria-describedby={hintId}
          aria-invalid={text !== "" && minor === null ? true : undefined}
        />
        <p id={hintId} className="w360-hint">
          {text !== "" && minor === null ? "Enter an amount above zero, with at most two decimals." : `In ${currency}, the billing currency.`}
        </p>
      </div>
      <div className={styles.quick} role="group" aria-label="Quick amounts">
        {QUICK.map((value) => (
          <button key={value} type="button" className="w360-btn w360-btn-secondary" onClick={() => setText(String(value / 100))}>
            {formatMoney(value, currency)}
          </button>
        ))}
      </div>
    </FormDialog>
  );
}

function BuyCreditsDialog({
  slug,
  packs,
  onClose,
  placeAndPay,
}: {
  slug: string;
  packs: CreditPack[];
  onClose: () => void;
  placeAndPay: ReturnType<typeof useWalletSpend>["placeAndPay"];
}) {
  const [chosen, setChosen] = useState<string>(packs[0]?.code ?? "");
  const pack = packs.find((p) => p.code === chosen);
  return (
    <FormDialog
      title={TITLES.ai_credits}
      intro="Credits are added once the payment is confirmed. Purchased credits do not expire."
      size="sm"
      columns={1}
      submitLabel={pack ? `Continue to pay ${formatMoney(pack.price_minor, pack.currency)}` : "Continue to payment"}
      canSubmit={Boolean(pack)}
      onClose={onClose}
      onSubmit={async () => {
        await placeAndPay((key) => orgAccountApi.buyCreditPack(slug, chosen, key));
        return "Opening the secure payment page…";
      }}
    >
      {packs.length === 0 ? (
        <p className={styles.muted}>No credit packs are on sale right now. Contact support to buy credits.</p>
      ) : (
        <fieldset className={styles.packs}>
          <legend className="w360-visually-hidden">Credit pack</legend>
          {packs.map((p) => (
            <label key={p.code} className={styles.pack} data-chosen={p.code === chosen || undefined}>
              <input type="radio" name="pack" value={p.code} checked={p.code === chosen} onChange={() => setChosen(p.code)} />
              <span className={styles.packName}>{p.name}</span>
              <span className={styles.packCredits}>{formatNumber(p.credits)} credits</span>
              <span className={styles.packPrice}>{formatMoney(p.price_minor, p.currency)}</span>
            </label>
          ))}
        </fieldset>
      )}
    </FormDialog>
  );
}
