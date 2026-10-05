/**
 * The Super Admin and partner-portal API clients (Phase 7), and their wire types.
 *
 * Two facts shape every function here:
 *
 * 1. **The backend is the authority.** Nothing below decides who may do what;
 *    the calls simply fail with 401 `step_up_required`, 403 or 404 and the
 *    screens present that. Hiding a nav item is a convenience.
 * 2. **The browser sends selections and never authority.** A body carries a
 *    choice (a plan version id, a capability key, a reason) - never a price, a
 *    commission amount, an approver, a status stamp or an organization it was
 *    not shown. Money on screen is server-derived.
 *
 * Every mutation that can be retried carries an `Idempotency-Key`, minted once
 * per user action by {@link newKey} so a double-click or a network retry is the
 * same command.
 */

import { apiFetch } from "./client";

export interface Page<T> {
  results: T[];
  next_cursor: string | null;
  has_more: boolean;
}

/** A fresh command id. `crypto.randomUUID` needs a secure context, which the
 * app always has (HTTPS in production, localhost in development). */
export function newKey(): string {
  return crypto.randomUUID();
}

function idem(key: string): HeadersInit {
  return { "Idempotency-Key": key };
}

const json = (body: unknown): string => JSON.stringify(body);

// --- shared shapes ---------------------------------------------------------

export interface PartnerSummary {
  id: string;
  code: string;
  display_name: string;
  legal_name: string;
  contact_name: string;
  contact_email: string;
  payout_currency: string;
  status: "draft" | "active" | "suspended" | "terminated";
  version: number;
  created_at: string;
  activated_at: string | null;
  status_reason: string;
}

export interface PartnerDetail extends PartnerSummary {
  capabilities: { capability: string; granted_at: string; expires_at: string | null; is_effective: boolean }[];
  members: { id: string; user_id: string; email: string; role: string; added_at: string; password_set: boolean }[];
  product_grants: { id: string; product_code: string; plan_code: string | null }[];
  live_customers: number;
}

export interface CustomerRow {
  organization_id: string;
  slug: string;
  display_name: string;
  status: string;
  created_at: string;
  owner_email: string;
  subscription_status: string;
  products: string[];
  seat_capacity: number | null;
  summary_as_of: string | null;
  /** Phase 9: "customer", "trial" or "" (its people are Explorers). */
  access_class: string;
  partner: { partner_id: string; partner_code: string; partner_name: string; link_status: string } | null;
}

export interface AuditRow {
  id: string;
  occurred_at: string;
  action: string;
  actor_kind: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  reason: string;
}

export interface SubscriptionItemView {
  id: string;
  product_code: string;
  plan_code: string;
  bundle_code: string;
  quantity: number;
  is_derived: boolean;
  removed_at: string | null;
}

export interface SubscriptionView {
  id: string;
  status: string;
  currency: string;
  billing_interval: string;
  current_period_end: string;
  cancel_at: string | null;
  version: number;
  items: SubscriptionItemView[];
}

export interface CustomerDetail {
  organization: { organization_id: string; slug: string; display_name: string; status: string; created_at: string; suspension_reason: string };
  members: Record<string, number>;
  subscriptions: SubscriptionView[];
  entitlements: Record<string, { status: string; seats: number | null }>;
  licenses: { product_code: string; seats_total: number; seats_assigned: number; over_limit: number }[];
  entitlement_overrides: { id: string; product_code: string; feature_key: string; reason: string; expires_at: string | null }[];
  billing: { currency: string; collection_mode: string; billing_region: string; provider: string; note: string } | null;
  partner: { partner_id: string; code: string; display_name: string; link_status: string } | null;
  audit: AuditRow[];
}

export interface DiagnosticStep {
  step: string;
  ok: boolean;
  code: string;
}

export interface Diagnosis {
  organization_id: string;
  product_code: string;
  can_launch: boolean;
  blocking_step: string | null;
  chain: DiagnosticStep[];
  read_only: true;
}

export interface AccrualRow {
  id: string;
  kind: string;
  amount_minor: number;
  currency: string;
  event_at: string;
  clears_at: string;
  reason_code: string;
  partner_id?: string;
  rule_code?: string | null;
  rule_version?: number | null;
  /** Staff ledger only (never on a partner's own view): whose sale it was, the
   * sale reference, the rate it was computed at, and the live payout that pays it. */
  organization_id?: string | null;
  organization_name?: string;
  source_ref?: string;
  rate_bp?: number | null;
  payout?: { id: string; status: string } | null;
}

