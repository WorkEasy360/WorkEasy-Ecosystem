/**
 * Automatic product access (2026-11-28): what this person can do with each
 * product right now, Start Free Trial in one request, Retry Setup, and the
 * workspace a customer without an organization gets automatically.
 *
 * The server decides every action from the live launch decision (entitled,
 * seated, integration accepting sign-in); the page only maps the action to a
 * button. "Open Product" is never shown before the server said `open`.
 */

import { apiFetch } from "./client";
import type { ProductTrial } from "./console";

const org = (slug: string) => `/api/v1/orgs/${encodeURIComponent(slug)}`;

/** The one primary action a product shows. Closed set, decided on the server. */
export type AccessAction =
  | "open"
  | "start_trial"
  | "buy"
  | "upgrade"
  | "setting_up"
  | "retry_setup"
  | "ask_owner"
  | "unavailable"
  | "contact_sales";

export interface ProductAccess {
  action: AccessAction;
  /** Why, when it matters to the words shown: `seat_missing`, `provisioning`,
   * `provisioning_failed`, `seats_full`, `no_seat`, `owner_starts_trials`,
   * `trial_ended`, `coming_soon`, `not_ready`, `temporarily_unavailable`. */
  reason: string | null;
  /** `trial`, `subscription`, or null when the organization does not hold it. */
  holding: "trial" | "subscription" | null;
  launch_state: string | null;
  upgrade_available: boolean;
  trial_ends_at: string | null;
  trial_days_left: number | null;
  trial_plan_code?: string;
  trial_days?: number | null;
  trial_outcome?: string | null;
}

export interface ProductAccessMap {
  products: Record<string, ProductAccess>;
}

export interface Workspace {
  organization_id: string;
  slug: string;
  display_name: string;
  created: boolean;
}

export const productAccessApi = {
  list: (slug: string) => apiFetch<ProductAccessMap>(`${org(slug)}/product-access`),
  startTrial: (slug: string, productCode: string, key: string, planCode?: string) =>
    apiFetch<{ trial: ProductTrial | null; access: ProductAccess }>(
      `${org(slug)}/product-access/${encodeURIComponent(productCode)}/trial`,
      {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify(planCode ? { plan_code: planCode } : {}),
      },
    ),
  repair: (slug: string, productCode: string) =>
    apiFetch<{ access: ProductAccess }>(`${org(slug)}/product-access/${encodeURIComponent(productCode)}/repair`, {
      method: "POST",
    }),
  /** The organization this person works in; created (they become its owner)
   * when they have none. Several organizations: refused - they choose one. */
  ensureWorkspace: (key: string, name?: string) =>
    apiFetch<Workspace>("/api/v1/organizations/workspace", {
      method: "POST",
      body: JSON.stringify({ idempotency_key: key, ...(name && name.trim() ? { name: name.trim() } : {}) }),
    }),
};
