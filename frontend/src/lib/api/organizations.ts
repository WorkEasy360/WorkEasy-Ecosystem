import { apiFetch } from "./client";
import type { MembershipSummary, OrganizationDetail } from "./types";

export function getOrganizationDetail(orgSlug: string): Promise<OrganizationDetail> {
  return apiFetch(`/api/v1/orgs/${orgSlug}/organization`);
}

export function getMembers(
  orgSlug: string,
  options: { limit?: number; cursor?: string } = {},
): Promise<{ results: MembershipSummary[]; next_cursor: string | null; has_more: boolean }> {
  return apiFetch(`/api/v1/orgs/${orgSlug}/memberships`, {
    query: { limit: options.limit, cursor: options.cursor },
  });
}
