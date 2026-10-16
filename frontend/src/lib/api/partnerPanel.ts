/**
 * The partner business panel client (ADR-045), and its wire types.
 *
 * Same two rules as `controlPlane.ts`: the backend is the authority (a call that
 * is not allowed simply fails and the screen says so), and the browser sends
 * selections - a product code, a role key, a capability key, a date range -
 * never an amount, a price, a balance or a partner id it was not shown. Every
 * figure on a screen was computed by the server and arrives as integer minor
 * units with its currency; the browser formats it and never adds two of them.
 */

import { apiFetch } from "./client";
import type { Page } from "./controlPlane";

const partner = (id: string) => `/api/v1/partner/${encodeURIComponent(id)}`;
const json = (body: unknown): string => JSON.stringify(body);

// --- shared ---------------------------------------------------------------------

export interface CurrencyAmount {
  currency: string;
  amount_minor: number;
}

/** One entry of the server's "this does not exist yet, and why" registry. */
export type NotImplemented = Record<string, string>;

// --- dashboard ------------------------------------------------------------------

export type RangeKey = "7d" | "30d" | "90d" | "custom";

export interface MoneyBalance {
  currency: string;
  balance_minor: number;
  pending_minor: number;
  available_minor: number;
  in_payouts_minor: number;
  paid_minor: number;
  entries: number;
}

export interface Dashboard {
  partner: { id: string; name: string; currency: string; status: string };
  range: { key: RangeKey; from: string; to: string };
  customers: {
    total: number;
    active: number;
    suspended: number;
    ended: number;
    active_subscriptions: number;
  } | null;
  sales: {
    transactions: number;
    gross: CurrencyAmount[];
    commission_earned: CurrencyAmount[];
    source: string;
  } | null;
  money: {
    balances: MoneyBalance[];
    pending_commission: CurrencyAmount[];
    pending_payout: CurrencyAmount[];
  } | null;
  charts: {
    gross: Record<string, { date: string; amount_minor: number }[]>;
    commission: Record<string, { date: string; amount_minor: number }[]>;
    transactions: { date: string; count: number }[];
  } | null;
  sub_partners: { total: number; active?: number; suspended?: number } | null;
  recent: {
    customers: { organization_id: string; name: string; link_status: string; started_at: string }[] | null;
    commissions: { id: string; kind: string; amount_minor: number; currency: string; event_at: string }[] | null;
    payouts: { id: string; status: string; payable_minor: number; currency: string; created_at: string }[] | null;
  };
  not_implemented: NotImplemented;
}

export interface Balance {
  balances: MoneyBalance[];
  entries: { id: string; kind: string; amount_minor: number; currency: string; event_at: string; clears_at: string }[];
  not_implemented: NotImplemented;
}

export interface Features {
  permissions: string[];
  not_implemented: NotImplemented;
  roles: { key: string; label: string; description: string; owner_only: boolean }[];
  hierarchy: { max_depth: number };
}

// --- products, plans, pricing ---------------------------------------------------------

export interface ProductPrice {
  plan_code: string;
  plan_name: string;
  intervals: { interval: string; partner_price_minor: number | null; list_price_minor: number | null }[];
}

export interface ManagedProduct {
  code: string;
  name: string;
  description: string;
  tagline: string;
  icon: string;
  category: string;
  status: string;
  grant_scope: "all_plans" | "some_plans";
  enabled_for_sale: boolean;
  show_in_explore: boolean;
  show_in_my_products: boolean;
  display_order: number;
  trial: { offered_by_platform: boolean; days: number | null; allowed_for_partner: boolean };
  portal_visible_by_platform: boolean;
  plans: ProductPrice[];
  customers: number | null;
  active_subscriptions: number | null;
}

export interface ProductsResponse {
  currency: string;
  products: ManagedProduct[];
  available: { code: string; name: string; tagline: string }[];
  can_manage: boolean;
  not_implemented: NotImplemented;
}

export interface PlanRow {
  plan_version_id: string;
  plan_code: string;
  plan_name: string;
  product_code: string;
  product_name: string;
  included_seats: number;
  trial_days: number;
  currency: string;
  enabled_for_sale: boolean;
  prices: {
    interval: string;
    partner_price_minor: number | null;
    list_price_minor: number | null;
    source: "partner_price_book" | "list";
  }[];
}

export interface PackageRow {
  code: string;
  name: string;
  caption: string;
  products: string[];
  items: { product_name: string; plan_name: string; quantity: number; is_optional: boolean }[];
}

export interface CurrencyRow {
  code: string;
  exponent: number;
  partner_priced: boolean;
  listed_publicly: boolean;
  is_default: boolean;
}

