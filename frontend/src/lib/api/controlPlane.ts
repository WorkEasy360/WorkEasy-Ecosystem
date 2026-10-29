/**
 * The Super Admin and partner-portal API clients (Phase 7), and their wire types.
 *
 * Two facts shape every function here:
 *
 * 1. **The backend is the authority.** Nothing below decides who may do what;
 *    the calls simply fail with 403 or 404 and the
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

import { apiFetch, apiUpload } from "./client";

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
  /** Which workspace the portal opens first (or empty for "what the grants earn
   * first"). A presentation preference: it decides no permission. */
  default_experience: string;
  version: number;
  created_at: string;
  activated_at: string | null;
  status_reason: string;
}

/** One person who acts for a partner. `invitation_sent_at` null with no password
 * means "named, not yet invited". */
export interface PartnerMemberRow {
  id: string;
  user_id: string;
  email: string;
  role: string;
  added_at: string;
  password_set: boolean;
  full_name: string;
  last_login_at: string | null;
  invitation_sent_at: string | null;
  /** True for someone whose setup link will be sent when the partner is first
   * activated. Null `invitation_sent_at` with this false and no password is a
   * person attached before invitations were tracked: show "Invitation pending". */
  invitation_deferred: boolean;
}

export interface PartnerDetail extends PartnerSummary {
  capabilities: { capability: string; granted_at: string; expires_at: string | null; is_effective: boolean }[];
  members: PartnerMemberRow[];
  product_grants: { id: string; product_code: string; plan_code: string | null; trial_allowed: boolean; portal_visible: boolean }[];
  live_customers: number;
  /** Derived by the server from the grants in force ("Reseller", ...). */
  experience: { key: string; label: string }[];
  /** The workspaces those grants open, and the one the portal lands on. Optional
   * only so a screen survives an answer from the release before this one. */
  workspaces?: { key: string; label: string }[];
  landing?: string | null;
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
  /** ADR-052: also earns on each paid renewal of the sale. */
  recurring: boolean;
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
  category?: string;
  icon?: string;
  // ADR-051: the store listing and where the product may be offered.
  store_title?: string;
  store_summary?: string;
  application_url?: string;
  documentation_url?: string;
  support_url?: string;
  customer_store_enabled?: boolean;
  reseller_enabled?: boolean;
  affiliate_enabled?: boolean;
  white_label_enabled?: boolean;
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
  // --- ADR-044: the Authentication section. Never a secret. ---------------
  manifest_version: string;
  sign_in_mode: "sso_only" | "sso_optional" | "local_only";
  login_url: string;
  post_logout_redirect_uris: string[];
  scopes: string[];
  token_endpoint_auth_method: string;
  sso_state: "enabled" | "integration_required" | "disabled";
}

/** The console fields an integration may be registered with or changed to. */
export type IntegrationChanges = Partial<
  Pick<
    ConsoleIntegration,
    | "display_name"
    | "launch_url"
    | "health_url"
    | "redirect_uris"
    | "contact_email"
    | "sign_in_mode"
    | "login_url"
    | "post_logout_redirect_uris"
    | "scopes"
  >
>;

/** A product credential by its public reference (ADR-044) - a secret is
 * never stored, so it can never be listed. */
export interface ConsoleCredential {
  credential_id: string;
  kind: "public_key" | "client_secret";
  status: "active" | "revoked";
  algorithm: string;
  label: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_on: string | null;
  created_at: string | null;
}

/** A credential just added. `client_secret` is present once, in this
 * response only, for a generated secret. */
export interface IssuedConsoleCredential {
  credential_id: string;
  kind: "public_key" | "client_secret";
  expires_at: string;
  client_secret?: string;
  notice?: string;
}

/** One listing image (ADR-051). The bytes are served same-origin by
 * {@link productMediaUrl}; a file id never reaches the browser. */
