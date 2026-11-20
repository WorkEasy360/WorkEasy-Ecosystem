"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ProductIcon, productIconKnown } from "@/components/ProductIcon";
import { StatusScreen } from "@/components/StatusScreen";
import { getSession } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { answerPendingAccess, getPendingAccess, type AccessChoice, type PendingAccess } from "@/lib/api/signIn";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import { AccountsBrand } from "./AccountsBrand";
import styles from "./ProductAccess.module.css";

type View = "current" | "another";

/** The URL parameter that marks screen 2, so the browser's Back returns to screen 1. */
const VIEW_PARAM = "view";

function viewFromLocation(): View {
  return new URLSearchParams(window.location.search).get(VIEW_PARAM) === "another" ? "another" : "current";
}

type Load = { status: "loading" } | { status: "invalid" } | { status: "error" } | { status: "ready"; access: PendingAccess };

/**
 * The access chooser (ADR-044, docs/universal-sso/design.md section 3.3;
 * ADR-066; two screens since ADR-076). Shown the first time a product is
 * opened in a sign-in (the Ask once default, every product including ones
 * added later), every time for "Ask me each time", or when the product asks
 * to confirm the account.
 *
 * Screen 1, "Continue to <product>": the account that is signed in, then
 * **Continue as <name>** and **Use another account** - nothing else
 * competes with them. Screen 2, "Choose how to continue": **Log in to another
 * account** or **Create new account**, and Back. Moving between the screens
 * is this page's own state (in the browser history, so the browser's Back
 * button returns to screen 1): nothing is signed out, switched or sent until
 * one of the four choices is pressed, so Back or Cancel never leaves a
 * half-switched identity.
 *
 * It only *answers*. The request it answers was verified and parked by the
 * authorization endpoint after membership, organization and access policy
 * had passed. Whether the organization holds the product is asked too but
 * delivered after this page (ADR-074), so the page appears whether or not it
 * was bought and "Use another account" is offered to someone signed in to an
 * account without it. "Continue" sends the browser to the resume endpoint,
 * which runs every check again - entitlement and seat included - before any
 * code exists; a refusal goes to the product's own "no access" page. The two
 * other-account choices are answered by the server too, which signs this
 * account out first. Every destination comes back from the server; this page
 * never builds one.
 */
