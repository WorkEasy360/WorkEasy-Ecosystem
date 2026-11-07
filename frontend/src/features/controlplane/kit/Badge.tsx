import { Icon } from "@/components/sidebar/icons";
import { EMPTY, titleCase } from "./format";
import type { Tone } from "./Layout";
import k from "./Kit.module.css";

/** One status -> colour map for the whole console. Text first; colour only
 * reinforces it (WCAG 1.4.1). */
const TONE: Record<string, Tone> = {
  active: "success",
  verified: "success",
  approved: "success",
  paid: "success",
  enabled: "success",
  connected: "success",
  granted: "success",
  accepted: "success",
  succeeded: "success",
  subscribed: "success",
  customer: "success",
  completed: "success",
  complete: "success",
  live: "success",
  published: "success",
  public: "success",
  healthy: "success",
  resolved: "success",
  closed: "neutral",
  trialing: "info",
  trial: "info",
  in_progress: "info",
  processing: "info",
  test: "info",
  sandbox: "info",
  affiliate: "brand",
  reseller: "brand",
  white_label: "brand",
  whitelabel: "brand",
  staff: "brand",
  owner: "brand",
  open: "neutral",
  pending: "warning",
  awaiting_payment: "warning",
  needs_setup: "warning",
  needs_review: "warning",
  pending_approval: "warning",
  profile_required: "warning",
  verification_pending: "warning",
  unverified: "warning",
  requested: "warning",
  urgent: "danger",
  high: "warning",
  locked: "warning",
  grace: "warning",
  suspended: "warning",
  past_due: "warning",
  degraded: "warning",
  half_open: "warning",
  not_available: "neutral",
  draft: "neutral",
  ready: "neutral",
  private: "neutral",
  explorer: "neutral",
  inactive: "neutral",
  default: "neutral",
  customized: "info",
  disconnected: "danger",
  error: "danger",
  void: "danger",
  revoked: "danger",
  expired: "danger",
  rejected: "danger",
  failed: "danger",
  terminated: "danger",
  cancelled: "danger",
  canceled: "danger",
  disabled: "danger",
  deactivated: "danger",
  retired: "neutral",
  ended: "neutral",
  refunded: "neutral",
  open_circuit: "danger",
};

/** Words a status is written as, when its key reads badly. */
const LABEL: Record<string, string> = {
  trialing: "Trial",
  needs_setup: "Needs setup",
  not_available: "Not available",
  past_due: "Past due",
  awaiting_payment: "Awaiting payment",
  pending_approval: "Pending approval",
  profile_required: "Profile required",
  verification_pending: "Verification pending",
  needs_review: "Needs review",
  white_label: "White-label",
  whitelabel: "White-label",
  in_progress: "In progress",
  half_open: "Recovering",
  disabled: "Deactivated",
};

export function toneOf(status: string | null | undefined): Tone {
  if (!status) return "neutral";
  return TONE[status.toLowerCase().replace(/[\s-]+/g, "_")] ?? "neutral";
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return EMPTY;
  const key = status.toLowerCase().replace(/[\s-]+/g, "_");
  return LABEL[key] ?? titleCase(key);
}

/** A badge in a tone, with any text. */
export function Badge({ tone = "neutral", children, icon, dot = false }: { tone?: Tone; children: React.ReactNode; icon?: string; dot?: boolean }) {
  return (
    <span className={`${k.badge} ${k[`tone-${tone}`]}`}>
      {dot && <span className={k.dot} aria-hidden="true" />}
      {icon && <Icon name={icon} size={12} />}
      {children}
    </span>
  );
}

/** A status in Title Case and its colour: "Active", "Trial", "Suspended". */
export function StatusBadge({ value, label }: { value: string | null | undefined; label?: string }) {
  if (!value) return <span className={k.muted}>{EMPTY}</span>;
  return (
    <Badge tone={toneOf(value)} dot>
      {label ?? statusLabel(value)}
    </Badge>
  );
}

/** A yes/no fact as a badge with an icon: "Verified" / "Unverified". */
export function BoolBadge({
  value,
  yes,
  no,
  noTone = "warning",
}: {
  value: boolean;
  yes: string;
  no: string;
  noTone?: Tone;
}) {
  return value ? (
    <Badge tone="success" icon="check">
      {yes}
    </Badge>
  ) : (
    <Badge tone={noTone} icon="x">
      {no}
    </Badge>
  );
}
