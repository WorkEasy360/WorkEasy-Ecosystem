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

// --- KYC ---------------------------------------------------------------------------

export type KycStatus = "draft" | "submitted" | "under_review" | "correction_required" | "approved" | "rejected";

export interface KycRequirement {
  id: string;
  key: string;
  label: string;
  help_text: string;
  is_required: boolean;
  is_active: boolean;
  display_order: number;
}

export interface KycDocument {
  id: string;
  requirement_key: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  uploaded_at: string;
  removed_at?: string | null;
  /** The malware scan (2026-11-10): clean, infected, pending (waiting for a
   * scanner; it cannot be opened yet) or not_required (scanning is off here). */
  scan?: "clean" | "infected" | "pending" | "not_required";
}

export interface KycQueueRow {
  partner_id: string;
  partner_name: string;
  partner_code: string;
  partner_status: string;
  /** The partner account's version, for the separate activation step. */
  partner_version?: number;
  contact_email: string;
  status: KycStatus;
  requested_experience: string;
  source: string;
  submitted_at: string | null;
  decided_at: string | null;
  reviewer_name: string;
  decided_by_name: string;
  created_at: string;
}

export interface KycDecision {
  id: string;
  decision: string;
  from_status: string;
  to_status: string;
  reason: string;
  flagged_requirements: string[];
  decided_by: string | null;
  decided_by_name: string;
  decided_at: string;
}

export interface KycApplicationDetail extends KycQueueRow {
  version: number;
  details: Record<string, string>;
  decision_reason: string;
  requirements: KycRequirement[];
  documents: KycDocument[];
  missing: string[];
  decisions: KycDecision[];
  document_access: { document_id: string; accessed_by_name: string; accessed_at: string }[];
  may_review: boolean;
  may_decide: boolean;
}

export const kycApi = {
  queue: (query: { status?: string; q?: string; limit?: number; offset?: number }) =>
    apiFetch<{
      results: KycQueueRow[];
      total: number;
      limit: number;
      offset: number;
      counts: Record<string, number>;
      awaiting_activation?: number;
      legacy_active?: number;
    }>(
      "/api/v1/admin/kyc/applications",
      { query },
    ),
  detail: (partnerId: string) => apiFetch<KycApplicationDetail>(`/api/v1/admin/kyc/applications/${partnerId}`),
  /** Every staff opening of a KYC document, across all applications (2026-11-10). */
  accessLog: (query: { limit?: number; offset?: number }) =>
    apiFetch<{
      results: { accessed_at: string; accessed_by_name: string; partner_id: string; partner_name: string; requirement_key: string; ip_prefix: string }[];
      total: number;
      limit: number;
      offset: number;
    }>("/api/v1/admin/kyc/document-access", { query }),
  startReview: (partnerId: string) =>
    apiFetch<KycApplicationDetail>(`/api/v1/admin/kyc/applications/${partnerId}/review`, post({})),
  decide: (
    partnerId: string,
    body: { decision: "approve" | "request_correction" | "reject"; reason?: string; flagged_requirements?: string[]; experience?: string; expected_version?: number },
  ) => apiFetch<KycApplicationDetail>(`/api/v1/admin/kyc/applications/${partnerId}/decision`, post(body)),
  /** Opened in a new tab. Every open is written to the access log by the server. */
  documentUrl: (partnerId: string, documentId: string) =>
    `/api/v1/admin/kyc/applications/${partnerId}/documents/${documentId}/content`,
  requirements: () => apiFetch<{ results: KycRequirement[] }>("/api/v1/admin/kyc/requirements"),
  createRequirement: (body: Partial<KycRequirement>) =>
    apiFetch<KycRequirement>("/api/v1/admin/kyc/requirements", post(body)),
  updateRequirement: (id: string, body: Partial<KycRequirement>) =>
    apiFetch<KycRequirement>(`/api/v1/admin/kyc/requirements/${id}`, put(body)),
};

export interface PartnerKycApplication {
  status: KycStatus;
  version: number;
  details: Record<string, string>;
  requested_experience: string;
  submitted_at: string | null;
  decided_at: string | null;
  decision_reason: string;
  correction: { reason: string; flagged_requirements: string[]; decided_at: string } | null;
  requirements: KycRequirement[];
  documents: KycDocument[];
  editable: boolean;
  may_edit: boolean;
}

