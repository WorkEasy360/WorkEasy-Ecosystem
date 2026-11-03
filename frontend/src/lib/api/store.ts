/**
 * The Store management API (ADR-056): Super Admin's dashboard, product-wise
 * commercial rules, plans & pricing, price ranges and the central engine's
 * preview; the customer's product page; a partner's own price.
 *
 * Every amount here is the server's. The browser never computes a price, a
 * margin or a commission - it sends identifiers and the values staff typed,
 * and shows what comes back.
 */
import { apiFetch } from "./client";

const admin = "/api/v1/admin/store";
const json = (body: unknown): string => JSON.stringify(body);

export type Channel = "direct" | "affiliate" | "reseller" | "white_label";

export interface ChannelQuote {
  channel: Channel;
  currency: string;
  interval: string;
  list_minor: number;
  discount_bp: number;
  base_minor: number;
  customer_minor: number;
  price_source: string;
  setup_fee_minor: number | null;
  tax_behaviour: string;
  tax_minor: number | null;
  partner_cost_minor: number | null;
  margin_minor: number | null;
  margin_bp: number | null;
  commission_minor: number | null;
  earning: "none" | "margin" | "commission";
  rule: Record<string, unknown> | null;
  partner_share_minor: number;
  platform_share_minor: number;
  payable_minor: number;
  price_range: PriceRange | null;
  notes: string[];
}

export interface PriceRow {
  price_id: string;
  plan_version_id: string;
  price_book_id: string;
  price_book_code: string;
  region: string;
  currency: string;
  interval: string;
  model: string;
  amount_minor: number | null;
  discount_bp: number;
  net_amount_minor: number | null;
  setup_fee_minor: number | null;
  tax_behaviour: string;
  valid_from: string;
  valid_to: string | null;
  state: "current" | "scheduled";
}

export interface PriceRange {
  id: string;
  experience: "reseller" | "white_label";
  plan_id: string;
  interval: string;
  currency: string;
  min_minor: number;
  max_minor: number | null;
  partner_may_set: boolean;
  updated_at: string | null;
}

export interface RuleSummary {
  code: string;
  version: number;
  scope: "plan" | "product" | "all";
  rate_bp: number;
  fixed_amount_minor: number | null;
  fixed_currency: string;
  recurring: boolean;
  recurring_months: number | null;
  effective_from: string;
}

export interface MarginSummary {
  code: string;
  version: number;
  scope: "product" | "all";
  discount_bp: number;
  recurring: boolean;
  effective_from: string;
}

export interface CommercialPlan {
  plan_id: string;
  plan_version_id: string;
  code: string;
  name: string;
  status: string;
  kind: "standard" | "add_on";
  billing_type: "paid" | "free_trial";
  recommended: boolean;
  published: boolean;
  trial_days: number;
  prices: PriceRow[];
  ranges: PriceRange[];
  channels: Partial<Record<Channel, Record<string, ChannelQuote>>>;
}

export interface CommercialProduct {
  id: string;
  code: string;
  name: string;
  icon: string;
  category: string;
  status: "draft" | "active" | "archived";
  listing: string;
  channels: { customer_store: boolean; affiliate: boolean; reseller: boolean; white_label: boolean };
  trial: { enabled: boolean; duration_days: number } | null;
  plans: CommercialPlan[];
  affiliate_commission?: RuleSummary | null;
  margins?: { reseller: MarginSummary | null; white_label: MarginSummary | null };
}

export interface CommercialRules {
  currency: string | null;
  currencies: string[];
  can_manage: { catalog: boolean; pricing: boolean; commission: boolean };
  shows_money: boolean;
  products: CommercialProduct[];
}

export interface Amount {
  currency: string;
  amount_minor: number;
}

export interface StoreDashboard {
  period: { days: number; start: string; end: string };
  catalog: Record<string, number>;
  sales?: {
    orders: number;
    revenue: (Amount & { payments: number })[];
    by_channel: Record<"direct" | "reseller" | "white_label", { orders: number; amounts: Amount[] }>;
    top_products: { code: string; name: string; orders: number }[];
  };
  affiliate_sales?: { currency: string; sales: number; basis_minor: number; commission_minor: number }[];
  subscriptions?: { active: number; trialing: number; past_due: number; cancelled: number };
  partner_requests?: { promote: number; sell: number };
}

export interface BoardVersion {
  id: string;
  version: number;
  published_at: string | null;
  included_seats: number;
  max_seats: number | null;
  trial_days: number;
  features: Record<string, unknown>;
}