export interface ProductMediaRow {
  id: string;
  kind: "logo" | "banner" | "screenshot";
  width: number;
  height: number;
  content_type: string;
  size_bytes: number;
  caption: string;
  sort_order: number;
}

export interface ReadinessItem {
  key: string;
  label: string;
  ok: boolean;
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
  /** ADR-051: the three states the Product Manager shows. */
  manager_status: "draft" | "active" | "archived";
  listing: "listed" | "coming_soon" | "hidden";
  availability: "draft" | "coming_soon" | "available" | "private" | "disabled";
  licensing_mode: string;
  sort_order: number;
  marketing_url: string;
  store_title: string;
  store_summary: string;
  application_url: string;
  documentation_url: string;
  support_url: string;
  customer_store_enabled: boolean;
  reseller_enabled: boolean;
  affiliate_enabled: boolean;
  white_label_enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
  activated_at: string | null;
  logo_media_id: string | null;
  media: ProductMediaRow[];
  /** Where a holder's "Open" goes (the integration's launch URL), or "". */
  launch_url: string;
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
  /** The categories the Product Manager offers (server configuration). */
  categories: string[];
}

/** One product whole (ADR-051): the console's view plus its publish checklist. */
export interface ManagedProduct {
  product: ConsoleProduct & { readiness: ReadinessItem[] };
  categories: string[];
  can_see_integrations: boolean;
  can_see_activity: boolean;
}

/** What the Add Product wizard sends. `code` is derived from the slug by the server. */
export interface NewProduct {
  name: string;
  slug: string;
  category?: string;
  tagline?: string;
  icon?: string;
  description?: string;
  marketing_url?: string;
  store_title?: string;
  store_summary?: string;
  application_url?: string;
  documentation_url?: string;
  support_url?: string;
  customer_store_enabled?: boolean;
  reseller_enabled?: boolean;
  affiliate_enabled?: boolean;
  white_label_enabled?: boolean;
  integration?: { launch_url: string; sso_enabled: boolean; redirect_uri?: string; health_url?: string } | null;
  trial?: { enabled: boolean; duration_days: number } | null;
}

export interface ProductActivityRow {
  id: string;
  occurred_at: string;
  action: string;
  label: string;
  summary: string;
  actor: string;
}

/** The same-origin address of one listing image (staff only). */
export function productMediaUrl(productId: string, mediaId: string): string {
  return `${admin}/products/${productId}/media/${mediaId}`;
}

export interface CapabilityInfo {
  key: string;
  label: string;
  /** The plain short name a setup form lists (`Sell products`); `label` is the full sentence. */
  short_label: string;
  /** Phase 10: the heading a form lists it under (`customer_management`, ...). */
  group: string;
}

// --- admin ----------------------------------------------------------------

const admin = "/api/v1/admin";