export const partnerKycApi = {
  get: (partnerId: string) => apiFetch<PartnerKycApplication>(`/api/v1/partner/${partnerId}/kyc`),
  save: (partnerId: string, details: Record<string, string>, expectedVersion?: number) =>
    apiFetch<PartnerKycApplication>(`/api/v1/partner/${partnerId}/kyc`, put({ details, expected_version: expectedVersion })),
  /** One file as the raw request body; the type comes from `Content-Type`. */
  upload: (partnerId: string, requirementKey: string, file: File) =>
    apiFetch<KycDocument>(`/api/v1/partner/${partnerId}/kyc/documents`, {
      method: "POST",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
      query: { requirement: requirementKey, filename: file.name },
    }),
  remove: (partnerId: string, documentId: string) =>
    apiFetch<{ ok: boolean }>(`/api/v1/partner/${partnerId}/kyc/documents/${documentId}`, { method: "DELETE" }),
  documentUrl: (partnerId: string, documentId: string) => `/api/v1/partner/${partnerId}/kyc/documents/${documentId}/content`,
  submit: (partnerId: string) => apiFetch<PartnerKycApplication>(`/api/v1/partner/${partnerId}/kyc/submit`, post({})),
};

export const partnerApplicationApi = {
  /** The answer is the same for every outcome: it never says whether an address is registered. */
  apply: (body: {
    business_name: string;
    first_name: string;
    last_name: string;
    email: string;
    password: string;
    country: string;
    phone?: string;
    interest: "affiliate" | "reseller" | "white_label";
  }) => apiFetch<{ status: string }>("/api/v1/public/partner-applications", post(body)),
};

// --- billing ------------------------------------------------------------------------

export interface InvoiceRow {
  invoice_id: string;
  organization_id: string;
  organization_name: string;
  order_id: string | null;
  subscription_id: string | null;
  number: string;
  status: string;
  currency: string;
  total_minor: number;
  amount_paid_minor: number;
  amount_refunded_minor: number;
  issued_at: string;
  due_at: string;
  paid_at: string | null;
  dunning_stage: string;
}

export interface PaymentRow {
  attempt_id: string;
  organization_id: string;
  organization_name: string;
  invoice_id: string;
  invoice_number: string;
  invoice_status: string;
  provider: string;
  status: string;
  amount_minor: number;
  currency: string;
  amount_refunded_minor: number;
  failure_code: string;
  created_at: string;
  completed_at: string | null;
}

export interface StuckOrderRow {
  order_id: string;
  organization_id: string;
  organization_name: string;
  number: string;
  status: string;
  description: string;
  amount_due_minor: number;
  currency: string;
  paid_at: string | null;
}

export interface RefundRow {
  refund_id: string;
  organization_id: string;
  organization_name: string;
  attempt_id: string;
  invoice_id: string | null;
  invoice_number: string;
  provider: string;
  original_amount_minor: number | null;
  credit_note: string;
  amount_minor: number;
  currency: string;
  reason: string;
  status: string;
  failure_code: string;
  created_at: string;
  completed_at: string | null;
}

export interface CreditNoteRow {
  id: string;
  number: string;
  organization_id: string;
  organization_name: string;
  invoice_id: string;
  invoice_number: string;
  refund_id: string;
  attempt_id: string;
  amount_minor: number;
  currency: string;
  reason: string;
  issued_at: string;
}

export interface SubscriptionLine {
  product_code: string;
  plan_code: string;
  bundle_code: string;
  quantity: number;
  total_amount_minor: number | null;
  currency: string;
  interval: string;
}

export interface SubscriptionRow {
  subscription_id: string;
  organization_id: string;
  organization_name: string;
  status: string;
  currency: string;
  billing_interval: string;
  renewal_policy: string;
  current_period_end: string;
  trial_ends_at: string | null;
  activated_at: string | null;
  cancel_at: string | null;
  ended_at: string | null;
  lines: SubscriptionLine[];
}

type PageQuery = { limit?: number; cursor?: string };

