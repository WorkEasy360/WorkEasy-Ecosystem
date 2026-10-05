"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  beginTotpEnrollment,
  confirmTotpEnrollment,
  getMfaFactors,
  removeMfaFactor,
} from "@/lib/api/mfa";
import type { MfaFactor } from "@/lib/api/types";
import { errorMessage, isStepUpRequired } from "./mfaErrors";
import { StepUpForm } from "./StepUpForm";

type FactorsState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; factors: MfaFactor[] };

type Enrollment =
  | { step: "idle" }
  | { step: "started"; factorId: string; secret: string; provisioningUri: string }
  | { step: "confirmed"; recoveryCodes: string[] };

// Adding a second authenticator app, and removing one, need a *recent*
// step-up (the session's AAL2 lapses 15 minutes after it is granted). The
// code must come from an app that is already enrolled - not the one being
// added - so the prompt says so explicitly.
const STEP_UP_PROMPT =
  "This action needs a recent verification. Enter a code from an authenticator app you've already set up, or a recovery code.";

/** Moves focus to a step's heading when the step appears, so keyboard and
 * screen-reader users land at the start of the new content instead of on
 * `<body>` (the button they pressed has just unmounted). */
function useFocusOnMount<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return ref;
}

/**
 * TOTP enrollment, recovery codes, factor removal and step-up (Task 37-39).
 * Session-scoped identity data shown inside the org settings shell, exactly
 * like the Profile page beside it - MFA belongs to the user, not the
 * organization (identity.User is global).
 */