export const adminApi = {
  session: () => apiFetch<{ is_staff: boolean; permissions: string[] }>(`${admin}/session`),
  /** `month` is `YYYY-MM` (default: this month) - the overview's month. */
  dashboard: (month?: string) => apiFetch<Dashboard>(`${admin}/dashboard`, { query: { month } }),
  /** What is waiting for staff - the console's notification bell. */
  attention: () => apiFetch<{ items: AttentionItem[] }>(`${admin}/attention`),
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

  // --- the Product Manager (ADR-051) -------------------------------------------------

  /** The Add Product wizard: a draft, hidden product with its integration and trial. */
  createManagedProduct: (body: NewProduct) =>
    apiFetch<ManagedProduct>(`${admin}/products`, { method: "POST", body: json(body) }),
  managedProduct: (id: string) => apiFetch<ManagedProduct>(`${admin}/products/${id}`),
  productLifecycle: (id: string, action: "publish" | "unpublish" | "archive" | "restore") =>
    apiFetch<ManagedProduct>(`${admin}/products/${id}/${action}`, { method: "POST" }),
  deleteProduct: (id: string) => apiFetch<{ deleted: boolean; code: string }>(`${admin}/products/${id}`, { method: "DELETE" }),
  productActivity: (id: string, cursor?: string) =>
    apiFetch<Page<ProductActivityRow>>(`${admin}/products/${id}/activity`, { query: { cursor } }),
  uploadProductMedia: (
    id: string,
    kind: ProductMediaRow["kind"],
    file: File,
    options: { caption?: string; onProgress?: (percent: number) => void } = {},
  ) =>
    apiUpload<ProductMediaRow>(`${admin}/products/${id}/media`, file, {
      query: { kind, caption: options.caption || undefined },
      onProgress: options.onProgress,
    }),
  captionProductMedia: (id: string, mediaId: string, caption: string) =>
    apiFetch<ProductMediaRow>(`${admin}/products/${id}/media/${mediaId}`, { method: "PATCH", body: json({ caption }) }),
  deleteProductMedia: (id: string, mediaId: string) =>
    apiFetch<{ deleted: boolean }>(`${admin}/products/${id}/media/${mediaId}`, { method: "DELETE" }),
  reorderProductMedia: (id: string, order: string[]) =>
    apiFetch<{ media: ProductMediaRow[] }>(`${admin}/products/${id}/media/reorder`, { method: "POST", body: json({ order }) }),
  setTrialConfig: (productId: string, config: TrialConfig) =>
    apiFetch<TrialConfig>(`${admin}/catalog/products/${productId}/trial`, { method: "PUT", body: json(config) }),
  updateIntegration: (productCode: string, changes: IntegrationChanges) =>
    apiFetch<ConsoleIntegration>(`${admin}/products/${productCode}/integration`, { method: "PATCH", body: json(changes) }),
  registerIntegration: (productCode: string, spec: IntegrationChanges) =>
    apiFetch<ConsoleIntegration>(`${admin}/products/${productCode}/integration`, { method: "POST", body: json(spec) }),
  integrationCredentials: (productCode: string) =>
    apiFetch<{ credentials: ConsoleCredential[] }>(`${admin}/products/${productCode}/integration/credentials`),
  addIntegrationCredential: (productCode: string, body: { kind: "public_key" | "client_secret"; public_key_pem?: string; label?: string }) =>
    apiFetch<IssuedConsoleCredential>(`${admin}/products/${productCode}/integration/credentials`, { method: "POST", body: json(body) }),
  revokeIntegrationCredential: (productCode: string, credentialId: string, reason: string) =>
    apiFetch<{ credential_id: string; status: string }>(
      `${admin}/products/${productCode}/integration/credentials/${encodeURIComponent(credentialId)}?reason=${encodeURIComponent(reason)}`,
      { method: "DELETE" },
    ),
  setIntegrationStatus: (productCode: string, status: string, reason: string) =>
    apiFetch<ConsoleIntegration>(`${admin}/products/${productCode}/integration/status`, {
      method: "POST",
      body: json({ status, reason }),
    }),
  checkIntegrationHealth: (productCode: string) =>
    apiFetch<{ status: string; detail: string }>(`${admin}/products/${productCode}/integration/health-check`, {
      method: "POST",
    }),

  partners: (q: { status?: string; q?: string; cursor?: string; limit?: number }) =>
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
  /** Guest login: open the partner's portal read-only, for a reason, for a limited time. */
  startGuestView: (id: string, reason: string) =>
    apiFetch<GuestView & { portal_path: string }>(`${admin}/partners/${id}/guest-view`, {
      method: "POST",
      body: json({ reason }),
    }),
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
  grantProduct: (id: string, productCode: string, planCode?: string, options: { trial_allowed?: boolean; portal_visible?: boolean } = {}) =>
    apiFetch(`${admin}/partners/${id}/product-grants`, {
      method: "POST",
      body: json({ product_code: productCode, plan_code: planCode || undefined, ...options }),
    }),

  rules: () => apiFetch<{ results: RuleRow[] }>(`${admin}/commission/rules`),
  createRule: (body: { code: string; rate_bp: number; reason: string; product_code?: string; partner_id?: string; recurring?: boolean }) =>
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
  overview: DashboardOverview;
}