export const billingApi = {
  invoices: (query: PageQuery & { status?: string; q?: string }) =>
    apiFetch<KeysetPage<InvoiceRow>>("/api/v1/admin/invoices", { query }),
  payments: (query: PageQuery & { status?: string; failed?: "1" }) =>
    apiFetch<KeysetPage<PaymentRow>>("/api/v1/admin/payments/all", { query }),
  stuckOrders: () => apiFetch<{ results: StuckOrderRow[] }>("/api/v1/admin/orders/stuck", { query: { limit: 100 } }),
  retryActivation: (orderId: string) =>
    apiFetch<{ order_id: string; outcome: string }>(`/api/v1/admin/orders/${orderId}/retry-activation`, post({})),
  refunds: (query: PageQuery & { status?: string; waiting?: string }) => apiFetch<KeysetPage<RefundRow>>("/api/v1/admin/refunds", { query }),
  creditNotes: (query: PageQuery & { q?: string }) =>
    apiFetch<KeysetPage<CreditNoteRow>>("/api/v1/admin/credit-notes", { query }),
  subscriptions: (query: PageQuery & { status?: string; product?: string; renewing?: number; q?: string }) =>
    apiFetch<KeysetPage<SubscriptionRow> & { counts: Record<string, number> }>("/api/v1/admin/subscriptions", { query }),
};

// --- wallets ------------------------------------------------------------------------

export type WalletKind = "platform" | "ai_credits" | "consumption";

export interface WalletRow {
  id: string;
  kind: WalletKind;
  kind_label: string;
  owner_type: "organization" | "partner";
  owner_id: string;
  owner_name: string;
  currency: string;
  balance: number;
  low_balance_threshold: number | null;
  is_low: boolean;
  updated_at: string;
}

export interface WalletEntryRow {
  id: string;
  wallet_id: string;
  type: string;
  type_label: string;
  amount: number;
  balance_after: number;
  reason: string;
  source_type: string;
  source_id: string;
  reverses: string | null;
  actor_user_id: string | null;
  created_at: string;
  wallet_kind?: WalletKind;
  owner_type?: string;
  owner_id?: string;
  owner_name?: string;
  currency?: string;
}

export interface UsageEventRow {
  id: string;
  organization_id: string;
  organization_name: string;
  product_code: string;
  meter: string;
  meter_label: string;
  quantity: number;
  occurred_at: string;
  received_at: string;
  outcome: "charged" | "included" | "not_rated" | "failed";
  failure_code: string;
  charged_units: number;
  amount: number;
  currency: string;
  entry_id: string | null;
  duplicate_count: number;
  last_duplicate_at: string | null;
  reviewed_at: string | null;
  review_note: string;
}

const key = (k: string): HeadersInit => ({ "Idempotency-Key": k });

export const walletsApi = {
  list: (query: PageQuery & { kind?: WalletKind; owner_type?: string; q?: string; low?: "1" }) =>
    apiFetch<KeysetPage<WalletRow>>("/api/v1/admin/wallets", { query }),
  get: (id: string) => apiFetch<WalletRow>(`/api/v1/admin/wallets/${id}`),
  open: (body: { owner_type: string; owner_id: string; kind: WalletKind; currency?: string }) =>
    apiFetch<WalletRow>("/api/v1/admin/wallets", post(body)),
  setThreshold: (id: string, low_balance_threshold: number | null) =>
    apiFetch<WalletRow>(`/api/v1/admin/wallets/${id}`, put({ low_balance_threshold })),
  recordTopUp: (id: string, body: { amount: number; reference: string; reason: string }, commandId: string) =>
    apiFetch<WalletEntryRow>(`/api/v1/admin/wallets/${id}/top-up`, { ...post(body), headers: key(commandId) }),
  adjust: (id: string, body: { amount: number; reason: string }, commandId: string) =>
    apiFetch<WalletEntryRow>(`/api/v1/admin/wallets/${id}/adjust`, { ...post(body), headers: key(commandId) }),
  entries: (query: PageQuery & { wallet?: string; kind?: WalletKind; type?: string }) =>
    apiFetch<KeysetPage<WalletEntryRow>>("/api/v1/admin/wallets/entries", { query }),
  reverse: (entryId: string, body: { amount?: number; reason: string }, commandId: string) =>
    apiFetch<WalletEntryRow>(`/api/v1/admin/wallets/entries/${entryId}/reverse`, { ...post(body), headers: key(commandId) }),
  reconcile: () =>
    apiFetch<{ balanced: boolean; mismatches: { wallet_id: string; balance: number; ledger_total: number }[] }>(
      "/api/v1/admin/wallets/reconcile",
    ),
  /** A top-up or credit-pack order for a customer; its invoice is then paid like any other. */
  placeOrder: (
    organizationId: string,
    body: { pack_code: string } | { wallet_kind: "platform" | "consumption"; amount_minor: number; currency: string },
    commandId: string,
  ) =>
    apiFetch<{ order_id: string; number: string; status: string; amount_due_minor: number; currency: string }>(
      `/api/v1/admin/customers/${organizationId}/wallet-orders`,
      { ...post(body), headers: key(commandId) },
    ),
  payInvoiceFromWallet: (organizationId: string, invoiceId: string, body: { reason: string; expected_version: number }) =>
    apiFetch<unknown>(`/api/v1/admin/customers/${organizationId}/invoices/${invoiceId}/pay-from-wallet`, post(body)),
  events: (query: PageQuery & { outcome?: string; meter?: string; duplicates?: "1"; unreviewed?: "1" }) =>
    apiFetch<KeysetPage<UsageEventRow>>("/api/v1/admin/wallets/events", { query }),
  retryEvent: (id: string) => apiFetch<UsageEventRow>(`/api/v1/admin/wallets/events/${id}/retry`, post({})),
  reviewEvent: (id: string, note: string) =>
    apiFetch<UsageEventRow>(`/api/v1/admin/wallets/events/${id}/review`, post({ note })),
};

