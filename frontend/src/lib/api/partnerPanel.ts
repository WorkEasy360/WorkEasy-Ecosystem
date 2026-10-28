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
  /** ADR-053: the margin rule that governs this partner's sales now, if any. */
  earning?: { model: "margin" | "commission"; discount_bp: number | null };
  recent: {
    customers: { organization_id: string; name: string; link_status: string; started_at: string }[] | null;
    commissions: { id: string; kind: string; amount_minor: number; currency: string; event_at: string }[] | null;
    payouts: { id: string; status: string; payable_minor: number; currency: string; created_at: string }[] | null;
  };
  not_implemented: NotImplemented;
}

// --- the workspaces (ADR-047) ------------------------------------------------------

/** One tracking link: a referral code, with the campaign it is filed under (its
 * label - tracking metadata only), its address and how it did in the period. */
/** What a link, campaign or product sold and earned, per currency (ADR-053):
 * the sale amounts the commission was computed on, and the net commission. */
export interface Earning {
  currency: string;
  sales_minor: number;
  commission_minor: number;
  sales: number;
}

export interface AffiliateLink {
  id: string;
  code: string;
  label: string;
  status: string;
  created_at: string;
  campaign: string;
  campaign_id: string | null;
  campaign_name: string | null;
  /** The product (and plan) the link promotes; empty for a general link. */
  product_code: string;
  product_name: string | null;
  plan_code: string;
  /** The address to share; null once the link has been switched off. */
  url: string | null;
  clicks: number;
  visitors: number;
  registrations: number;
  conversions: number;
  /** Null for a role that may see referrals but not commission. */
  earnings: Earning[] | null;
}

export interface AffiliateCampaign {
  campaign: string;
  campaign_id: string | null;
  links: number;
  clicks: number;
  visitors: number;
  registrations: number;
  conversions: number;
  /** Whole basis points, rounded half up; null when the campaign had no clicks. */
  conversion_rate_bp: number | null;
  earnings: Earning[] | null;
}

export interface CampaignRow {
  id: string;
  name: string;
  status: string;
  links: number;
  created_at: string;
}

export interface ProductMedia {
  id: string;
  kind: "logo" | "banner" | "screenshot" | string;
  width: number | null;
  height: number | null;
  content_type: string;
  caption: string;
  url: string;
}

/** A Store product an affiliate may promote, with its approved listing content. */
export interface PromotableProduct {
  code: string;
  name: string;
  slug: string;
  tagline: string;
  summary: string;
  description: string;
  category: string;
  icon: string;
  plans: { plan_code: string; plan_name: string }[];
  media: ProductMedia[];
}

export interface FunnelDay {
  date: string;
  clicks: number;
  visitors?: number;
  registrations?: number;
  conversions: number;
}

export interface AffiliateConversionRow {
  id: string;
  code: string;
  code_label: string;
  captured_at: string;
  registered_at?: string | null;
  converted_at: string | null;
  /** What the referred customer bought (product and plan codes only). */
  products?: { product_code: string; plan_code: string }[];
  sales_minor?: number;
  status?: "purchased" | "signed_up";
  /** Null for a role that may see referrals but not commission. */
  commission_minor: number | null;
  currency: string;
}

export interface AffiliateOverview {
  partner: { id: string; name: string; currency: string; status: string };
  range: { key: RangeKey; from: string; to: string };
  kpis: {
    clicks: number;
    unique_visitors: number;
    /** Accounts created by visitors who followed a link (ADR-053). */
    signups: number;
    conversions: number;
    /** Whole basis points, rounded half up; null when there were no clicks. */
    conversion_rate_bp: number | null;
  };
  primary_link: AffiliateLink | null;
  links: AffiliateLink[];
  campaigns: AffiliateCampaign[];
  campaign_list: CampaignRow[];
  campaign_presets: string[];
  products: PromotableProduct[];
  top_products: { product_code: string; product_name: string; sales: number; earnings: Earning[] }[] | null;
  series: FunnelDay[];
  money: {
    sales: (CurrencyAmount & { sales: number })[];
    earned: CurrencyAmount[];
    pending: CurrencyAmount[];
    available: CurrencyAmount[];
    paid: CurrencyAmount[];
    balances: MoneyBalance[];
  } | null;
  recent: {
    conversions: AffiliateConversionRow[];
    payouts: { id: string; status: string; payable_minor: number; currency: string; created_at: string }[] | null;
  };
  not_implemented: NotImplemented;
}

