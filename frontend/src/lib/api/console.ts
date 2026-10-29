/**
 * The organization console and account API (Phase 8 extension).
 *
 * The same rules as every client here: the backend is the authority (a hidden
 * menu item is a convenience; every call is authorized again), the browser
 * sends selections and never authority, and money on screen is server-derived
 * integer minor units with a currency.
 */

import { apiFetch } from "./client";
import type { Page } from "./controlPlane";

const json = (body: unknown): string => JSON.stringify(body);
const org = (slug: string) => `/api/v1/orgs/${encodeURIComponent(slug)}`;

// --- who am I here --------------------------------------------------------------

export interface ConsoleAccess {
  organization: {
    organization_id: string;
    slug: string;
    display_name: string;
    status: string;
    write_allowed: boolean;
  };
  membership: { membership_id: string; is_owner: boolean; roles: string[] };
  permissions: string[];
  /** "admin": owners and administrators land on the dashboard; "member": on their apps. */
  console: "admin" | "member";
}

/** Codes the tenancy middleware answers with when the organization's own
 * access policy refuses a request (after membership was verified). */
export type AccessPolicyDenial =
  | "network_not_allowed"
  | "reauthentication_required"
  | "password_change_required";

export const ACCESS_POLICY_DENIALS: readonly AccessPolicyDenial[] = [
  "network_not_allowed",
  "reauthentication_required",
  "password_change_required",
];

// --- dashboard -------------------------------------------------------------------

export interface DashboardSummary {
  window_days: number;
  generated_at: string;
  organization: ConsoleAccess["organization"] | null;
  members?: {
    active_members: number;
    suspended_members: number;
    invited_members: number;
    members_added: number;
    pending_invitations: number;
    expired_invitations: number;
    invitations_accepted: number;
  };
  activity?: {
    series: { date: string; members: number; sign_ins: number }[];
    most_active: {
      membership_id: string;
      email: string;
      name: string;
      sign_ins: number;
      last_sign_in_at: string;
    }[];
  };
  apps?: { entitled: number };
  subscription?: {
    status: string;
    billing_interval: string;
    current_period_end: string;
    trial_ends_at: string | null;
    products: string[];
  } | null;
}

// --- members & invitations ----------------------------------------------------------

export type SystemRole = "owner" | "admin" | "billing_manager" | "member" | "readonly";

export const ASSIGNABLE_ROLES: { key: Exclude<SystemRole, "owner">; label: string }[] = [
  { key: "admin", label: "Administrator" },
  { key: "billing_manager", label: "Billing Manager" },
  { key: "member", label: "Member" },
  { key: "readonly", label: "Read Only" },
];

export interface MemberRow {
  membership_id: string;
  user_id: string;
  email: string;
  name: string;
  status: "active" | "invited" | "suspended" | "removed";
  is_owner: boolean;
  member_type: string;
  joined_at: string | null;
  added_at: string;
  last_sign_in_at: string | null;
  roles: string[];
}

export interface InvitationRow {
  invitation_id: string;
  email: string;
  role: string;
  state: "pending" | "expired" | "accepted" | "revoked";
  invited_by: string | null;
  created_at: string;
  expires_at: string;
  last_sent_at: string | null;
  send_count: number;
  accepted_at: string | null;
}

export interface AuditEntry {
  id: string;
  occurred_at: string;
  action: string;
  actor_kind: string;
  actor_user_id: string | null;
  resource_type: string;
  resource_id: string;
  outcome: string;
  reason: string;
  changes: Record<string, unknown> | null;
}

export interface SignInRow {
  occurred_at: string;
  membership_id: string;
  email: string;
  name: string;
  ip_prefix: string;
  device_label: string;
}

// --- security policy -------------------------------------------------------------------

export interface SecurityPolicy {
  session_max_age_minutes: number | null;
  password_min_length: number | null;
  ip_allowlist_enabled: boolean;
  ip_allowlist: string[];
  version: number;
  updated_at: string | null;
  platform_password_min_length: number;
  /** Present on the network endpoint only: where the caller is connecting from. */
  your_address?: string;
  your_network?: string;
}

// --- billing ---------------------------------------------------------------------------

