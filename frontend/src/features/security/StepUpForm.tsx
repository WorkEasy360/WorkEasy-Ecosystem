"use client";

import { useId, useState } from "react";
import { stepUp } from "@/lib/api/mfa";
import { errorMessage } from "./mfaErrors";
import styles from "./StepUpForm.module.css";

/**
 * Raises the session to AAL2 with a TOTP or recovery code (Phase 5,
 * Task 37-39). Shared because every surface that performs a ``dangerous``
 * action - MFA management, and since Phase 6 connections and automations -
 * meets the same ``step_up_required`` answer and must offer the same way
 * through it, not a second implementation of it.
 */
export function StepUpForm({
  prompt,
  onVerified,
  onCancel,
}: {
  prompt: string;
  onVerified: () => void;
  onCancel: () => void;
}) {
  const codeId = useId();
  const errorId = useId();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await stepUp(code.trim());
      onVerified();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  // Free text, no length or pattern check: it accepts a 6-digit TOTP code
  // *or* a recovery code, whose format is the server's business (it changed
  // from xxxx-xxxx to xxxxx-xxxxx) - the server normalises and verifies.
  return (
    <form onSubmit={handleSubmit} noValidate className={styles.form}>
      <label className="w360-label" htmlFor={codeId}>
        {prompt}
      </label>
      <input
        id={codeId}
        className="w360-input"
        type="text"
        autoComplete="one-time-code"
        autoFocus
        required
        value={code}
        onChange={(e) => setCode(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error && (
        <p id={errorId} className="w360-error-text" role="alert">
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={submitting || !code.trim()}>
          {submitting ? "Verifying…" : "Verify"}
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
