"use client";

import { useEffect, useState } from "react";
import { getSession, redeemHandoff } from "@/lib/api/auth";
import { StatusScreen } from "@/components/StatusScreen";

/**
 * Where the central sign-in hands a sign-in to this portal (ADR-043).
 *
 * The one-time code arrives in the URL **fragment** (`#code=...`), which no
 * server, proxy or `Referer` ever sees; it is removed from the address and the
 * history straight away, then spent once for this host's own session. The
 * page to open comes back from the server (validated there against this
 * portal's allowlist) - never from the address.
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
      .then(({ next }) => window.location.replace(next.startsWith("/") && !next.startsWith("//") ? next : "/"))
      .catch(() => setFailed(true));
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