export interface BillingAccount {
  id: string;
  currency: string;
  collection_mode: string;
  billing_email: string;
  billing_region: string;
}

export interface Invoice {
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
  /** Phase 9: the order this invoice charges for, when it is an order's. */
  order_id: string | null;
  version: number;
}

export interface PaymentAttempt {
  attempt_id: string;
  invoice_id: string;
  status: "pending" | "awaiting_payment" | "succeeded" | "failed" | "expired" | "cancelled";
  amount_minor: number;
  currency: string;
  provider: string;
  /** The provider's hosted payment page - only while it is the thing to do. */
  checkout_url: string | null;
  failure_code: string | null;
  expires_at: string;
}

export interface InvoiceDetail extends Invoice {
  lines: {
    description: string;
    product_code: string;
    plan_code: string;
    bundle_code: string;
    quantity: number;
    amount_minor: number;
  }[];
  attempts: PaymentAttempt[];
}

export interface ListPrice {
  interval: string;
  currency: string;
  model: string;
  amount_minor: number | null;
  from_tiers: boolean;
}

export interface MarketplaceOffers {
  currency: string | null;
  billing_account: boolean;
  held_products: string[];
  plans: {
    plan_version_id: string;
    plan_code: string;
    name: string;
    caption: string;
    description: string;
    billing_type: "paid" | "free_trial";
    kind: "standard" | "add_on";
    addon_for: string | null;
    product_code: string;
    product_name: string;
    product_slug: string;
    included_seats: number;
    max_seats: number | null;
    trial_days: number;
    prices: ListPrice[];
  }[];
  bundles: {
    bundle_version_id: string;
    bundle_code: string;
    name: string;
    caption: string;
    description: string;
    billing_type: "paid" | "free_trial";
    trial_days: number;
    includes: { product_code: string; product_name: string; plan_name: string; quantity: number }[];
    prices: ListPrice[];
  }[];
}

/** `GET /orgs/<slug>/subscriptions` (`commercial.subscription.view`): the
 * customer's view of its subscriptions. No price or plan-version ids - a
 * customer payload never carries a pricing row's id back to the browser. */
export interface OrgSubscription {
  id: string;
  status: string;
  currency: string;
  billing_interval: string;
  renewal_policy: string;
  current_period_start: string;
  current_period_end: string;
  trial_ends_at: string | null;
  cancel_at: string | null;
  version: number;
  items: {
    id: string;
    product_code: string;
    plan_code: string;
    /** The plan as people read it ("CRM Professional"); empty for a bundle line. */
    plan_name: string;
    bundle_code: string;
    quantity: number;
    added_at: string;
    removed_at: string | null;
  }[];
}

// --- orders (Phase 9: pay first) --------------------------------------------------------

/** What an order may ask for: identifiers and choices - never a price, a
 * currency or a status; the server derives every one. */
export interface OrderRequest {
  plan_version_id?: string;
  bundle_version_id?: string;
  activate_subscription_id?: string;
  declined_plan_version_ids?: string[];
  billing_interval?: string;
  quantity?: number;
  with_trial?: boolean;
  /** ADR-052: several products in one order - plan ids only, never a price. */
  cart_items?: { plan_version_id: string; quantity?: number }[];
  /** ADR-053: bought in a white-label partner's branded Store (its address,
   * e.g. `acme-partners`). The server verifies the store and prices from the
   * partner's book; nothing about the partner is trusted from here. */
  storefront?: string;
}

/** One product's line in a cart quote or order, priced by the server. */
export interface OrderLine {
  product_code: string;
  plan_name: string;
  quantity: number;
  recurring_amount_minor: number;
  amount_due_minor: number;
  subscription_item_id?: string | null;
}

export interface OrderQuote {
  kind: "plan" | "bundle" | "activation" | "cart";
  /** ADR-053: `direct`, or `storefront` when sold through a partner's branded Store. */
  channel?: string;
  description: string;
  product_codes: string[];
  currency: string;
  billing_interval: string;
  quantity: number;
  recurring_amount_minor: number;
  amount_due_minor: number;
  prorated: boolean;
  is_trial: boolean;
  trial_days: number;
  requires_payment: boolean;
  /** A cart's lines (empty for every other kind). */
  lines?: OrderLine[];
}