// --- usage pricing ------------------------------------------------------------------

export interface UsageRateRow {
  id: string;
  meter: string;
  meter_label: string;
  currency: string;
  unit_amount: number;
  per_units: number;
  included_units: number;
  effective_from: string;
  effective_to: string | null;
  reason: string;
}

export interface CreditPackRow {
  id: string;
  code: string;
  name: string;
  credits: number;
  price_minor: number;
  currency: string;
  status: "active" | "archived";
}

export const usagePricingApi = {
  rates: (history = false) =>
    apiFetch<{ meters: { meter: string; label: string }[]; results: UsageRateRow[] }>("/api/v1/admin/usage-rates", {
      query: { history: history ? "1" : undefined },
    }),
  setRate: (body: { meter: string; currency?: string; unit_amount: number; per_units: number; included_units: number; reason: string }) =>
    apiFetch<UsageRateRow>("/api/v1/admin/usage-rates", post(body)),
  removeRate: (id: string, reason: string) => apiFetch<{ ok: boolean }>(`/api/v1/admin/usage-rates/${id}/remove`, post({ reason })),
  packs: () => apiFetch<{ results: CreditPackRow[] }>("/api/v1/admin/credit-packs"),
  createPack: (body: { name: string; credits: number; price_minor: number; currency: string }) =>
    apiFetch<CreditPackRow>("/api/v1/admin/credit-packs", post(body)),
  updatePack: (id: string, body: { name: string; credits: number; price_minor: number; currency: string; status: string }) =>
    apiFetch<CreditPackRow>(`/api/v1/admin/credit-packs/${id}`, put(body)),
  setAppLinks: (productId: string, app_links: Record<string, string>) =>
    apiFetch<{ app_links: Record<string, string> }>(`/api/v1/admin/catalog/products/${productId}/app-links`, put({ app_links })),
  setPlanRules: (planId: string, body: { renewal_rule: string; change_rule: string; included_usage: Record<string, number> }) =>
    apiFetch<{ renewal_rule: string; change_rule: string; included_usage: Record<string, number> }>(
      `/api/v1/admin/catalog/plans/${planId}/rules`,
      put(body),
    ),
};

// --- reports ------------------------------------------------------------------------

export interface ReportColumn {
  key: string;
  label: string;
  kind: "text" | "date" | "status" | "money" | "amount" | "number";
}

export interface ReportPage {
  key: string;
  label: string;
  description: string;
  columns: ReportColumn[];
  from: string;
  to: string;
  results: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
}

export type ReportQuery = {
  from?: string;
  to?: string;
  product?: string;
  q?: string;
  limit?: number;
  offset?: number;
};

export const reportsApi = {
  catalog: () => apiFetch<{ results: { key: string; label: string; description: string }[] }>("/api/v1/admin/reports"),
  run: (kind: string, query: ReportQuery) => apiFetch<ReportPage>(`/api/v1/admin/reports/${kind}`, { query }),
  /** A same-origin download. Every export is audited by the server. */
  csvUrl: (kind: string, query: ReportQuery) => {
    const params = new URLSearchParams({ format: "csv" });
    for (const [name, value] of Object.entries(query)) if (value !== undefined && value !== "") params.set(name, String(value));
    return `/api/v1/admin/reports/${kind}?${params.toString()}`;
  },
};

// --- support ------------------------------------------------------------------------

