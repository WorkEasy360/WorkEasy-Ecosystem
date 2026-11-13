"use client";

import { useEffect, useRef, useState } from "react";
import { Card, EmptyState, LoadingBlock, PageHeader as KitPageHeader, type MenuItem } from "@/features/controlplane/kit";
import local from "./ConsoleScreens.module.css";

/**
 * Small pieces the organization console's screens share (Phase 8 extension).
 * The heavier kit - loading, actions, tables, money - is
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

/** What each system role may do - the access-control model's own descriptions
 * (`platform/authz/services.py` SYSTEM_ROLES), so "View permissions" says what the
 * server enforces. */
export const ROLE_DESCRIPTIONS: Record<string, string> = {
  owner: "Full control, including billing, deletion and ownership transfer.",
  admin: "Members, roles, product access, security and integrations. Not billing.",
  billing_manager: "Billing, invoices, subscriptions and wallets - the finance-authorized role. Separate from administrators on purpose.",
  member: "Signs in, sees the organization and uses the products they hold a seat for.",
  readonly: "Can view, with no ability to change anything.",
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

/** The page's one `<h1>`, its lead and the page-level actions - the consoles'
 * one page header (the kit's), so the User Admin reads like the Admin and the
 * Partner panel. `primary` is the page's main action; `actions` sit beside it. */
export function PageHeader({
  title,
  lead,
  actions,
  primary,
  secondary,
  info,
}: {
  title: string;
  lead?: React.ReactNode;
  actions?: React.ReactNode;
  primary?: React.ReactNode;
  secondary?: MenuItem[];
  /** A longer explanation, behind an (i) after the lead. */
  info?: React.ReactNode;
}) {
  return <KitPageHeader title={title} subtitle={lead} actions={actions} primary={primary} secondary={secondary} info={info} />;
}

/** A titled card: a `<section>` named by its `<h2>` (the kit's Card). */
export function Panel({
  title,
  children,
  description,
  actions,
  flush,
}: {
  title: string;
  children: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  /** No padding (a table that runs edge to edge). */
  flush?: boolean;
}) {
  return (
    <Card title={title} description={description} actions={actions} flush={flush}>
      <div className={local.section}>{children}</div>
    </Card>
  );
}

/** "Contact Organization Admin" (the brief, Part 2 section 05): what a member
 * without the billing or wallet permission sees in place of a financial action. */
export function ContactOrgAdmin({ what = "this" }: { what?: string }) {
  return (
    <p className={local.contactAdmin} role="note">
      <strong>Contact Organization Admin</strong> - only your organization&apos;s owner and Billing Managers can manage{" "}
      {what}.
    </p>
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
  if (state === "loading") return <LoadingBlock what={what} />;
  if (state === "forbidden") {
    return (
      <div role="alert">
        <EmptyState icon="lock" title={`You don't have access to ${what}.`} hint="Contact Organization Admin if you need it." compact />
      </div>
    );
  }
  if (state === "error") {
    return (
      <div role="alert">
        <EmptyState icon="alert" title={`Couldn't load ${what}. Try again in a moment.`} compact />
      </div>
    );
  }
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
