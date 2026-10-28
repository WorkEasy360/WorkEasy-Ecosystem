"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import styles from "./Modal.module.css";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const noSubscription = () => () => {};

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
}: {
  labelledBy: string;
  onClose: () => void;
  children: React.ReactNode;
  /** A wider card, for content rather than a short form (the store preview). */
  wide?: boolean;
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
  return createPortal(
    <div className={styles.backdrop}>
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className={wide ? `${styles.dialog} ${styles.wide}` : styles.dialog}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
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
export const dialog = { form: styles.dialogForm, body: styles.dialogBody, foot: styles.dialogFoot };
