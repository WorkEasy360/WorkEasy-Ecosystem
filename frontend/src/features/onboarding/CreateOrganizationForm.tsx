"use client";

import { useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createOrganization } from "@/lib/api/organizations";
import { ApiError } from "@/lib/api/client";

type ErrorCode =
  | "organization_validation_error"
  | "organization_creation_rate_limited"
  | "rate_limited"
  | "idempotency_conflict"
  | "login_required"
  | "unknown";

const ERROR_COPY: Record<ErrorCode, string> = {
  organization_validation_error: "That name isn't valid. Try a shorter or different name.",
  organization_creation_rate_limited: "Too many attempts. Wait a moment and try again.",
  rate_limited: "Too many attempts. Wait a moment and try again.",
  idempotency_conflict:
    "This submission is already being processed, or was already submitted with a different name. Try again.",
  login_required: "Your session has expired. Sign in again to continue.",
  unknown: "Something went wrong creating your organization. Please try again.",
};

/** Task 15: every one of these is reachable and handled - no blank page, no
 * accidental repeated organization creation. */
function classify(err: unknown): ErrorCode {
  if (err instanceof ApiError) {
    const code = err.code as ErrorCode;
    if (code in ERROR_COPY) return code;
  }
  return "unknown";
}

export function CreateOrganizationForm() {
  const router = useRouter();
  const nameId = useId();
  const errorId = useId();

  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Task 6: one idempotency key for this form's lifetime. A network-failure
  // retry (the user clicking "Create" again after a timeout) reuses it, so
  // the same submission is never turned into two organizations. Regenerated
  // only when the server itself reports the key was already used for a
  // different request (an edge case, not the common retry path).
  const idempotencyKey = useRef(crypto.randomUUID());

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return; // belt-and-suspenders alongside the disabled button
    setError(null);
    setSubmitting(true);
    try {
      const result = await createOrganization(name.trim(), idempotencyKey.current);
      router.replace(`/o/${result.slug}`);
    } catch (err) {
      const code = classify(err);
      if (code === "login_required") {
        router.replace("/login");
        return;
      }
      if (code === "idempotency_conflict") {
        idempotencyKey.current = crypto.randomUUID();
      }
      setError(ERROR_COPY[code]);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div style={{ marginBottom: "var(--w360-space-4)" }}>
        <label className="w360-label" htmlFor={nameId}>
          Organization name
        </label>
        <input
          id={nameId}
          className="w360-input"
          type="text"
          name="name"
          autoComplete="organization"
          autoFocus
          required
          maxLength={255}
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-describedby={error ? errorId : undefined}
          aria-invalid={error ? true : undefined}
          placeholder="ABC Company"
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
        disabled={submitting || name.trim().length === 0}
      >
        {submitting ? "Creating…" : "Create organization"}
      </button>
    </form>
  );
}
