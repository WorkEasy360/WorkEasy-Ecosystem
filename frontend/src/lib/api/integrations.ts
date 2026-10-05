/**
 * The organization's own integrations (Phase 6): connections, webhook
 * subscriptions and deliveries, and automation rules.
 *
 * Two responses carry a signing secret - create and rotate. Their callers
 * show it once and then drop it: it is never written to localStorage,
 * sessionStorage, a URL or a log (Task 83), and nothing here caches it.
 */

import { apiFetch } from "./client";
import type {
  AutomationExecution,
  AutomationRule,
  CatalogEvent,
  Connection,
  ConnectionStatus,
  DeliverySummary,
  Page,
  RuleDefinition,
  RuleStatus,
} from "./types";

const base = (orgSlug: string) => `/api/v1/orgs/${encodeURIComponent(orgSlug)}`;

export function listConnections(orgSlug: string, cursor?: string): Promise<Page<Connection>> {
  return apiFetch(`${base(orgSlug)}/connections`, { query: { cursor } });
}

export function createWebhookConnection(
  orgSlug: string,
  displayName: string,
  url: string,
): Promise<{ connection: Connection; secret: string; notice: string }> {
  return apiFetch(`${base(orgSlug)}/connections`, {
    method: "POST",
    body: JSON.stringify({ connector_key: "webhook", display_name: displayName, config: { url } }),
  });
}

export function setConnectionStatus(
  orgSlug: string,
  id: string,
  status: ConnectionStatus,
): Promise<Connection> {
  return apiFetch(`${base(orgSlug)}/connections/${id}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

export function rotateSecret(
  orgSlug: string,
  id: string,
): Promise<{ version: number; secret: string; previous_valid_until: string }> {
  return apiFetch(`${base(orgSlug)}/connections/${id}/secret`, { method: "POST" });
}

export function getSubscriptions(orgSlug: string, id: string): Promise<{ event_filters: string[] }> {
  return apiFetch(`${base(orgSlug)}/connections/${id}/subscriptions`);
}

export function setSubscriptions(
  orgSlug: string,
  id: string,
  filters: string[],
): Promise<{ event_filters: string[] }> {
  return apiFetch(`${base(orgSlug)}/connections/${id}/subscriptions`, {
    method: "PUT",
    body: JSON.stringify({ event_filters: filters }),
  });
}

export function pingConnection(orgSlug: string, id: string): Promise<{ delivery_id: string }> {
  return apiFetch(`${base(orgSlug)}/connections/${id}/ping`, { method: "POST" });
}

export function getEventCatalog(orgSlug: string): Promise<{ results: CatalogEvent[] }> {
  return apiFetch(`${base(orgSlug)}/event-catalog`);
}

export function listDeliveries(
  orgSlug: string,
  filters: { status?: string; cursor?: string } = {},
): Promise<Page<DeliverySummary>> {
  return apiFetch(`${base(orgSlug)}/deliveries`, { query: filters });
}

export function replayDelivery(orgSlug: string, id: string): Promise<{ delivery_id: string }> {
  return apiFetch(`${base(orgSlug)}/deliveries/${id}/replay`, { method: "POST" });
}

export function listRules(orgSlug: string, cursor?: string): Promise<Page<AutomationRule>> {
  return apiFetch(`${base(orgSlug)}/automations`, { query: { cursor } });
}

export function createRule(
  orgSlug: string,
  name: string,
  definition: RuleDefinition,
): Promise<AutomationRule> {
  return apiFetch(`${base(orgSlug)}/automations`, {
    method: "POST",
    body: JSON.stringify({ name, definition }),
  });
}

export function setRuleStatus(
  orgSlug: string,
  rule: AutomationRule,
  status: RuleStatus,
): Promise<AutomationRule> {
  return apiFetch(`${base(orgSlug)}/automations/${rule.id}/status`, {
    method: "POST",
    body: JSON.stringify({ status, revision: rule.revision }),
  });
}

export function listExecutions(orgSlug: string, ruleId: string): Promise<Page<AutomationExecution>> {
  return apiFetch(`${base(orgSlug)}/automations/${ruleId}/executions`);
}
