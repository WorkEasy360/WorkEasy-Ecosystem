"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { signInToProduct } from "@/lib/api/launcher";
import { launchDenialMessage } from "./launchCopy";
import styles from "./AppCard.module.css";

/**
 * "Sign in" for a product the organization may not hold (ADR-074), from
 * Explore Products. The same contract as `LaunchButton`: a same-origin POST
 * answered with one server-built destination, then an explicit top-level
 * navigation - no destination is ever rendered into the page.
 *
 * What happens next is WorkEasy360's, not this button's: the product starts
 * its own sign-in, WorkEasy360 asks "Continue as <name>" or "Use another
 * account", and the authorization endpoint then decides access. When the
 * organization does not hold the product the product shows its own "no
 * access" page; this button never grants anything.
 */
export function SignInButton({
  orgSlug,
  productCode,
  productName,
  primary = false,
}: {
  orgSlug: string;
  productCode: string;
  productName: string;
  primary?: boolean;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signIn = async () => {
    setPending(true);
    setError(null);
    try {
      const { redirect_url: redirectUrl } = await signInToProduct(orgSlug, productCode);
      window.location.assign(redirectUrl);
    } catch (err) {
      const body =
        err instanceof ApiError && typeof err.body === "object" && err.body !== null
          ? (err.body as { state?: string; code?: string })
          : undefined;
      setError(launchDenialMessage(body?.state ? `sign_in_${body.state}` : body?.code));
      setPending(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className={primary ? "w360-btn w360-btn-primary" : "w360-btn w360-btn-secondary"}
        onClick={signIn}
        disabled={pending}
      >
        {pending ? "Opening…" : `Sign in to ${productName}`}
      </button>
      {error && (
        <p role="alert" className={styles.launchError}>
          {error}
        </p>
      )}
    </>
  );
}
