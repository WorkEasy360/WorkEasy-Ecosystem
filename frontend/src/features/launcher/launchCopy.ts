import type { AppDirectoryEntry } from "@/lib/api/types";

/** Why a launch was refused, in words - never the raw code, and this fixed
 * text for a code neither side of the launcher knows (Task 41: the UI must
 * not lie, and must offer a real next step). Shared by the button that tried
 * the launch and the page it may have to redirect to (a stale link, a
 * pre-JS fallback). */
export const LAUNCH_DENIAL_COPY: Record<AppDirectoryEntry["state"] | string, string> = {
  no_seat: "You don't have a seat for that product yet. Ask an administrator to assign you one.",
  no_entitlement: "Your organization doesn't own that product yet.",
  integration_suspended: "That product is temporarily unavailable. Please try again shortly.",
  product_unavailable: "That product isn't available yet.",
  // Phase 12: the organization itself is read-only (suspended) - a refusal
  // with an error *code*, not a launcher state.
  organization_not_writable:
    "Your organization is read-only at the moment, so products can't be opened. An administrator can contact Workeasy360 support.",
};

export function launchDenialMessage(state: string | null | undefined): string {
  return (state && LAUNCH_DENIAL_COPY[state]) || "That app couldn't be opened right now.";
}
