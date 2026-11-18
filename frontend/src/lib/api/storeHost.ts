/**
 * The Digital Store's own API (ADR-065, store.workeasy360.com): the Recharge
 * page's public terms, the checkout's "Pay via", the return address a product's
 * purchase may come back to, the footer policies and FAQs, paying an invoice in
 * part or in full from the Platform Wallet, and the "My Account" pass to User
 * Admin.
 *
 * Same rules as every client here: the backend is the authority. Every amount is
 * the server's (integer minor units with a currency); the wallet amount the payer
 * chooses is checked again on the server against the invoice and the wallet; a
 * return address is only ever followed after the server approved it.
 */

import { apiFetch } from "./client";
import type { PaymentAttempt } from "./console";

const json = (body: unknown): string => JSON.stringify(body);
const org = (slug: string) => `/api/v1/orgs/${encodeURIComponent(slug)}`;

/** A meter's live rate, as an Admin entered it. `unit_amount` is minor units of
 * `currency` per `per_units`, or credits for AI tokens (`currency` empty). */
export interface StoreRate {
  meter: "ai_tokens" | "whatsapp" | "sms" | "email" | "api_trigger" | "webhook";
  label: string;
  wallet: "ai_credits" | "consumption";
  currency: string;
  unit_amount: number;
  per_units: number;
  included_units: number;
  effective_from: string;
}

export interface StoreCreditPack {
  code: string;
  name: string;
  credits: number;
  price_minor: number;
  currency: string;
}

/** Whether online payment is open, and through which kind of provider (its name only). */
export interface PaymentOption {
  available: boolean;
  provider: "razorpay" | "stripe" | "fake" | string | null;
  label: string | null;
  methods: string[];
  test_mode?: boolean;
}

export interface StoreRecharge {
  currency: string;
  rates: StoreRate[];
  credit_packs: StoreCreditPack[];
  payment: PaymentOption;
}

/** A payment attempt with what the Platform Wallet pays alongside it (ADR-065). */
export interface StorePaymentAttempt extends PaymentAttempt {
  wallet_amount_minor?: number;
  wallet_status?: "held" | "captured" | "released" | null;
}

/** One published help article (plain text, never HTML). */
export interface HelpArticle {
  slug: string;
  kind: "faq" | "setup_guide" | "sales_guide" | "policy";
  title: string;
  body: string;
  product_code: string;
}

/** The Partners page: the public programmes, the KYC documents an Admin asks for. */
export interface PartnerProgramme {
  applications_open: boolean;
  programmes: { key: "affiliate" | "reseller"; label: string; description: string }[];
  kyc_requirements: { label: string; help_text: string; is_required: boolean }[];
  kyc_required: boolean;
  stages: { key: string; label: string }[];
}

export const storeHostApi = {
  partnerProgramme: () => apiFetch<PartnerProgramme>("/api/v1/public/store/partner-programme"),
  recharge: (currency?: string) => apiFetch<StoreRecharge>("/api/v1/public/store/recharge", { query: { currency } }),
  paymentOption: (currency: string) => apiFetch<PaymentOption>("/api/v1/public/store/payment-option", { query: { currency } }),
  /** May the Store send the buyer back to `url` after a purchase `product` started? */
  returnCheck: (product: string, url: string) =>
    apiFetch<{ allowed: boolean }>("/api/v1/public/store/return-check", { query: { product, url } }),
  policies: () => apiFetch<{ results: HelpArticle[] }>("/api/v1/public/help", { query: { kind: "policy" } }),
  faqs: () => apiFetch<{ results: HelpArticle[] }>("/api/v1/public/help", { query: { audience: "customer", kind: "faq" } }),
  /** Pay an invoice: `walletMinor` 0 is the provider alone, the whole amount due
   * is the Platform Wallet alone, anything between is split (the wallet's part is
   * held until the provider confirms the rest). `key` is minted once per payment. */
  payInvoice: (slug: string, invoiceId: string, key: string, walletMinor: number) =>
    apiFetch<StorePaymentAttempt>(`${org(slug)}/billing/invoices/${invoiceId}/pay`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: json({ wallet_amount_minor: walletMinor }),
    }),
  payment: (slug: string, attemptId: string) => apiFetch<StorePaymentAttempt>(`${org(slug)}/billing/payments/${attemptId}`),
  /** "My Account": a single-use pass the Store page posts to User Admin, in a form body. */
  accountPass: (next: string) =>
    apiFetch<{ action: string; code: string }>("/api/v1/auth/account-pass", { method: "POST", body: json({ next }) }),
};
