import type { UpgradeRequest, UpgradeState, UpgradeStatus, UpgradeTarget } from "@/lib/api/partnerPanel";
import type { ExperienceKey } from "./experiences";

/**
 * Partner upgrades (ADR-049): what the screens say, and which of them to draw.
 *
 * Nothing here decides what a partner may do. The server says where the partner
 * stands (`current`), which upgrades it may ask for (`eligible`), whether this
 * member may act (`can_request`) and what is open; these functions only turn that
 * answer into a layout. They read no stored state - not the address, not the
 * browser's storage - so editing either changes nothing a person is allowed to do.
 */

export const TARGETS: readonly UpgradeTarget[] = ["reseller", "white_label"];

export const TARGET_LABEL: Record<UpgradeTarget, string> = {
  reseller: "Reseller",
  white_label: "White-label",
};

/** One line under each card (the brief's words: nothing promised that the platform lacks). */
export const TARGET_LEAD: Record<UpgradeTarget, string> = {
  reseller: "Manage customers, subscriptions and direct sales.",
  white_label: "Sell under your own brand and domain.",
};

export const STATUS_LABEL: Record<UpgradeStatus, string> = {
  requested: "Started",
  profile_required: "More information needed",
  pending_approval: "Pending approval",
  approved: "Approved",
  rejected: "Not approved",
  cancelled: "Withdrawn",
};

/** Status is text first; the badge colour only reinforces it. */
export const STATUS_BADGE: Record<UpgradeStatus, string> = {
  requested: "w360-badge w360-badge-neutral",
  profile_required: "w360-badge w360-badge-warning",
  pending_approval: "w360-badge w360-badge-info",
  approved: "w360-badge w360-badge-success",
  rejected: "w360-badge w360-badge-danger",
  cancelled: "w360-badge w360-badge-neutral",
};

/** `white_label` <-> the address segment `white-label`. */
export const targetSlug = (target: UpgradeTarget): string => (target === "white_label" ? "white-label" : "reseller");

export function targetFromSlug(slug: string): UpgradeTarget | null {
  return TARGETS.find((t) => targetSlug(t) === slug) ?? null;
}

/** Where the upgrade page for a target lives inside a workspace. */
export const upgradeHref = (base: string, target?: UpgradeTarget): string =>
  target ? `${base}/upgrade/${targetSlug(target)}` : `${base}/upgrade`;

export const isTarget = (value: unknown): value is UpgradeTarget => value === "reseller" || value === "white_label";

export type GrowView =
  | { kind: "none" }
  /** A request is open: show where it stands and a way back into it. */
  | { kind: "open"; request: UpgradeRequest; canAct: boolean }
  /** Nothing is open: offer the upgrades above this workspace. */
  | { kind: "cards"; targets: UpgradeTarget[]; canAct: boolean; note: UpgradeNote | null };

/** A decided request worth saying a word about on the dashboard. */
export type UpgradeNote =
  | { kind: "approved"; target: UpgradeTarget }
  | { kind: "rejected"; target: UpgradeTarget; text: string };

/**
 * The "Grow Your Partnership" section for one workspace, from the server's answer.
 *
 * - an open request replaces the cards (a partner has at most one);
 * - otherwise exactly the upgrades the server says are possible are offered - so a
 *   Reseller is never offered Reseller and White-label is offered nothing;
 * - a request the partner cannot act on (a member without the right) is described,
 *   never given a button;
 * - the newest decision is mentioned: an approval while the partner is still looking
 *   at its old workspace (it then points to the new one), a refusal with staff's words.
 */
export function growView(state: UpgradeState, viewing: ExperienceKey): GrowView {
  if (state.open) return { kind: "open", request: state.open, canAct: state.can_request };
  const latest = state.recent[0];
  let note: UpgradeNote | null = null;
  if (latest?.status === "approved" && state.current === latest.to_experience && viewing !== state.current) {
    note = { kind: "approved", target: latest.to_experience };
  } else if (latest?.status === "rejected" && state.eligible.includes(latest.to_experience)) {
    note = { kind: "rejected", target: latest.to_experience, text: latest.decision_note };
  }
  if (state.eligible.length === 0 && note === null) return { kind: "none" };
  return { kind: "cards", targets: state.eligible, canAct: state.can_request, note };
}

/** The steps of one target, in the server's order. */
export function stepsFor(state: UpgradeState, target: UpgradeTarget): { key: string; label: string }[] {
  return state.steps[target] ?? [];
}