export type OrderStatus = "pending_payment" | "paid" | "fulfilled" | "cancelled" | "expired" | "failed";

export interface Order {
  order_id: string;
  number: string;
  kind: "plan" | "bundle" | "activation" | "cart";
  status: OrderStatus;
  description: string;
  product_codes: string[];
  currency: string;
  billing_interval: string;
  quantity: number;
  recurring_amount_minor: number;
  amount_due_minor: number;
  is_trial: boolean;
  requires_payment: boolean;
  invoice_id: string | null;
  invoice_number: string | null;
  created_at: string;
  expires_at: string | null;
  paid_at: string | null;
  fulfilled_at: string | null;
  cancelled_at: string | null;
  failure_reason: string;
  lines?: OrderLine[];
}

// --- product trials (Phase 11) ----------------------------------------------------------
//
// A separate, independent-per-product trial - not the order/checkout trial
// above, which is subscription-wide. Starting one commits nothing: no
// invoice, no plan-version id sent back to the browser.

export interface ProductTrial {
  id: string;
  product_code: string;
  plan_code: string;
  status: "active" | "converted" | "expired" | "cancelled";
  seats: number;
  duration_days: number;
  started_at: string;
  ends_at: string;
  ended_at: string | null;
}

/** What an organization may discover and do about each product (Phase 11):
 * the public catalog's own fields, priced in this organization's currency,
 * plus what it already holds. */
export interface ExploreProduct {
  code: string;
  slug: string;
  name: string;
  tagline: string;
  description: string;
  availability: "available" | "coming_soon";
  category: string;
  icon: string;
  features: { name: string; description: string }[];
  plans: MarketplaceOffers["plans"];
  starting_price: { amount_minor: number; currency: string; interval: string } | null;
  trial: { available: boolean; duration_days: number | null; seat_limit: number | null; plan_codes: string[] };
  holding: "subscription" | "trial" | null;
  trial_state: "owned" | "active" | "used" | "available" | "unavailable";
  /** Server time only (multi-portal brief): the running trial's end and whole
   * days left (rounded up), or - for one that ran - how it ended. */
  trial_ends_at: string | null;
  trial_days_left: number | null;
  trial_outcome: "expired" | "cancelled" | "converted" | "ended" | null;
}

export interface ExploreCatalog {
  currency: string | null;
  billing_account: boolean;
  products: ExploreProduct[];
  bundles: MarketplaceOffers["bundles"];
}

