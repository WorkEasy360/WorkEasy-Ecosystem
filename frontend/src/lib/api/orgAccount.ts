/**
 * The User Admin Panel's own API (2026-11-11): the organization's three wallets,
 * the bell, the member's alert choices and the billing profile.
 *
 * Same rules as every client here: the backend is the authority - every call is
 * authorized again from the member's verified organization - and money is
 * server-derived integer minor units with a currency. AI credits are whole credits.
 * **The three balances are never added together**, here or on any screen.
 */

import { apiFetch } from "./client";
import type { Order } from "./console";

const json = (body: unknown): string => JSON.stringify(body);
const org = (slug: string) => `/api/v1/orgs/${encodeURIComponent(slug)}`;

// --- wallets ------------------------------------------------------------------------

export type WalletKind = "platform" | "ai_credits" | "consumption";

/** One movement. `amount` is signed: minor units of the wallet's currency, or
 * whole credits for AI Credits. */
export interface WalletEntry {
  id: string;
  kind?: WalletKind;
  currency?: string;
  entry_type: string;
  entry_type_label: string;
  amount: number;
  balance_after: number;
  reason: string;
  source_type: string;
  source_id: string;
  created_at: string;
}

export interface MeterMonth {
  meter: string;
  label: string;
  quantity: number;
  /** Free each month (the rate's plus the plans'); null when the meter has no rate and is not charged. */
  included_units: number | null;
  charged_units: number;
  amount_minor: number;
  failed?: number;
}

export interface MoneyWallet {
  exists: boolean;
  currency: string;
  balance_minor: number;
  low_balance_threshold: number | null;
  is_low: boolean;
  recent: WalletEntry[];
  other_currencies: { currency: string; balance_minor: number }[];
}

export interface CreditPack {
  code: string;
  name: string;
  credits: number;
  price_minor: number;
  currency: string;
}

export interface OrgWallets {
  /** The billing account's currency; null before one is opened. */
  currency: string | null;
  /** Holds `platform.billing.manage`: may add money and buy credits. */
  can_spend: boolean;
  platform: MoneyWallet;
  ai_credits: {
    exists: boolean;
    balance: number;
    low_balance_threshold: number | null;
    is_low: boolean;
    /** AI tokens free each month; null when none are included. */
    included_per_month: number | null;
    /** AI tokens used this month. */
    used_this_month: number;
    credits_spent_this_month?: number;
    purchased_total: number;
    /** When the free monthly allowance starts again (purchased credits do not expire). */
    resets_at: string | null;
    recent: WalletEntry[];
  };
  consumption: Omit<MoneyWallet, "other_currencies"> & {
    other_currencies?: MoneyWallet["other_currencies"];
    this_month: MeterMonth[];
  };
  credit_packs: CreditPack[];
  generated_at?: string;
}

export interface TransactionQuery {
  kind?: WalletKind;
  type?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export interface TransactionPage {
  items: (WalletEntry & { kind: WalletKind; currency: string })[];
  total: number;
  limit: number;
  offset: number;
  types: { value: string; label: string }[];
}

export interface UsageQuery {
  meter?: string;
  product?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export interface UsageEventRow {
  id: string;
  product_code: string;
  meter: string;
  meter_label: string;
  quantity: number;
  outcome: "charged" | "included" | "not_rated" | "failed";
  outcome_label: string;
  charged_units: number;
  /** Minor units of `currency`, or credits for AI tokens (`currency` empty). */
  amount: number;
  currency: string;
  occurred_at: string;
}

export interface UsagePage {
  items: UsageEventRow[];
  total: number;
  limit: number;
  offset: number;
  products: string[];
  meters: { value: string; label: string }[];
}

// --- the bell and its choices ------------------------------------------------------

export type NotificationCategory = "setup" | "renewal" | "payment" | "low_balance" | "ticket";

export interface OrgNotification {
  id: string;
  category: NotificationCategory;
  tone: "info" | "warning" | "danger";
  title: string;
  detail: string;
  /** A page below `/o/<slug>/`, e.g. "apps?filter=setup". */
  href: string;
  occurred_at: string | null;
}

export interface NotificationPreferences {
  categories: { key: NotificationCategory; label: string; description: string; in_app: boolean }[];
}

// --- the billing profile ------------------------------------------------------------

export interface BillingProfile {
  id: string;
  currency: string;
  collection_mode: string;
  billing_email: string;
  billing_region: string;
  tax_reference: string;
  legal_name: string;
  address_line1: string;
  address_line2: string;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  updated_at: string | null;
}

/** What the customer may change: never the currency, collection mode or region. */
export type BillingProfileChanges = Partial<
  Pick<
    BillingProfile,
    | "legal_name"
    | "address_line1"
    | "address_line2"
    | "city"
    | "state"
    | "postal_code"
    | "country"
    | "tax_reference"
    | "billing_email"
  >
>;

const clean = <T extends object>(q: T): Record<string, string | number | undefined> =>
  Object.fromEntries(Object.entries(q).map(([k, v]) => [k, v === "" || v === null ? undefined : v]));

export const orgAccountApi = {
  wallets: (slug: string) => apiFetch<OrgWallets>(`${org(slug)}/wallets`),
  transactions: (slug: string, query: TransactionQuery = {}) =>
    apiFetch<TransactionPage>(`${org(slug)}/wallets/transactions`, { query: clean(query) }),
  usage: (slug: string, query: UsageQuery = {}) =>
    apiFetch<UsagePage>(`${org(slug)}/wallets/usage`, { query: clean(query) }),
  /** An order for money into the Platform or Consumption wallet (billing managers
   * and owners). `key` is minted once per confirm: a retry is the same order. The
   * money arrives only once its invoice is paid. */
  topUp: (
    slug: string,
    body: { wallet_kind: "platform" | "consumption"; amount_minor: number; currency?: string | null },
    key: string,
  ) =>
    apiFetch<Order>(`${org(slug)}/wallets/top-up`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: json(body),
    }),
  buyCreditPack: (slug: string, packCode: string, key: string) =>
    apiFetch<Order>(`${org(slug)}/wallets/credit-packs`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: json({ pack_code: packCode }),
    }),
  notifications: (slug: string) =>
    apiFetch<{ items: OrgNotification[]; count: number }>(`${org(slug)}/notifications`),
  notificationPreferences: (slug: string) =>
    apiFetch<NotificationPreferences>(`${org(slug)}/notification-preferences`),
  updateNotificationPreferences: (
    slug: string,
    body: { categories: { key: NotificationCategory; in_app: boolean }[] },
  ) =>
    apiFetch<NotificationPreferences>(`${org(slug)}/notification-preferences`, {
      method: "PUT",
      body: json(body),
    }),
  billingProfile: (slug: string) =>
    apiFetch<{ billing_account: BillingProfile | null }>(`${org(slug)}/billing-account`),
  updateBillingProfile: (slug: string, body: BillingProfileChanges) =>
    apiFetch<{ billing_account: BillingProfile }>(`${org(slug)}/billing-account`, {
      method: "PATCH",
      body: json(body),
    }),
};
