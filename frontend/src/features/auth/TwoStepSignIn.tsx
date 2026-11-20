"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import { loginTwoStep, type LoginResult } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import adminStyles from "./AdminLogin.module.css";

/**
 * Accounts V3: the second step of every sign-in form, for an account with two-step
 * verification on. The password step answers 401 `two_step_required` with an opaque
 * `challenge` and no session; this asks for the code and sends both to
 * `auth/login/two-step`, which answers exactly what a successful sign-in answers - so
 * the form then continues as it always does (its redirect, `resume`, `next`).
 *
 * The challenge and the code live in component state only: never in a URL, a log or
 * browser storage.
 */

/** The challenge of a password step that needs a code, or null for any other answer. */
export function twoStepChallenge(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 401 || err.code !== "two_step_required") return null;
  const body = err.body as { challenge?: unknown } | undefined;
  return typeof body?.challenge === "string" && body.challenge ? body.challenge : null;
}

/** A code as typed: spaces dropped (an app shows "123 456"), letters lower-cased. */
export function normalizeCode(raw: string): string {
  return raw.replace(/\s+/g, "").toLowerCase();
}

export const EXPIRED_MESSAGE = "That sign-in took too long. Enter your password again.";

function problemOf(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "Too many attempts. Wait a minute, then try again.";
    if (err.code === "two_step_code_invalid") return "That code isn't right. Check your authenticator app and try again, or use a recovery code.";
    if (err.code === "account_locked") return "This account is temporarily locked after several failed attempts. Try again in a few minutes.";
    if (err.code === "account_disabled") return "This account has been disabled. Contact your administrator.";
  }
  return "Something went wrong. Please try again.";
}

/** "plain": the platform's own form classes; "split": the Admin, Partner and User
 * Admin sign-in frames (AdminLogin.module.css). */
export type TwoStepLook = "plain" | "split";

export function TwoStepSignIn({
  challenge,
  onSignedIn,
  onRestart,
  look = "plain",
}: {
  challenge: string;
  /** What the form does after a successful sign-in, exactly as after the password step. */
  onSignedIn: (result: LoginResult) => void | Promise<void>;
  /** Back to the password step: the challenge expired (with the reason) or the person
   * chose to start again (null). */
  onRestart: (message: string | null) => void;
  look?: TwoStepLook;
}) {
  const headingId = useId();
  const codeId = useId();
  const hintId = useId();
  const errorId = useId();
  const input = useRef<HTMLInputElement | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const split = look === "split";

  // The password field this replaced has gone: the code field takes focus.
  useEffect(() => {
    input.current?.focus();
  }, []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const value = normalizeCode(code);
    if (!value) {
      setError("Enter the 6-digit code from your authenticator app, or a recovery code.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await loginTwoStep(challenge, value);
      setCode("");
      await onSignedIn(result);
    } catch (err) {
      if (err instanceof ApiError && err.code === "two_step_challenge_expired") {
        onRestart(EXPIRED_MESSAGE);
        return;
      }
      setError(problemOf(err));
      setCode("");
      input.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  const described = [hintId, error ? errorId : null].filter(Boolean).join(" ");
  const field = (
    <input
      ref={input}
      id={codeId}
      className={split ? adminStyles.input : "w360-input"}
      type="text"
      name="code"
      inputMode="text"
      autoComplete="one-time-code"
      autoCapitalize="off"
      autoCorrect="off"
      spellCheck={false}
      maxLength={32}
      required
      value={code}
      onChange={(e) => setCode(e.target.value)}
      aria-describedby={described}
      aria-invalid={error ? true : undefined}
    />
  );
  return (
    <form method="post" onSubmit={submit} noValidate aria-labelledby={headingId} className={split ? adminStyles.form : "w360-stack-form"}>
      <h2 id={headingId} className={split ? adminStyles.label : undefined}>
        Two-step verification
      </h2>
      <p id={hintId}>Enter the 6-digit code from your authenticator app. Lost your phone? Use one of your recovery codes instead.</p>
      <div>
        <label className={split ? adminStyles.label : "w360-label"} htmlFor={codeId}>
          6-digit code or a recovery code
        </label>
        {split ? (
          <span className={adminStyles.inputWrap}>
            <Icon name="shield" size={18} />
            {field}
          </span>
        ) : (
          field
        )}
      </div>
      {error && (
        <p id={errorId} className={split ? adminStyles.error : "w360-error-text"} role="alert">
          {error}
        </p>
      )}
      <button type="submit" className={split ? adminStyles.submit : "w360-btn w360-btn-primary"} disabled={busy}>
        {split && <Icon name="login" size={20} />}
        {busy ? "Checking…" : "Verify and sign in"}
      </button>
      <p>
        <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => onRestart(null)}>
          Back to the password
        </button>
      </p>
      <p className="w360-hint">
        No code and no recovery code? <a href="/help/account">Get help with your account</a>.
      </p>
    </form>
  );
}
