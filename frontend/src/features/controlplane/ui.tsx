"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { StatusBadge } from "./kit/Badge";
import { Button } from "./kit/Button";
import { ConfirmDialog } from "./kit/Confirm";
import { EmptyState, LoadingBlock } from "./kit/Layout";
import { EMPTY, formatDateTime } from "./kit/format";
import kit from "./kit/Kit.module.css";

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
  if (load.status === "loading") return <LoadingBlock what={what} />;
  if (load.status === "forbidden") {
    return (
      <div role="alert">
        <EmptyState icon="lock" title={`You don't have access to ${what}.`} compact />
      </div>
    );
  }
  if (load.status === "error") {
    return (
      <div role="alert">
        <EmptyState icon="alert" title={load.message} compact />
      </div>
    );
  }
  return <>{children(load.data)}</>;
}

// --- actions ------------------------------------------------------------------------

/**
 * Runs an action and reports its outcome: busy while it runs, then the success
 * message or the server's own refusal. Every control-plane action goes through
 * this one flow.
 */
export function useAction(after?: () => void) {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const outcome = useRef<HTMLParagraphElement | null>(null);

  // The control that started an action is often gone when it settles - a
  // reason prompt unmounts on submit - which drops keyboard
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
      await task();
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

/** The reason recorded for an audited action. Nobody is asked to type one (the
 * owner's call, 2026-10-27); the server still requires a reason, so the action
 * states what was done and where, and the audit entry keeps who and when. */
export function autoReason(action: string, where = "Admin console"): string {
  return `${action} - ${where}`.slice(0, 200);
}

/** A button for an audited action. It asks for no reason (`autoReason` is
 * sent). `danger` marks a destructive action (suspend, remove, revoke): it asks
 * once to confirm, drawn in the semantic red, never the brand green. */
export function ReasonAction({
  label,
  confirmLabel,
  onConfirm,
  busy,
  danger = false,
  where,
  message,
  typeToConfirm,
  size,
  icon,
}: {
  label: string;
  confirmLabel?: string;
  onConfirm: (reason: string) => void;
  busy?: boolean;
  danger?: boolean;
  /** Where the action is taken, for the recorded reason (default: the Super Admin console). */
  where?: string;
  /** What the confirmation dialog says will happen (destructive actions). */
  message?: React.ReactNode;
  /** The word to type before a grave action runs (Terminate). */
  typeToConfirm?: string;
  size?: "sm" | "md";
  icon?: string;
}) {
  const [confirming, setConfirming] = useState(false);
  const run = (reason?: string) => {
    setConfirming(false);
    onConfirm(reason ?? autoReason(label, where));
  };

  return (
    <>
      <Button
        variant={danger ? "danger" : "secondary"}
        size={size}
        icon={icon}
        disabled={busy}
        onClick={() => (danger ? setConfirming(true) : run())}
      >
        {label}
      </Button>
      {danger && confirming && (
        <ConfirmDialog
          options={{
            title: `${label}?`,
            message: message ?? "This takes effect at once and is recorded in the audit log.",
            confirmLabel: confirmLabel ?? `Confirm ${label.toLowerCase()}`,
            danger: true,
            reason: true,
            groupLabel: `Confirm ${label}`,
            actionName: label,
            typeToConfirm,
            where,
            onConfirm: (reason) => run(reason),
          }}
          onClose={() => setConfirming(false)}
        />
      )}
    </>
  );
}

// --- presentation ------------------------------------------------------------------------

/** Status is text first; colour only reinforces it (WCAG 1.4.1). One map for
 * the whole console: kit/Badge. */
export function Status({ value, label }: { value: string; label?: string }) {
  return <StatusBadge value={value} label={label} />;
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

/** "24 Oct 2026, 6:45 AM" (kit/format). */
export function when(iso: string | null | undefined): string {
  return iso ? formatDateTime(iso) : EMPTY;
}

/** A simple table in the console's one table style (kit/DataTable for sorting,
 * row menus and pages). The caption is read by assistive technology only: the
 * section's heading already says what the table is. */
export function Table({
  caption,
  head,
  children,
  empty,
  isEmpty,
  emptyHint,
  emptyAction,
  numeric = [],
}: {
  caption: string;
  head: string[];
  children: React.ReactNode;
  empty?: string;
  isEmpty?: boolean;
  emptyHint?: React.ReactNode;
  emptyAction?: React.ReactNode;
  /** Headings of the columns that hold numbers or amounts (right-aligned). */
  numeric?: string[];
}) {
  const captionId = useId();
  if (isEmpty) {
    return (
      <div className={kit.tableCard}>
        <EmptyState icon="box" title={empty ?? "Nothing to show."} hint={emptyHint} action={emptyAction} compact />
      </div>
    );
  }
  // A table wider than its column scrolls sideways inside its card; the
  // wrapper is a named, focusable region so a keyboard user can scroll it too
  // (WCAG 2.1.1).
  return (
    <div className={kit.tableCard}>
      <div className={kit.tableScroll} role="region" aria-labelledby={captionId} tabIndex={0}>
        <table className={kit.dt}>
          <caption id={captionId}>{caption}</caption>
          <thead>
            <tr>
              {head.map((h, i) => (
                <th key={`${h}-${i}`} scope="col" className={numeric.includes(h) ? kit.num : undefined}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>{children}</tbody>
        </table>
      </div>
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
    <Button size="sm" onClick={onMore}>
      Load more
    </Button>
  );
}
