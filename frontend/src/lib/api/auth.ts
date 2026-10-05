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

export function logout(): Promise<void> {
  return apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
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