export function MfaSettings() {
  const [factors, setFactors] = useState<FactorsState>({ status: "loading" });
  const [enrollment, setEnrollment] = useState<Enrollment>({ step: "idle" });
  const [enrollNeedsStepUp, setEnrollNeedsStepUp] = useState(false);
  const [enrollError, setEnrollError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  // No synchronous setState of its own (react-hooks/set-state-in-effect):
  // safe to call directly from the mount effect below.
  function applyFactors() {
    getMfaFactors()
      .then(({ factors }) => setFactors({ status: "ready", factors }))
      .catch(() => setFactors({ status: "error" }));
  }

  function reload() {
    setFactors({ status: "loading" });
    applyFactors();
  }

  useEffect(applyFactors, []);

  async function startEnrollment() {
    setEnrollError(null);
    setStarting(true);
    try {
      const result = await beginTotpEnrollment();
      setEnrollNeedsStepUp(false);
      setEnrollment({
        step: "started",
        factorId: result.factor_id,
        secret: result.secret,
        provisioningUri: result.provisioning_uri,
      });
    } catch (err) {
      if (isStepUpRequired(err)) {
        setEnrollNeedsStepUp(true);
      } else {
        setEnrollError(errorMessage(err));
      }
    } finally {
      setStarting(false);
    }
  }

  const hasTotp = factors.status === "ready" && factors.factors.some((f) => f.kind === "totp");

  return (
    <div style={{ display: "grid", gap: "var(--w360-space-5)" }}>
      <div className="w360-card" style={{ padding: "var(--w360-space-5)" }}>
        <h2 style={{ fontSize: 16, marginTop: 0 }}>Authenticator app</h2>
        <p style={{ color: "var(--w360-text-muted)", fontSize: 14 }}>
          Some actions (like assigning roles or deleting the organization) require a second
          factor. Add an authenticator app to enable them.
        </p>

        {factors.status === "loading" && <p>Loading…</p>}
        {factors.status === "error" && <p role="alert">Couldn&apos;t load your factors.</p>}

        {factors.status === "ready" && enrollment.step === "idle" && enrollNeedsStepUp && (
          <StepUpForm
            prompt={STEP_UP_PROMPT}
            onVerified={startEnrollment}
            onCancel={() => setEnrollNeedsStepUp(false)}
          />
        )}

        {factors.status === "ready" && enrollment.step === "idle" && !enrollNeedsStepUp && (
          <>
            <FactorList
              factors={factors.factors}
              onRemoved={reload}
              onAddAnother={startEnrollment}
              adding={starting}
              hasTotp={hasTotp}
            />
            {enrollError && (
              <p className="w360-error-text" role="alert">
                {enrollError}
              </p>
            )}
          </>
        )}

        {enrollment.step === "started" && (
          <EnrollmentConfirm
            factorId={enrollment.factorId}
            secret={enrollment.secret}
            provisioningUri={enrollment.provisioningUri}
            onConfirmed={(codes) => setEnrollment({ step: "confirmed", recoveryCodes: codes })}
            onCancel={() => setEnrollment({ step: "idle" })}
          />
        )}

        {enrollment.step === "confirmed" && (
          <RecoveryCodes
            codes={enrollment.recoveryCodes}
            onDone={() => {
              setEnrollment({ step: "idle" });
              reload();
            }}
          />
        )}
      </div>
    </div>
  );
}

function FactorList({
  factors,
  onRemoved,
  onAddAnother,
  adding,
  hasTotp,
}: {
  factors: MfaFactor[];
  onRemoved: () => void;
  onAddAnother: () => void;
  adding: boolean;
  hasTotp: boolean;
}) {
  const [removing, setRemoving] = useState<string | null>(null);
  const [stepUpFor, setStepUpFor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleRemove(factorId: string) {
    setError(null);
    setRemoving(factorId);
    try {
      await removeMfaFactor(factorId);
      onRemoved();
    } catch (err) {
      if (isStepUpRequired(err)) {
        setStepUpFor(factorId);
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setRemoving(null);
    }
  }

  if (stepUpFor) {
    return (
      <StepUpForm
        prompt={STEP_UP_PROMPT}
        onVerified={() => {
          const id = stepUpFor;
          setStepUpFor(null);
          handleRemove(id);
        }}
        onCancel={() => setStepUpFor(null)}
      />
    );
  }

  return (
    <div>
      {factors.length === 0 ? (
        <p style={{ color: "var(--w360-text-muted)", fontSize: 14 }}>No factors enrolled yet.</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: "var(--w360-space-4) 0", display: "grid", gap: "var(--w360-space-2)" }}>
          {factors.map((f) => (
            <li
              key={f.factor_id ?? f.kind}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "var(--w360-space-3)",
                border: "1px solid var(--w360-border)",
                borderRadius: 8,
              }}
            >
              <span>
                {f.label}
                {f.confirmed ? "" : " (pending confirmation)"}
              </span>
              {f.factor_id && f.kind === "totp" && (
                <button
                  className="w360-btn w360-btn-secondary"
                  onClick={() => handleRemove(f.factor_id as string)}
                  disabled={removing === f.factor_id}
                  aria-label={removing === f.factor_id ? undefined : `Remove ${f.label}`}
                >
                  {removing === f.factor_id ? "Removing…" : "Remove"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}
      <button className="w360-btn w360-btn-primary" onClick={onAddAnother} disabled={adding}>
        {adding ? "Starting…" : hasTotp ? "Add another authenticator app" : "Add an authenticator app"}
      </button>
    </div>
  );
}

function EnrollmentConfirm({
  factorId,
  secret,
  provisioningUri,
  onConfirmed,
  onCancel,
}: {
  factorId: string;
  secret: string;
  provisioningUri: string;
  onConfirmed: (recoveryCodes: string[]) => void;
  onCancel: () => void;
}) {
  const codeId = useId();
  const errorId = useId();
  const headingRef = useFocusOnMount<HTMLHeadingElement>();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [needsStepUp, setNeedsStepUp] = useState(false);

  async function confirm() {
    setError(null);
    setSubmitting(true);
    try {
      const { recovery_codes } = await confirmTotpEnrollment(factorId, code);
      onConfirmed(recovery_codes);
    } catch (err) {
      if (isStepUpRequired(err)) {
        setNeedsStepUp(true);
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm();
  }

  if (needsStepUp) {
    // The session's verification lapsed between starting and confirming
    // (or this is a second app). Verify with an existing factor, then retry
    // the confirmation with the code already typed for the new app.
    return (
      <StepUpForm
        prompt={STEP_UP_PROMPT}
        onVerified={() => {
          setNeedsStepUp(false);
          confirm();
        }}
        onCancel={() => setNeedsStepUp(false)}
      />
    );
  }

  return (
    <div style={{ marginTop: "var(--w360-space-4)" }}>
      <h3 ref={headingRef} tabIndex={-1} style={{ fontSize: 15, marginTop: 0 }}>
        Set up your authenticator app
      </h3>
      <p style={{ fontSize: 14 }}>
        Enter this setup key in your authenticator app:
      </p>
      <code
        style={{
          display: "block",
          padding: "var(--w360-space-3)",
          background: "var(--w360-neutral-bg)",
          borderRadius: 8,
          fontSize: 14,
          wordBreak: "break-all",
          marginBottom: "var(--w360-space-3)",
        }}
      >
        {secret}
      </code>
      <details style={{ marginBottom: "var(--w360-space-4)" }}>
        <summary style={{ cursor: "pointer", fontSize: 13, color: "var(--w360-text-muted)" }}>
          Provisioning URI (for password managers)
        </summary>
        <code style={{ fontSize: 12, wordBreak: "break-all" }}>{provisioningUri}</code>
      </details>
      <form onSubmit={handleSubmit} noValidate>
        <label className="w360-label" htmlFor={codeId}>
          Enter the 6-digit code
        </label>
        <input
          id={codeId}
          className="w360-input"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          required
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
        {error && (
          <p id={errorId} className="w360-error-text" role="alert">
            {error}
          </p>
        )}
        <div style={{ display: "flex", gap: "var(--w360-space-2)", marginTop: "var(--w360-space-3)" }}>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={submitting || code.length !== 6}>
            {submitting ? "Verifying…" : "Confirm"}
          </button>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const headingRef = useFocusOnMount<HTMLHeadingElement>();
  return (
    <div style={{ marginTop: "var(--w360-space-4)" }}>
      <h3 ref={headingRef} tabIndex={-1} style={{ fontSize: 15, marginTop: 0 }}>
        Save your recovery codes
      </h3>
      <p role="alert" style={{ fontWeight: 600 }}>
        Save these recovery codes now. They will not be shown again.
      </p>
      <p style={{ fontSize: 14, color: "var(--w360-text-muted)" }}>
        Each one can be used once if you lose access to your authenticator app.
      </p>
      <ul
        style={{
          listStyle: "none",
          padding: "var(--w360-space-3)",
          background: "var(--w360-neutral-bg)",
          borderRadius: 8,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(14ch, 1fr))",
          gap: "var(--w360-space-2)",
          fontFamily: "monospace",
        }}
      >
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <button className="w360-btn w360-btn-primary" style={{ marginTop: "var(--w360-space-4)" }} onClick={onDone}>
        I&apos;ve saved these codes
      </button>
    </div>
  );
}
