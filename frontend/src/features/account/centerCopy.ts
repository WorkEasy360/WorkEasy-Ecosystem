import type { ConnectedProduct } from "@/lib/api/console";
import { formatDateTime, formatRelative } from "@/features/controlplane/kit";
import type { Tone } from "@/features/controlplane/kit";

/**
 * The Accounts Control Center's words for what the platform knows (ADR-072) - pure,
 * so the rules are tested once:
 *
 * - **Never "Active" from history.** No signal says a product session exists right now:
 *   a product's own sign-out, or its session timing out, never reaches WorkEasy360. What
 *   is known is "the WorkEasy360 sign-in that opened it is still open", "last signed in",
 *   "last seen" (the product's last question about you) - or "unknown".
 * - Purchased, assigned, connected, signed in before, sign-in still open, and ended or
 *   signed out are different facts and are said differently.
 */

export interface Chip {
  label: string;
  tone: Tone;
}

type OrgAccess = NonNullable<ConnectedProduct["organizations"][number]["access"]>;

/** "Purchased" / "Trial" / "Payment overdue" - what the organization holds. */
export function holdingLabel(entitlementStatus: string | null | undefined): Chip | null {
  switch (entitlementStatus) {
    case null:
    case undefined:
    case "":
      return null;
    case "trial":
      return { label: "Trial", tone: "info" };
    case "past_due":
    case "grace":
      return { label: "Purchased - payment overdue", tone: "warning" };
    case "suspended":
      return { label: "Purchased - suspended", tone: "danger" };
    default:
      return { label: "Purchased", tone: "success" };
  }
}

/** Whether this person may open it in that organization: the launcher's state, in words. */
export function assignmentLabel(access: Pick<OrgAccess, "state" | "licensing_mode"> | null | undefined): Chip {
  if (!access) return { label: "No access", tone: "neutral" };
  switch (access.state) {
    case "active":
      return access.licensing_mode === "per_seat"
        ? { label: "Seat assigned to you", tone: "success" }
        : { label: "Included for every member", tone: "success" };
    case "no_seat":
      return { label: "Not assigned to you", tone: "warning" };
    case "integration_suspended":
      return { label: "Temporarily unavailable", tone: "warning" };
    case "no_entitlement":
      return { label: "Not purchased", tone: "neutral" };
    default:
      return { label: "Not available", tone: "neutral" };
  }
}

/** "Last seen 5 min ago" - the product's last entitlement question about you - or unknown. */
export function lastSeenLabel(iso: string | null | undefined, now: number = Date.now()): string {
  return iso ? `Last seen ${formatRelative(iso, now).toLowerCase()}` : "Last seen: unknown";
}

export function lastSignedInLabel(iso: string | null | undefined, now: number = Date.now()): string {
  return iso ? `Last signed in ${formatRelative(iso, now).toLowerCase()}` : "Never signed in with WorkEasy360";
}

/** The session line. About the WorkEasy360 sign-in that opened the product - never a
 * claim about the product's own session. */
export function sessionLabel(product: Pick<ConnectedProduct, "session" | "sso_connected">): Chip & { detail: string } {
  const { platform_sign_in: state, since, ended_at: ended } = product.session;
  if (state === "open") {
    return {
      label: "WorkEasy360 sign-in still open",
      tone: "info",
      detail: `The sign-in you opened it with${since ? ` (${formatDateTime(since)})` : ""} has not ended. The product may have ended its own session; it does not tell WorkEasy360.`,
    };
  }
  if (state === "ended") {
    return {
      label: "Signed out",
      tone: "neutral",
      detail: ended
        ? `The WorkEasy360 sign-in it was opened with ended ${formatDateTime(ended)}.`
        : "The WorkEasy360 sign-in it was opened with has ended.",
    };
  }
  if (state === "untracked") {
    return {
      label: "Session: unknown",
      tone: "neutral",
      detail: "This product does not take WorkEasy360's sign-out, so whether it is signed in is unknown.",
    };
  }
  return {
    label: product.sso_connected ? "Session: unknown" : "Not signed in yet",
    tone: "neutral",
    detail: product.sso_connected ? "Whether it is signed in now is unknown." : "You have not opened it with WorkEasy360.",
  };
}

const SIGN_OUT_WORDS: Record<NonNullable<ConnectedProduct["last_sign_out"]>["status"], Chip> = {
  delivered: { label: "Sign-out confirmed by the product", tone: "success" },
  pending: { label: "Sign-out pending - the product has not confirmed yet", tone: "warning" },
  failed: { label: "Sign-out could not be delivered", tone: "danger" },
};

export function lastSignOutLabel(product: Pick<ConnectedProduct, "last_sign_out">): (Chip & { at: string }) | null {
  const last = product.last_sign_out;
  if (!last) return null;
  return { ...SIGN_OUT_WORDS[last.status], at: last.finished_at ?? last.requested_at };
}

/** What "Sign out of this product" did, in words. */
export function productSignOutMessage(name: string, status: "delivered" | "pending" | "failed" | "unsupported"): string {
  switch (status) {
    case "delivered":
      return `${name} confirmed: you are signed out of it everywhere.`;
    case "pending":
      return `${name} has been told to sign you out and has not confirmed yet. It ends your session at its next check at the latest.`;
    case "failed":
      return `${name} could not be told. It ends your session at its next check at the latest.`;
    default:
      return `${name} does not take WorkEasy360's sign-out. Sign out inside ${name} itself.`;
  }
}

/** Every chip of one product, in order - used by the card and by the tests. */
export function connectionChips(product: ConnectedProduct, now: number = Date.now()): Chip[] {
  const chips: Chip[] = [];
  const holdings = new Set(
    product.organizations.map((o) => holdingLabel(o.access?.entitlement_status)?.label).filter(Boolean),
  );
  for (const label of holdings) chips.push({ label: label as string, tone: label === "Trial" ? "info" : "success" });
  if (product.organizations.some((o) => o.access?.state === "active")) {
    chips.push({ label: "Assigned to you", tone: "success" });
  }
  chips.push(
    product.sso_connected
      ? { label: "SSO connection established", tone: "brand" }
      : { label: "No SSO connection yet", tone: "neutral" },
  );
  if (product.last_sign_in_at) chips.push({ label: lastSignedInLabel(product.last_sign_in_at, now), tone: "neutral" });
  const session = sessionLabel(product);
  chips.push({ label: session.label, tone: session.tone });
  const signedOut = lastSignOutLabel(product);
  if (signedOut) chips.push({ label: signedOut.label, tone: signedOut.tone });
  return chips;
}
