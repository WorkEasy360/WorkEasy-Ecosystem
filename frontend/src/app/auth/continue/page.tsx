"use client";

import { useEffect, useState } from "react";
import { getSession, redeemHandoff } from "@/lib/api/auth";
import { StatusScreen } from "@/components/StatusScreen";

/** ADR-071: one automatic retry of a failed hand-off, then the message. */
const RETRY_KEY = "w360_handoff_retry";
const RETRY_WINDOW_MS = 2 * 60 * 1000;

function retriedRecently(): boolean {
  try {
    const at = Number(window.sessionStorage.getItem(RETRY_KEY) ?? "0");
    return Date.now() - at < RETRY_WINDOW_MS;
  } catch {
    return true; // no storage: never loop
  }
}

function markRetry(clear = false): void {
  try {
    if (clear) window.sessionStorage.removeItem(RETRY_KEY);
    else window.sessionStorage.setItem(RETRY_KEY, String(Date.now()));
  } catch {
    /* no storage: the message shows instead of a retry */
  }
}

/**
 * Where WorkEasy360 Accounts hands a sign-in to this portal (ADR-043, and
 * ADR-071 for the customer portal).
 *
 * The one-time code arrives in the URL **fragment** (`#code=...`), which no
 * server, proxy or `Referer` ever sees; it is removed from the address and the
 * history straight away, then spent once for this host's own session. The
 * page to open comes back from the server (validated there against this
 * portal's allowlist) - never from the address.
 *
 * ADR-071: a hand-off that cannot be completed (the link expired, a second tab
 * spent it, the binding cookie was lost) is retried once, automatically: this
 * portal's `/login` starts a fresh sign-in through Accounts, which - still
 * signed in - hands it back without asking for the password. Only a second
 * failure within two minutes shows the message, so nobody is left on it by a
 * single dropped step (the ADR-059 complaint).
 */
export default function ContinueSignInPage() {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const code = new URLSearchParams(window.location.hash.slice(1)).get("code");
    window.history.replaceState(null, "", window.location.pathname);
    Promise.resolve(code)
      .then((found) => {
        if (!found) throw new Error("no hand-off code");
        // The session call issues this host's CSRF cookie before the write.
        return getSession().then(() => redeemHandoff(found));
      })
      .then(({ next }) => {
        markRetry(true);
        window.location.replace(next.startsWith("/") && !next.startsWith("//") ? next : "/");
      })
      .catch(() => {
        if (!retriedRecently()) {
          markRetry();
          window.location.replace("/login");
          return;
        }
        setFailed(true);
      });
  }, []);

  if (failed) {
    return (
      <StatusScreen role="alert">
        <h1>Sign-in couldn&apos;t be completed</h1>
        <p>The sign-in link has expired or was already used. Signing in again takes you straight back.</p>
        <p>
          <a className="w360-btn w360-btn-primary" href="/login">
            Sign in again
          </a>
        </p>
      </StatusScreen>
    );
  }
  return (
    <StatusScreen>
      <p>Signing you in…</p>
    </StatusScreen>
  );
}
