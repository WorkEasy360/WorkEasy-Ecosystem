import { apiFetch } from "./client";
import type { OrganizationSummary, SessionState } from "./types";

export type LoginErrorCode =
  | "invalid_credentials"
  | "account_locked"
  | "account_disabled";

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

export function getOrganizations(): Promise<{ organizations: OrganizationSummary[] }> {
  return apiFetch("/api/v1/organizations");
}
