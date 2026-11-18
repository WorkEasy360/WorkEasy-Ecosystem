/**
 * The partner portal's Platform Wallet, wallet-paid plans, subscriptions, billing
 * records, help and sales material (ADR-063). Identifiers and choices go up; every
 * amount comes down from the server, and a wallet charge is confirmed by sending
 * back exactly the amount the server showed (`expected_charge_minor`).
 */
import { apiFetch } from "./client";

const partner = (id: string) => `/api/v1/partner/${encodeURIComponent(id)}`;
const json = (body: unknown): string => JSON.stringify(body);

export interface WalletBalance {
  wallet_id: string | null;
  currency: string;
  balance_minor: number;
  low_balance_threshold: number | null;
  is_low: boolean;
}

export interface WalletEntry {
  id: string;
  currency: string;
  entry_type: "top_up" | "subscription" | "refund" | "adjustment" | "reversal" | "usage" | "credit_purchase" | string;
  entry_type_label: string;
  amount_minor: number;
  balance_after_minor: number;
  reason: string;
  source_type: string;
  source_id: string;
  reverses_id: string | null;
  created_at: string;
  order?: { id: string; number: string } | null;
  customer?: { organization_id: string; name: string | null } | null;
}

export interface FeePayment {
  id: string;
  status: "pending" | "awaiting_payment" | "succeeded" | "failed" | "expired" | string;
  amount_minor: number;
  currency: string;
  checkout_url: string | null;
  test_mode: boolean;
  completed_at: string | null;
  failure_code: string;
  purpose?: string;
  created_at?: string;
}

export interface PlatformWallet {
  currency: string;
  wallets: WalletBalance[];
  top_up: {
    allowed: boolean;
    online: Record<string, boolean>;
    min_minor: number;
    max_minor: number;
    offline_note: string;
  };
  pays_for_customers: boolean;
  top_ups: FeePayment[];
  recent: WalletEntry[];
}

