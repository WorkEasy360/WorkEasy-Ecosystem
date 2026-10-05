"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { login, type LoginErrorCode } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { publicApi } from "@/lib/api/public";

const ERROR_COPY: Record<LoginErrorCode | "unknown", string> = {
  invalid_credentials: "That email or password isn't right.",
  account_locked:
    "This account is temporarily locked after several failed attempts. Try again in a few minutes.",
  account_disabled: "This account has been disabled. Contact your administrator.",
  email_not_verified:
    "Confirm your email address first: open the link we emailed you when you signed up.",
  unknown: "Something went wrong. Please try again.",
};

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const resume = searchParams.get("resume");
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [unverified, setUnverified] = useState(false);
  const [resent, setResent] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setUnverified(false);
    setResent(false);
    setSubmitting(true);
    try {
      await login(email, password);
      if (resume) {
        // A full top-level navigation, not client-side routing: this must
        // reach the Django resume endpoint with the session cookie the
        // login above just set, and the browser must follow whatever it
        // redirects to next - the platform's org chooser or the product
        // itself (Task 31/32).
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = `/api/v1/integrations/oauth/resume?resume=${encodeURIComponent(resume)}`;
        return;
      }
      // Phase 9: everyone lands on the central dashboard, which decides
      // where (organization, chooser, onboarding, Super Admin).
      router.replace("/dashboard");
    } catch (err) {
      const code = err instanceof ApiError ? (err.code as LoginErrorCode) : "unknown";
      setError(ERROR_COPY[code] ?? ERROR_COPY.unknown);
      setUnverified(code === "email_not_verified");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div style={{ marginBottom: "var(--w360-space-4)" }}>
        <label className="w360-label" htmlFor={emailId}>
          Email
        </label>
        <input
          id={emailId}
          className="w360-input"
          type="email"
          name="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-describedby={error ? errorId : undefined}
          aria-invalid={error ? true : undefined}
        />
      </div>
      <div style={{ marginBottom: "var(--w360-space-4)" }}>
        <label className="w360-label" htmlFor={passwordId}>
          Password
        </label>
        <input
          id={passwordId}
          className="w360-input"
          type="password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-describedby={error ? errorId : undefined}
          aria-invalid={error ? true : undefined}
        />
      </div>
      {error && (
        <p id={errorId} className="w360-error-text" role="alert">
          {error}
        </p>
      )}
      {unverified && (
        <p>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => publicApi.resendVerification(email.trim()).then(() => setResent(true), () => setResent(true))}
          >
            Email me the link again
          </button>
          {resent && <span role="status"> Sent - only the newest link works.</span>}
        </p>
      )}
      <button
        type="submit"
        className="w360-btn w360-btn-primary"
        style={{ width: "100%", marginTop: "var(--w360-space-2)" }}
        disabled={submitting}
      >
        {submitting ? "Signing in…" : "Sign in"}
      </button>
      <p className="w360-hint">
        <Link href="/forgot-password">Forgotten your password?</Link> · <Link href="/signup">Create an account</Link>
      </p>
    </form>
  );
}
