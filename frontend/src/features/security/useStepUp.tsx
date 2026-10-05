"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { isStepUpRequired } from "./mfaErrors";
import { StepUpForm } from "./StepUpForm";

const PROMPT =
  "This change needs a recent verification. Enter a code from your authenticator app, or a recovery code.";

function retryFailure(err: unknown): string {
  const detail = err instanceof ApiError ? (err.body as { detail?: string } | undefined)?.detail : undefined;
  return detail || "The change could not be completed. Please try again.";
}

/**
 * Runs an action that may need AAL2 (a ``dangerous`` permission). If the
 * server answers ``step_up_required``, the returned ``stepUp`` element
 * offers verification and then retries the same action - the one flow for
 * every protected change, rather than each screen improvising its own.
 *
 * The retry happens after the original ``run`` has returned, so its outcome
 * cannot reach the caller's ``try``. A caller that tracks progress and errors
 * (``useAction``) passes ``onRetry`` and runs the retry through the same path
 * as the first attempt; otherwise a failed retry is shown here, never lost.
 */
export function useStepUp(options: { onRetry?: (again: () => Promise<void>) => void } = {}) {
  const [pending, setPending] = useState<(() => Promise<void>) | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(action: () => Promise<void>): Promise<void> {
    try {
      await action();
    } catch (err) {
      if (isStepUpRequired(err)) {
        setFailure(null);
        setPending(() => action);
        return;
      }
      throw err;
    }
  }

  const stepUp = pending ? (
    <StepUpForm
      prompt={PROMPT}
      onVerified={() => {
        const again = pending;
        setPending(null);
        if (options.onRetry) {
          options.onRetry(again);
          return;
        }
        run(again).catch((err: unknown) => setFailure(retryFailure(err)));
      }}
      onCancel={() => setPending(null)}
    />
  ) : failure ? (
    <p className="w360-error-text" role="alert">
      {failure}
    </p>
  ) : null;

  return { run, stepUp };
}