export interface TicketRow {
  id: string;
  number: number;
  reference: string;
  direction: string;
  audience: "partner" | "customer";
  subject: string;
  category: string;
  priority: string;
  status: string;
  opened_at: string;
  updated_at: string;
  resolved_at: string | null;
  sla: Record<string, unknown>;
  partner_id: string | null;
  partner_name: string;
  organization_id: string | null;
  organization_name: string;
  assigned_to: string | null;
  assigned_to_name: string;
  resolution: string;
}

export interface TicketMessage {
  id: string;
  side: string;
  author: string;
  body: string;
  internal: boolean;
  created_at: string;
}

export interface TicketDetail extends TicketRow {
  messages: TicketMessage[];
  may_answer: boolean;
}

export interface HelpArticleRow {
  id: string;
  slug: string;
  kind: "faq" | "setup_guide";
  audience: "all" | "customer" | "partner";
  status: "draft" | "published" | "archived";
  title: string;
  body: string;
  product_code: string;
  display_order: number;
  published_at: string | null;
  updated_at: string;
}

export const supportApi = {
  tickets: (query: { audience?: string; status?: string; assigned?: string; q?: string; limit?: number; offset?: number }) =>
    apiFetch<{ items: TicketRow[]; counts: Record<string, number>; total: number; limit: number; offset: number }>("/api/v1/admin/tickets", { query }),
  ticket: (id: string) => apiFetch<TicketDetail>(`/api/v1/admin/tickets/${id}`),
  reply: (id: string, body: { message?: string; status?: string; internal?: boolean }) =>
    apiFetch<TicketDetail>(`/api/v1/admin/tickets/${id}`, post(body)),
  assign: (id: string, user_id: string | null) => apiFetch<TicketDetail>(`/api/v1/admin/tickets/${id}/assign`, post({ user_id })),
  resolve: (id: string, resolution: string) => apiFetch<TicketDetail>(`/api/v1/admin/tickets/${id}/resolve`, post({ resolution })),
  people: () => apiFetch<{ results: { id: string; name: string }[] }>("/api/v1/admin/ticket-people"),
  articles: (query: { kind?: string; status?: string; q?: string }) =>
    apiFetch<{ results: HelpArticleRow[] }>("/api/v1/admin/help-content", { query }),
  createArticle: (body: Partial<HelpArticleRow>) => apiFetch<HelpArticleRow>("/api/v1/admin/help-content", post(body)),
  updateArticle: (id: string, body: Partial<HelpArticleRow>) => apiFetch<HelpArticleRow>(`/api/v1/admin/help-content/${id}`, put(body)),
  setArticleStatus: (id: string, status: string) =>
    apiFetch<HelpArticleRow>(`/api/v1/admin/help-content/${id}/status`, post({ status })),
};

// --- customers ----------------------------------------------------------------------

export interface MemberRow {
  membership_id: string;
  user_id: string;
  email: string;
  name: string;
  status: string;
  is_owner: boolean;
  joined_at: string;
}

export interface AssignmentRow {
  item_id: string;
  subscription_id: string;
  subscription_status: string;
  product_code: string;
  plan_code: string;
  bundle_code: string;
  quantity: number;
  price_reason: string;
  total_amount_minor: number | null;
  currency: string;
  interval: string;
  added_at: string;
  removed_at: string | null;
}

export interface SetupRequirementRow {
  id: string;
  product_code: string;
  key: string;
  label: string;
  help_text: string;
  is_required: boolean;
  is_active: boolean;
  display_order: number;
}

export interface SetupRecordRow {
  id: string;
  organization_id: string;
  organization_name: string;
  product_code: string;
  status: "pending" | "submitted" | "correction_required" | "approved";
  answers: Record<string, string>;
  submitted_at: string | null;
  decided_at: string | null;
  decided_by_name: string;
  decision_reason: string;
  version: number;
}

/** One partner relationship of a customer, as staff see it (2026-11-10). */
export interface PartnerLinkRow {
  link_id: string;
  partner_id: string;
  partner_code: string;
  partner_name: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  end_reason: string;
  started_by_name: string;
  ended_by_name: string;
}

export interface CustomerPartnerAssignment {
  current: PartnerLinkRow | null;
  history: PartnerLinkRow[];
  may_manage: boolean;
}