export interface BoardPlan {
  id: string;
  code: string;
  name: string;
  caption: string;
  description: string;
  status: string;
  is_public: boolean;
  sort_order: number;
  billing_type: "paid" | "free_trial";
  kind: "standard" | "add_on";
  recommended: boolean;
  version: BoardVersion | null;
  draft_version: BoardVersion | null;
  prices: PriceRow[];
}

export interface PriceBookRow {
  id: string;
  code: string;
  name: string;
  currency: string;
  region: string;
  priority: number;
}

export interface PlanBoard {
  product: { id: string; code: string; name: string };
  books: PriceBookRow[];
  plans: BoardPlan[];
  features: { key: string; name: string; kind: string; unit: string; is_public: boolean }[];
  can_manage: { catalog: boolean; pricing: boolean };
}

export interface PriceInput {
  price_book_id: string;
  plan_version_id: string;
  interval: string;
  amount_minor: number;
  discount_bp?: number;
  setup_fee_minor?: number | null;
  valid_from?: string | null;
  valid_to?: string | null;
}

export interface CommissionInput {
  product_code: string;
  plan_code?: string;
  rate_bp?: number;
  fixed_amount_minor?: number | null;
  fixed_currency?: string;
  recurring?: boolean;
  recurring_months?: number | null;
  effective_from?: string | null;
}

export interface RangeInput {
  experience: "reseller" | "white_label";
  plan_id: string;
  interval: string;
  currency: string;
  min_minor: number;
  max_minor: number | null;
  partner_may_set: boolean;
}

export const storeAdminApi = {
  dashboard: (days = 30) => apiFetch<StoreDashboard>(`${admin}/dashboard`, { query: { days: String(days) } }),
  commercialRules: (currency?: string) => apiFetch<CommercialRules>(`${admin}/commercial-rules`, { query: { currency } }),
  setCommission: (body: CommissionInput) => apiFetch<{ code: string; version: number }>(`${admin}/commission`, { method: "POST", body: json(body) }),
  quote: (q: { plan_version_id: string; interval: string; currency: string; channel: Channel; partner_id?: string }) =>
    apiFetch<ChannelQuote>(`${admin}/quote`, { query: q }),
  planBoard: (productId: string) => apiFetch<PlanBoard>(`${admin}/products/${productId}/plans`),
  duplicatePlan: (planId: string, code: string, name: string) =>
    apiFetch<{ id: string; code: string; prices_copied: number }>(`${admin}/plans/${planId}/duplicate`, { method: "POST", body: json({ code, name }) }),
  publishPrice: (body: PriceInput) => apiFetch<{ id: string }>(`/api/v1/admin/catalog/prices`, { method: "POST", body: json(body) }),
  closePrice: (priceId: string, at?: string | null) => apiFetch(`${admin}/prices/${priceId}/close`, { method: "POST", body: json({ at: at ?? null }) }),
  cancelPrice: (priceId: string) => apiFetch(`${admin}/prices/${priceId}/cancel`, { method: "POST", body: json({}) }),
  updatePlan: (planId: string, changes: { recommended?: boolean; is_public?: boolean }) =>
    apiFetch(`/api/v1/admin/catalog/plans/${planId}`, { method: "PATCH", body: json(changes) }),
  setPlanStatus: (planId: string, status: string) =>
    apiFetch(`/api/v1/admin/catalog/plans/${planId}/status`, { method: "POST", body: json({ status }) }),
  publishVersion: (planVersionId: string) =>
    apiFetch(`/api/v1/admin/catalog/plan-versions/${planVersionId}/publish`, { method: "POST", body: json({}) }),
  setRange: (body: RangeInput) => apiFetch<PriceRange>(`${admin}/price-ranges`, { method: "POST", body: json(body) }),
  removeRange: (id: string) => apiFetch(`${admin}/price-ranges/${id}`, { method: "DELETE" }),
  setMargin: (body: { experience: "reseller" | "white_label"; product_code: string; discount_bp: number }) =>
    apiFetch(`/api/v1/admin/margins`, { method: "POST", body: json(body) }),
};

/** A partner sets its own customer price inside Super Admin's range. */
export const partnerPriceApi = {
  setOwnPrice: (partnerId: string, body: { plan_version_id: string; interval: string; amount_minor: number }) =>
    apiFetch<{ price_id: string; amount_minor: number }>(`/api/v1/partner/${partnerId}/own-price`, { method: "POST", body: json(body) }),
};
