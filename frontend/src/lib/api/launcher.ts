import { apiFetch } from "./client";
import type { AppDirectoryEntry, SeatOverview } from "./types";

export function getAppDirectory(orgSlug: string): Promise<{ apps: AppDirectoryEntry[] }> {
  return apiFetch(`/api/v1/orgs/${orgSlug}/apps`);
}

/**
 * Requests a launch. The backend re-checks access and, if allowed, answers
 * with a redirect it expects the *browser* to follow as a full navigation
 * (Task 54: never an iframe) - so this throws on denial (409) and otherwise
 * returns nothing for the caller to do except navigate the browser itself,
 * via a real `<form>` submission or `window.location`, never a `fetch`
 * that would swallow the redirect (Task 17: no raw token handling here -
 * the launcher never sees a code, a token or the product's launch_url).
 */
export const LAUNCH_PATH = (orgSlug: string, productCode: string): string =>
  `/api/v1/orgs/${orgSlug}/apps/${productCode}/launch`;

export function getSeatOverview(
  orgSlug: string,
  productCode: string,
): Promise<SeatOverview> {
  return apiFetch(`/api/v1/orgs/${orgSlug}/apps/${productCode}/seats`);
}

export function assignSeat(
  orgSlug: string,
  productCode: string,
  membershipId: string,
): Promise<void> {
  return apiFetch(`/api/v1/orgs/${orgSlug}/apps/${productCode}/seats/${membershipId}`, {
    method: "POST",
  });
}

export function releaseSeat(
  orgSlug: string,
  productCode: string,
  membershipId: string,
): Promise<void> {
  return apiFetch(`/api/v1/orgs/${orgSlug}/apps/${productCode}/seats/${membershipId}`, {
    method: "DELETE",
  });
}
