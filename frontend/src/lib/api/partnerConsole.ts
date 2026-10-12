/**
 * The reseller and white-label console API (Phase 10), and its wire types.
 *
 * The same two rules as `controlPlane.ts` apply: the backend is the authority
 * (these calls simply fail with 401 `step_up_required`, 403 or 404), and the
 * browser sends selections and never authority - a body carries a choice (a
 * capability key, a product code, an amount the operator typed) and never a
 * computed price, margin, discount or commission. Every figure on screen comes
 * back from the server.
 *
 * Interfaces are declared one field per line so the backend's
 * `tests/phase10/test_contract_shapes.py` can read them and check them against
 * real responses.
 */

import { apiFetch } from "./client";
import {
  partnerApi,
  type Brand,
  type BrandPortalFields,
  type CapabilityInfo,
  type DomainRow,
  type Page,
  type PartnerSummary,
} from "./controlPlane";

const admin = "/api/v1/admin";

// --- derived labels ---------------------------------------------------------------

/** "Reseller", "White-label", "Affiliate": computed by the server from the
 * capabilities a partner holds. There is no partner type. */
export interface ExperienceLabel {
  key: string;
  label: string;
}

// --- overview -----------------------------------------------------------------------

export interface OverviewAmount {
  currency: string;
  amount_minor: number;
  entries: number;
}

export interface OverviewCard {
  key: string;
  label: string;
  count: number;
  amounts?: OverviewAmount[];
}

export interface PartnersOverview {
  as_of: string;
  cards: OverviewCard[];
}

// --- the reseller list ----------------------------------------------------------------

export interface ResellerProduct {
  product_code: string;
  plan_code: string | null;
}

export interface ResellerCommission {
  currency: string;
  balance_minor: number;
  unpaid_minor: number;
  entries: number;
}

/** What payouts confirmed as paid amounted to, per currency (never added across). */
export interface ResellerPaid {
  currency: string;
  amount_minor: number;
  payouts: number;
}

export interface ResellerPayouts {
  open: number;
  latest: { id: string; status: string; currency: string; period_end: string; payable_minor: number } | null;
  paid: ResellerPaid[];
}

/** Referral figures for the Affiliates list: counts only, never a prospect. */
export interface ResellerReferrals {
  codes: string[];
  active_codes: number;
  captured: number;
  converted: number;
}

export interface ResellerRow {
  id: string;
  code: string;
  display_name: string;
  status: "draft" | "active" | "suspended" | "terminated";
  payout_currency: string;
  created_at: string;
  experience: ExperienceLabel[];
  capability_count: number;
  customers: number;
  products: ResellerProduct[];
  active_subscriptions: number;
  commission: ResellerCommission[];
  payouts: ResellerPayouts;
  referrals: ResellerReferrals;
  last_activity_at: string;
}

// --- recent activity (Partner Control Center) ---------------------------------------------

export interface RecentPartner {
  id: string;
  code: string;
  display_name: string;
  status: "draft" | "active" | "suspended" | "terminated";
  created_at: string;
  experience: ExperienceLabel[];
}

export interface RecentProvisioning {
  organization_id: string;
  name: string;
  partner_id: string;
  partner_name: string;
  link_status: string;
  started_at: string;
}

export interface RecentLedgerEntry {
  id: string;
  kind: string;
  amount_minor: number;
  currency: string;
  event_at: string;
  partner_id: string;
  partner_name: string;
  organization_name?: string;
}

export interface RecentPayout {
  id: string;
  status: string;
  currency: string;
  period_end: string;
  payable_minor: number;
  partner_id: string;
  partner_name: string;
}

export interface RecentDomain {
  id: string;
  hostname: string;
  status: string;
  requested_at: string;
  partner_id: string;
  partner_name: string;
}

/** Sections the caller may not read (money, without the commission
 * permission) are `null` - absent, which is not the same as empty. */
