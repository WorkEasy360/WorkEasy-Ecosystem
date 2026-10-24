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

export function login(email: string, password: string): Promise<LoginResult> {
  return apiFetch<LoginResult>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

/** Ends this sign-in everywhere it was handed to: the central sign-in host
 * and every portal (ADR-043). */
export function logout(): Promise<void> {
  return apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
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