export function ProductAccessChooser() {
  const searchParams = useSearchParams();
  const resume = searchParams.get("resume") ?? "";
  const portals = usePortals();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState<AccessChoice | null>(null);
  const [failed, setFailed] = useState(false);
  const [view, setView] = useState<View>(() => (searchParams.get(VIEW_PARAM) === "another" ? "another" : "current"));
  // Whether screen 2 was reached from screen 1 on this page: then Back is the browser's own Back.
  const pushed = useRef(false);
  const headingId = useId();
  const rememberId = useId();
  const sameHintId = useId();
  const otherHintId = useId();
  const newHintId = useId();
  const productHintId = useId();

  // The browser's Back and Forward move between the two screens.
  useEffect(() => {
    const onPop = () => {
      const now = viewFromLocation();
      if (now === "current") pushed.current = false;
      setView(now);
      setFailed(false);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function showAnother() {
    const url = new URL(window.location.href);
    url.searchParams.set(VIEW_PARAM, "another");
    window.history.pushState(window.history.state, "", url);
    pushed.current = true;
    setFailed(false);
    setView("another");
  }

  function showCurrent() {
    if (pushed.current) {
      // The browser's own Back, so screen 1 is not in the history twice.
      window.history.back();
      return;
    }
    const url = new URL(window.location.href);
    if (url.searchParams.get(VIEW_PARAM) === "another") {
      url.searchParams.delete(VIEW_PARAM);
      window.history.replaceState(window.history.state, "", url);
    }
    setFailed(false);
    setView("current");
  }

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
      // "Don't ask me again" belongs to screen 1's Continue (and the product's own sign-in).
      const keep = remember && (choice === "workeasy360" || choice === "product");
      const { redirect_url: next } = await answerPendingAccess(resume, choice, keep);
      // A real top-level navigation to a server-built destination.
      window.location.assign(next);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 401) {
        // The WorkEasy360 session ended while this page was open: sign in
        // again, then the same request comes back here (bound to its person).
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.assign(`/login?resume=${encodeURIComponent(resume)}`);
        return;
      }
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
          <p>WorkEasy360 could not be reached right now.</p>
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

  const header = (title: string) => (
    <div className={styles.header}>
      <span className={styles.glyph} aria-hidden="true">
        {productIconKnown(access.product.icon) ? <ProductIcon icon={access.product.icon} size={24} /> : product.slice(0, 1)}
      </span>
      <div>
        <p className={styles.productName}>WorkEasy360 sign-in</p>
        <h1 id={headingId} className={styles.title}>
          {title}
        </h1>
      </div>
    </div>
  );
  const problem = failed && (
    <p role="alert" className={styles.error}>
      That didn&apos;t work. The link may have expired - go back to {product} and try again.
    </p>
  );

  // Screen 2: another account, or a new one. Nothing has changed yet.
  if (view === "another") {
    return (
      <main id="w360-main" className={styles.main}>
        <div className={styles.column}>
          <AccountsBrand />
          <section className={`w360-card ${styles.card}`} aria-labelledby={headingId}>
            {header("Choose how to continue")}
            <p className={styles.lead}>
              Opening {product}. You&apos;ll be signed out of {shownName} first.
            </p>
            <div className={styles.choices} role="group" aria-label="Choose how to continue">
              <button
                type="button"
                className={`${styles.choice} ${styles.primary}`}
                aria-describedby={otherHintId}
                disabled={busy !== null}
                onClick={() => void choose("switch_account")}
              >
                <span className={styles.choiceLabel}>{busy === "switch_account" ? "Signing out…" : "Log in to another account"}</span>
                <span id={otherHintId} className={styles.choiceHint}>
                  Sign in with another WorkEasy360 email and password.
                </span>
              </button>
              <button
                type="button"
                className={styles.choice}
                aria-describedby={newHintId}
                disabled={busy !== null || !offersNewAccount}
                onClick={() => void choose("create_account")}
              >
                <span className={styles.choiceLabel}>{busy === "create_account" ? "Opening…" : "Create new account"}</span>
                <span id={newHintId} className={styles.choiceHint}>
                  {offersNewAccount
                    ? "Register a new WorkEasy360 account, then verify your email."
                    : "New accounts can't be created right now."}
                </span>
              </button>
            </div>
            <div className={styles.secondary}>
              <button type="button" className={styles.linkButton} disabled={busy !== null} onClick={showCurrent}>
                Back
              </button>
              {offersProductSignIn && (
                <button
                  type="button"
                  className={styles.linkButton}
                  aria-describedby={productHintId}
                  disabled={busy !== null}
                  onClick={() => void choose("product")}
                >
                  {busy === "product" ? "Opening…" : `Use ${product} sign-in`}
                </button>
              )}
              {offersProductSignIn && (
                <span id={productHintId} className={styles.srOnly}>
                  Use the product&apos;s separate sign-in page.
                </span>
              )}
            </div>
            {problem}
          </section>
        </div>
      </main>
    );
  }

  // Screen 1: the account that is signed in.
  return (
    <main id="w360-main" className={styles.main}>
      <div className={styles.column}>
        <AccountsBrand />
        <section className={`w360-card ${styles.card}`} aria-labelledby={headingId}>
          {header(`Continue to ${product}`)}

          <div className={styles.account} aria-label="Signed in as">
            <p className={styles.accountCaption}>Signed in as</p>
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

          <div className={styles.choices} role="group" aria-label={`Continue to ${product}`}>
            <button
              type="button"
              className={`${styles.choice} ${styles.primary}`}
              aria-describedby={sameHintId}
              disabled={busy !== null}
              onClick={() => void choose("workeasy360")}
            >
              <span className={styles.choiceLabel}>{busy === "workeasy360" ? "Continuing…" : `Continue as ${shownName}`}</span>
              <span id={sameHintId} className={styles.choiceHint}>
                No password needed: you&apos;re already signed in.
              </span>
            </button>
            <button type="button" className={styles.choice} disabled={busy !== null} onClick={showAnother}>
              <span className={styles.choiceLabel}>Use another account</span>
            </button>
          </div>

          <div className={styles.remember}>
            <input
              id={rememberId}
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              disabled={busy !== null}
            />
            <label htmlFor={rememberId}>Don&apos;t ask me again for {product}</label>
          </div>

          <div className={styles.secondary}>
            <button type="button" className={styles.linkButton} disabled={busy !== null} onClick={() => void choose("cancel")}>
              Cancel
            </button>
            <a href={portalHref(portals, "app", "/settings/sign-in")}>Sign-in preferences</a>
          </div>
          <p className={styles.note}>
            You&apos;re asked once per product each time you sign in. You can change this for every product in sign-in
            preferences.
          </p>
          {problem}
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
