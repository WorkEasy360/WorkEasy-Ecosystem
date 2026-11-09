/**
 * The Admin Panel programme's API (2026-11-09). Every call is authorized again by the
 * server against the staff member's live grants. Money is integer minor units per
 * currency, never converted, never added across currencies.
 */

import { apiFetch } from "./client";

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});
const put = (body: unknown): RequestInit => ({ method: "PUT", body: JSON.stringify(body) });

export interface KeysetPage<T> {
  results: T[];
  next_cursor: string | null;
  has_more: boolean;
}

// --- dashboard --------------------------------------------------------------------

export interface RunningTotal {
  total: number;
  at_start: number;
  added: number;
  change_percent: number | null;
}

export interface Amount {
  amount_minor: number;
  previous_minor: number;
  change_percent: number | null;
}

export interface WalletTotal {
  kind: string;
  label: string;
  currency: string;
  balance: number;
  wallets: number;
  other_currencies: { currency: string; balance: number; wallets: number }[];
}

export interface PendingAction {
  key: string;
  label: string;
  hint: string;
  count: number;
  action: string;
  href: string;
}

export interface ActivityEvent {
  kind: string;
  at: string;
  title: string;
  detail?: string;
  amount_minor?: number;
  currency?: string;
  href: string;
}

export interface TopProduct {
  code: string;
  name: string;
  active_subscriptions: number;
  revenue_minor: number;
  currency: string;
}

export interface DashboardOverviewV2 {
  period: { from: string; to: string; days: number; compared_from: string; compared_to: string };
  definitions: { sales: string; collections: string };
  currency?: string;
  currencies?: string[];
  cards: {
    customers: RunningTotal;
    active_subscriptions: RunningTotal;
    partners?: RunningTotal;
    sales?: Amount;
    collections?: Amount;
  };
  wallets?: { platform: WalletTotal; ai_credits: WalletTotal; consumption: WalletTotal };
  pending: PendingAction[];
  activity: ActivityEvent[];
  top_products?: TopProduct[];
}

export interface GrowthTrend {
  months: string[];
  customers: number[];
  subscriptions: number[];
  partners?: number[];
}

export interface MoneyTrend {
  months: string[];
  currency: string;
  currencies: string[];
  sales: number[];
  collections: number[];
}

export interface SearchResult {
  kind: string;
  id: string;
  label: string;
  hint: string;
  href: string;
}

export const adminPanelApi = {
  overview: (query: { days?: number; from?: string; to?: string; currency?: string }) =>
    apiFetch<DashboardOverviewV2>("/api/v1/admin/dashboard/overview", { query }),
  growth: (months: number) => apiFetch<GrowthTrend>("/api/v1/admin/dashboard/growth", { query: { months } }),
  money: (months: number, currency?: string) =>
    apiFetch<MoneyTrend>("/api/v1/admin/dashboard/money", { query: { months, currency } }),
  search: (q: string) => apiFetch<{ query: string; results: SearchResult[] }>("/api/v1/admin/search", { query: { q } }),
};

export { post as jsonPost, put as jsonPut };
