"use client";

import { useEffect, useId, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { AddPartnerForm } from "./addpartner/AddPartnerForm";
import styles from "./addpartner/AddPartner.module.css";

/**
 * Partners → Add Partner (ADR-048): the short form - partner name, administrator
 * email and password, partner type, optional country - in a dialog over All
 * Partners (2026-10-26). `?type=` only preselects the type; nothing in the
 * address grants anything. Everything else is set in the partner's Complete
 * Your Profile. Closing returns to All Partners.
 */
export function AddPartner({ onClose }: { onClose?: () => void } = {}) {
  const router = useRouter();
  const titleId = useId();
  const close = onClose ?? (() => router.push("/admin/partners/all"));
  return (
    <Modal labelledBy={titleId} onClose={close}>
      <AddPartnerForm titleId={titleId} onClose={close} />
    </Modal>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const noSubscription = () => () => {};

/** A modal dialog, drawn on the page's body (above the console's bars): focus
 * moves into it (the first field), stays inside it while it is open, Escape
 * closes it, the page behind does not scroll, and focus goes back where it was
 * when it closes. A click on the backdrop does nothing, so a
 * half-typed form is never lost by a stray click. */
function Modal({ labelledBy, onClose, children }: { labelledBy: string; onClose: () => void; children: React.ReactNode }) {
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
      <div ref={box} role="dialog" aria-modal="true" aria-labelledby={labelledBy} className={styles.dialog}>
        {children}
      </div>
    </div>,
    document.body,
  );
}
