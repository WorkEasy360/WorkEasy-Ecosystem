"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api/client";
import { accountApi } from "@/lib/api/console";
import { useSession } from "@/lib/auth/useSession";
import { describeError } from "@/features/controlplane/ui";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";

type CheckoutLink = { reference: string; org: string };

function readCheckoutLink(): CheckoutLink | null {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const reference = params.get("reference") ?? "";
  const org = params.get("org") ?? "";
  return reference && org && reference.length <= 128 && org.length <= 64 ? { reference, org } : null;
}

/**
 * The development test payment provider's "hosted page" (Phase 8 extension).
 * It exists only while the backend has the test provider switched on
 * (never in production): the outcome chosen here is signed like a real
 * provider's callback and settled through the same path.
 */
export function TestCheckout() {
  const session = useSession();
  const [link, setLink] = useState<CheckoutLink | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Read after hydration: the fragment never reaches the server.
    void Promise.resolve().then(() => {
      if (!cancelled) setLink(readCheckoutLink());
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function complete(outcome: "succeeded" | "failed") {
    if (!link) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      await accountApi.completeTestCheckout(link.reference, link.org, outcome);
      setDone(
        outcome === "succeeded"
          ? "Test payment succeeded. The invoice is marked as paid once the confirmation is processed."
          : "Test payment failed. Nothing was paid; the invoice stays open.",
      );
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 404
          ? "This test payment wasn't found, or test payments are switched off on this server."
          : describeError(err),
      );
    } finally {
      setBusy(false);
    }
  }

  const ready = session.status !== "loading" && link !== undefined;

  return (
    <main id="w360-main" className={styles.gate}>
      <div className={`w360-card ${styles.gateCard}`}>
        <p>
          <span className="w360-badge w360-badge-warning">Development test page</span>
        </p>
        <h1 className={styles.gateTitle}>Test payment</h1>
        <p>
          This page stands in for a payment provider&apos;s checkout while Workeasy360 runs in development. No card is
          charged and no money moves.
        </p>
        {!ready && <p aria-live="polite">Loading…</p>}
        {ready && link === null && <p role="alert">This test payment link is incomplete. Start the payment again.</p>}
        {ready && link && (
          <>
            <dl className={local.details}>
              <div>
                <dt>Reference</dt>
                <dd className={cp.mono}>{link.reference}</dd>
              </div>
            </dl>
            {!done && (
              <div className={local.actions}>
                <button
                  type="button"
                  className="w360-btn w360-btn-primary"
                  disabled={busy}
                  onClick={() => void complete("succeeded")}
                >
                  Pay (succeed)
                </button>
                <button
                  type="button"
                  className="w360-btn w360-btn-secondary"
                  disabled={busy}
                  onClick={() => void complete("failed")}
                >
                  Fail
                </button>
              </div>
            )}
          </>
        )}
        {done && <p role="status">{done}</p>}
        {error && (
          <p role="alert" className="w360-error-text">
            {error}
          </p>
        )}
        <p>
          <Link href="/">Return to Workeasy360</Link> and open your organization&apos;s billing page to see the result.
        </p>
      </div>
    </main>
  );
}
