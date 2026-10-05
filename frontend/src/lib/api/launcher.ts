import { apiFetch } from "./client";
import type { AppDirectoryEntry, SeatOverview } from "./types";

export function getAppDirectory(orgSlug: string): Promise<{ apps: AppDirectoryEntry[] }> {
  return apiFetch(`/api/v1/orgs/${orgSlug}/apps`);
}

export const LAUNCH_PATH = (orgSlug: string, productCode: string): string =>
  `/api/v1/orgs/${orgSlug}/apps/${productCode}/launch`;

export interface LaunchResult {
  redirect_url: string;
  state: string;
}

/**
 * Requests a launch. The backend re-checks access and, on success, answers
 * with the **one, server-computed destination** to send the browser to next
 * - never a value this app chose or a client-supplied URL (Task 55: launch
 * URLs are admin-configured; the browser never submits one).
 *
 * Not a real `<form>` POST to a 302, on purpose: every real product's launch
 * URL is a different origin, and this app's own CSP (`form-action 'self'`)
 * refuses a form submission that answers with a cross-origin redirect -
 * verified in Chrome, not assumed. `apiFetch` already sends
 * `Accept: application/json`, which is what makes the backend answer with
 * this JSON body instead of a 302 (`launcher_views._wants_json`). A denial
 * (409) throws `ApiError`, whose `body.state` is one of
 * `AppDirectoryEntry["state"]` - the caller renders it with
 * `launchCopy.launchDenialMessage`, never the raw code.
 *
 * The caller is responsible for the actual navigation
 * (`window.location.assign(redirect_url)`), which is what makes it a real
 * top-level browser navigation and not a background fetch of the product's
 * page (Task 17/54 preserved: this function never reads the response body
 * as anything but a URL to hand the browser, and never handles a token).
 */
export function launchProduct(orgSlug: string, productCode: string): Promise<LaunchResult> {
  return apiFetch(LAUNCH_PATH(orgSlug, productCode), { method: "POST" });
}

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
