"use client";

import { useEffect, useId, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ProductIcon, productIconKnown } from "@/components/ProductIcon";
import { StatusScreen } from "@/components/StatusScreen";
import { getSession } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { answerPendingAccess, getPendingAccess, type AccessChoice, type PendingAccess } from "@/lib/api/signIn";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import styles from "./ProductAccess.module.css";

type Load = { status: "loading" } | { status: "invalid" } | { status: "error" } | { status: "ready"; access: PendingAccess };

/**
 * The access chooser (ADR-044, docs/universal-sso/design.md section 3.3; the
 * unified-login brief section 5, ADR-066): "Welcome to WorkEasy360 - how would
 * you like to continue?" with the account that is signed in, and Continue with
 * Same Account / Use Different Account / Create New Account. Shown the first
 * time a product is opened in a sign-in (the Ask once default), every time
 * for "Ask me each time", or when the product asks to confirm the account.
 *
 * It only *answers*. The request it answers was verified and parked by the
 * authorization endpoint after every check - membership, entitlement, seat,
 * access policy - had already passed, and "Continue" sends the browser to
 * the resume endpoint, which runs them all again before any code exists.
 * Every destination comes back from the server; this page never builds one.
 */
export function ProductAccessChooser() {
  const searchParams = useSearchParams();
  const resume = searchParams.get("resume") ?? "";
  const portals = usePortals();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState<AccessChoice | null>(null);
  const [failed, setFailed] = useState(false);
  const headingId = useId();
  const rememberId = useId();
  const sameHintId = useId();
  const otherHintId = useId();
  const newHintId = useId();
  const productHintId = useId();

  useEffect(() => {
    let cancelled = false;
    if (!resume) {
      // Deferred so no state is set synchronously inside the effect.
      Promise.resolve().then(() => !cancelled && setLoad({ status: "invalid" }));
      return () => {
        cancelled = true;
      };
    }
    // The session call first: it issues this host's CSRF cookie, which the
    // answer (a POST) needs.
    getSession()
      .then(() => getPendingAccess(resume))
      .then((access) => !cancelled && setLoad({ status: "ready", access }))
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          // Signed out since the request was parked: sign in, then the same
          // request comes back here (it is bound to the person who parked it).
          // A full page load, so the sign-in page reads the session afresh.
          // eslint-disable-next-line @next/next/no-location-assign-relative-destination
          window.location.assign(`/login?resume=${encodeURIComponent(resume)}`);
          return;
        }
        setLoad({ status: err instanceof ApiError && (err.status === 404 || err.status === 400) ? "invalid" : "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [resume]);

  async function choose(choice: AccessChoice) {
    setBusy(choice);
    setFailed(false);
    try {
      const { redirect_url: next } = await answerPendingAccess(resume, choice, remember);
      // A real top-level navigation to a server-built destination.
      window.location.assign(next);
    } catch {
      setBusy(null);
      setFailed(true);
    }
  }

  if (load.status === "loading") return <StatusScreen>Loading…</StatusScreen>;
  if (load.status === "invalid") {
    return (
      <StatusScreen role="alert">
        <div>
          <p>This sign-in link has expired or was already used.</p>
          <p>Go back to the product you were opening and try again.</p>
        </div>
      </StatusScreen>
    );
  }
  if (load.status === "error") {
    return (
      <StatusScreen role="alert">
        <div>
          <p>Workeasy360 could not be reached right now.</p>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </StatusScreen>
    );
  }

  const { access } = load;
  const product = access.product.name;
  const offersProductSignIn = access.choices.product;
  const offersNewAccount = access.choices.create_account === true;
  const shownName = access.account.name || access.account.email;

  return (
    <main id="w360-main" className={styles.main}>
      <div className={styles.column}>
        <section className={`w360-card ${styles.card}`} aria-labelledby={headingId}>
          <div className={styles.header}>
            <span className={styles.glyph} aria-hidden="true">
              {productIconKnown(access.product.icon) ? <ProductIcon icon={access.product.icon} size={24} /> : product.slice(0, 1)}
            </span>
            <div>
              <p className={styles.productName}>Opening {product}</p>
              <h1 id={headingId} className={styles.title}>
                Welcome to WorkEasy360
              </h1>
            </div>
          </div>
          <p className={styles.lead}>How would you like to continue?</p>

          <div className={styles.account} aria-label="Currently signed in as">
            <p className={styles.accountCaption}>Currently signed in as</p>
            <div className={styles.accountRow}>
              <span className={styles.avatar} aria-hidden="true">
                {initials(shownName)}
              </span>
              <div className={styles.accountText}>
                <p className={styles.accountName}>{shownName}</p>
                {access.account.name && <p className={styles.accountDetail}>{access.account.email}</p>}
                {access.organization.name && <p className={styles.accountDetail}>{access.organization.name}</p>}
              </div>
            </div>
          </div>

          <div className={styles.choices} role="group" aria-label="How would you like to continue?">
            <button
              type="button"
              className={`${styles.choice} ${styles.primary}`}
              aria-describedby={sameHintId}
              disabled={busy !== null}
              onClick={() => void choose("workeasy360")}
            >
              <span className={styles.choiceLabel}>{busy === "workeasy360" ? "Continuing…" : "Continue with Same Account"}</span>
              <span id={sameHintId} className={styles.choiceHint}>
                Continue using your existing WorkEasy360 login.
              </span>
            </button>
            <button
              type="button"
              className={styles.choice}
              aria-describedby={otherHintId}
              disabled={busy !== null}
              onClick={() => void choose("switch_account")}
            >
              <span className={styles.choiceLabel}>{busy === "switch_account" ? "Signing out…" : "Use Different Account"}</span>
              <span id={otherHintId} className={styles.choiceHint}>
                Sign in using another WorkEasy360 email/account.
              </span>
            </button>
            {offersNewAccount && (
              <button
                type="button"
                className={styles.choice}
                aria-describedby={newHintId}
                disabled={busy !== null}
                onClick={() => void choose("create_account")}
              >
                <span className={styles.choiceLabel}>{busy === "create_account" ? "Opening…" : "Create New Account"}</span>
                <span id={newHintId} className={styles.choiceHint}>
                  Register a new WorkEasy360 account. You&apos;ll be signed out of this one first.
                </span>
              </button>
            )}
            {offersProductSignIn && (
              <button
                type="button"
                className={styles.choice}
                aria-describedby={productHintId}
                disabled={busy !== null}
                onClick={() => void choose("product")}
              >
                <span className={styles.choiceLabel}>{busy === "product" ? "Opening…" : `Use ${product} sign-in`}</span>
                <span id={productHintId} className={styles.choiceHint}>
                  Use the product&apos;s separate sign-in page.
                </span>
              </button>
            )}
          </div>

          <div className={styles.remember}>
            <input
              id={rememberId}
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              disabled={busy !== null}
            />
            <label htmlFor={rememberId}>
              {offersProductSignIn ? `Remember my choice for ${product}` : `Don't ask me again for ${product}`}
            </label>
          </div>

          <div className={styles.secondary}>
            <button type="button" className={styles.linkButton} disabled={busy !== null} onClick={() => void choose("cancel")}>
              Cancel
            </button>
            <a href={portalHref(portals, "app", "/settings/sign-in")}>Sign-in preferences</a>
          </div>
          <p className={styles.note}>
            You&apos;re asked once per product each time you sign in. You can change this in sign-in preferences.
          </p>
          {failed && (
            <p role="alert" className={styles.error}>
              That didn&apos;t work. The link may have expired - go back to {product} and try again.
            </p>
          )}
        </section>
      </div>
    </main>
  );
}

/** Up to two initials for the account badge ("Rahul Sharma" -> "RS"). */
function initials(name: string): string {
  const words = name.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[words.length - 1][0] : (words[0] ?? "?").slice(0, 2);
  return letters.toUpperCase();
}
