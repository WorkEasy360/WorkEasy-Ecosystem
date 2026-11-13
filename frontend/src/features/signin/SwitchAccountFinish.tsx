"use client";

import { useEffect, useState } from "react";
import { StatusScreen } from "@/components/StatusScreen";
import { finishSwitchAccount, getSession } from "@/lib/api/auth";
import { forgetAccountState, leaveTo } from "@/lib/accountState";

/**
 * Switch Account, step two (ADR-066), on the central sign-in host - the
 * session products are signed in with. The portal the switch started on has
 * already signed itself out; this page hands the signed hand-over from the
 * URL fragment (never sent to a server by the browser) to the server, which
 * ends this host's session only if it is the same person's, and then opens
 * the sign-in page the server names. Nothing here chooses a destination.
 */
export function SwitchAccountFinish() {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const handle = new URLSearchParams(window.location.hash.slice(1)).get("handle") ?? "";
    // The session call first: it issues this host's CSRF cookie. On success
    // `leaveTo` replaces this history entry, so the hand-over never lingers in
    // Back; on failure it stays, so Retry can use it again.
    getSession()
      .then(() => finishSwitchAccount(handle))
      .then(({ redirect_url }) => {
        if (cancelled) return;
        forgetAccountState();
        leaveTo(redirect_url);
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) {
    return (
      <StatusScreen role="alert">
        <div>
          <p>WorkEasy360 could not finish switching accounts.</p>
          <p>Close this browser window to end every sign-in, or try again.</p>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </StatusScreen>
    );
  }
  return <StatusScreen>Switching account…</StatusScreen>;
}