export interface WalletPage {
  items: WalletEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface WalletLine {
  currency: string;
  balance_minor: number;
  charge_minor: number;
  balance_after_minor: number;
  sufficient: boolean;
}

export interface ActivationQuote {
  customer: { organization_id: string };
  plan: { plan_version_id: string; product_code: string; plan_code: string; name: string };
  payer: { usable: boolean; reason: string | null; message?: string | null; adopted?: boolean };
  charge: {
    partner_cost_minor: number;
    due_now_minor: number;
    setup_fee_minor: number;
    currency: string;
    billing_interval: string;
    quantity: number;
    channel: string;
    period: { prorated: boolean; start?: string; end?: string };
  } | null;
  charge_minor: number | null;
  wallet: WalletLine | null;
}

export interface ActivationResult {
  order: { order_id: string; number: string; status: string; description: string; paid_by: string | null };
  wallet: { currency: string; balance_minor: number };
}

export interface RenewalQuote {
  subscription_id: string;
  period_end: string;
  already_paid: boolean;
  blocked: "not_live" | "not_renewing" | "too_early" | null;
  message: string | null;
  charge_minor: number;
  lines: { product_code: string; plan_code: string; quantity: number; partner_cost_minor: number }[];
  wallet: WalletLine;
}

export interface PlanChangeQuote {
  direction: string;
  timing: "immediate" | "period_end" | string;
  from_cost_minor: number;
  to_cost_minor: number;
  charge_minor: number;
  wallet: WalletLine;
}

export type SubscriptionFilter = "all" | "active" | "pending_setup" | "expiring" | "expired";

export interface PartnerSubscriptionRow {
  subscription_id: string;
  organization_id: string;
  customer: string | null;
  status: string;
  currency: string;
  billing_interval: string;
  current_period_start: string;
  current_period_end: string;
  renewal_date: string | null;
  cancel_at: string | null;
  version: number;
  paid_by: "partner_wallet" | "customer";
  renewal_prepaid: boolean;
  state: { expired: boolean; expiring: boolean; pending_setup: boolean; active: boolean; renews: boolean; ends_at: string | null };
  lines: {
    subscription_item_id: string;
    product_code: string;
    product_name: string;
    plan_code: string;
    plan_name: string;
    plan_version_id: string | null;
    quantity: number;
    added_at: string;
    removed_at: string | null;
    order: { order_id: string; number: string; status: string; paid: boolean } | null;
  }[];
}

export interface PartnerSubscriptions {
  items: PartnerSubscriptionRow[];
  counts: Record<SubscriptionFilter, number>;
  filter: SubscriptionFilter;
  truncated: boolean;
}

export interface CustomerProducts {
  organization_id: string;
  members: { total?: number; active?: number; [key: string]: number | undefined } | null;
  products: {
    product_code: string;
    product_name: string;
    website_url: string | null;
    application_url: string | null;
    app_links: Record<string, string>;
    plans: {
      subscription_id: string;
      subscription_item_id: string;
      plan_name: string;
      quantity: number;
      valid_from: string;
      valid_until: string;
      subscription_status: string;
    }[];
    setup: {
      product_code: string;
      status: "pending" | "submitted" | "correction_required" | "approved" | string;
      submitted_at: string | null;
      decided_at: string | null;
      decision_reason: string;
      requirements: { label: string; required: boolean }[];
    } | null;
  }[];
}

export interface BillingRecords {
  partner_payments: {
    id: string;
    purpose: string;
    label: string;
    amount_minor: number;
    currency: string;
    status: string;
    created_at: string;
    completed_at: string | null;
    refund_status: string;
  }[];
  wallet_charges: WalletEntry[];
  orders_visible: boolean;
}

export interface HelpArticle {
  slug: string;
  kind: "faq" | "setup_guide" | "sales_guide" | string;
  title: string;
  body: string;
  product_code: string;
}

export interface SalesResources {
  products: {
    product_code: string;
    name: string;
    summary: string;
    description: string;
    documentation_url: string | null;
    images: { id: string; kind: string; caption: string; url: string; width: number | null; height: number | null }[];
    video: { kind?: string; url?: string; embed_url?: string; file_url?: string; [key: string]: unknown } | null;
    guides: HelpArticle[];
  }[];
  guides: HelpArticle[];
}

const withKey = (key: string) => ({ "Idempotency-Key": key });

export const walletApi = {
  wallet: (id: string) => apiFetch<PlatformWallet>(`${partner(id)}/platform-wallet`),
  transactions: (id: string, query: { type?: string; currency?: string; limit?: number; offset?: number } = {}) =>
    apiFetch<WalletPage>(`${partner(id)}/platform-wallet/transactions`, {
      query: {
        type: query.type,
        currency: query.currency,
        limit: query.limit !== undefined ? String(query.limit) : undefined,
        offset: query.offset !== undefined ? String(query.offset) : undefined,
      },
    }),
  /** Opens the provider's hosted page; the wallet is credited by its verified callback. */
  /** `amount` is what the person typed ("5000.00"): the server turns it into minor units. */
  startTopUp: (id: string, amount: string, currency?: string) =>
    apiFetch<FeePayment>(`${partner(id)}/platform-wallet/top-up`, {
      method: "POST",
      body: json({ amount, currency }),
    }),
  activationQuote: (id: string, orgId: string, body: { plan_version_id: string; billing_interval: string; quantity: number }) =>
    apiFetch<ActivationQuote>(`${partner(id)}/customers/${encodeURIComponent(orgId)}/activation/quote`, {
      method: "POST",
      body: json(body),
    }),
  activate: (
    id: string,
    orgId: string,
    body: { plan_version_id: string; billing_interval: string; quantity: number; expected_charge_minor: number },
    idempotencyKey: string,
  ) =>
    apiFetch<ActivationResult>(`${partner(id)}/customers/${encodeURIComponent(orgId)}/activation`, {
      method: "POST",
      body: json(body),
      headers: withKey(idempotencyKey),
    }),
  renewalQuote: (id: string, orgId: string, subscriptionId: string) =>
    apiFetch<RenewalQuote>(
      `${partner(id)}/customers/${encodeURIComponent(orgId)}/subscriptions/${encodeURIComponent(subscriptionId)}/renewal`,
    ),
  renew: (id: string, orgId: string, subscriptionId: string, expectedChargeMinor: number) =>
    apiFetch<RenewalQuote>(
      `${partner(id)}/customers/${encodeURIComponent(orgId)}/subscriptions/${encodeURIComponent(subscriptionId)}/renewal`,
      { method: "POST", body: json({ expected_charge_minor: expectedChargeMinor }) },
    ),
  planChangeQuote: (id: string, orgId: string, body: { subscription_item_id: string; to_plan_version_id: string }) =>
    apiFetch<PlanChangeQuote>(`${partner(id)}/customers/${encodeURIComponent(orgId)}/plan-change/quote`, {
      method: "POST",
      body: json(body),
    }),
  changePlan: (
    id: string,
    orgId: string,
    body: { subscription_item_id: string; to_plan_version_id: string; expected_charge_minor: number; expected_version?: number },
    idempotencyKey: string,
  ) =>
    apiFetch<PlanChangeQuote & { subscription_item_id: string }>(
      `${partner(id)}/customers/${encodeURIComponent(orgId)}/plan-change`,
      { method: "POST", body: json(body), headers: withKey(idempotencyKey) },
    ),
  subscriptions: (id: string, status: SubscriptionFilter, q = "") =>
    apiFetch<PartnerSubscriptions>(`${partner(id)}/subscriptions`, { query: { status, q: q || undefined } }),
  customerProducts: (id: string, orgId: string) =>
    apiFetch<CustomerProducts>(`${partner(id)}/customers/${encodeURIComponent(orgId)}/products-setup`),
  billingRecords: (id: string) => apiFetch<BillingRecords>(`${partner(id)}/billing-records`),
  helpArticles: (id: string, kind?: string) =>
    apiFetch<{ items: HelpArticle[] }>(`${partner(id)}/help-articles`, { query: { kind } }),
  salesResources: (id: string) => apiFetch<SalesResources>(`${partner(id)}/sales-resources`),
};
