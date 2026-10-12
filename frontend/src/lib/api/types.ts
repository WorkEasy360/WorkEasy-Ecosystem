/** Wire types, matching the Django JSON shapes exactly (interfaces/api/v1/*).
 * One file so a backend field rename is one place to fix on this side. */

export interface SessionState {
  authenticated: boolean;
  user_id?: string;
  email?: string;
  name?: string;
  /** Phase 8 extension: whether an organization's "require MFA" can be met. */
  mfa_enrolled?: boolean;
  /** Which consoles the shell may offer. Navigation only - every request is
   * authorized again by the backend. */
  consoles?: { staff: boolean; partner: boolean };
}

export interface OrganizationSummary {
  organization_id: string;
  slug: string;
  display_name: string;
  status: string;
  membership_id: string;
  is_owner: boolean;
}

export interface OrganizationsPage {
  organizations: OrganizationSummary[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface CreateOrganizationResult {
  organization_id: string;
  slug: string;
  display_name: string;
  membership_id: string;
}

export interface OrganizationDetail {
  organization_id: string;
  slug: string;
  display_name: string;
  status: string;
  write_allowed: boolean;
}

/** The five closed states from platform.launcher.access.AppAccessState. */
export type AppAccessState =
  | "active"
  | "no_seat"
  | "no_entitlement"
  | "integration_suspended"
  | "product_unavailable";

/** Whether single sign-on can complete for this product right now (Phase 11):
 * `connected` means an unrevoked, unexpired credential exists, never that the
 * product's own client code works - that is proven only by a real sign-in. */
export type SsoStatus = "connected" | "integration_required";

/** The last bounded reachability check's result (Phase 11) - telemetry, never
 * an access decision: an unhealthy product is still `can_launch`. */
export interface ProductHealth {
  status: "unknown" | "healthy" | "degraded" | "unreachable";
  checked_at: string | null;
}

export interface AppDirectoryEntry {
  product_code: string;
  display_name: string;
  description: string;
  licensing_mode: "per_seat" | "per_organization" | "usage";
  state: AppAccessState;
  can_launch: boolean;
  /** Phase 9: the organization's entitlement status when it holds the
   * product - "trial", "active", "past_due"... - else null. */
  entitlement_status: string | null;
  // --- Phase 11: presentation, connection facts, and what is held ---------
  slug: string;
  tagline: string;
  category: string;
  icon: string;
  sso: SsoStatus;
  health: ProductHealth;
  /** A running product trial, only when this organization has one. */
  trial: { id: string | null; ends_at: string; days_left: number } | null;
  /** The plan held, shown only to a member who may see subscriptions. */
  plan: { code: string; name: string } | null;
  /** Seat usage, shown only to a member who may administer seats or see
   * subscriptions. */
  seats: { total: number; used: number } | null;
  renews_at: string | null;
  cancel_at: string | null;
  /** ADR-044: how this person enters the product - whether Workeasy360
   * sign-in works for it, their own setting, and what therefore happens.
   * Decided by the server; null when the product has no registration. */
  sign_in: AppSignIn | null;
}

/** The card's sign-in facts (ADR-044). */
export interface AppSignIn {
  sso: "enabled" | "integration_required" | "disabled";
  mode: "sso_only" | "sso_optional" | "local_only";
  product_login_available: boolean;
  override: "inherit" | "auto_sso" | "ask" | "product_login";
  preference: "auto_sso" | "ask" | "product_login";
  effective: "auto_sso" | "ask" | "product_login";
  constraint: "" | "sso_unavailable" | "product_requires_sso";
}

export interface SeatMember {
  membership_id: string;
  email: string;
  held: boolean;
}

export interface SeatOverview {
  product_code: string;
  display_name: string;
  licensing_mode: string;
  seats_total: number;
  seats_assigned: number;
  members: SeatMember[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface MembershipSummary {
  membership_id: string;
  user_id: string;
  email: string;
  status: string;
  member_type: string;
  is_owner: boolean;
  joined_at: string | null;
}

export interface MfaFactor {
  factor_id: string | null;
  kind: "totp" | "webauthn" | "recovery_code" | "sms";
  label: string;
  confirmed: boolean;
  created_at: string | null;
  last_used_at: string | null;
}

export interface TotpEnrollmentStart {
  factor_id: string;
  secret: string;
  provisioning_uri: string;
}

// --- Phase 6: connections, webhooks, automation -------------------------

export type ConnectionStatus = "draft" | "active" | "suspended" | "disabled";

export interface Connection {
  id: string;
  connector_key: string;
  display_name: string;
  status: ConnectionStatus;
  status_reason: string;
  revision: number;
  /** Origin only (e.g. "https://hooks.example.com/…") - the full URL can
   * itself carry a token, so the API never returns it after creation. */
  destination: string;
  health: {
    consecutive_failures: number;
    circuit_open_until: string | null;
    last_success_at: string | null;
    last_failure_at: string | null;
  };
  activated_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SecretVersion {
  version: number;
  hint: string;
  status: "active" | "previous";
  expires_at: string | null;
  created_at: string;
}

export interface ConnectionDetail extends Connection {
  secrets: SecretVersion[];
}

export interface Page<T> {
  results: T[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface CatalogEvent {
  ref: string;
  event_type: string;
  version: number;
  product_code: string;
  description: string;
}

export type DeliveryStatus = "pending" | "in_progress" | "retry" | "succeeded" | "dead";

export interface DeliverySummary {
  id: string;
  status: DeliveryStatus;
  origin: "subscription" | "automation" | "ping" | "replay";
  connection_id: string;
  connection_name: string;
  event: { event_id: string; event_type: string; event_version: number; source: string } | null;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string | null;
  last_attempt_at: string | null;
  last_http_status: number | null;
  last_category: string;
  dead_reason: string;
  replay_of: string | null;
  replayable: boolean;
  created_at: string;
}

export type RuleStatus = "draft" | "active" | "paused" | "archived";

export interface ConditionRule {
  path: string;
  op: "equals" | "not_equals" | "contains" | "exists" | "greater_than" | "less_than" | "in";
  value: string | number | boolean | null | (string | number)[];
}

export interface RuleDefinition {
  trigger:
    | { kind: "event"; event: string }
    | { kind: "schedule"; schedule: Record<string, unknown> };
  conditions: { match: "all" | "any"; rules: ConditionRule[] };
  actions: { type: string; config: Record<string, unknown> }[];
}

export interface AutomationRule {
  id: string;
  name: string;
  description: string;
  status: RuleStatus;
  revision: number;
  trigger_kind: "event" | "schedule";
  trigger_filter: string;
  definition: RuleDefinition | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AutomationExecution {
  id: string;
  status: string;
  rule_version: number;
  attempts: number;
  conditions_matched: boolean | null;
  failure_category: string;
  created_at: string;
  event: { event_type: string; event_version: number } | null;
}