export const customersApi = {
  partner: (organizationId: string) => apiFetch<CustomerPartnerAssignment>(`/api/v1/admin/customers/${organizationId}/partner`),
  /** Assign a partner, or change it: the current relationship ends and the new one starts. */
  assignPartner: (organizationId: string, body: { partner_id: string; reason: string }) =>
    apiFetch<CustomerPartnerAssignment>(`/api/v1/admin/customers/${organizationId}/partner`, post(body)),
  assignablePartners: (q: string) =>
    apiFetch<{ results: { id: string; code: string; display_name: string }[] }>("/api/v1/admin/partners/assignable", { query: { q } }),
  create: (body: { name: string; owner_email: string; owner_name: string; reason: string }) =>
    apiFetch<{ organization_id: string; slug: string; owner_invited: boolean }>("/api/v1/admin/customers/create", post(body)),
  members: (organizationId: string) => apiFetch<{ results: MemberRow[] }>(`/api/v1/admin/customers/${organizationId}/members`),
  history: (organizationId: string) =>
    apiFetch<{ results: AssignmentRow[] }>(`/api/v1/admin/customers/${organizationId}/assignment-history`),
  assignPlan: (
    organizationId: string,
    body: { plan_version_id: string; billing_interval: string; quantity: number; reason: string; currency?: string },
    commandId: string,
  ) =>
    apiFetch<{ order_id: string; number: string; status: string; requires_payment: boolean }>(
      `/api/v1/admin/customers/${organizationId}/assign-plan`,
      { ...post(body), headers: key(commandId) },
    ),
  setup: (query: { status?: string; product?: string; organization?: string; limit?: number; offset?: number }) =>
    apiFetch<{
      results: SetupRecordRow[];
      total: number;
      limit: number;
      offset: number;
      counts: Record<string, number>;
      requirements: SetupRequirementRow[];
    }>("/api/v1/admin/setup", { query }),
  decideSetup: (recordId: string, body: { decision: "approve" | "request_correction"; reason?: string; expected_version?: number }) =>
    apiFetch<SetupRecordRow>(`/api/v1/admin/setup/${recordId}/decision`, post(body)),
  setupRequirements: (product?: string) =>
    apiFetch<{ results: SetupRequirementRow[] }>("/api/v1/admin/setup/requirements", { query: { product } }),
  createSetupRequirement: (body: Partial<SetupRequirementRow>) =>
    apiFetch<SetupRequirementRow>("/api/v1/admin/setup/requirements", post(body)),
  updateSetupRequirement: (id: string, body: Partial<SetupRequirementRow>) =>
    apiFetch<SetupRequirementRow>(`/api/v1/admin/setup/requirements/${id}`, put(body)),
};

// --- settings and security ------------------------------------------------------------

export interface DomainsOverview {
  portals: { key: string; label: string; origin: string }[];
  apps: {
    code: string;
    name: string;
    status: string;
    application_url: string;
    marketing_url: string;
    documentation_url: string;
    support_url: string;
    app_links: Record<string, string>;
    store_link: string;
    purchase_routes: { store: boolean; reseller: boolean; affiliate: boolean; white_label: boolean };
  }[];
  partner_domains: { id: string; hostname: string; status: string; partner_id: string; partner_name: string; verified_at: string | null }[];
  brands: { partner_id: string; partner_name: string; brand_name: string; has_logo: boolean }[];
  dns_provider_connected: boolean;
}

export interface LoginClientRow {
  product_code: string;
  client_id: string;
  status: string;
  redirect_uris: string[];
  post_logout_redirect_uris: string[];
  launch_url: string;
}

export interface SignInEventRow {
  id: string;
  user_id: string;
  email: string;
  name: string;
  is_staff: boolean;
  kind: string;
  kind_label: string;
  reason: string;
  ip_prefix: string;
  device_label: string;
  occurred_at: string;
}

export const accessApi = {
  domains: () => apiFetch<DomainsOverview>("/api/v1/admin/settings/domains"),
  checkHost: (host: string) =>
    apiFetch<{ host: string; ok: boolean; detail: string }>("/api/v1/admin/settings/domains/check", post({ host })),
  loginClients: () => apiFetch<{ results: LoginClientRow[] }>("/api/v1/admin/settings/login-clients"),
  createStaff: (body: { email: string; full_name: string; role: string; reason: string }) =>
    apiFetch<{ user_id: string; new_account: boolean; role: string; added: string[] }>("/api/v1/admin/security/accounts", post(body)),
  signIns: (query: { kind?: string; q?: string; from?: string; to?: string; everyone?: "1"; limit?: number; offset?: number }) =>
    apiFetch<{ results: SignInEventRow[]; total: number; limit: number; offset: number }>("/api/v1/admin/security/sign-ins", { query }),
};

export { post as jsonPost, put as jsonPut };
