/**
 * The organization's AI settings, usage and analytics shell, and the Super
 * Admin's AI registry (Phase 8).
 *
 * What never reaches the browser: a provider credential (the API shows only
 * whether one is configured), a prompt, a model response, a retrieved passage
 * or an event's payload. Usage is metadata - tokens, estimated cost, outcome.
 * Estimated AI cost is a spend-control figure, not an invoice.
 *
 * Every call goes through {@link apiFetch}: same origin, `no-store`, CSRF
 * header on writes. Nothing here is cached across users or organizations.
 */

import { apiFetch } from "./client";
import type { Page } from "./controlPlane";

const org = (orgSlug: string) => `/api/v1/orgs/${encodeURIComponent(orgSlug)}`;
const json = (body: unknown): string => JSON.stringify(body);

// --- the organization's AI -------------------------------------------------------------

export interface AiPolicy {
  ai_enabled: boolean;
  monthly_budget_micros: number | null;
  allowed_providers: string[];
  allow_provider_fallback: boolean;
  platform_suspended: boolean;
  platform_cap_micros: number;
  organization_cap_micros: number;
  premium_models_allowed: boolean;
  revision: number;
}

export interface AiProvider {
  key: string;
  display_name: string;
  status: string;
  data_region: string;
  retains_inputs_days: number | null;
  trains_on_inputs: boolean;
  data_policy_note: string;
}

export interface AiSpend {
  spent_micros: number;
  reserved_micros: number;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  embedding_tokens: number;
}

export interface AiUsage {
  currency: string;
  period_start: string;
  resets_at: string;
  status: "enabled" | "disabled" | "suspended_by_platform";
  organization: AiSpend & { limit_micros: number };
  products: (AiSpend & { product_code: string; ai_entitled: boolean; limit_micros: number | null })[];
  note: string;
}

export interface AiRequestRow {
  request_id: string;
  product_code: string;
  operation: string;
  skill: string;
  skill_version: number | null;
  status: string;
  outcome: string;
  model: string;
  provider: string;
  fallback_used: boolean;
  input_tokens: number;
  output_tokens: number;
  embedding_tokens: number;
  cost_micros: number;
  created_at: string;
}

export function getAiSettings(orgSlug: string): Promise<{ policy: AiPolicy; providers: AiProvider[] }> {
  return apiFetch(`${org(orgSlug)}/ai/settings`);
}

export interface AiSettingsChange {
  revision: number;
  ai_enabled?: boolean;
  monthly_budget_micros?: number | null;
  allowed_providers?: string[];
  allow_provider_fallback?: boolean;
}

export function updateAiSettings(orgSlug: string, change: AiSettingsChange): Promise<{ policy: AiPolicy }> {
  return apiFetch(`${org(orgSlug)}/ai/settings`, { method: "PATCH", body: json(change) });
}

export function getAiUsage(orgSlug: string): Promise<AiUsage> {
  return apiFetch(`${org(orgSlug)}/ai/usage`);
}

export function listAiRequests(orgSlug: string, cursor?: string): Promise<Page<AiRequestRow>> {
  return apiFetch(`${org(orgSlug)}/ai/requests`, { query: { cursor } });
}

// --- the analytics shell ------------------------------------------------------------------

export interface MetricDefinition {
  key: string;
  version: number;
  owner: string;
  status: string;
  description: string;
  unit: string;
  aggregation: "sum" | "count";
  value_type: "integer" | "money";
  event: { type: string; version: number };
  dimensions: { name: string; values: string[] }[];
  shared_with: string[];
}

export interface MetricRow {
  bucket: string;
  dimensions: Record<string, string>;
  count: number;
  value?: number;
  amount_minor?: number;
  currency?: string;
}

export interface MetricResult {
  metric: { key: string; version: number; unit: string; aggregation: string; value_type: string };
  granularity: "hour" | "day" | "month";
  start: string;
  end: string;
  group_by: string[];
  results: MetricRow[];
  freshness: {
    pending_facts: number;
    oldest_pending_since: string | null;
    last_projected_at: string | null;
    up_to_date: boolean;
  };
}

export interface MetricQuery {
  metric: string;
  version?: number;
  start: string;
  end: string;
  granularity: "hour" | "day" | "month";
  group_by?: string;
}

export function listMetrics(orgSlug: string): Promise<{ results: MetricDefinition[] }> {
  return apiFetch(`${org(orgSlug)}/analytics/metrics`);
}

export function queryMetric(orgSlug: string, query: MetricQuery): Promise<MetricResult> {
  return apiFetch(`${org(orgSlug)}/analytics/query`, {
    query: {
      metric: query.metric,
      version: query.version,
      start: query.start,
      end: query.end,
      granularity: query.granularity,
      group_by: query.group_by || undefined,
    },
  });
}

// --- the Super Admin ---------------------------------------------------------------------

export interface RegistryProvider extends AiProvider {
  adapter: string;
  credential_configured: boolean;
}

export interface RegistryModel {
  key: string;
  provider: string;
  model_id: string;
  status: "active" | "deprecated" | "disabled";
  tier: "standard" | "premium";
  capabilities: string[];
  context_window_tokens: number;
  max_output_tokens: number;
  embedding_dimensions: number | null;
  priority: number;
  circuit_open_until: string | null;
  price: {
    version: number;
    currency: string;
    input_micros_per_mtok: number;
    output_micros_per_mtok: number;
  } | null;
}

export interface Registry {
  providers: RegistryProvider[];
  models: RegistryModel[];
}

export interface OrganizationAi {
  organization_id: string;
  policy: AiPolicy;
  currency: string;
  periods: (AiSpend & { scope: string })[];
  recent_executions: {
    id: string;
    product_code: string;
    operation: string;
    skill_key: string;
    status: string;
    outcome: string;
    model_key: string;
    provider_key: string;
    cost_micros: number;
    fallback_used: boolean;
    created_at: string;
  }[];
}

const admin = "/api/v1/admin/ai";

export const adminAiApi = {
  registry: (): Promise<Registry> => apiFetch(`${admin}/registry`),
  setProviderStatus: (key: string, status: string, reason: string): Promise<Registry> =>
    apiFetch(`${admin}/providers/${encodeURIComponent(key)}/status`, {
      method: "POST",
      body: json({ status, reason }),
    }),
  updateModel: (key: string, change: { status?: string; priority?: number; reason: string }): Promise<Registry> =>
    apiFetch(`${admin}/models/${encodeURIComponent(key)}`, { method: "PATCH", body: json(change) }),
  organization: (organizationId: string): Promise<OrganizationAi> =>
    apiFetch(`${admin}/organizations/${encodeURIComponent(organizationId)}`),
  setOrganizationPolicy: (
    organizationId: string,
    change: { platform_suspended?: boolean; platform_cap_micros?: number | null; premium_models_allowed?: boolean; reason: string },
  ): Promise<OrganizationAi> =>
    apiFetch(`${admin}/organizations/${encodeURIComponent(organizationId)}/policy`, {
      method: "POST",
      body: json(change),
    }),
};

// --- presentation ------------------------------------------------------------------------

/** Estimated cost in micro-units, for display only - the browser never sums
 * or sends an amount. */
export function formatMicros(micros: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: micros > 0 && micros < 10_000 ? 4 : 2,
    }).format(micros / 1_000_000);
  } catch {
    return `${micros} ${currency} micro-units`;
  }
}
