/**
 * Super Admin API additions (Phase 8 extension): the plan builder, payments,
 * users, communication and the network break-glass.
 *
 * Kept apart from `controlPlane.ts`, whose shapes the backend's contract tests
 * parse. The same rules apply: every call is re-authorized server-side against
 * the staff member's live grants, dangerous ones need a recent second factor
 * (`401 step_up_required`, handled by `useAction`), money is integer minor
 * units, and the browser never sends an amount it was not asked for.
 */

import { apiFetch } from "./client";
import type { Page } from "./controlPlane";

const json = (body: unknown): string => JSON.stringify(body);
const idem = (key: string): HeadersInit => ({ "Idempotency-Key": key });

// --- the plan builder -------------------------------------------------------------

export type BillingInterval = "monthly" | "quarterly" | "semi_annual" | "annual";
export type CatalogStatus = "draft" | "available" | "grandfathered" | "retired";

export const INTERVAL_LABELS: Record<string, string> = {
  monthly: "Monthly (30 days)",
  quarterly: "Quarterly (90 days)",
  semi_annual: "Bi-Annual (180 days)",
  annual: "Annual (360 days)",
  one_time: "One time",
};

export interface CatalogPrice {
  price_id: string;
  plan_version_id: string | null;
  bundle_version_id: string | null;
  price_book_id: string;
  price_book_code: string;
  channel: string;
  partner_id: string | null;
  currency: string;
  interval: string;
  model: string;
  amount_minor: number | null;
  valid_from: string;
}

export interface PlanVersionRow {
  id: string;
  version: number;
  published_at: string | null;
  effective_from: string;
  effective_to: string | null;
  included_seats: number;
  max_seats: number | null;
  trial_days: number;
  features: Record<string, unknown>;
  prices: CatalogPrice[];
}

export interface PlanRow {
  id: string;
  code: string;
  name: string;
  caption: string;
  description: string;
  status: CatalogStatus;
  is_public: boolean;
  sort_order: number;
  billing_type: "paid" | "free_trial";
  kind: "standard" | "add_on";
  product: { id: string; code: string; name: string };
  addon_for: { id: string; code: string } | null;
  versions: PlanVersionRow[];
}

export interface BundleRow {
  id: string;
  code: string;
  name: string;
  caption: string;
  description: string;
  status: CatalogStatus;
  is_public: boolean;
  kind: string;
  billing_type: "paid" | "free_trial";
  versions: {
    id: string;
    version: number;
    published_at: string | null;
    effective_to: string | null;
    trial_days: number;
    items: {
      plan_version_id: string;
      plan_code: string;
      plan_name: string;
      plan_version: number;
      product_code: string;
      product_name: string;
      quantity: number;
      is_optional: boolean;
    }[];
    prices: CatalogPrice[];
  }[];
}

export interface PriceBookRow {
  id: string;
  code: string;
  name: string;
  channel: string;
  partner_id: string | null;
  region: string;
  currency: string;
  priority: number;
  is_active: boolean;
  valid_from: string;
  valid_to: string | null;
}

export interface CatalogOverview {
  products: { id: string; code: string; name: string; status: string; licensing_mode: string }[];
  plans: PlanRow[];
  bundles: BundleRow[];
  price_books: PriceBookRow[];
  currencies: string[];
  intervals: BillingInterval[];
  billing_types: string[];
}

export interface FeatureRow {
  id: string;
  key: string;
  name: string;
  kind: "boolean" | "quota" | "enum";
  unit: string;
  default_value: unknown;
  enum_ordering: string[] | null;
}

// --- payments ------------------------------------------------------------------------

export interface PaymentProviderRow {
  id: string;
  key: string;
  adapter: "stripe" | "razorpay" | "fake";
  display_name: string;
  mode: "test" | "live";
  status: "enabled" | "disabled";
  currencies: string[];
  priority: number;
  version: number;
  credential_ref: string;
  webhook_secret_ref: string;
  credential_configured: boolean;
  webhook_secret_configured: boolean;
  credential_variable: string;
  webhook_secret_variable: string;
  webhook_path: string;
}

export interface PaymentEventRow {
  id: string;
  provider: string;
  event_id: string;
  kind: string;
  outcome: string;
  organization_id: string | null;
  attempt_id: string | null;
  amount_minor: number | null;
  currency: string;
  received_at: string;
}