export interface PayoutRow {
  id: string;
  status: "draft" | "ready" | "approved" | "paid" | "cancelled";
  currency: string;
  period_end: string;
  gross_minor: number;
  adjustments_minor: number;
  payable_minor: number;
  line_count: number;
  version?: number;
  partner_id?: string;
  created_by?: string;
  approved_by?: string | null;
  paid_reference?: string;
}

export interface RuleRow {
  id: string;
  code: string;
  version: number;
  partner_id: string | null;
  product_code: string;
  rate_bp: number;
  effective_from: string;
  retired_at: string | null;
}

export interface CatalogProduct {
  id: string;
  code: string;
  name: string;
  status: string;
  licensing_mode: string;
  /** Phase 9: how the product is presented and listed on the public site. */
  description: string;
  sort_order: number;
  slug: string;
  tagline: string;
  marketing_url: string;
  listing: "listed" | "coming_soon" | "hidden";
  /** Derived by the server from status and listing - never stored. */
  availability: "available" | "coming_soon" | "private" | "disabled" | "draft";
}

/** The product fields the product manager may change (never the code). */
export interface ProductChanges {
  name?: string;
  description?: string;
  tagline?: string;
  slug?: string;
  marketing_url?: string;
  listing?: CatalogProduct["listing"];
  sort_order?: number;
}

// --- the products console (Phase 11) ---------------------------------------------

/** How one product may be trialled - absent (`null`) means never configured,
 * not "disabled with defaults": the platform ships no default anywhere. */
export interface TrialConfig {
  enabled: boolean;
  duration_days: number;
  seat_limit: number | null;
  payment_method_required: boolean;
  eligible_plan_codes: string[];
}

export interface ConsoleProductPlan {
  code: string;
  name: string;
  status: string;
  is_public: boolean;
  kind: "standard" | "add_on";
  billing_type: "paid" | "free_trial";
  priced: boolean;
}

export interface ConsoleIntegration {
  client_id: string;
  status: "draft" | "active" | "suspended" | "retired";
  display_name: string;
  launch_url: string;
  health_url: string;
  redirect_uris: string[];
  contact_email: string;
  suspension_reason: string;
  sso: "connected" | "integration_required";
  health: { status: "unknown" | "healthy" | "degraded" | "unreachable"; checked_at: string | null; detail: string };
}

export interface ConsoleProduct {
  id: string;
  code: string;
  name: string;
  slug: string;
  tagline: string;
  description: string;
  category: string;
  icon: string;
  status: string;
  listing: "listed" | "coming_soon" | "hidden";
  availability: "draft" | "coming_soon" | "available" | "private" | "disabled";
  licensing_mode: string;
  sort_order: number;
  marketing_url: string;
  plans: ConsoleProductPlan[];
  channels: {
    direct: { listed: boolean; available: boolean; public_plans: number; priced_plans: number; ready: boolean };
    reseller: { partners: number };
    affiliate: { product_rules: number; programme_default: boolean };
    trial: TrialConfig | null;
  };
  /** `null` when the caller may not see integrations, or none is registered. */
  integration: ConsoleIntegration | null;
}

export interface ProductsConsole {
  products: ConsoleProduct[];
  can_see_integrations: boolean;
}

export interface CapabilityInfo {
  key: string;
  label: string;
  /** Phase 10: the heading a form lists it under (`customer_management`, ...). */
  group: string;
}

// --- admin ----------------------------------------------------------------

const admin = "/api/v1/admin";

