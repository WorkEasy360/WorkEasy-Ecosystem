"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { useStepUp } from "@/features/security/useStepUp";
import styles from "./ControlPlane.module.css";

// --- errors ------------------------------------------------------------------

/** A calm, specific message for an API failure. Only the server's own
 * ``detail`` is shown - never an exception's message or a stack. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { detail?: string } | undefined;
    if (err.status === 401 && err.code === "login_required") return "Your session has ended. Sign in again.";
    if (err.status === 403) return body?.detail || "You don't have permission to do that.";
    if (err.status === 404) return "That doesn't exist, or you don't have access to it.";
    if (err.status === 429) return "Too many requests. Wait a moment and try again.";
    if (body?.detail) return body.detail;
  }
  return "Something went wrong. Please try again.";
}

// --- loading ---------------------------------------------------------------------

export type Load<T> =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

/** A stable string for a dependency list, used to tell "the inputs changed"
 * (show loading) from "reload the same inputs" (keep what is on screen). */
const stampOf = (deps: unknown[]): string => deps.map((d) => String(d)).join("");

/** Loads on mount and whenever ``deps`` change; ``reload`` re-runs it and keeps
 * the current data visible until the new data arrives. */
export function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[]): [Load<T>, () => void] {
  const stamp = stampOf(deps);
  const [state, setState] = useState<{ stamp: string; load: Load<T> }>({ stamp, load: { status: "loading" } });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetcher()
      .then((data) => {
        if (!cancelled) setState({ stamp, load: { status: "ready", data } });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const load: Load<T> =
          err instanceof ApiError && (err.status === 403 || err.status === 404)
            ? { status: "forbidden" }
            : { status: "error", message: describeError(err) };
        setState({ stamp, load });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp, tick]);

  return [state.stamp === stamp ? state.load : { status: "loading" }, () => setTick((n) => n + 1)];
}

export function LoadGate<T>({
  load,
  what,
  children,
}: {
  load: Load<T>;
  what: string;
  children: (data: T) => React.ReactNode;
}) {
  if (load.status === "loading") return <p aria-live="polite">Loading {what}…</p>;
  if (load.status === "forbidden") {
    return <p role="alert">You don&apos;t have access to {what}.</p>;
  }
  if (load.status === "error") return <p role="alert">{load.message}</p>;
  return <>{children(load.data)}</>;
}

// --- actions ------------------------------------------------------------------------

/**
 * Runs actions that may need a second factor and reports their outcome.
 * Every dangerous control-plane action goes through this one flow.
 */
