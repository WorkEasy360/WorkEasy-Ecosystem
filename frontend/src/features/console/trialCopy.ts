import { ApiError } from "@/lib/api/client";

/** Why a product trial could not be started, in words - never the raw reason
 * code (Phase 11; mirrors `TrialNotAllowed.reason` in the backend). */
const TRIAL_DENIAL_COPY: Record<string, string> = {
  not_offered: "This product doesn't offer a free trial.",
  plan_not_eligible: "That plan isn't eligible for a free trial.",
  product_unavailable: "This product isn't available right now.",
  already_used: "Your organization has already trialled this product.",
  already_owned: "Your organization already has this product.",
};

export function trialDenialMessage(err: unknown): string {
  if (err instanceof ApiError && typeof err.body === "object" && err.body !== null) {
    const reason = (err.body as { reason?: string }).reason;
    if (reason && TRIAL_DENIAL_COPY[reason]) return TRIAL_DENIAL_COPY[reason];
  }
  return "That trial couldn't be started right now.";
}
