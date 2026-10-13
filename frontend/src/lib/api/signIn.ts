import { apiFetch } from "./client";

/**
 * Universal, user-controlled product sign-in (ADR-044).
 *
 * Everything here is the person's own: their default for every product, any
 * per-product override, and their answer on the access chooser. The server
 * decides what each product offers and what therefore happens; this module
 * only carries the person's choices there and the server's answers back.
 */

/** A stored preference. `inherit` exists only per product ("use my default"). */
export type SignInPreferenceValue = "inherit" | "auto_sso" | "ask" | "product_login";
/** What actually happens for a product (never `inherit`). */
export type EffectiveSignIn = Exclude<SignInPreferenceValue, "inherit">;
/** Whether Workeasy360 sign-in works for a product, as the server sees it. */
export type SsoState = "enabled" | "integration_required" | "disabled";
/** How the product itself lets people in (its registration). */
export type ProductSignInMode = "sso_only" | "sso_optional" | "local_only";

export interface ProductSignInRow {
  product_code: string;
  display_name: string;
  name: string;
  icon: string;
  mode: ProductSignInMode;
  sso: SsoState;
  product_login_available: boolean;
  /** This person's per-product setting (`inherit` when none). */
  override: SignInPreferenceValue;
  /** Their own wish for this product after inheriting the default. */
  preference: EffectiveSignIn;
  effective: EffectiveSignIn;
  /** Why `effective` differs from `preference`, or "". */
  constraint: "" | "sso_unavailable" | "product_requires_sso";
  /** The per-product values worth offering (empty: nothing to choose). */
  choices: SignInPreferenceValue[];
}

export interface SignInPreferences {
  default: EffectiveSignIn;
  default_is_set: boolean;
  default_choices: EffectiveSignIn[];
  products: ProductSignInRow[];
}

export interface SignInPreferenceChanges {
  default?: EffectiveSignIn;
  products?: Record<string, SignInPreferenceValue>;
}

export function getSignInPreferences(): Promise<SignInPreferences> {
  return apiFetch("/api/v1/auth/sign-in-preferences");
}

export function saveSignInPreferences(changes: SignInPreferenceChanges): Promise<SignInPreferences> {
  return apiFetch("/api/v1/auth/sign-in-preferences", { method: "PUT", body: JSON.stringify(changes) });
}

/** "Reset all to default": the platform default (Automatic) again, every override gone. */
export function resetSignInPreferences(): Promise<SignInPreferences> {
  return apiFetch("/api/v1/auth/sign-in-preferences", { method: "DELETE" });
}

// --- the access chooser ------------------------------------------------------

/** What the access chooser may show: display facts only. */
export interface PendingAccess {
  product: { code: string; name: string; icon: string };
  organization: { name: string };
  account: { email: string; name: string };
  choices: { workeasy360: boolean; product: boolean };
  remembered: SignInPreferenceValue;
}

export type AccessChoice = "workeasy360" | "product" | "switch_account" | "cancel";

export function getPendingAccess(resume: string): Promise<PendingAccess> {
  return apiFetch("/api/v1/integrations/oauth/pending", { query: { resume } });
}

/**
 * The person's answer. The server replies with the one place to go next -
 * always a URL it built from the registry, never one this page chose - and
 * the page navigates there itself (a cross-origin destination cannot be a
 * redirect of a `fetch`).
 */
export function answerPendingAccess(
  resume: string,
  choice: AccessChoice,
  remember: boolean,
): Promise<{ redirect_url: string }> {
  return apiFetch("/api/v1/integrations/oauth/pending/choose", {
    method: "POST",
    body: JSON.stringify({ resume, choice, remember }),
  });
}

// --- RP-initiated sign-out ---------------------------------------------------

export interface SignOutRequest {
  product: { name: string };
  signed_in: boolean;
  account: { email: string } | null;
}

export function getSignOutRequest(request: string): Promise<SignOutRequest> {
  return apiFetch("/api/v1/integrations/oauth/logout/request", { query: { request } });
}

export function confirmSignOut(request: string, signOut: boolean): Promise<{ redirect_url: string }> {
  return apiFetch("/api/v1/integrations/oauth/logout/confirm", {
    method: "POST",
    body: JSON.stringify({ request, sign_out: signOut }),
  });
}