export function useAction(after?: () => void) {
  // A retry after verification goes through ``settle`` like the first
  // attempt: busy while it runs, its success or error shown here.
  const { run, stepUp } = useStepUp({ onRetry: (again) => void settle(again) });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const outcome = useRef<HTMLParagraphElement | null>(null);

  // The control that started an action is often gone when it settles - a
  // reason prompt or the MFA prompt unmounts on submit - which drops keyboard
  // focus to <body> and sends a keyboard user back to the top of the page.
  // Only in that case, focus moves to the outcome (which is also announced);
  // focus the user has placed elsewhere is never taken.
  useEffect(() => {
    const active = document.activeElement;
    if ((message || error) && (active === null || active === document.body)) {
      outcome.current?.focus();
    }
  }, [message, error]);

  async function settle(task: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await run(task);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function act(action: () => Promise<unknown>, success: string) {
    if (busy) return;
    await settle(async () => {
      await action();
      setMessage(success);
      after?.();
    });
  }

  const feedback = (
    <>
      {stepUp}
      {message && (
        <p role="status" tabIndex={-1} ref={outcome}>
          {message}
        </p>
      )}
      {error && (
        <p className="w360-error-text" role="alert" tabIndex={-1} ref={outcome}>
          {error}
        </p>
      )}
    </>
  );
  return { act, busy, feedback };
}

/** A button that asks for the *reason* an auditor will read, then confirms. */
export function ReasonAction({
  label,
  confirmLabel,
  onConfirm,
  busy,
  minLength = 10,
}: {
  label: string;
  confirmLabel?: string;
  onConfirm: (reason: string) => void;
  busy?: boolean;
  minLength?: number;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");

  if (!open) {
    return (
      <button
        type="button"
        className="w360-btn w360-btn-secondary"
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
    );
  }
  return (
    <form
      className={styles.field}
      onSubmit={(e) => {
        e.preventDefault();
        onConfirm(reason.trim());
        setOpen(false);
        setReason("");
      }}
    >
      <label className="w360-label" htmlFor={id}>
        Reason for “{label}” (recorded in the audit log)
      </label>
      <input
        id={id}
        className="w360-input"
        autoFocus
        required
        minLength={minLength}
        maxLength={200}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <div className={styles.row}>
        <button
          type="submit"
          className="w360-btn w360-btn-primary"
          disabled={busy || reason.trim().length < minLength}
        >
          {confirmLabel ?? "Confirm"}
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// --- presentation ------------------------------------------------------------------------

const BADGES: Record<string, string> = {
  active: "w360-badge w360-badge-success",
  verified: "w360-badge w360-badge-success",
  approved: "w360-badge w360-badge-success",
  paid: "w360-badge w360-badge-success",
  enabled: "w360-badge w360-badge-success",
  connected: "w360-badge w360-badge-success",
  granted: "w360-badge w360-badge-success",
  needs_setup: "w360-badge w360-badge-warning",
  not_available: "w360-badge w360-badge-neutral",
  disconnected: "w360-badge w360-badge-danger",
  error: "w360-badge w360-badge-danger",
  accepted: "w360-badge w360-badge-success",
  succeeded: "w360-badge w360-badge-success",
  trialing: "w360-badge w360-badge-neutral",
  open: "w360-badge w360-badge-neutral",
  pending: "w360-badge w360-badge-neutral",
  awaiting_payment: "w360-badge w360-badge-neutral",
  locked: "w360-badge w360-badge-warning",
  grace: "w360-badge w360-badge-warning",
  needs_review: "w360-badge w360-badge-warning",
  void: "w360-badge w360-badge-danger",
  revoked: "w360-badge w360-badge-danger",
  expired: "w360-badge w360-badge-danger",
  draft: "w360-badge w360-badge-neutral",
  ready: "w360-badge w360-badge-neutral",
  requested: "w360-badge w360-badge-neutral",
  verification_pending: "w360-badge w360-badge-warning",
  suspended: "w360-badge w360-badge-warning",
  past_due: "w360-badge w360-badge-warning",
  failed: "w360-badge w360-badge-danger",
  terminated: "w360-badge w360-badge-danger",
  cancelled: "w360-badge w360-badge-danger",
  disabled: "w360-badge w360-badge-danger",
  ended: "w360-badge w360-badge-danger",
};

/** Status is text first; colour only reinforces it (WCAG 1.4.1). */
export function Status({ value }: { value: string }) {
  if (!value) return <span className={styles.muted}>—</span>;
  return <span className={BADGES[value] ?? "w360-badge w360-badge-neutral"}>{value.replace(/_/g, " ")}</span>;
}

/** Money is (integer minor units, currency). Display only: the browser never
 * computes, sums or sends an amount. */
export function money(minor: number, currency: string): string {
  const exponent = currency === "JPY" ? 0 : 2;
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(minor / 10 ** exponent);
  } catch {
    return `${minor} ${currency} (minor units)`;
  }
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export function Table({
  caption,
  head,
  children,
  empty,
  isEmpty,
}: {
  caption: string;
  head: string[];
  children: React.ReactNode;
  empty?: string;
  isEmpty?: boolean;
}) {
  const captionId = useId();
  if (isEmpty) return <p className={styles.muted}>{empty ?? "Nothing to show."}</p>;
  // A table wider than its column scrolls sideways; the wrapper is a named,
  // focusable region so a keyboard user can scroll it too (WCAG 2.1.1).
  return (
    <div className={styles.tableWrap} role="region" aria-labelledby={captionId} tabIndex={0}>
      <table className={styles.table}>
        <caption id={captionId}>{caption}</caption>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** Keyset pagination: the server hands back an opaque cursor. */
export function usePaged<T>(
  fetchPage: (cursor?: string) => Promise<{ results: T[]; next_cursor: string | null; has_more: boolean }>,
  deps: unknown[],
) {
  const stamp = stampOf(deps);
  const [data, setData] = useState<{
    stamp: string;
    rows: T[];
    next: string | null;
    state: "ready" | "error" | "forbidden";
  } | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchPage()
      .then((page) => {
        if (!cancelled) setData({ stamp, rows: page.results, next: page.has_more ? page.next_cursor : null, state: "ready" });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const state = err instanceof ApiError && (err.status === 403 || err.status === 404) ? "forbidden" : "error";
        setData({ stamp, rows: [], next: null, state });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp, tick]);

  const current = data && data.stamp === stamp ? data : null;

  async function more() {
    if (!current?.next) return;
    try {
      const page = await fetchPage(current.next);
      setData((d) =>
        d && d.stamp === stamp
          ? { ...d, rows: [...d.rows, ...page.results], next: page.has_more ? page.next_cursor : null }
          : d,
      );
    } catch {
      setData((d) => (d ? { ...d, state: "error" } : d));
    }
  }

  return {
    rows: current?.rows ?? [],
    hasMore: current?.next != null,
    more,
    state: (current ? current.state : "loading") as "loading" | "ready" | "error" | "forbidden",
    reload: () => setTick((n) => n + 1),
  };
}

export function MoreButton({ hasMore, onMore }: { hasMore: boolean; onMore: () => void }) {
  if (!hasMore) return null;
  return (
    <button type="button" className="w360-btn w360-btn-secondary" onClick={onMore}>
      Load more
    </button>
  );
}
