"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { launchProduct } from "@/lib/api/launcher";
import { launchDenialMessage } from "./launchCopy";
import styles from "./AppCard.module.css";

/**
 * Opens a product. See `lib/api/launcher.ts` for why this is a same-origin
 * `fetch` to the platform's own launch endpoint, followed by an explicit
 * top-level navigation to whatever it answers with - never a plain `<form>`
 * POST, because every real product's launch URL is a different origin and
 * this app's CSP refuses a form submission that answers with a cross-origin
 * redirect (verified in Chrome).
 *
 * Nothing here ever renders a destination into the page before the click: a
 * screen reader or a "view source" sees a plain button, and the URL exists
 * only for the instant between the response arriving and the browser leaving
 * this page.
 */
export function LaunchButton({ orgSlug, productCode }: { orgSlug: string; productCode: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = async () => {
    setPending(true);
    setError(null);
    try {
      const { redirect_url: redirectUrl } = await launchProduct(orgSlug, productCode);
      window.location.assign(redirectUrl);
    } catch (err) {
      const body = err instanceof ApiError && typeof err.body === "object" && err.body !== null
        ? (err.body as { state?: string; code?: string })
        : undefined;
      // A launcher denial names its `state`; an organization-level refusal
      // (read-only, 402) only has an error `code`.
      const state = body?.state ?? body?.code;
      setError(launchDenialMessage(state));
      setPending(false);
    }
  };

  return (
    <>
      <button type="button" className="w360-btn w360-btn-primary" onClick={open} disabled={pending}>
        {pending ? "Opening…" : "Open"}
      </button>
      {error && (
        <p role="alert" className={styles.launchError}>
          {error}
        </p>
      )}
    </>
  );
}
