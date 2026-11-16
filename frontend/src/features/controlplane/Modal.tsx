"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import styles from "./Modal.module.css";
import { describeError } from "./ui";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const noSubscription = () => () => {};

/** sm: a short form in one column; md: a form in two columns; lg: content or a
 * long form; xl (2026-11-16): a whole page in a popup - a sheet with its title
 * fixed at the top, its own scrolling body and its actions fixed at the bottom
 * (the Product Publisher), full screen on a phone. */
export type DialogSize = "sm" | "md" | "lg" | "xl";
const SIZE_CLASS: Record<DialogSize, string> = { sm: "", md: styles.medium, lg: styles.wide, xl: styles.sheet };

/** A modal dialog, drawn on the page's body (above the console's bars): focus
 * moves into it (the first field), stays inside it while it is open, Escape
 * closes it, the page behind does not scroll, and focus goes back where it was
 * when it closes. A click on the backdrop does nothing, so a
 * half-typed form is never lost by a stray click. */
export function Modal({
  labelledBy,
  onClose,
  children,
  wide = false,
  size,
  closing = false,
}: {
  labelledBy: string;
  onClose: () => void;
  children: React.ReactNode;
  /** A wider card, for content rather than a short form (the store preview). Same as size="lg". */
  wide?: boolean;
  size?: DialogSize;
  /** 2026-11-16: the popup is on its way out - it fades and sinks for the
   * caller's short exit before the caller removes it (`useClosing`). */
  closing?: boolean;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);
  // On the body only in the browser: the server render has no body to draw on,
  // and the first client render matches it (nothing), then the dialog appears.
  const inBrowser = useSyncExternalStore(noSubscription, () => true, () => false);

  useEffect(() => {
    if (!inBrowser) return;
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    box.current?.querySelector<HTMLElement>("input, select, textarea")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close.current();
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      before?.focus();
    };
  }, [inBrowser]);

  if (!inBrowser) return null;
  const sized = size ?? (wide ? "lg" : "sm");
  return createPortal(
    <div className={`${styles.backdrop} ${sized === "xl" ? styles.sheetBackdrop : ""} ${closing ? styles.leaving : ""}`.trim()} data-closing={closing || undefined}>
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className={`${styles.dialog} ${SIZE_CLASS[sized]}`.trim()}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** How long a popup's exit lasts (Modal.module.css `.leaving`). */
export const EXIT_MS = 160;

/**
 * A popup that leaves smoothly (2026-11-16, the owner: popups that open and
 * close "very smoothly"): `close()` marks it closing - the Modal fades and
 * sinks - and calls `onClosed` once the exit has played. With reduced motion
 * asked for, or without timers (tests), it closes at once.
 */
export function useClosing(onClosed: () => void): { closing: boolean; close: () => void } {
  const [closing, setClosing] = useState(false);
  const done = useRef(onClosed);
  useEffect(() => {
    done.current = onClosed;
  }, [onClosed]);
  const close = () => {
    const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced || closing) {
      done.current();
      return;
    }
    setClosing(true);
    window.setTimeout(() => done.current(), EXIT_MS);
  };
  return { closing, close };
}

/** The dialog's title and its close button (Escape closes it too). */
export function DialogHead({ titleId, title, onClose }: { titleId: string; title: string; onClose: () => void }) {
  return (
    <div className={styles.dialogHead}>
      <h2 id={titleId} className={styles.dialogTitle}>
        {title}
      </h2>
      <button type="button" className={styles.closeButton} aria-label="Close" onClick={onClose}>
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}

/** The class names of the dialog's parts, for a form laid out inside it. */
export const dialog = {
  form: styles.dialogForm,
  body: styles.dialogBody,
  foot: styles.dialogFoot,
  /** Fields side by side in two equal columns (one on a narrow screen). */
  grid: styles.dialogGrid,
  /** A field, fieldset or note across both columns of `grid`. */
  span: styles.span,
  /** The short explanation under the title. */
  intro: styles.dialogIntro,
};

/**
 * A form in a popup (2026-11-05, the owner: every form in a popup, aligned):
 * the title and close at the top, the fields in one aligned grid, an error
 * inside the popup, Cancel and the main button at the bottom right.
 * `onSubmit` resolves to the success message (or nothing) and the popup
 * closes; a failure stays in the popup with the typed values kept. The caller
 * owns the field state.
 */
export function FormDialog({
  title,
  intro,
  size = "md",
  columns,
  submitLabel = "Save",
  cancelLabel = "Cancel",
  canSubmit = true,
  danger = false,
  onSubmit,
  onDone,
  onClose,
  children,
}: {
  title: string;
  intro?: React.ReactNode;
  size?: DialogSize;
  /** 2 lays the fields side by side (the default for md and lg); mark a full-width one with `dialog.span`. */
  columns?: 1 | 2;
  submitLabel?: string;
  cancelLabel?: string;
  /** False keeps the main button disabled (a required field is empty or invalid). */
  canSubmit?: boolean;
  /** The main button is a destructive action. */
  danger?: boolean;
  onSubmit: () => Promise<string | void> | string | void;
  /** After a successful save, with its message. */
  onDone?: (message: string | undefined) => void;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const titleId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const twoColumns = (columns ?? (size === "sm" ? 1 : 2)) === 2;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || busy) return;
    setBusy(true);
    setError(null);
    let message: string | void;
    try {
      message = await onSubmit();
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
      return;
    }
    setBusy(false);
    onDone?.(message || undefined);
    onClose();
  }

  return (
    <Modal labelledBy={titleId} onClose={onClose} size={size}>
      <form className={styles.dialogForm} onSubmit={(e) => void submit(e)} noValidate>
        <DialogHead titleId={titleId} title={title} onClose={onClose} />
        <div className={styles.dialogBody}>
          {intro && <p className={styles.dialogIntro}>{intro}</p>}
          {error && (
            <p className="w360-error-text" role="alert">
              {error}
            </p>
          )}
          <div className={twoColumns ? styles.dialogGrid : styles.dialogStack}>{children}</div>
        </div>
        <div className={styles.dialogFoot}>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
            {cancelLabel}
          </button>
          <button type="submit" className={danger ? "w360-btn w360-btn-danger" : "w360-btn w360-btn-primary"} disabled={busy || !canSubmit} aria-busy={busy || undefined}>
            {submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/**
 * A button that opens a `FormDialog` ("New coupon", "Edit details"...). The
 * success message shows beside the button once the popup closes. `onOpen`
 * can reset or prefill the caller's field state each time it opens.
 */
export function FormPopup({
  label,
  buttonClassName = "w360-btn w360-btn-primary",
  buttonAriaLabel,
  disabled = false,
  onOpen,
  onDone,
  ...dialogProps
}: {
  /** The text on the button that opens the popup. */
  label: React.ReactNode;
  buttonClassName?: string;
  buttonAriaLabel?: string;
  disabled?: boolean;
  onOpen?: () => void;
  onDone?: (message: string | undefined) => void;
} & Omit<React.ComponentProps<typeof FormDialog>, "onClose" | "onDone">) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        className={buttonClassName}
        aria-haspopup="dialog"
        aria-label={buttonAriaLabel}
        disabled={disabled}
        onClick={() => {
          setMessage(null);
          onOpen?.();
          setOpen(true);
        }}
      >
        {label}
      </button>
      {message && (
        <span className={styles.popupMessage} role="status">
          {message}
        </span>
      )}
      {open && (
        <FormDialog
          {...dialogProps}
          onDone={(m) => {
            setMessage(m ?? null);
            onDone?.(m);
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