/** The dashboard's month (UTC days): figures for the month, a value per day for
 * the charts (`null` for days still to come) and the period it is compared with.
 * Each section is present only when the staff member holds its screen's grant. */
export interface DashboardOverview {
  month: string;
  first_day: string;
  last_day: string;
  compared_with: { first_day: string; last_day: string };
  users?: { total: number; new: number; previous: number; daily: (number | null)[] };
  partners?: { total: number; new: number };
  products?: { total: number; available: number; new: number };
  orders?: { paid: number; previous: number; by_product: { code: string; name: string; orders: number }[] };
  revenue?: {
    currency: string;
    amount_minor: number;
    payments: number;
    previous_minor: number;
    daily: (number | null)[];
  }[];
}

export interface AttentionItem {
  key: "partner_upgrades" | "draft_partners" | "payouts";
  count: number;
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
  /** "Partner Owner", "Sales", ... - the server's wording for the role. */
  role_label?: string;
  is_admin: boolean;
  capabilities: string[];
  /** The workspaces these grants open (Reseller, White-label, Affiliate), in a
   * fixed order. Derived by the server on every read; a switcher is offered
   * only when there is more than one. */
  workspaces?: { key: string; label: string }[];
  /** The stored landing preference. It selects among `workspaces`; it opens none. */
  default_experience?: string;
  /** The workspace to open first, resolved by the server; null for a partner
   * whose grants earn none (the generic portal opens). */
  landing?: string | null;
  /** What the server will let this person do right now. Navigation is drawn
   * from it; every request is authorized again by the service it reaches. */
  permissions?: string[];
  /** Present when a staff member is viewing this partner as a guest (read-only). */
  guest?: GuestView;
}

/** A staff guest view of one partner's portal: read-only, time-boxed, audited. */
export interface GuestView {
  partner_id: string;
  reason: string;
  started_at?: string;
  expires_at: string;
}

export interface PartnerCustomerRow {
  organization_id: string;
  name: string;
  organization_status: string;
  link_status: string;
  started_at: string;
  ended_at: string | null;
  subscription_status: string;
  /** `customer` (a paid or granted product is live), `trial` or empty. */
  access_class?: string;
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
  portal: BrandPortalFields;
  version: number;
}

/** The customer-portal words and sender identity a brand stores (plain text). */
export interface BrandPortalFields {
  short_name: string;
  support_name: string;
  support_phone: string;
  website_url: string;
  /** ADR-053: the partner's own legal pages, linked from its branded Store. */
  terms_url?: string;
  privacy_url?: string;
  welcome_message: string;
  footer_text: string;
  sender_name: string;
  reply_to_email: string;
  login_show_welcome: boolean;
  login_show_support: boolean;
  use_for_invitations: boolean;
}