export const adminApi = {
  session: () => apiFetch<{ is_staff: boolean; aal: number; permissions: string[] }>(`${admin}/session`),
  dashboard: () => apiFetch<Dashboard>(`${admin}/dashboard`),
  audit: (cursor?: string, action?: string) =>
    apiFetch<Page<AuditRow>>(`${admin}/audit`, { query: { cursor, action } }),

  /** Phase 9 filters: `subscription` (a status, or "none"), `access`
   * ("customer" | "trial" | "none") and `product` (holds it now). */
  customers: (q: {
    q?: string;
    status?: string;
    cursor?: string;
    subscription?: string;
    access?: string;
    product?: string;
  }) => apiFetch<Page<CustomerRow>>(`${admin}/customers`, { query: q }),
  customer: (id: string) => apiFetch<CustomerDetail>(`${admin}/customers/${id}`),
  suspendCustomer: (id: string, reason: string) =>
    apiFetch(`${admin}/customers/${id}/suspend`, { method: "POST", body: json({ reason }) }),
  reactivateCustomer: (id: string, reason: string) =>
    apiFetch(`${admin}/customers/${id}/reactivate`, { method: "POST", body: json({ reason }) }),
  diagnose: (id: string, productCode: string, memberEmail?: string) =>
    apiFetch<Diagnosis>(`${admin}/customers/${id}/diagnose`, {
      method: "POST",
      body: json({ product_code: productCode, member_email: memberEmail || undefined }),
    }),
  subscriptionAction: (
    orgId: string,
    subscriptionId: string,
    action: "suspend" | "resume" | "cancel",
    reason: string,
    expectedVersion?: number,
    immediate = false,
  ) =>
    apiFetch(`${admin}/customers/${orgId}/subscriptions/${subscriptionId}/${action}`, {
      method: "POST",
      headers: idem(newKey()),
      body: json({ reason, immediate, expected_version: expectedVersion }),
    }),
  grantOverride: (
    orgId: string,
    body: { product_code?: string; feature_key?: string; reason: string; expires_at: string },
  ) =>
    apiFetch(`${admin}/customers/${orgId}/entitlement-overrides`, { method: "POST", body: json(body) }),
  revokeOverride: (orgId: string, grantId: string, reason: string) =>
    apiFetch(`${admin}/customers/${orgId}/entitlement-overrides/${grantId}/revoke`, {
      method: "POST",
      body: json({ reason }),
    }),

  products: () => apiFetch<{ results: CatalogProduct[] }>(`${admin}/catalog/products`),
  createProduct: (body: { code: string; name: string; description?: string }) =>
    apiFetch(`${admin}/catalog/products`, { method: "POST", headers: idem(newKey()), body: json(body) }),
  setProductStatus: (id: string, status: string) =>
    apiFetch(`${admin}/catalog/products/${id}/status`, { method: "POST", body: json({ status }) }),
  updateProduct: (id: string, changes: ProductChanges) =>
    apiFetch<CatalogProduct>(`${admin}/catalog/products/${id}`, { method: "PATCH", body: json(changes) }),

  // --- the products console (Phase 11) --------------------------------------------

  productsConsole: () => apiFetch<ProductsConsole>(`${admin}/products/console`),
  setTrialConfig: (productId: string, config: TrialConfig) =>
    apiFetch<TrialConfig>(`${admin}/catalog/products/${productId}/trial`, { method: "PUT", body: json(config) }),
  updateIntegration: (
    productCode: string,
    changes: Partial<Pick<ConsoleIntegration, "display_name" | "launch_url" | "health_url" | "redirect_uris" | "contact_email">>,
  ) => apiFetch<ConsoleIntegration>(`${admin}/products/${productCode}/integration`, { method: "PATCH", body: json(changes) }),
  setIntegrationStatus: (productCode: string, status: string, reason: string) =>
    apiFetch<ConsoleIntegration>(`${admin}/products/${productCode}/integration/status`, {
      method: "POST",
      body: json({ status, reason }),
    }),
  checkIntegrationHealth: (productCode: string) =>
    apiFetch<{ status: string; detail: string }>(`${admin}/products/${productCode}/integration/health-check`, {
      method: "POST",
    }),

  partners: (q: { status?: string; q?: string; cursor?: string }) =>
    apiFetch<Page<PartnerSummary>>(`${admin}/partners`, { query: q }),
  partner: (id: string) => apiFetch<PartnerDetail>(`${admin}/partners/${id}`),
  createPartner: (body: {
    code: string;
    display_name: string;
    payout_currency: string;
    legal_name?: string;
    contact_name?: string;
    contact_email?: string;
  }) =>
    apiFetch<PartnerSummary>(`${admin}/partners`, { method: "POST", body: json(body) }),
  setPartnerStatus: (id: string, status: string, reason: string, expectedVersion: number) =>
    apiFetch(`${admin}/partners/${id}/status`, {
      method: "POST",
      body: json({ status, reason, expected_version: expectedVersion }),
    }),
  capabilities: () => apiFetch<{ results: CapabilityInfo[] }>(`${admin}/partners/capabilities`),
  grantCapability: (id: string, capability: string, reason: string) =>
    apiFetch(`${admin}/partners/${id}/capabilities`, { method: "POST", body: json({ capability, reason }) }),
  revokeCapability: (id: string, capability: string, reason: string) =>
    apiFetch(`${admin}/partners/${id}/capabilities/revoke`, { method: "POST", body: json({ capability, reason }) }),
  addMember: (id: string, email: string, role: string) =>
    apiFetch(`${admin}/partners/${id}/members`, { method: "POST", body: json({ email, role }) }),
  removeMember: (id: string, memberId: string) =>
    apiFetch(`${admin}/partners/${id}/members/${memberId}/remove`, { method: "POST", body: json({}) }),
  revokeProductGrant: (id: string, grantId: string) =>
    apiFetch(`${admin}/partners/${id}/product-grants/${grantId}/revoke`, { method: "POST", body: json({}) }),
  domains: (id: string) => apiFetch<{ results: DomainRow[] }>(`${admin}/partners/${id}/domains`),
  disableDomain: (domainId: string) =>
    apiFetch<DomainRow>(`${admin}/domains/${domainId}/disable`, { method: "POST", body: json({}) }),
  grantProduct: (id: string, productCode: string, planCode?: string) =>
    apiFetch(`${admin}/partners/${id}/product-grants`, {
      method: "POST",
      body: json({ product_code: productCode, plan_code: planCode || undefined }),
    }),

  rules: () => apiFetch<{ results: RuleRow[] }>(`${admin}/commission/rules`),
  createRule: (body: { code: string; rate_bp: number; reason: string; product_code?: string; partner_id?: string }) =>
    apiFetch(`${admin}/commission/rules`, { method: "POST", body: json(body) }),
  /** `unpaid: "1"` keeps only entries that are in no live payout. */
  accruals: (q: { partner_id?: string; cursor?: string; unpaid?: string }) =>
    apiFetch<Page<AccrualRow>>(`${admin}/commission/accruals`, { query: q }),
  reconcileCommissions: () => apiFetch<Reconciliation>(`${admin}/commission/reconcile`),

  payouts: (q: { status?: string; cursor?: string; partner_id?: string }) =>
    apiFetch<Page<PayoutRow>>(`${admin}/payouts`, { query: q }),
  preparePayout: (partnerId: string, periodEnd: string) =>
    apiFetch<PayoutRow>(`${admin}/payouts`, {
      method: "POST",
      headers: idem(newKey()),
      body: json({ partner_id: partnerId, period_end: periodEnd }),
    }),
  payoutAction: (id: string, action: "ready" | "approve" | "paid" | "cancel", version: number, extra: Record<string, string>) =>
    apiFetch<PayoutRow>(`${admin}/payouts/${id}/${action}`, {
      method: "POST",
      body: json({ expected_version: version, ...extra }),
    }),

  securityGrants: () =>
    apiFetch<{
      results: { id: string; email: string; permission: string; granted_at: string; expires_at: string | null; reason: string }[];
    }>(`${admin}/security/grants`),
  grantPermission: (email: string, permission: string, reason: string, expiresAt?: string) =>
    apiFetch(`${admin}/security/grants`, {
      method: "POST",
      body: json({ email, permission, reason, expires_at: expiresAt || undefined }),
    }),
  revokePermission: (id: string, reason: string) =>
    apiFetch(`${admin}/security/grants/${id}/revoke`, { method: "POST", body: json({ reason }) }),
};

