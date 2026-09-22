"use client";

import { useId, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { login, type LoginErrorCode } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

const ERROR_COPY: Record<LoginErrorCode | "unknown", string> = {
  invalid_credentials: "That email or password isn't right.",
  account_locked:
    "This account is temporarily locked after several failed attempts. Try again in a few minutes.",
  account_disabled: "This account has been disabled. Contact your administrator.",
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
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
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
      router.replace("/");
    } catch (err) {
      const code = err instanceof ApiError ? (err.code as LoginErrorCode) : "unknown";
      setError(ERROR_COPY[code] ?? ERROR_COPY.unknown);
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
      <button
        type="submit"
        className="w360-btn w360-btn-primary"
        style={{ width: "100%", marginTop: "var(--w360-space-2)" }}
        disabled={submitting}
      >
        {submitting ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