export interface PriceRule {
  kind: string;
  plan_code: string;
  plan_name: string;
  product_name: string;
  interval: string;
  currency: string;
  amount_minor: number;
  effective_from: string;
}

// --- network --------------------------------------------------------------------------

export interface SubPartnerRow {
  id: string;
  link_id: string;
  name: string;
  code: string;
  status: string;
  link_status: string;
  currency: string;
  country: string;
  administrator: { name: string; email: string; invited: boolean; last_login_at: string | null };
  customers: number;
  products: string[];
  capabilities: string[];
  experiences: string[];
  created_at: string;
}

export interface SubPartnerOptions {
  capabilities: { key: string; label: string; description: string }[];
  products: { code: string; name: string }[];
  depth: { max: number; can_have_children: boolean };
  not_implemented: NotImplemented;
}

export interface Affiliates {
  programme: {
    enabled: boolean;
    attribution_window_days: number;
    visible_to_you: string[];
    never_shown: string[];
    commission_rules:
      | { code: string; version: number; product_code: string; rate_bp: number; state: string; effective_from: string }[]
      | null;
  };
  summary: Record<string, number>;
  codes: { id: string; code: string; label: string; status: string; url: string }[];
  affiliates: {
    id: string;
    name: string;
    status: string;
    administrator_email: string;
    active_codes: number;
    captured: number;
    converted: number;
    created_at: string;
  }[];
  not_implemented: NotImplemented;
}

export interface ConversionRow {
  id: string;
  code: string;
  code_label: string;
  captured_at: string;
  converted_at: string | null;
  commission_minor: number;
  currency: string;
}

export interface MemberRow {
  id: string;
  name: string;
  email: string;
  role: string;
  role_label: string;
  status: string;
  setup_pending: boolean;
  mfa_enrolled: boolean;
  invited_at: string | null;
  last_login_at: string | null;
  joined_at: string;
  is_you: boolean;
}

export interface MembersResponse {
  members: MemberRow[];
  roles: { key: string; label: string; description: string }[];
  can_manage: boolean;
}

// --- activity, settings, statements ------------------------------------------------------

export interface OrderRow {
  id: string;
  number: string;
  kind: string;
  status: string;
  description: string;
  products: string[];
  currency: string;
  amount_due_minor: number;
  customer: string;
  created_at: string;
  paid_at: string | null;
}

export interface ActivityRow {
  id: string;
  occurred_at: string;
  label: string;
  target: string;
  outcome: string;
  actor: string;
}

export interface OwnProfile {
  partner_id: string;
  version: number;
  business: {
    display_name: string;
    legal_name: string;
    business_email: string;
    website: string;
    phone: string;
    alternative_email: string;
    registration_number: string;
    tax_number: string;
    country: string;
    timezone: string;
    address: Record<string, string>;
  };
  contacts: Record<string, { same_as_primary?: boolean; name?: string; job_title?: string; email?: string; phone?: string }>;
  support: { email: string; phone: string; url: string };
  payout: { currency: string };
  updated_at: string;
}

export interface AuthenticationStatement {
  mode: string;
  custom_domain_sign_in: boolean;
  summary: string;
  sign_in_url: string;
  brand_domains: { hostname: string; status: string }[];
  team: { members: number; with_second_factor: number };
  controlled_by_workeasy360: string[];
  partner_choices: string[];
}

export interface CommunicationOverview {
  channels: {
    key: string;
    label: string;
    status: string;
    via?: string;
    sender_name?: string;
    reply_to?: string;
    use_for_invitations?: boolean;
    editable?: boolean;
  }[];
  templates: { key: string; name: string; description: string; subject: string; variables: string[]; editable: boolean }[];
  alerts: unknown[];
  logs: unknown[];
  not_implemented: NotImplemented;
}

// --- the client --------------------------------------------------------------------------