export interface PartnersRecent {
  as_of: string;
  partners: RecentPartner[];
  provisioning: RecentProvisioning[];
  sales: RecentLedgerEntry[] | null;
  commission: RecentLedgerEntry[] | null;
  payouts: RecentPayout[] | null;
  domains: RecentDomain[];
}

// --- one partner's referrals (staff, read-only) ------------------------------------------------

export interface ReferralCodeRow {
  id: string;
  code: string;
  label: string;
  status: string;
  created_at: string;
}

export interface ReferralRow {
  id: string;
  code: string;
  label: string;
  status: string;
  captured_at: string;
  converted_at: string | null;
}

export interface PartnerReferrals {
  summary: Record<string, number>;
  codes: ReferralCodeRow[];
  results: ReferralRow[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface ResellerFilters {
  q?: string;
  status?: string;
  product?: string;
  capability?: string;
  scope?: string;
  cursor?: string;
}

// --- capability presentation ------------------------------------------------------------

export interface CapabilityGroup {
  key: string;
  label: string;
  capabilities: string[];
}

export interface CapabilityPreset {
  key: string;
  label: string;
  description: string;
  capabilities: string[];
}

export interface CapabilityCatalog {
  results: CapabilityInfo[];
  groups: CapabilityGroup[];
  presets: CapabilityPreset[];
}

// --- the catalog the console sells from ------------------------------------------------------

export interface ConsolePlan {
  plan_version_id: string;
  plan_code: string;
  plan_name: string;
  included_seats: number;
  trial_days: number;
}

/** What the platform configured for one product's free trial. A partner can
 * only narrow it (`ReviewTrial.partner_allows`); it never adds a trial. */
export interface ConsoleTrial {
  enabled: boolean;
  duration_days: number;
  seat_limit: number | null;
  payment_method_required: boolean;
  eligible_plan_codes: string[];
}

export interface ConsoleProduct {
  code: string;
  name: string;
  status: string;
  plans: ConsolePlan[];
  trial: ConsoleTrial | null;
}

// --- the Add Reseller review (a server-side dry run) -------------------------------------------

export interface ReviewProblem {
  field: string;
  message: string;
}

export interface ReviewPricingRow {
  product_code: string;
  product_name: string;
  plan_version_id: string;
  plan_code: string;
  plan_name: string;
  interval: string;
  currency: string;
  list_price_minor: number | null;
  partner_price_minor: number | null;
  partner_price_source: "proposed" | "list";
  discount_minor: number | null;
  discount_bp: number | null;
  commission_bp: number | null;
  commission_minor: number | null;
  platform_net_minor: number | null;
}

/** A worked example from the configured rate, computed by the server in whole
 * minor units. Absent when there is no rate to compute one from. */
export interface CommissionExample {
  currency: string;
  purchase_minor: number;
  commission_minor: number;
  platform_net_minor: number;
  rate_bp: number;
}

export interface ReviewCommission {
  mode: "default" | "custom";
  rate_bp: number | null;
  programme_default_bp: number | null;
  source: "custom" | "programme_default" | "none";
  product_rates: Record<string, number | null>;
  /** The one method the platform has: a percentage of what the customer pays. */
  method: "percentage";
  scope: "all" | "products";
  product_codes: string[];
  effective_from: string | null;
  /** Days before a commission becomes payable: a platform setting, not a choice. */
  hold_days: number;
  example: CommissionExample | null;
}

/** For one chosen product: what the platform configured and what this partner's
 * customers would get. */
export interface ReviewTrial {
  product_code: string;
  product_name: string;
  platform_trial_enabled: boolean;
  duration_days: number | null;
  seat_limit: number | null;
  eligible_plan_codes: string[];
  partner_allows: boolean;
  available_to_customers: boolean;
  portal_visible: boolean;
}

export interface ReadinessItem {
  key: string;
  label: string;
  detail: string;
}

/** What is still missing before the partner can be *activated* - never what
 * stops a draft being saved. */
export interface ActivationReadiness {
  can_activate: boolean;
  missing: ReadinessItem[];
}

export interface OptionalItem {
  key: string;
  label: string;
}

export interface ReviewWhiteLabel {
  requested: boolean;
  capabilities_added: string[];
}

export interface ReviewAdministrator {
  email: string;
  role: string;
  first_name: string;
  last_name: string;
  send_invitation: "now" | "on_activation";
}

export interface ReviewPartner {
  code: string;
  display_name: string;
  legal_name: string;
  contact_name: string;
  contact_email: string;
}

export interface ReviewProductPlan {
  plan_code: string;
  plan_name: string;
}

export interface ReviewProduct {
  code: string;
  name: string;
  plans: ReviewProductPlan[];
}

/** What the review says would be stored beyond the account itself: only the
 * parts that were sent, as the server cleaned them. */
export interface ReviewProfile {
  business: Record<string, unknown>;
  contacts: Record<string, Record<string, string> | null>;
  support: Record<string, string>;
  payout: Record<string, string>;
  has_internal_notes: boolean;
}

export interface ResellerReview {
  ok: boolean;
  problems: ReviewProblem[];
  partner: ReviewPartner;
  profile: ReviewProfile;
  capabilities: { key: string; label: string }[];
  experience: ExperienceLabel[];
  products: ReviewProduct[];
  pricing_mode: "standard" | "custom";
  pricing: ReviewPricingRow[];
  trials: ReviewTrial[];
  commission: ReviewCommission;
  administrator: ReviewAdministrator | null;
  white_label: ReviewWhiteLabel;
  warnings: string[];
  readiness: ActivationReadiness;
  missing_optional: OptionalItem[];
}

export type ContactKind = "primary" | "billing" | "technical" | "support";
export type PayoutFrequency = "monthly" | "quarterly" | "manual";

/** One contact as typed. A billing, technical or support contact that is the
 * same person as the primary is sent as `{ same_as_primary: true }` (or null),
 * which stores nothing - never a copy that could drift. */
export type ContactInput = { name?: string; job_title?: string; email?: string; phone?: string } | { same_as_primary: true } | null;

export type AddressInput = { line1?: string; line2?: string; city?: string; region?: string; postal_code?: string };

/** The profile as typed: every part optional, and only what is sent changes. */
export type ProfileInput = {
  business?: {
    display_name?: string;
    legal_name?: string;
    business_email?: string;
    website?: string;
    phone?: string;
    alternative_email?: string;
    registration_number?: string;
    tax_number?: string;
    country?: string;
    address?: AddressInput;
  };
  contacts?: Partial<Record<ContactKind, ContactInput>>;
  support?: { email?: string; phone?: string; url?: string };
  payout?: { frequency?: PayoutFrequency; contact_email?: string };
  internal_notes?: string;
};

/** What the operator chose - selections and typed amounts, never a computed figure. */
export interface ResellerDraftBody {
  partner: {
    code: string;
    display_name: string;
    legal_name: string;
    contact_name: string;
    contact_email: string;
    payout_currency: string;
  };
  profile?: ProfileInput;
  capabilities: string[];
  white_label?: boolean;
  products: string[];
  product_options?: Record<string, { trial_allowed?: boolean; portal_visible?: boolean }>;
  pricing_mode?: "standard" | "custom";
  partner_prices: { plan_version_id: string; interval: string; amount_minor: number }[];
  commission: { mode: "default" | "custom"; rate_bp?: number; product_codes?: string[]; effective_from?: string };
  administrator?: {
    email: string;
    role: string;
    first_name?: string;
    last_name?: string;
    send_invitation?: "now" | "on_activation";
  };
}

// --- a partner's business profile and contacts (staff only) ---------------------------------------

export interface PartnerAddress {
  line1: string;
  line2: string;
  city: string;
  region: string;
  postal_code: string;
}

export interface PartnerBusiness {
  display_name: string;
  legal_name: string;
  business_email: string;
  website: string;
  phone: string;
  alternative_email: string;
  registration_number: string;
  tax_number: string;
  country: string;
  address: PartnerAddress;
}

/** A contact role. The primary contact always carries its four fields; any
 * other role is either `same_as_primary` or carries its own. */
export interface PartnerContactView {
  same_as_primary?: boolean;
  name?: string;
  job_title?: string;
  email?: string;
  phone?: string;
}

export interface PartnerContacts {
  primary: PartnerContactView;
  billing: PartnerContactView;
  technical: PartnerContactView;
  support: PartnerContactView;
}

export interface PartnerSupportDetails {
  email: string;
  phone: string;
  url: string;
}

/** Payout *preferences*. Nothing runs on them and nothing is paid automatically. */
export interface PartnerPayoutPrefs {
  currency: string;
  frequency: PayoutFrequency;
  contact_email: string;
}

export interface PartnerProfile {
  partner_id: string;
  version: number;
  business: PartnerBusiness;
  contacts: PartnerContacts;
  support: PartnerSupportDetails;
  payout: PartnerPayoutPrefs;
  /** Staff only: never shown to a partner or a customer. */
  internal_notes: string;
  updated_at: string;
}

// --- a partner's commission -----------------------------------------------------------------------

export interface PartnerRule {
  id: string;
  code: string;
  version: number;
  product_code: string;
  rate_bp: number;
  effective_from: string;
  retired_at: string | null;
  state: "in_force" | "scheduled" | "superseded";
  reason: string;
}

export interface PartnerCommissionState {
  partner_earns: boolean;
  rules: PartnerRule[];
  live_versions: Record<string, number>;
  in_force: { code: string; version: number; rate_bp: number; product_code: string; scope: string; effective_from: string } | null;
}

// --- one partner's pages --------------------------------------------------------------------------

export interface SetupStep {
  key: string;
  label: string;
  state: "complete" | "incomplete" | "pending" | "optional";
  detail: string;
}

export interface SummaryCapability {
  key: string;
  label: string;
  group: string;
  granted_at: string;
  expires_at: string | null;
}

export interface SummaryProduct {
  grant_id: string;
  product_code: string;
  product_name: string;
  plan_code: string | null;
  trial_allowed: boolean;
  portal_visible: boolean;
}

export interface SummaryCustomers {
  live: number;
  active: number;
  suspended: number;
  ended: number;
  owner_activation_pending: number;
}

export interface InForceRule {
  code: string;
  version: number;
  rate_bp: number;
  product_code: string;
  scope: string;
  effective_from: string;
}

export interface SummaryPayouts {
  open: number;
  by_status: Record<string, number>;
  latest: { id: string; status: string; currency: string; period_end: string; payable_minor: number } | null;
}

export interface SummaryWhiteLabel {
  enabled: boolean;
  brand_configured: boolean;
  domain_status: string;
  domains: { active: number; verified: number; awaiting: number };
}

export interface PartnerSummaryView {
  partner: PartnerSummary;
  experience: ExperienceLabel[];
  capabilities: SummaryCapability[];
  customers: SummaryCustomers;
  products: SummaryProduct[];
  active_subscriptions: number;
  commission: ResellerCommission[];
  commission_rule: InForceRule | null;
  payouts: SummaryPayouts;
  white_label: SummaryWhiteLabel;
  referrals: ResellerReferrals;
  setup_progress: SetupStep[];
  /** The same readiness rule the Add Reseller review applies to a draft. */
  activation: ActivationReadiness;
}

/** The factual state of a customer owner's activation. There is no token here
 * and there never is: only what a person acts on. */
export interface OwnerActivation {
  activation: "pending" | "activated" | "expired" | "revoked" | "declined" | "not_applicable";
  email: string;
  invitation_id: string | null;
  expires_at: string | null;
  resolved_at: string | null;
  last_sent_at: string | null;
  send_count: number;
}

/** What resending or withdrawing an activation link answers: the state, never the link. */
export interface OwnerInvitationState {
  activation: OwnerActivation["activation"];
  expires_at: string;
  resolved_at: string | null;
  last_sent_at: string | null;
  send_count: number;
}

/** What the activation page shows before the person commits (public, by link). */
export interface OwnerInvitationPreview {
  organization_name: string;
  partner_name: string;
  email: string;
  expires_at: string;
  account_exists: boolean;
  signed_in_as_invitee: boolean;
  needs_name: boolean;
}

export interface OwnerInvitationClaim {
  organization_slug: string;
  organization_id: string;
  credentials_set: boolean;
}

export interface PartnerCustomerAdminRow {
  link_id: string;
  organization_id: string;
  name: string;
  slug: string;
  organization_status: string;
  partner_id: string;
  partner_code: string;
  partner_name: string;
  link_status: "active" | "suspended" | "ended";
  started_at: string;
  ended_at: string | null;
  end_reason: string;
  subscription_status: string;
  products: string[];
  seat_capacity: number | null;
  owner: OwnerActivation;
}

export interface CustomerFilters {
  q?: string;
  status?: string;
  activation?: string;
  subscription?: string;
  cursor?: string;
}

export interface PartnerPricingRow {
  product_code: string;
  product_name: string;
  plan_version_id: string;
  plan_code: string;
  plan_name: string;
  interval: string;
  currency: string;
  list_price_minor: number | null;
  partner_price_minor: number | null;
  partner_price_source: "partner_book" | "list";
  discount_minor: number | null;
  discount_bp: number | null;
  commission_bp: number | null;
  commission_minor: number | null;
  platform_net_minor: number | null;
}

export interface AuditEntry {
  id: string;
  occurred_at: string;
  action: string;
  /** What the action is called to a person ("Partner activated"). */
  label: string;
  /** The person's email, "System", or the kind of actor. */
  actor: string;
  actor_kind: string;
  /** What it was done to, from a closed list of facts - never a secret value. */
  target: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  reason: string;
}

export interface StoredBrand {
  brand_name: string;
  logo_file_id: string | null;
  accent_color: string;
  theme: Record<string, string>;
  support_email: string;
  support_url: string;
  portal: BrandPortalFields;
  version: number;
  updated_at: string;
}

// --- the white-label setup wizard's one read ---------------------------------------------------------

/** One catalog product as the white-label Products step shows it. "Visible in
 * portal" decides what is *shown*; it never grants or removes an entitlement. */
export interface WhiteLabelProductRow {
  product_code: string;
  product_name: string;
  status: string;
  grant_id: string | null;
  available_to_partner: boolean;
  visible_in_portal: boolean;
  sell_allowed: boolean;
  platform_trial_enabled: boolean;
  partner_allows_trial: boolean;
  trial_available: boolean;
}

export interface WhiteLabelAuthentication {
  mode: string;
  custom_domain_sign_in: boolean;
  summary: string;
}

export interface WhiteLabelChecks {
  brand_saved: boolean;
  logo_saved: boolean;
  domain_requested: boolean;
}

export interface WhiteLabelSetup {
  partner: { id: string; code: string; display_name: string; status: string; version: number };
  experience: ExperienceLabel[];
  capabilities: string[];
  enabled: boolean;
  missing_capabilities: string[];
  customers: number;
  brand: StoredBrand | null;
  products: WhiteLabelProductRow[];
  domain: DomainRow | null;
  branding_on_custom_domain: "none" | "pending" | "verified_not_active" | "active";
  authentication: WhiteLabelAuthentication;
  checks: WhiteLabelChecks;
}

// --- the white-label list --------------------------------------------------------------------------

export interface WhiteLabelBrand {
  configured: boolean;
  name: string;
  has_logo: boolean;
}

export interface WhiteLabelRow {
  id: string;
  code: string;
  display_name: string;
  status: "draft" | "active" | "suspended" | "terminated";
  experience: ExperienceLabel[];
  brand: WhiteLabelBrand;
  domain: { hostname: string; status: string } | null;
  customers: number;
  updated_at: string;
}

export interface WhiteLabelFilters {
  q?: string;
  status?: string;
  domain_status?: string;
  brand?: string;
  cursor?: string;
}

const json = (body: unknown): string => JSON.stringify(body);

// --- what the white-label wizard needs, whoever is running it ---------------------------------------------

/** What a person types into the brand form. Nothing here is markup: the server
 * validates a name, a hex colour, an address and an https link, and refuses the rest. */
export interface BrandDraftBody {
  brand_name: string;
  accent_color: string;
  support_email: string;
  support_url: string;
  theme: Record<string, string>;
  logo_file_id?: string;
  /** Only the keys sent change; the rest of the stored portal is kept. */
  portal?: Partial<BrandPortalFields>;
}

/**
 * The one wizard, two callers. A partner's own portal and a staff member
 * preparing a partner both run these eight calls; only the address differs,
 * and the server decides who may (a partner user needs branding.manage, and a
 * second factor for anything about a domain; staff need partner management and
 * a second factor for everything). The wizard never chooses an endpoint by role.
 */
export interface WhiteLabelApi {
  brand: () => Promise<{ brand: Brand | null }>;
  saveBrand: (body: BrandDraftBody, expectedVersion?: number) => Promise<Brand>;
  uploadLogo: (file: File) => Promise<{ logo_file_id: string }>;
  domains: () => Promise<{ results: DomainRow[] }>;
  requestDomain: (hostname: string) => Promise<DomainRow>;
  verifyDomain: (domainId: string) => Promise<DomainRow>;
  activateDomain: (domainId: string) => Promise<DomainRow>;
  disableDomain: (domainId: string) => Promise<DomainRow>;
  /** A same-origin address for the stored logo, when there is one to preview. */
  logoUrl: string | null;
}

const staffPartner = (id: string) => `${admin}/partners/${encodeURIComponent(id)}`;

export function staffWhiteLabel(partnerId: string): WhiteLabelApi {
  return {
    brand: () => apiFetch<{ brand: Brand | null }>(`${staffPartner(partnerId)}/brand`),
    saveBrand: (body, expectedVersion) =>
      apiFetch<Brand>(`${staffPartner(partnerId)}/brand`, {
        method: "PUT",
        body: json({ ...body, expected_version: expectedVersion }),
      }),
    uploadLogo: (file) =>
      apiFetch<{ logo_file_id: string }>(`${staffPartner(partnerId)}/brand/logo`, {
        method: "POST",
        headers: { "Content-Type": file.type },
        body: file,
      }),
    domains: () => apiFetch<{ results: DomainRow[] }>(`${staffPartner(partnerId)}/domains`),
    requestDomain: (hostname) =>
      apiFetch<DomainRow>(`${staffPartner(partnerId)}/domains/request`, { method: "POST", body: json({ hostname }) }),
    verifyDomain: (id) => apiFetch<DomainRow>(`${admin}/domains/${id}/verify`, { method: "POST", body: json({}) }),
    activateDomain: (id) => apiFetch<DomainRow>(`${admin}/domains/${id}/activate`, { method: "POST", body: json({}) }),
    disableDomain: (id) => apiFetch<DomainRow>(`${admin}/domains/${id}/disable`, { method: "POST", body: json({}) }),
    logoUrl: `${staffPartner(partnerId)}/brand/logo/view`,
  };
}

export function portalWhiteLabel(partnerId: string): WhiteLabelApi {
  return {
    brand: () => partnerApi.brand(partnerId),
    saveBrand: (body, expectedVersion) => partnerApi.saveBrand(partnerId, body, expectedVersion),
    uploadLogo: (file) => partnerApi.uploadLogo(partnerId, file),
    domains: () => partnerApi.domains(partnerId),
    requestDomain: (hostname) => partnerApi.requestDomain(partnerId, hostname),
    verifyDomain: (id) => partnerApi.domainAction(partnerId, id, "verify"),
    activateDomain: (id) => partnerApi.domainAction(partnerId, id, "activate"),
    disableDomain: (id) => partnerApi.domainAction(partnerId, id, "disable"),
    logoUrl: null,
  };
}

export const partnerConsoleApi = {
  overview: () => apiFetch<PartnersOverview>(`${admin}/partners/overview`),
  recent: () => apiFetch<PartnersRecent>(`${admin}/partners/recent`),
  referrals: (partnerId: string, cursor?: string) =>
    apiFetch<PartnerReferrals>(`${admin}/partners/${partnerId}/referrals`, { query: { cursor } }),
  resellers: (q: ResellerFilters) => apiFetch<Page<ResellerRow>>(`${admin}/partners/resellers`, { query: { ...q } }),
  capabilityCatalog: () => apiFetch<CapabilityCatalog>(`${admin}/partners/capabilities`),
  products: () => apiFetch<{ results: ConsoleProduct[] }>(`${admin}/partners/products`),

  review: (body: ResellerDraftBody) =>
    apiFetch<ResellerReview>(`${admin}/partners/reseller-review`, { method: "POST", body: json(body) }),
  commission: (partnerId: string) => apiFetch<PartnerCommissionState>(`${admin}/partners/${partnerId}/commission`),
  assignCommission: (
    partnerId: string,
    body: { rate_bp: number; reason: string; expected_version: number; product_code?: string; effective_from?: string },
  ) =>
    apiFetch<{
      id: string;
      code: string;
      version: number;
      rate_bp: number;
      effective_from: string;
      created: boolean;
      previous_rate_bp: number | null;
    }>(
      `${admin}/partners/${partnerId}/commission`,
      { method: "POST", body: json(body) },
    ),
  setPartnerPrice: (partnerId: string, body: { plan_version_id: string; interval: string; amount_minor: number }) =>
    apiFetch<{ id: string; interval: string; amount_minor: number }>(`${admin}/partners/${partnerId}/prices`, {
      method: "POST",
      body: json(body),
    }),
  inviteAdministrator: (
    partnerId: string,
    email: string,
    role: string,
    extra: { first_name?: string; last_name?: string; send_invitation?: "now" | "on_activation" } = {},
  ) =>
    apiFetch<{ id: string; role: string; invited: boolean }>(`${admin}/partners/${partnerId}/administrators`, {
      method: "POST",
      body: json({ email, role, ...extra }),
    }),
  changeMemberRole: (partnerId: string, memberId: string, role: string) =>
    apiFetch<{ id: string; role: string }>(`${admin}/partners/${partnerId}/members/${memberId}/role`, {
      method: "POST",
      body: json({ role }),
    }),
  /** A partner's business details, contacts, support details, payout
   * preferences and staff notes (staff only). */
  profile: (partnerId: string) => apiFetch<PartnerProfile>(`${admin}/partners/${partnerId}/profile`),
  saveProfile: (partnerId: string, expectedVersion: number, body: ProfileInput) =>
    apiFetch<PartnerProfile>(`${admin}/partners/${partnerId}/profile`, {
      method: "PUT",
      body: json({ ...body, expected_version: expectedVersion }),
    }),
  updateProductGrant: (partnerId: string, grantId: string, body: { trial_allowed?: boolean; portal_visible?: boolean }) =>
    apiFetch<{ id: string; trial_allowed: boolean; portal_visible: boolean }>(
      `${admin}/partners/${partnerId}/product-grants/${grantId}`,
      { method: "PATCH", body: json(body) },
    ),
  /** Everything the white-label wizard shows for one partner, in one read. */
  whiteLabelSetup: (partnerId: string) => apiFetch<WhiteLabelSetup>(`${admin}/partners/${partnerId}/white-label`),
  /** Give the partner the white-label pair of capabilities (the same account). */
  enableWhiteLabel: (partnerId: string, reason: string) =>
    apiFetch<{ granted: string[] }>(`${admin}/partners/${partnerId}/white-label/enable`, {
      method: "POST",
      body: json({ reason }),
    }),
  whiteLabel: (q: WhiteLabelFilters) => apiFetch<Page<WhiteLabelRow>>(`${admin}/partners/white-label`, { query: { ...q } }),
  summary: (partnerId: string) => apiFetch<PartnerSummaryView>(`${admin}/partners/${partnerId}/summary`),
  /** One partner's customers (a partner id), or every partner's (none). */
  customers: (partnerId: string | undefined, q: CustomerFilters) =>
    apiFetch<Page<PartnerCustomerAdminRow>>(
      partnerId ? `${admin}/partners/${partnerId}/customers` : `${admin}/partners/customers`,
      { query: { ...q } },
    ),
  pricing: (partnerId: string) =>
    apiFetch<{ currency: string; results: PartnerPricingRow[] }>(`${admin}/partners/${partnerId}/pricing`),
  activity: (partnerId: string, cursor?: string) =>
    apiFetch<Page<AuditEntry>>(`${admin}/partners/${partnerId}/activity`, { query: { cursor } }),
  brand: (partnerId: string) => apiFetch<{ brand: StoredBrand | null }>(`${admin}/partners/${partnerId}/brand`),
  /** Send a customer's owner a new activation link (the old one stops working). */
  resendOwnerInvitation: (partnerId: string, organizationId: string) =>
    apiFetch<OwnerInvitationState>(
      `${admin}/partners/${partnerId}/customers/${organizationId}/owner-invitation/resend`,
      { method: "POST", body: json({}) },
    ),
  revokeOwnerInvitation: (partnerId: string, organizationId: string) =>
    apiFetch<OwnerInvitationState>(
      `${admin}/partners/${partnerId}/customers/${organizationId}/owner-invitation/revoke`,
      { method: "POST", body: json({}) },
    ),
  resendSetup: (partnerId: string, memberId: string) =>
    apiFetch<{ sent: boolean }>(`${admin}/partners/${partnerId}/members/${memberId}/resend-setup`, {
      method: "POST",
      body: json({}),
    }),
};

const portalPartner = (partnerId: string) => `/api/v1/partner/${encodeURIComponent(partnerId)}`;

/** The same two actions from the provisioning partner's own portal. */
export const portalOwnerInvitationApi = {
  resend: (partnerId: string, organizationId: string) =>
    apiFetch<OwnerInvitationState>(`${portalPartner(partnerId)}/customers/${organizationId}/owner-invitation/resend`, {
      method: "POST",
      body: json({}),
    }),
  revoke: (partnerId: string, organizationId: string) =>
    apiFetch<OwnerInvitationState>(`${portalPartner(partnerId)}/customers/${organizationId}/owner-invitation/revoke`, {
      method: "POST",
      body: json({}),
    }),
};

/** The customer owner's side of the emailed link. POST, so the token is in the
 * body and never in a URL - and so never in an access log. */
export const ownerInvitationApi = {
  preview: (token: string) =>
    apiFetch<OwnerInvitationPreview>("/api/v1/public/owner-invitations/preview", {
      method: "POST",
      body: json({ token }),
    }),
  accept: (token: string, body: { full_name?: string; password?: string } = {}) =>
    apiFetch<OwnerInvitationClaim>("/api/v1/public/owner-invitations/accept", {
      method: "POST",
      body: json({ token, ...body }),
    }),
};