export const consoleApi = {
  access: (slug: string) => apiFetch<ConsoleAccess>(`${org(slug)}/access`),
  subscriptions: (slug: string) =>
    apiFetch<{ results: OrgSubscription[] }>(`${org(slug)}/subscriptions`),
  /** Cancel at the end of the paid period (owner or billing manager) - never
   * `immediate`, which is a staff remedy. `key` is minted once per confirm;
   * `expectedVersion` refuses a subscription that changed since it was shown. */
  cancelSubscription: (slug: string, subscriptionId: string, expectedVersion: number, key: string) =>
    apiFetch<{ id: string; status: string; cancel_at: string | null }>(
      `${org(slug)}/subscriptions/${subscriptionId}/cancel`,
      {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: json({ expected_version: expectedVersion }),
      },
    ),
  dashboard: (slug: string, windowDays = 7) =>
    apiFetch<DashboardSummary>(`${org(slug)}/dashboard`, { query: { window: windowDays } }),

  renameOrganization: (slug: string, displayName: string) =>
    apiFetch<ConsoleAccess["organization"]>(`${org(slug)}/organization`, {
      method: "PATCH",
      body: json({ display_name: displayName }),
    }),

  members: (slug: string, opts: { status?: string; q?: string; cursor?: string } = {}) =>
    apiFetch<Page<MemberRow>>(`${org(slug)}/members`, {
      query: { status: opts.status, q: opts.q, cursor: opts.cursor },
    }),
  changeRole: (slug: string, membershipId: string, role: string) =>
    apiFetch<MemberRow>(`${org(slug)}/members/${membershipId}/role`, {
      method: "POST",
      body: json({ role }),
    }),
  suspendMember: (slug: string, membershipId: string) =>
    apiFetch<MemberRow>(`${org(slug)}/members/${membershipId}/suspend`, { method: "POST" }),
  reactivateMember: (slug: string, membershipId: string) =>
    apiFetch<MemberRow>(`${org(slug)}/members/${membershipId}/reactivate`, { method: "POST" }),
  signOutMember: (slug: string, membershipId: string) =>
    apiFetch<MemberRow>(`${org(slug)}/members/${membershipId}/sign-out`, { method: "POST" }),
  removeMember: (slug: string, membershipId: string) =>
    apiFetch<{ ok: boolean }>(`${org(slug)}/memberships/${membershipId}`, { method: "DELETE" }),

  invitations: (slug: string, state = "pending", cursor?: string) =>
    apiFetch<Page<InvitationRow>>(`${org(slug)}/invitations`, { query: { state, cursor } }),
  invite: (slug: string, email: string, role: string) =>
    apiFetch<InvitationRow>(`${org(slug)}/invitations`, {
      method: "POST",
      body: json({ email, role }),
    }),
  resendInvitation: (slug: string, invitationId: string) =>
    apiFetch<InvitationRow>(`${org(slug)}/invitations/${invitationId}/resend`, { method: "POST" }),
  revokeInvitation: (slug: string, invitationId: string) =>
    apiFetch<InvitationRow>(`${org(slug)}/invitations/${invitationId}/revoke`, { method: "POST" }),

  audit: (slug: string, opts: { action?: string; cursor?: string } = {}) =>
    apiFetch<Page<AuditEntry>>(`${org(slug)}/audit`, {
      query: { action: opts.action, cursor: opts.cursor },
    }),
  signIns: (slug: string, cursor?: string) =>
    apiFetch<Page<SignInRow>>(`${org(slug)}/sign-ins`, { query: { cursor } }),

  securityPolicy: (slug: string) => apiFetch<SecurityPolicy>(`${org(slug)}/security/policy`),
  updateSecurityPolicy: (
    slug: string,
    body: {
      session_max_age_minutes: number | null;
      password_min_length: number | null;
      version: number;
    },
  ) =>
    apiFetch<SecurityPolicy>(`${org(slug)}/security/policy`, { method: "PUT", body: json(body) }),
  networkPolicy: (slug: string) => apiFetch<SecurityPolicy>(`${org(slug)}/security/network`),
  updateNetworkPolicy: (
    slug: string,
    body: { ip_allowlist_enabled: boolean; ip_allowlist: string[]; version: number },
  ) =>
    apiFetch<SecurityPolicy>(`${org(slug)}/security/network`, { method: "PUT", body: json(body) }),

  billingAccount: (slug: string) =>
    apiFetch<{ billing_account: BillingAccount | null }>(`${org(slug)}/billing-account`),
  /** Phase 9: only the currency and an address - how the account is
   * collected and its region are Workeasy360's to set, never the customer's. */
  openBillingAccount: (slug: string, body: { currency: string; billing_email: string }) =>
    // The create answers with the account's identity only; read it back with
    // ``billingAccount`` (which needs ``platform.billing.view``) for the rest.
    apiFetch<Pick<BillingAccount, "id" | "currency" | "collection_mode">>(`${org(slug)}/billing-account`, {
      method: "POST",
      body: json(body),
    }),
  invoices: (slug: string, status?: string, cursor?: string) =>
    apiFetch<Page<Invoice>>(`${org(slug)}/billing/invoices`, { query: { status, cursor } }),
  invoice: (slug: string, invoiceId: string) =>
    apiFetch<InvoiceDetail>(`${org(slug)}/billing/invoices/${invoiceId}`),
  /** `key` is minted once per user action: a double click is the same payment. */
  payInvoice: (slug: string, invoiceId: string, key: string) =>
    apiFetch<PaymentAttempt>(`${org(slug)}/billing/invoices/${invoiceId}/pay`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
    }),
  payment: (slug: string, attemptId: string) =>
    apiFetch<PaymentAttempt>(`${org(slug)}/billing/payments/${attemptId}`),

  marketplace: (slug: string) => apiFetch<MarketplaceOffers>(`${org(slug)}/marketplace`),
  explore: (slug: string) => apiFetch<ExploreCatalog>(`${org(slug)}/explore`),

  trials: (slug: string) => apiFetch<{ results: ProductTrial[] }>(`${org(slug)}/trials`),
  /** Start a trial (owner or billing manager). `key` is minted once per click:
   * a retry returns the trial the first click created, never a second one. */
  startTrial: (slug: string, productCode: string, planCode: string, key: string) =>
    apiFetch<ProductTrial>(`${org(slug)}/trials`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: json({ product_code: productCode, plan_code: planCode }),
    }),
  cancelTrial: (slug: string, trialId: string) =>
    apiFetch<ProductTrial>(`${org(slug)}/trials/${trialId}/cancel`, { method: "POST" }),

  /** Phase 9: what an order would cost now (nothing is committed). */
  quoteOrder: (slug: string, body: OrderRequest) =>
    apiFetch<OrderQuote>(`${org(slug)}/orders/quote`, { method: "POST", body: json(body) }),
  /** Place an order (owner). `key` is minted once per checkout: a retry is the
   * same order. `requires_payment` then means: pay its invoice. */
  placeOrder: (slug: string, body: OrderRequest, key: string) =>
    apiFetch<Order>(`${org(slug)}/orders`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: json(body),
    }),
  orders: (slug: string, cursor?: string) =>
    apiFetch<Page<Order>>(`${org(slug)}/orders`, { query: { cursor } }),
  order: (slug: string, orderId: string) => apiFetch<Order>(`${org(slug)}/orders/${orderId}`),
  cancelOrder: (slug: string, orderId: string) =>
    apiFetch<Order>(`${org(slug)}/orders/${orderId}/cancel`, { method: "POST" }),
};

