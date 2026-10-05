"use client";

import { useEffect, useId, useRef, useState } from "react";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";

/**
 * Small pieces the organization console's screens share (Phase 8 extension).
 * The heavier kit - loading, actions with step-up, tables, money - is
 * `features/controlplane/ui.tsx`; this adds page vocabulary only.
 */

// --- words ------------------------------------------------------------------------

/** System roles as people read them. A key the table does not know is shown
 * as itself, never dropped. */
export const ROLE_LABELS: Record<string, string> = {
  owner: "Owner",
  admin: "Administrator",
  billing_manager: "Billing Manager",
  member: "Member",
  readonly: "Read Only",
  collaborator: "External Collaborator",
};

export function roleLabel(key: string): string {
  return ROLE_LABELS[key] ?? key.replace(/_/g, " ");
}

export const INTERVAL_LABELS: Record<string, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  semi_annual: "Bi-annual",
  annual: "Annual",
  one_time: "One-time",
};

export function intervalLabel(key: string): string {
  return INTERVAL_LABELS[key] ?? key.replace(/_/g, " ");
}

/** A calendar day. A bare `YYYY-MM-DD` is a day, not an instant: it is read as
 * local midnight so it never shows as the day before west of UTC. */
export function day(iso: string | null | undefined, opts?: Intl.DateTimeFormatOptions): string {
  if (!iso) return "—";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T00:00:00`) : new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, opts ?? { year: "numeric", month: "short", day: "numeric" });
}

/** A UUID as the backend issues them - checked before one from a URL is put
 * into an API path. */
export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// --- page furniture -------------------------------------------------------------------

/** The page's one `<h1>`, its lead and the page-level actions. */
export function PageHeader({
  title,
  lead,
  actions,
}: {
  title: string;
  lead?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className={styles.pageHeader}>
      <div>
        <h1 className={styles.pageTitle}>{title}</h1>
        {lead && <p className={styles.lead}>{lead}</p>}
      </div>
      {actions && <div className={local.actions}>{actions}</div>}
    </div>
  );
}

/** A titled card: a `<section>` named by its `<h2>`. */
export function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <section className={`w360-card ${styles.panel} ${local.section}`} aria-labelledby={id}>
      <h2 id={id} className={styles.panelTitle}>
        {title}
      </h2>
      {children}
    </section>
  );
}

/** The non-ready states of a `usePaged` list. */
export function PagedState({
  state,
  what,
}: {
  state: "loading" | "ready" | "error" | "forbidden";
  what: string;
}) {
  if (state === "loading") return <p aria-live="polite">Loading {what}…</p>;
  if (state === "forbidden") return <p role="alert">You don&apos;t have access to {what}.</p>;
  if (state === "error") return <p role="alert">Couldn&apos;t load {what}. Try again in a moment.</p>;
  return null;
}

/**
 * A button that asks "are you sure?" in place before an irreversible action.
 * The confirm button takes focus when it appears; Cancel gives focus back to
 * the button that opened it. (On confirm the row usually changes or goes, so
 * `useAction` moves focus to the announced outcome instead.)
 */
export function ConfirmAction({
  label,
  accessibleLabel,
  prompt,
  confirmLabel,
  onConfirm,
  busy,
}: {
  label: string;
  accessibleLabel?: string;
  prompt: string;
  confirmLabel: string;
  onConfirm: () => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const confirmButton = useRef<HTMLButtonElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const restoreFocus = useRef(false);

  useEffect(() => {
    if (open) {
      confirmButton.current?.focus();
    } else if (restoreFocus.current) {
      restoreFocus.current = false;
      trigger.current?.focus();
    }
  }, [open]);

  if (!open) {
    return (
      <button
        ref={trigger}
        type="button"
        className="w360-btn w360-btn-secondary"
        aria-label={accessibleLabel}
        disabled={busy}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
    );
  }
  return (
    <div className={local.confirm} role="group" aria-label={accessibleLabel ?? label}>
      <p>{prompt}</p>
      <div className={local.actions}>
        <button
          ref={confirmButton}
          type="button"
          className="w360-btn w360-btn-primary"
          disabled={busy}
          onClick={() => {
            setOpen(false);
            onConfirm();
          }}
        >
          {confirmLabel}
        </button>
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          onClick={() => {
            restoreFocus.current = true;
            setOpen(false);
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