export const panelApi = {
  dashboard: (id: string, range: RangeKey, from?: string, to?: string) =>
    apiFetch<Dashboard>(`${partner(id)}/dashboard`, { query: { range, from, to } }),
  balance: (id: string) => apiFetch<Balance>(`${partner(id)}/balance`),
  features: (id: string) => apiFetch<Features>(`${partner(id)}/features`),
  orders: (id: string, cursor?: string) => apiFetch<Page<OrderRow>>(`${partner(id)}/orders`, { query: { cursor } }),
  activity: (id: string, cursor?: string) => apiFetch<Page<ActivityRow>>(`${partner(id)}/activity`, { query: { cursor } }),

  products: (id: string) => apiFetch<ProductsResponse>(`${partner(id)}/products`),
  updateProduct: (
    id: string,
    code: string,
    body: Partial<Pick<ManagedProduct, "enabled_for_sale" | "show_in_explore" | "show_in_my_products">>,
  ) => apiFetch(`${partner(id)}/products/${encodeURIComponent(code)}`, { method: "PUT", body: json(body) }),
  orderProducts: (id: string, order: string[]) =>
    apiFetch<{ order: string[] }>(`${partner(id)}/products/order`, { method: "PUT", body: json({ order }) }),
  plans: (id: string) =>
    apiFetch<{ currency: string; plans: PlanRow[]; can_create_plan: boolean; not_implemented: NotImplemented }>(
      `${partner(id)}/plans`,
    ),
  packages: (id: string) =>
    apiFetch<{ packages: PackageRow[]; can_create_package: boolean; not_implemented: NotImplemented }>(
      `${partner(id)}/packages`,
    ),
  currencies: (id: string) =>
    apiFetch<{ default: string; supported: CurrencyRow[]; fx: string }>(`${partner(id)}/currencies`),
  priceBook: (id: string) =>
    apiFetch<{ currency: string; rules: PriceRule[]; can_edit: boolean; not_implemented: NotImplemented }>(
      `${partner(id)}/price-book`,
    ),

  subPartners: (id: string, cursor?: string) =>
    apiFetch<Page<SubPartnerRow>>(`${partner(id)}/sub-partners`, { query: { cursor } }),
  subPartnerOptions: (id: string) => apiFetch<SubPartnerOptions>(`${partner(id)}/sub-partners/options`),
  createSubPartner: (
    id: string,
    body: {
      display_name: string;
      administrator_email: string;
      administrator_name: string;
      capabilities: string[];
      products: string[];
    },
  ) => apiFetch<{ id: string; code: string; status: string }>(`${partner(id)}/sub-partners`, { method: "POST", body: json(body) }),
  subPartnerAction: (id: string, childId: string, action: "activate" | "suspend" | "resume", reason?: string) =>
    apiFetch<{ id: string; status: string }>(`${partner(id)}/sub-partners/${childId}/${action}`, {
      method: "POST",
      body: json(reason ? { reason } : {}),
    }),
  equipSubPartner: (id: string, childId: string, capabilities: string[], products: string[]) =>
    apiFetch(`${partner(id)}/sub-partners/${childId}/equipment`, { method: "PUT", body: json({ capabilities, products }) }),

  affiliates: (id: string) => apiFetch<Affiliates>(`${partner(id)}/affiliates`),
  conversions: (id: string, cursor?: string) =>
    apiFetch<Page<ConversionRow>>(`${partner(id)}/affiliates/conversions`, { query: { cursor } }),

  members: (id: string) => apiFetch<MembersResponse>(`${partner(id)}/members`),
  inviteMember: (id: string, body: { email: string; full_name: string; role: string }) =>
    apiFetch<{ id: string; status: string }>(`${partner(id)}/members`, { method: "POST", body: json(body) }),
  changeRole: (id: string, memberId: string, role: string) =>
    apiFetch(`${partner(id)}/members/${memberId}`, { method: "PATCH", body: json({ role }) }),
  removeMember: (id: string, memberId: string) => apiFetch(`${partner(id)}/members/${memberId}`, { method: "DELETE" }),
  resendSetup: (id: string, memberId: string) =>
    apiFetch<{ sent: boolean }>(`${partner(id)}/members/${memberId}/resend-setup`, { method: "POST", body: json({}) }),

  profile: (id: string) => apiFetch<OwnProfile>(`${partner(id)}/profile`),
  saveProfile: (id: string, expectedVersion: number, body: Record<string, unknown>) =>
    apiFetch<OwnProfile>(`${partner(id)}/profile`, {
      method: "PUT",
      body: json({ ...body, expected_version: expectedVersion }),
    }),
  authentication: (id: string) => apiFetch<AuthenticationStatement>(`${partner(id)}/authentication`),
  communication: (id: string) => apiFetch<CommunicationOverview>(`${partner(id)}/communication`),

  /** The customer CSV, fetched (not navigated to) so a refusal is shown as a
   * message instead of replacing the page. Returns the bytes and whether the
   * server cut the list at its cap. */
  exportCustomers: async (id: string): Promise<{ blob: Blob; truncated: boolean }> => {
    const response = await fetch(`${partner(id)}/customers/export`, { credentials: "same-origin" });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { detail?: string };
      throw new Error(body.detail || (response.status === 429 ? "Too many exports. Wait a moment." : "The export was refused."));
    }
    return { blob: await response.blob(), truncated: response.headers.get("X-Export-Truncated") === "true" };
  },
};
