"use client";

import { useEffect, useId, useRef } from "react";
import styles from "./Integrations.module.css";

/**
 * Shows a signing secret exactly once (Task 82).
 *
 * The value lives only in the parent's React state for as long as this is
 * on screen: it is never written to localStorage, sessionStorage, the URL
 * or a log, and "I've stored it" discards it. There is no way back to it -
 * the API cannot return it again - so the copy says so plainly.
 */
export function SecretReveal({
  label,
  secret,
  onDone,
}: {
  label: string;
  secret: string;
  onDone: () => void;
}) {
  const headingId = useId();
  const inputId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <section className={styles.secretBox} aria-labelledby={headingId}>
      <h2 id={headingId} ref={headingRef} tabIndex={-1} className={styles.cardTitle}>
        Signing secret for {label}
      </h2>
      <p className={styles.muted}>
        Copy this now. It is shown once and cannot be retrieved again. Your receiver uses it to
        verify the <code>X-W360-Signature</code> header on every delivery.
      </p>
      <label className="w360-label" htmlFor={inputId}>
        Signing secret
      </label>
      <input
        id={inputId}
        className={`w360-input ${styles.secret}`}
        readOnly
        value={secret}
        autoComplete="off"
        spellCheck={false}
        onFocus={(e) => e.currentTarget.select()}
      />
      <div className={styles.row}>
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          onClick={() => void navigator.clipboard?.writeText(secret)}
        >
          Copy
        </button>
        <button type="button" className="w360-btn w360-btn-primary" onClick={onDone}>
          I&apos;ve stored it
        </button>
      </div>
    </section>
  );
}