export interface Dashboard {
  organizations: Record<string, number>;
  subscriptions: Record<string, number>;
  mrr: { available: boolean; reason: string };
  outbox: Record<string, number>;
  partners?: Record<string, number>;
  pending_payouts?: { status: string; currency: string; count: number; payable_minor: number }[];
}

export interface Reconciliation {
  organizations: number;
  is_clean: boolean;
  missing: unknown[];
  duplicates: unknown[];
  wrong: unknown[];
  unexpected: unknown[];
  skipped: Record<string, number>;
}

// --- partner portal ----------------------------------------------------------

export interface PortalPartner {
  partner_id: string;
  code: string;
  display_name: string;
  status: string;
  role: string;
  is_admin: boolean;
  capabilities: string[];
}

export interface PartnerCustomerRow {
  organization_id: string;
  name: string;
  organization_status: string;
  link_status: string;
  started_at: string;
  ended_at: string | null;
  subscription_status: string;
  products: string[];
  owner_activation: PortalOwnerActivation;
}

/** What a partner sees of its customer's owner activation: the state and its dates -
 * never the address the link went to, the link, or anything that could redeem one. */
export interface PortalOwnerActivation {
  activation: "pending" | "activated" | "expired" | "revoked" | "declined" | "not_applicable";
  expires_at: string | null;
  send_count: number;
}

export interface ApprovedPlan {
  plan_version_id: string;
  plan_code: string;
  plan_name: string;
  product_code: string;
  product_name: string;
  included_seats: number;
  trial_days: number;
  price: { unit_amount_minor: number | null; total_amount_minor: number; currency: string; interval: string; source: string } | null;
}

export interface Brand {
  brand_name: string;
  logo_file_id: string | null;
  accent_color: string;
  theme: Record<string, string>;
  support_email: string;
  support_url: string;
  version: number;
}