/** The part of the portal a customer's browser receives. */
export interface PublicPortalFields {
  short_name: string;
  support_name: string;
  support_phone: string;
  website_url: string;
  welcome_message: string;
  footer_text: string;
  login_show_welcome: boolean;
  login_show_support: boolean;
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
  endGuestView: () => apiFetch<{ ended: boolean }>("/api/v1/partner/guest-view/end", { method: "POST" }),
  customers: (id: string, cursor?: string, q?: string) =>
    apiFetch<Page<PartnerCustomerRow>>(`${partner(id)}/customers`, { query: { cursor, q: q || undefined } }),
  provision: (id: string, organizationName: string, ownerEmail: string, key: string, owner?: { first?: string; last?: string }) =>
    apiFetch<{ organization_id: string; slug: string; replayed: boolean }>(`${partner(id)}/customers`, {
      method: "POST",
      headers: idem(key),
      body: json({
        organization_name: organizationName,
        owner_email: ownerEmail,
        owner_first_name: owner?.first || undefined,
        owner_last_name: owner?.last || undefined,
      }),
    }),
  catalog: (id: string) => apiFetch<{ results: ApprovedPlan[] }>(`${partner(id)}/catalog`),
  sell: (id: string, orgId: string, planVersionId: string, quantity: number, key: string) =>
    apiFetch(`${partner(id)}/customers/${orgId}/sell`, {
      method: "POST",
      headers: idem(key),
      body: json({ plan_version_id: planVersionId, quantity }),
    }),
  /** A related customer's subscription lines (ADR-053's Manage subscription). */
  customerSubscription: (id: string, orgId: string) =>
    apiFetch<CustomerSubscription[]>(`${partner(id)}/customers/${orgId}/subscription`),
  changeCustomerPlan: (id: string, orgId: string, itemId: string, toPlanVersionId: string, expectedVersion: number, key: string) =>
    apiFetch(`${partner(id)}/customers/${orgId}/items/${itemId}/change-plan`, {
      method: "POST",
      headers: idem(key),
      body: json({ to_plan_version_id: toPlanVersionId, expected_version: expectedVersion }),
    }),
  cancelCustomerSubscription: (id: string, orgId: string, subscriptionId: string, expectedVersion: number, key: string) =>
    apiFetch(`${partner(id)}/customers/${orgId}/subscriptions/${subscriptionId}/cancel`, {
      method: "POST",
      headers: idem(key),
      body: json({ expected_version: expectedVersion }),
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
      /** Everyone who followed a link, whatever became of them: the server's sum. */
      total: number;
      codes: { id: string; code: string; label: string; status: string }[];
      /** The newest referrals: a date, a campaign and a status - never a person. */
      referrals: Page<{
        id: string;
        code: string;
        label: string;
        status: string;
        captured_at: string;
        /** ADR-053: when the visitor created an account. */
        registered_at?: string | null;
        converted_at: string | null;
      }>;
    }>(`${partner(id)}/referrals`),
  createReferralCode: (id: string, label: string) =>
    apiFetch(`${partner(id)}/referrals`, { method: "POST", body: json({ label }) }),
  brand: (id: string) => apiFetch<{ brand: Brand | null }>(`${partner(id)}/branding`),
  saveBrand: (
    id: string,
    // ``portal`` is merged by the server, so a save may carry only the keys it changes.
    body: Partial<Omit<Brand, "logo_file_id" | "portal">> & { brand_name: string; logo_file_id?: string; portal?: Partial<BrandPortalFields> },
    expectedVersion?: number,
  ) =>
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
  portal: PublicPortalFields;
  /** What the partner itself has worded, by locale (ADR-046). */
  translations?: Record<string, Record<string, string>>;
}

export const brandingApi = {
  forHost: (host: string) =>
    apiFetch<{ brand: (Omit<Brand, "logo_file_id" | "version" | "portal"> & { logo_url: string | null; portal: PublicPortalFields; translations?: Record<string, Record<string, string>> }) | null }>(
      "/api/v1/public/branding",
      { query: { host } },
    ),
  /** Phase 11, Step 30: this organization's own white-label brand, resolved
   * through its live partner link - never a Host header. `null` for an
   * organization with no white-label partner. */
  own: (slug: string) => apiFetch<{ brand: OwnBrand | null }>(`/api/v1/orgs/${encodeURIComponent(slug)}/brand`),
};

/** One subscription of a partner's customer, as the partner may act on it. */
export interface CustomerSubscription {
  subscription_id: string;
  status: string;
  version: number;
  currency: string;
  billing_interval: string;
  current_period_end: string;
  cancel_at: string | null;
  items: {
    item_id: string;
    product_code: string;
    plan_code: string;
    bundle_code: string;
    quantity: number;
    is_derived: boolean;
    removed_at: string | null;
  }[];
}