export interface ProductPerformance {
  product_code: string;
  product_name: string;
  customers_holding: number;
  active_subscriptions: number;
}

export interface PartnerReports {
  partner: { id: string; name: string; currency: string; status: string };
  range: { key: RangeKey; from: string; to: string };
  customers: {
    total: number;
    active: number;
    suspended: number;
    ended: number;
    active_subscriptions: number;
  } | null;
  products: ProductPerformance[] | null;
  sales: {
    transactions: number;
    gross: CurrencyAmount[];
    commission_earned: CurrencyAmount[];
    charts: Dashboard["charts"];
    source: string;
  } | null;
  referrals: {
    clicks: number;
    unique_visitors?: number;
    registrations?: number;
    conversions: number;
    conversion_rate_bp: number | null;
    by_campaign: { campaign: string; clicks: number; conversions: number; conversion_rate_bp: number | null }[];
    series: FunnelDay[];
  } | null;
  not_implemented: NotImplemented;
}

export interface WhiteLabelStatus {
  brand: { state: "not_started" | "in_progress" | "complete"; name: string; logo: boolean } | null;
  domain: { state: string; hostname: string } | null;
  /** ADR-053: the partner's branded Store, at `/store/<partner>` on the customer portal. */
  storefront?: { url: string; live: boolean } | null;
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
  /** ADR-053: how the partner earns on this plan - its margin (a discount off
   * list) or a commission. */
  earning?: { model: "margin" | "commission"; discount_bp: number | null };
  prices: {
    interval: string;
    partner_price_minor: number | null;
    list_price_minor: number | null;
    /** What the customer pays: the partner's book, else list (server-decided). */
    customer_price_minor?: number | null;
    /** List less the partner's discount; null when the partner earns commission. */
    partner_cost_minor?: number | null;
    margin_minor?: number | null;
    margin_bp?: number | null;
    /** List minus the partner's price, in minor units (the server's subtraction). */
    difference_minor: number | null;
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
    eligible_products: string[];
    payout_currency: string;
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
  registered_at?: string | null;
  converted_at: string | null;
  products?: { product_code: string; plan_code: string }[];
  sales_minor?: number;
  status?: "purchased" | "signed_up";
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
  plans?: string[];
  currency: string;
  amount_due_minor: number;
  recurring_amount_minor?: number;
  /** `partner` (you placed it), `storefront` (your branded Store) or `direct`. */
  channel?: string;
  sold_by_you?: boolean;
  /** What the order earned you on the ledger; null without commission access or before it accrues. */
  earning?: {
    model: "margin" | "commission";
    amount_minor: number;
    partner_cost_minor: number | null;
    currency: string;
  } | null;
  payment_status?: "awaiting_payment" | "paid" | "nothing_due" | "not_paid";
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
  team: { members: number };
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

// --- roles, channels, translations, API keys, help (ADR-046) -----------------------------

export interface RoleView {
  key: string;
  label: string;
  description: string;
  administrator: boolean;
  can_change: boolean;
  owner_only: boolean;
  members: number;
  capabilities: { key: string; label: string; access: "view" | "manage" }[];
}

export interface RolesResponse {
  roles: RoleView[];
  partner_capabilities: { key: string; label: string }[];
  account_access: { everyone: string[]; administrators: string[] };
  can_manage: boolean;
  not_implemented: NotImplemented;
}

export type ChannelState = "connected" | "needs_setup" | "disconnected" | "error" | "not_available";

export interface ChannelManagerResponse {
  channels: { key: string; label: string; state: ChannelState; detail: string; editable: boolean }[];
  states: { key: ChannelState; label: string; meaning: string }[];
  not_implemented: NotImplemented;
}

export interface TranslationString {
  key: string;
  group: string;
  label: string;
  max_length: number;
  /** What the partner has worded for this locale ("" = nothing). */
  value: string;
  effective: string;
  source: "partner" | "platform" | "english";
  english: string;
}

export interface TranslationsOverview {
  locale: string;
  locales: { code: string; name: string; translated: number }[];
  strings: TranslationString[];
  can_manage: boolean;
  fallback: string[];
  never_translatable: string[];
}

export interface ApiKeyRow {
  id: string;
  name: string;
  key_id: string;
  scopes: { key: string; label: string }[];
  status: "active" | "revoked" | "expired";
  created_at: string;
  created_by: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  rotated_from: boolean;
}

export interface ApiKeysOverview {
  keys: ApiKeyRow[];
  scopes: { key: string; label: string; description: string }[];
  expiry_days: number[];
  default_expiry_days: number;
  can_manage: boolean;
  limit: number;
  never_issued: string[];
}

/** A freshly minted credential. The `secret` is shown once and never again. */
export interface IssuedKey {
  key: ApiKeyRow;
  secret: string;
}

export type NotificationView = "all" | "unread" | "important";

export interface NotificationItem {
  id: string;
  occurred_at: string;
  title: string;
  category: string;
  important: boolean;
  detail: string;
  read: boolean;
}

export interface NotificationsResponse {
  view: NotificationView;
  items: NotificationItem[];
  counts: { all: number; unread: number; important: number; important_unread: number };
  window: number;
  truncated: boolean;
  not_implemented: NotImplemented;
}

export interface SupportResponse {
  contact: { email: string; available: boolean };
  help_desk: { name: string; available: boolean; url: string; suspended: boolean };
  partner: { name: string };
  priorities: string[];
  /** Whether the platform sends a request to its support address (ADR-053). */
  can_send?: boolean;
  requests: null;
  not_implemented: NotImplemented;
}

// --- growing the partnership: upgrade requests (ADR-049) ----------------------------------

export type UpgradeTarget = "reseller" | "white_label";
export type UpgradeStatus =
  | "requested"
  | "profile_required"
  | "pending_approval"
  | "approved"
  | "rejected"
  | "cancelled";

/** What the partner proposed that has no other home yet. Every key is optional:
 * a section the partner has not reached is absent. */
export interface UpgradeProposal {
  products?: string[];
  pricing?: { preference: "standard" | "negotiated"; notes: string };
  commercial?: {
    expected_customers: number | null;
    team_size: number | null;
    customer_management: string;
    notes: string;
  };
  brand?: { brand_name: string; primary_color: string; secondary_color: string; support_email: string };
  domain?: { hostname: string };
}

export interface UpgradeRequest {
  id: string;
  partner_id: string;
  from_experience: string;
  to_experience: UpgradeTarget;
  status: UpgradeStatus;
  proposal: UpgradeProposal;
  requested_at: string;
  submitted_at: string | null;
  decided_at: string | null;
  /** What staff wrote when they asked for more - shown to the partner. */
  info_request_note: string;
  decision_note: string;
  capabilities_added: string[];
  capabilities_removed: string[];
  products_granted: string[];
  version: number;
  editable: boolean;
  /** ADR-053: the set-up fee fixed when the request opened; null is no fee. */
  setup_fee?: { amount_minor: number; currency: string; settled: boolean } | null;
}

export interface UpgradeChecklistItem {
  key: string;
  label: string;
  required: boolean;
  done: boolean;
  detail: string;
}

export interface UpgradeState {
  /** The workspace the partner opens first now (the server's word). */
  current: string | null;
  /** The upgrades it may ask for from here; empty at the top. */
  eligible: UpgradeTarget[];
  /** Whether this member may act (administrators only); anyone may read. */
  can_request: boolean;
  open: UpgradeRequest | null;
  recent: UpgradeRequest[];
  steps: Record<UpgradeTarget, { key: string; label: string }[]>;
  /** The products on sale, offered on the Products step (only while a request is open). */
  products: { code: string; name: string }[];
  checklist: UpgradeChecklistItem[];
  /** What starting each upgrade would cost now; null is no fee (ADR-053). */
  fees?: Partial<Record<UpgradeTarget, CurrencyAmount | null>>;
}

/** The section names the server accepts. The business step is the profile. */
export type UpgradeSection = "products" | "pricing" | "commercial" | "brand" | "domain";

// --- the client --------------------------------------------------------------------------

export const panelApi = {
  dashboard: (id: string, range: RangeKey, from?: string, to?: string) =>
    apiFetch<Dashboard>(`${partner(id)}/dashboard`, { query: { range, from, to } }),
  balance: (id: string) => apiFetch<Balance>(`${partner(id)}/balance`),
  affiliateOverview: (id: string, range: RangeKey, from?: string, to?: string) =>
    apiFetch<AffiliateOverview>(`${partner(id)}/affiliate/overview`, { query: { range, from, to } }),
  reports: (id: string, range: RangeKey, from?: string, to?: string) =>
    apiFetch<PartnerReports>(`${partner(id)}/reports`, { query: { range, from, to } }),
  whiteLabelStatus: (id: string) => apiFetch<WhiteLabelStatus>(`${partner(id)}/white-label/status`),
  /** A new referral link (ADR-053): optionally a chosen code, a product (and
   * plan) to open, and one of the partner's campaigns. The server builds the
   * address and decides where it leads - the browser never composes either. */
  createTrackingLink: (
    id: string,
    body: { label?: string; code?: string; product_code?: string; plan_code?: string; campaign_id?: string },
  ) =>
    apiFetch<{ id: string; code: string; label: string; url: string }>(`${partner(id)}/referrals`, {
      method: "POST",
      body: json(body),
    }),
  campaigns: (id: string) => apiFetch<{ campaigns: CampaignRow[] }>(`${partner(id)}/campaigns`),
  createCampaign: (id: string, name: string) =>
    apiFetch<{ id: string; name: string; status: string }>(`${partner(id)}/campaigns`, {
      method: "POST",
      body: json({ name }),
    }),
  setCampaignActive: (id: string, campaignId: string, active: boolean) =>
    apiFetch<{ id: string; status: string }>(`${partner(id)}/campaigns/${encodeURIComponent(campaignId)}/status`, {
      method: "POST",
      body: json({ active }),
    }),
  disableTrackingLink: (id: string, codeId: string) =>
    apiFetch<{ id: string; status: string }>(`${partner(id)}/referral-codes/${encodeURIComponent(codeId)}/disable`, {
      method: "POST",
      body: json({}),
    }),
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
  // ADR-049: an upgrade is a request on the same account; nothing here grants a power.
  upgrade: (id: string) => apiFetch<UpgradeState>(`${partner(id)}/upgrade`),
  startUpgrade: (id: string, experience: UpgradeTarget) =>
    apiFetch<UpgradeRequest>(`${partner(id)}/upgrade/requests`, { method: "POST", body: json({ experience }) }),
  saveUpgradeSection: (id: string, requestId: string, section: UpgradeSection, data: unknown, expectedVersion: number) =>
    apiFetch<UpgradeRequest>(`${partner(id)}/upgrade/requests/${requestId}/sections/${section}`, {
      method: "PUT",
      body: json({ data, expected_version: expectedVersion }),
    }),
  submitUpgrade: (id: string, requestId: string, expectedVersion: number) =>
    apiFetch<UpgradeRequest>(`${partner(id)}/upgrade/requests/${requestId}/submit`, {
      method: "POST",
      body: json({ expected_version: expectedVersion }),
    }),
  cancelUpgrade: (id: string, requestId: string) =>
    apiFetch<UpgradeRequest>(`${partner(id)}/upgrade/requests/${requestId}/cancel`, { method: "POST", body: json({}) }),
  authentication: (id: string) => apiFetch<AuthenticationStatement>(`${partner(id)}/authentication`),
  communication: (id: string) => apiFetch<CommunicationOverview>(`${partner(id)}/communication`),
  channels: (id: string) => apiFetch<ChannelManagerResponse>(`${partner(id)}/channels`),
  roles: (id: string) => apiFetch<RolesResponse>(`${partner(id)}/roles`),
  suspendMember: (id: string, memberId: string) =>
    apiFetch(`${partner(id)}/members/${memberId}/suspend`, { method: "POST", body: json({}) }),
  resumeMember: (id: string, memberId: string) =>
    apiFetch(`${partner(id)}/members/${memberId}/resume`, { method: "POST", body: json({}) }),

  translations: (id: string, locale: string) =>
    apiFetch<TranslationsOverview>(`${partner(id)}/translations`, { query: { locale } }),
  saveTranslations: (id: string, locale: string, strings: Record<string, string>) =>
    apiFetch<TranslationsOverview>(`${partner(id)}/translations`, { method: "PUT", body: json({ locale, strings }) }),

  apiKeys: (id: string) => apiFetch<ApiKeysOverview>(`${partner(id)}/api-keys`),
  createApiKey: (id: string, body: { name: string; scopes: string[]; expires_in_days: number }) =>
    apiFetch<IssuedKey>(`${partner(id)}/api-keys`, { method: "POST", body: json(body) }),
  rotateApiKey: (id: string, keyId: string, expiresInDays: number) =>
    apiFetch<IssuedKey>(`${partner(id)}/api-keys/${keyId}/rotate`, {
      method: "POST",
      body: json({ expires_in_days: expiresInDays }),
    }),
  revokeApiKey: (id: string, keyId: string) =>
    apiFetch<{ key: ApiKeyRow }>(`${partner(id)}/api-keys/${keyId}/revoke`, { method: "POST", body: json({}) }),

  notifications: (id: string, view: NotificationView) =>
    apiFetch<NotificationsResponse>(`${partner(id)}/notifications`, { query: { view } }),
  markNotificationsRead: (id: string, body: { ids: string[] } | { all: true }) =>
    apiFetch<NotificationsResponse>(`${partner(id)}/notifications/read`, { method: "POST", body: json(body) }),
  support: (id: string) => apiFetch<SupportResponse>(`${partner(id)}/support`),
  /** ADR-053: a support request, sent by the platform's email to its support address. */
  sendSupport: (id: string, body: { subject: string; message: string; priority: string; workspace?: string }) =>
    apiFetch<{ sent: boolean; reference: string }>(`${partner(id)}/support`, { method: "POST", body: json(body) }),
  /** ADR-053: a central Order for one of the partner's customers - identifiers only. */
  quoteCustomerOrder: (id: string, orgId: string, body: CustomerOrderRequest) =>
    apiFetch<CustomerOrderQuote>(`${partner(id)}/customers/${encodeURIComponent(orgId)}/orders/quote`, {
      method: "POST",
      body: json(body),
    }),
  placeCustomerOrder: (id: string, orgId: string, body: CustomerOrderRequest, idempotencyKey: string) =>
    apiFetch<CustomerOrderPlaced>(`${partner(id)}/customers/${encodeURIComponent(orgId)}/orders`, {
      method: "POST",
      body: json(body),
      headers: { "Idempotency-Key": idempotencyKey },
    }),

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

// --- orders for a customer (ADR-053) ------------------------------------------------------

export interface CustomerOrderRequest {
  plan_version_id?: string;
  cart_items?: { plan_version_id: string; quantity?: number }[];
  billing_interval?: string;
  quantity?: number;
}

export interface CustomerOrderQuote {
  kind: string;
  description: string;
  product_codes: string[];
  currency: string;
  billing_interval: string;
  quantity: number;
  recurring_amount_minor: number;
  amount_due_minor: number;
  requires_payment: boolean;
  is_trial: boolean;
  channel: string;
  lines: { plan_version_id: string; product_code: string; plan_name: string; quantity: number; recurring_amount_minor: number }[];
}

export interface CustomerOrderPlaced {
  order_id: string;
  number: string;
  status: string;
  description: string;
  currency: string;
  amount_due_minor: number;
  recurring_amount_minor: number;
  requires_payment: boolean;
  channel: string;
}