// --- the account itself ---------------------------------------------------------------

export interface SessionRow {
  session_id: string;
  current: boolean;
  signed_in_at: string;
  last_active_at: string;
  expires_at: string;
  ip_prefix: string;
  device_label: string;
}

export interface AccountEvent {
  occurred_at: string;
  kind: string;
  reason: string;
  ip_prefix: string;
  device_label: string;
}

export interface InvitationPreview {
  organization_name: string;
  email: string;
  role: string;
  role_name: string;
  expires_at: string;
  account_exists: boolean;
  signed_in_as_invitee: boolean;
}

export const accountApi = {
  changePassword: (currentPassword: string, newPassword: string) =>
    apiFetch<{ ok: boolean }>("/api/v1/auth/password", {
      method: "POST",
      body: json({ current_password: currentPassword, new_password: newPassword }),
    }),
  sessions: () => apiFetch<{ sessions: SessionRow[] }>("/api/v1/auth/sessions"),
  revokeSession: (sessionId: string) =>
    apiFetch<{ ok: boolean }>(`/api/v1/auth/sessions/${sessionId}/revoke`, { method: "POST" }),
  revokeOtherSessions: () =>
    apiFetch<{ ok: boolean; ended: number }>("/api/v1/auth/sessions/revoke-others", {
      method: "POST",
    }),
  activity: () => apiFetch<{ events: AccountEvent[] }>("/api/v1/auth/activity"),

  /** POST, so the token never travels in a URL (or an access log). */
  previewInvitation: (token: string) =>
    apiFetch<InvitationPreview>("/api/v1/public/invitations/preview", {
      method: "POST",
      body: json({ token }),
    }),
  acceptInvitation: (token: string, body: { full_name?: string; password?: string } = {}) =>
    apiFetch<{ organization_slug: string; membership_id: string; account_created: boolean }>(
      "/api/v1/public/invitations/accept",
      { method: "POST", body: json({ token, ...body }) },
    ),

  /** Development only: the test payment provider's "hosted page" reports an outcome. */
  completeTestCheckout: (reference: string, organizationId: string, outcome: "succeeded" | "failed") =>
    apiFetch<{ received: boolean; outcome: string }>("/api/v1/public/payments/test-checkout/complete", {
      method: "POST",
      body: json({ reference, organization_id: organizationId, outcome }),
    }),
};