export interface DomainRow {
  id: string;
  hostname: string;
  status: string;
  verification_record: { type: string; name: string; value: string };
  failure_code: string;
  version: number;
}

const partner = (id: string) => `/api/v1/partner/${encodeURIComponent(id)}`;

export const partnerApi = {
  portal: () => apiFetch<{ partners: PortalPartner[] }>("/api/v1/partner/portal"),
  customers: (id: string, cursor?: string) =>
    apiFetch<Page<PartnerCustomerRow>>(`${partner(id)}/customers`, { query: { cursor } }),
  provision: (id: string, organizationName: string, ownerEmail: string, key: string) =>
    apiFetch<{ organization_id: string; slug: string; replayed: boolean }>(`${partner(id)}/customers`, {
      method: "POST",
      headers: idem(key),
      body: json({ organization_name: organizationName, owner_email: ownerEmail }),
    }),
  catalog: (id: string) => apiFetch<{ results: ApprovedPlan[] }>(`${partner(id)}/catalog`),
  sell: (id: string, orgId: string, planVersionId: string, quantity: number, key: string) =>
    apiFetch(`${partner(id)}/customers/${orgId}/sell`, {
      method: "POST",
      headers: idem(key),
      body: json({ plan_version_id: planVersionId, quantity }),
    }),
  diagnose: (id: string, orgId: string, productCode: string, memberEmail?: string) =>
    apiFetch<Diagnosis>(`${partner(id)}/customers/${orgId}/diagnose`, {
      method: "POST",
      body: json({ product_code: productCode, member_email: memberEmail || undefined }),
    }),
  commissions: (id: string, cursor?: string) =>
    apiFetch<Page<AccrualRow> & { balances: { currency: string; earned_minor: number; adjustments_minor: number; balance_minor: number }[] }>(
      `${partner(id)}/commissions`,
      { query: { cursor } },
    ),
  payouts: (id: string, cursor?: string) => apiFetch<Page<PayoutRow>>(`${partner(id)}/payouts`, { query: { cursor } }),
  referrals: (id: string) =>
    apiFetch<{
      counts: Record<string, number>;
      codes: { id: string; code: string; label: string; status: string }[];
    }>(`${partner(id)}/referrals`),
  createReferralCode: (id: string, label: string) =>
    apiFetch(`${partner(id)}/referrals`, { method: "POST", body: json({ label }) }),
  brand: (id: string) => apiFetch<{ brand: Brand | null }>(`${partner(id)}/branding`),
  saveBrand: (id: string, body: Partial<Omit<Brand, "logo_file_id">> & { brand_name: string; logo_file_id?: string }, expectedVersion?: number) =>
    apiFetch<Brand>(`${partner(id)}/branding`, {
      method: "PUT",
      body: json({ ...body, expected_version: expectedVersion }),
    }),
  uploadLogo: (id: string, file: File) =>
    apiFetch<{ logo_file_id: string }>(`${partner(id)}/branding/logo`, {
      method: "POST",
      headers: { "Content-Type": file.type },
      body: file,
    }),
  domains: (id: string) => apiFetch<{ results: DomainRow[] }>(`${partner(id)}/domains`),
  requestDomain: (id: string, hostname: string) =>
    apiFetch<DomainRow>(`${partner(id)}/domains`, { method: "POST", body: json({ hostname }) }),
  domainAction: (id: string, domainId: string, action: "verify" | "activate" | "disable") =>
    apiFetch<DomainRow>(`${partner(id)}/domains/${domainId}/${action}`, { method: "POST", body: json({}) }),
};

// --- account-level ------------------------------------------------------------

export const invitationApi = {
  list: () => apiFetch<{ results: { organization_id: string; organization_name: string; expires_at: string }[] }>("/api/v1/invitations"),
  accept: (organizationId: string) =>
    apiFetch(`/api/v1/invitations/${organizationId}/accept`, { method: "POST", body: json({}) }),
};

export interface OwnBrand {
  brand_name: string;
  accent_color: string;
  support_email: string;
  support_url: string;
  logo_url: string | null;
}

export const brandingApi = {
  forHost: (host: string) =>
    apiFetch<{ brand: (Omit<Brand, "logo_file_id" | "version"> & { logo_url: string | null }) | null }>(
      "/api/v1/public/branding",
      { query: { host } },
    ),
  /** Phase 11, Step 30: this organization's own white-label brand, resolved
   * through its live partner link - never a Host header. `null` for an
   * organization with no white-label partner. */
  own: (slug: string) => apiFetch<{ brand: OwnBrand | null }>(`/api/v1/orgs/${encodeURIComponent(slug)}/brand`),
};
