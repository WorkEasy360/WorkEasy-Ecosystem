"use client";

import { useEffect, useId, useState } from "react";
import { useSearchParams } from "next/navigation";
import { StatusScreen } from "@/components/StatusScreen";
import { getSession } from "@/lib/api/auth";
import { confirmSignOut, getSignOutRequest, type SignOutRequest } from "@/lib/api/signIn";
import styles from "./ProductAccess.module.css";

type Load = { status: "loading" } | { status: "invalid" } | { status: "ready"; request: SignOutRequest };

/**
 * A product's "sign out of Workeasy360 too" (RP-initiated logout, ADR-044).
 *
 * Opening this page signs no one out: a link alone must never end a session
 * (logout CSRF). The person confirms, and only then is the Workeasy360
 * sign-in ended - every portal session of it. Either way the browser returns
 * to the product's registered sign-out page, which the server names.
 */
export function SignOutConfirm() {
  const searchParams = useSearchParams();
  const handle = searchParams.get("request") ?? "";
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const headingId = useId();

  useEffect(() => {
    let cancelled = false;
    getSession()
      .then(() => getSignOutRequest(handle))
      .then((request) => !cancelled && setLoad({ status: "ready", request }))
      .catch(() => !cancelled && setLoad({ status: "invalid" }));
    return () => {
      cancelled = true;
    };
  }, [handle]);

  async function answer(signOut: boolean) {
    setBusy(true);
    setFailed(false);
    try {
      const { redirect_url: next } = await confirmSignOut(handle, signOut);
      window.location.assign(next);
    } catch {
      setBusy(false);
      setFailed(true);
    }
  }

  if (load.status === "loading") return <StatusScreen>Loading…</StatusScreen>;
  if (load.status === "invalid") {
    return (
      <StatusScreen role="alert">
        <p>This sign-out link has expired. You can close this page or go back to the product.</p>
      </StatusScreen>
    );
  }
  const { request } = load;
  const product = request.product.name;

  return (
    <main id="w360-main" className={styles.main}>
      <div className={styles.column}>
        <section className={`w360-card ${styles.card}`} aria-labelledby={headingId}>
          <h1 id={headingId} className={styles.title}>
            {request.signed_in ? "Sign out of Workeasy360 too?" : "You're signed out of Workeasy360"}
          </h1>
          <p className={styles.lead}>
            {request.signed_in
              ? `You signed out of ${product}. Workeasy360 is still signed in on this device, so opening a product would sign you straight back in.`
              : `You signed out of ${product}, and Workeasy360 is not signed in on this device.`}
          </p>
          {request.signed_in && request.account && (
            <dl className={styles.identity}>
              <div>
                <dt>Signed in to Workeasy360 as</dt>
                <dd>{request.account.email}</dd>
              </div>
            </dl>
          )}
          <div className={styles.choices}>
            {request.signed_in ? (
              <>
                <button type="button" className={`${styles.choice} ${styles.primary}`} disabled={busy} onClick={() => void answer(true)}>
                  <span className={styles.choiceLabel}>Sign out of Workeasy360</span>
                  <span className={styles.choiceHint}>
                    Ends your Workeasy360 session on this device. Other products stay signed in until you sign out of them.
                  </span>
                </button>
                <button type="button" className={styles.choice} disabled={busy} onClick={() => void answer(false)}>
                  <span className={styles.choiceLabel}>Stay signed in to Workeasy360</span>
                  <span className={styles.choiceHint}>Return to {product}.</span>
                </button>
              </>
            ) : (
              <button type="button" className={`${styles.choice} ${styles.primary}`} disabled={busy} onClick={() => void answer(false)}>
                <span className={styles.choiceLabel}>Return to {product}</span>
              </button>
            )}
          </div>
          {failed && (
            <p role="alert" className={styles.error}>
              That didn&apos;t work. Please try again.
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
