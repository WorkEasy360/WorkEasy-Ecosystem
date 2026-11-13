import { apiFetch } from "./client";
import type { OrganizationsPage, SessionState } from "./types";

export type LoginErrorCode =
  | "invalid_credentials"
  | "account_locked"
  | "account_disabled"
  | "email_not_verified";

export interface LoginResult {
  user_id: string;
  email: string;
  name: string;
}

/** Issues the CSRF cookie as a side effect (backend auth_views module
 * docstring) - call this on shell/login mount, before any form can submit. */
export function getSession(): Promise<SessionState> {
  return apiFetch<SessionState>("/api/v1/auth/session");
}

/** `remember` is the Admin and Partner sign-ins' "Remember me": left out by every other form,
 * which keep the normal session. */
export function login(email: string, password: string, options: { remember?: boolean } = {}): Promise<LoginResult> {
  return apiFetch<LoginResult>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password, ...options }),
  });
}

/** Ends this sign-in everywhere it was handed to: the central sign-in host
 * and every portal (ADR-043). */
export function logout(): Promise<void> {
  return apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
}

/** Switch Account (ADR-066), step one, on a portal: signs this portal out and
 * answers where to go next - the central sign-in host's `/switch-account`
 * page (a signed hand-over in the fragment) on portal hosts, or this
 * portal's sign-in page on a single host. */
export function switchAccount(): Promise<{ redirect_url: string }> {
  return apiFetch("/api/v1/auth/switch-account", { method: "POST" });
}

/** Switch Account, step two, on the central sign-in host: ends its session if
 * it is the switching person's, and answers the sign-in page to open. */
export function finishSwitchAccount(handle: string): Promise<{ redirect_url: string }> {
  return apiFetch("/api/v1/auth/switch-account/finish", {
    method: "POST",
    body: JSON.stringify({ handle }),
  });
}

export type HandoffErrorCode = "portal_access_denied" | "handoff_invalid" | "login_required";

/** On the central sign-in host: hand this sign-in to one portal. The answer
 * is a URL whose fragment carries a one-time code for that portal. `mode` is
 * only where to go; the server decides whether this account may. */
export function issueHandoff(mode: string, next: string | null, state: string): Promise<{ redirect_url: string }> {
  return apiFetch("/api/v1/auth/handoff", {
    method: "POST",
    body: JSON.stringify({ mode, next, state }),
  });
}

/** On a portal host: spend the one-time code for this host's own session. */
export function redeemHandoff(code: string): Promise<{ next: string }> {
  return apiFetch("/api/v1/auth/handoff/redeem", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

/** Task 25-27: bounded and paginated - a user with hundreds of memberships
 * (Task 67) gets a page, never every row in one response. */
export function getOrganizations(
  options: { limit?: number; cursor?: string; search?: string } = {},
): Promise<OrganizationsPage> {
  return apiFetch("/api/v1/organizations", {
    query: { limit: options.limit, cursor: options.cursor, q: options.search },
  });
}