export interface StaffRefund {
  refund_id: string;
  attempt_id: string;
  amount_minor: number;
  currency: string;
  status: string;
  reason: string;
  failure_code: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface StaffAttempt {
  attempt_id: string;
  invoice_id: string;
  status: string;
  amount_minor: number;
  currency: string;
  provider: string;
  checkout_url: string | null;
  failure_code: string | null;
  expires_at: string;
  amount_refunded_minor: number;
  created_at: string;
  refunds: StaffRefund[];
}

export interface StaffInvoice {
  invoice_id: string;
  number: string;
  status: "open" | "paid" | "void";
  currency: string;
  total_minor: number;
  amount_paid_minor: number;
  amount_refunded_minor: number;
  amount_due_minor: number;
  billing_interval: string;
  period_start: string;
  period_end: string;
  issued_at: string;
  due_at: string;
  paid_at: string | null;
  dunning_stage: string | null;
  version: number;
  attempts: StaffAttempt[];
}

// --- users -----------------------------------------------------------------------------

/** Phase 9: one identity, classified by what its organizations hold. */
export type Lifecycle = "explorer" | "trial" | "customer" | "unverified";

/** The directory's segments; "all" is no segment. */
export const USER_SEGMENTS = [
  { key: "", label: "All users" },
  { key: "customers", label: "Subscribed" },
  { key: "free", label: "Free tier" },
  { key: "trial", label: "Trial" },
  { key: "explorers", label: "Explorers" },
  { key: "unverified", label: "Unverified" },
  { key: "suspended", label: "Suspended" },
  { key: "deactivated", label: "Deactivated" },
] as const;

export interface UserRow {
  user_id: string;
  email: string;
  name: string;
  status: "active" | "disabled" | "locked" | "pending";
  is_staff: boolean;
  locked: boolean;
  mfa_enrolled: boolean;
  organizations: number | null;
  email_verified: boolean;
  acquisition_source: string;
  created_at: string;
  last_sign_in_at: string | null;
  lifecycle: Lifecycle;
  products: string[];
  subscription_status: string;
  organization_names: string[];
}

export interface StaffOrderRow {
  order_id: string;
  organization_id: string;
  organization_name: string;
  number: string;
  kind: string;
  status: string;
  description: string;
  product_codes: string[];
  currency: string;
  amount_due_minor: number;
  created_at: string;
  paid_at: string | null;
}

export interface PlatformSettings {
  signup_enabled: boolean;
  support_email: string;
  sales_email: string;
  catalog_currency: string;
  public_catalog_currency: string;
  default_currency: string;
  currencies: string[];
  version: number;
}

export interface StaffRoleRow {
  key: string;
  name: string;
  description: string;
  permissions: string[];
  holders: { user_id: string; email: string }[];
}

export interface UserDetail extends Omit<UserRow, "lifecycle" | "products" | "subscription_status" | "organization_names"> {
  lifecycle: Lifecycle;
  orders: {
    order_id: string;
    organization_id: string;
    number: string;
    status: string;
    description: string;
    currency: string;
    amount_due_minor: number;
    created_at: string;
  }[];
  memberships: {
    organization_id: string;
    slug: string;
    name: string;
    is_owner: boolean;
    access_class: string;
    subscription_status: string;
    products: string[];
  }[];
  sessions: {
    session_id: string;
    signed_in_at: string;
    last_active_at: string;
    expires_at: string;
    ip_prefix: string;
    device_label: string;
  }[];
  activity: { occurred_at: string; kind: string; reason: string; ip_prefix: string; device_label: string }[];
}

export type UserAction = "sign-out" | "unlock" | "disable" | "enable";

// --- communication ------------------------------------------------------------------------

export interface TemplateRow {
  key: string;
  description: string;
  variables: string[];
  required: string[];
  default_subject: string;
  default_body_text: string;
  override: { subject: string; body_text: string; version: number; updated_at: string } | null;
}

export interface SenderSettings {
  sender_name: string;
  reply_to: string;
  from_address: string;
  version: number;
}

export const adminConsoleApi = {
  // plan builder
  catalogOverview: (product?: string) =>
    apiFetch<CatalogOverview>("/api/v1/admin/catalog/overview", { query: { product } }),
  productFeatures: (productId: string) =>
    apiFetch<{ results: FeatureRow[] }>(`/api/v1/admin/catalog/products/${productId}/features`),
  createPlan: (body: {
    product_id: string;
    code: string;
    name: string;
    caption?: string;
    description?: string;
    is_public?: boolean;
    billing_type?: "paid" | "free_trial";
    kind?: "standard" | "add_on";
    addon_for_product_id?: string | null;
  }) => apiFetch<{ id: string; code: string }>("/api/v1/admin/catalog/plans", { method: "POST", body: json(body) }),
  updatePlan: (
    planId: string,
    body: Partial<{ name: string; caption: string; description: string; is_public: boolean; sort_order: number; billing_type: string }>,
  ) => apiFetch<PlanRow>(`/api/v1/admin/catalog/plans/${planId}`, { method: "PATCH", body: json(body) }),
  setPlanStatus: (planId: string, status: CatalogStatus) =>
    apiFetch<{ id: string; status: string }>(`/api/v1/admin/catalog/plans/${planId}/status`, {
      method: "POST",
      body: json({ status }),
    }),
  draftPlanVersion: (body: {
    plan_id: string;
    included_seats: number;
    max_seats: number | null;
    trial_days: number;
    features?: Record<string, unknown>;
    publish?: boolean;
  }) =>
    apiFetch<{ id: string; version: number; published: boolean }>("/api/v1/admin/catalog/plan-versions", {
      method: "POST",
      body: json(body),
    }),
  publishPlanVersion: (versionId: string) =>
    apiFetch<{ id: string; version: number; published: boolean }>(
      `/api/v1/admin/catalog/plan-versions/${versionId}/publish`,
      { method: "POST" },
    ),
  createBundle: (body: {
    code: string;
    name: string;
    caption?: string;
    description?: string;
    is_public?: boolean;
    billing_type?: "paid" | "free_trial";
  }) => apiFetch<{ id: string; code: string }>("/api/v1/admin/catalog/bundles", { method: "POST", body: json(body) }),
  updateBundle: (
    bundleId: string,
    body: Partial<{ name: string; caption: string; description: string; is_public: boolean; billing_type: string }>,
  ) => apiFetch<BundleRow>(`/api/v1/admin/catalog/bundles/${bundleId}`, { method: "PATCH", body: json(body) }),
  setBundleStatus: (bundleId: string, status: CatalogStatus) =>
    apiFetch<{ id: string; status: string }>(`/api/v1/admin/catalog/bundles/${bundleId}/status`, {
      method: "POST",
      body: json({ status }),
    }),
  draftBundleVersion: (body: {
    bundle_id: string;
    items: { plan_version_id: string; quantity: number; is_optional?: boolean }[];
    trial_days?: number;
    publish?: boolean;
  }) =>
    apiFetch<{ id: string; version: number; published: boolean }>("/api/v1/admin/catalog/bundle-versions", {
      method: "POST",
      body: json(body),
    }),
  publishBundleVersion: (versionId: string) =>
    apiFetch<{ id: string; version: number; published: boolean }>(
      `/api/v1/admin/catalog/bundle-versions/${versionId}/publish`,
      { method: "POST" },
    ),
  createPriceBook: (body: { code: string; name: string; currency: string; region?: string; priority?: number }) =>
    apiFetch<{ id: string; code: string }>("/api/v1/admin/catalog/price-books", { method: "POST", body: json(body) }),
  publishPrice: (body: {
    price_book_id: string;
    plan_version_id?: string;
    bundle_version_id?: string;
    interval: string;
    amount_minor: number;
    price_model?: "flat" | "per_seat";
  }) =>
    apiFetch<{ id: string; amount_minor: number }>("/api/v1/admin/catalog/prices", {
      method: "POST",
      body: json({ price_model: "flat", ...body }),
    }),

  // payments
  paymentProviders: () => apiFetch<{ results: PaymentProviderRow[] }>("/api/v1/admin/payments/providers"),
  createPaymentProvider: (body: {
    key: string;
    adapter: string;
    display_name: string;
    mode: "test" | "live";
    credential_ref: string;
    webhook_secret_ref: string;
    currencies: string[];
    priority?: number;
  }) => apiFetch<PaymentProviderRow>("/api/v1/admin/payments/providers", { method: "POST", body: json(body) }),
  updatePaymentProvider: (key: string, body: Record<string, unknown> & { version: number }) =>
    apiFetch<PaymentProviderRow>(`/api/v1/admin/payments/providers/${key}`, { method: "PATCH", body: json(body) }),
  paymentEvents: () => apiFetch<{ results: PaymentEventRow[] }>("/api/v1/admin/payments/events"),
  customerBilling: (organizationId: string) =>
    apiFetch<{ invoices: StaffInvoice[] }>(`/api/v1/admin/customers/${organizationId}/billing`),
  markInvoicePaid: (organizationId: string, invoiceId: string, body: { reference: string; reason: string; version: number }) =>
    apiFetch<StaffInvoice>(`/api/v1/admin/customers/${organizationId}/invoices/${invoiceId}/mark-paid`, {
      method: "POST",
      body: json(body),
    }),
  voidInvoice: (organizationId: string, invoiceId: string, body: { reason: string; version: number }) =>
    apiFetch<StaffInvoice>(`/api/v1/admin/customers/${organizationId}/invoices/${invoiceId}/void`, {
      method: "POST",
      body: json(body),
    }),
  refund: (organizationId: string, attemptId: string, body: { amount_minor: number; reason: string }, key: string) =>
    apiFetch<StaffRefund>(`/api/v1/admin/customers/${organizationId}/payments/${attemptId}/refund`, {
      method: "POST",
      headers: idem(key),
      body: json(body),
    }),
  resetNetworkPolicy: (organizationId: string, reason: string) =>
    apiFetch<{ organization_id: string; ip_allowlist_enabled: boolean }>(
      `/api/v1/admin/customers/${organizationId}/network-policy/reset`,
      { method: "POST", body: json({ reason }) },
    ),

  // users
  users: (opts: { q?: string; segment?: string; cursor?: string } = {}) =>
    apiFetch<Page<UserRow>>("/api/v1/admin/users", {
      query: { q: opts.q, segment: opts.segment, cursor: opts.cursor },
    }),
  user: (userId: string) => apiFetch<UserDetail>(`/api/v1/admin/users/${userId}`),
  userAction: (userId: string, action: UserAction, reason: string) =>
    apiFetch<UserDetail>(`/api/v1/admin/users/${userId}/${action}`, { method: "POST", body: json({ reason }) }),

  // communication
  templates: () => apiFetch<{ results: TemplateRow[] }>("/api/v1/admin/communication/templates"),
  saveTemplate: (key: string, body: { subject: string; body_text: string; version: number | null }) =>
    apiFetch<TemplateRow>(`/api/v1/admin/communication/templates/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: json(body),
    }),
  resetTemplate: (key: string, version: number) =>
    apiFetch<TemplateRow>(`/api/v1/admin/communication/templates/${encodeURIComponent(key)}`, {
      method: "DELETE",
      body: json({ version }),
    }),
  previewTemplate: (key: string, body: { subject?: string; body_text?: string } = {}) =>
    apiFetch<{ subject: string; body_text: string }>(
      `/api/v1/admin/communication/templates/${encodeURIComponent(key)}/preview`,
      { method: "POST", body: json(body) },
    ),
  sendTestTemplate: (key: string) =>
    apiFetch<{ queued: boolean; to: string }>(
      `/api/v1/admin/communication/templates/${encodeURIComponent(key)}/test`,
      { method: "POST" },
    ),
  // Phase 9: orders, platform settings, staff roles, billing terms
  orders: (opts: { status?: string; cursor?: string } = {}) =>
    apiFetch<Page<StaffOrderRow>>("/api/v1/admin/orders", { query: { status: opts.status, cursor: opts.cursor } }),
  settings: () => apiFetch<PlatformSettings>("/api/v1/admin/settings"),
  saveSettings: (body: {
    signup_enabled: boolean;
    support_email: string;
    sales_email: string;
    public_catalog_currency: string;
    version: number;
  }) => apiFetch<PlatformSettings>("/api/v1/admin/settings", { method: "PUT", body: json(body) }),
  roles: () => apiFetch<{ results: StaffRoleRow[] }>("/api/v1/admin/security/roles"),
  grantRole: (body: { email: string; role: string; reason: string }) =>
    apiFetch<{ user_id: string; role: string; added: string[] }>("/api/v1/admin/security/roles", {
      method: "POST",
      body: json(body),
    }),
  revokeRole: (body: { user_id: string; role: string; reason: string }) =>
    apiFetch<{ user_id: string; role: string; ended: string[] }>("/api/v1/admin/security/roles/revoke", {
      method: "POST",
      body: json(body),
    }),
  setBillingTerms: (organizationId: string, body: { collection_mode: string; billing_region: string; reason: string }) =>
    apiFetch<{ collection_mode: string; billing_region: string }>(
      `/api/v1/admin/customers/${organizationId}/billing-terms`,
      { method: "POST", body: json(body) },
    ),
  sender: () => apiFetch<SenderSettings>("/api/v1/admin/communication/sender"),
  saveSender: (body: { sender_name: string; reply_to: string; version: number }) =>
    apiFetch<SenderSettings>("/api/v1/admin/communication/sender", { method: "PUT", body: json(body) }),
};
