"use client";

import { useCallback, useEffect, useState } from "react";
import { issueHandoff, logout } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { MODE_TO_PORTAL, isState, portalHref, safeNext, type Destination, type PortalOrigins } from "@/lib/portals";
import type { SignInMode } from "./signInMode";
import styles from "./LoginScreen.module.css";

const PORTAL_NAME: Record<Destination, string> = {
  admin: "Admin portal",
  partner: "Partner portal",
  app: "your workspace",
};

type Step =
  | { kind: "working" }
  | { kind: "denied" }
  | { kind: "failed" };

/**
 * The central sign-in's last step (ADR-043): this person is signed in here;
 * hand the sign-in to the portal they chose.
 *
 * The choice (`mode`) only says where to go. The server answers from the
 * account itself - live StaffGrants for the Admin portal, an active partner
 * membership for the Partner portal - and the portal checks again on every
 * request. Its answers are shown as they are: go (a one-time code in the URL
 * fragment of that portal's `/auth/continue`), or no access (nothing about the
 * portal is shown).
 *
 * Without the portal's binding value (`state`) - someone opened this page
 * directly - the browser first visits that portal's own sign-in address,
 * which sets the binding cookie on that host and sends it straight back here.
 */
export function PortalHandoff({
  origins,
  mode,
  next,
  state,
}: {
  origins: PortalOrigins;
  mode: SignInMode;
  next: string | null;
  state: string | null;
}) {
  const portal: Destination = MODE_TO_PORTAL[mode];
  const [step, setStep] = useState<Step>({ kind: "working" });

  const go = useCallback(() => {
    if (!isState(state)) {
      const page = safeNext(portal, next);
      window.location.assign(portalHref(origins, portal, `/login?next=${encodeURIComponent(page)}`));
      return;
    }
    issueHandoff(mode, next, state).then(
      ({ redirect_url }) => window.location.assign(redirect_url),
      (err: unknown) => {
        const code = err instanceof ApiError ? err.code : "";
        if (code === "portal_access_denied") setStep({ kind: "denied" });
        else setStep({ kind: "failed" });
      },
    );
  }, [origins, portal, mode, next, state]);

  useEffect(() => {
    go();
  }, [go]);

  // A full load after signing out, so nothing of the signed-in page survives.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  const signOut = () => logout().finally(() => window.location.assign(`${origins.auth}/login`));
  const asUser = () => window.location.assign(portalHref(origins, "app", "/login"));

  if (step.kind === "denied") {
    return (
      <div className={styles.denied}>
        <div role="alert" aria-live="assertive">
          <h2 className={styles.cardTitle}>You don&apos;t have access to the {PORTAL_NAME[portal]}.</h2>
          <p>You are signed in, but this account can&apos;t open it. You can continue to your own organization and products.</p>
        </div>
        <div className={styles.deniedActions}>
          <button type="button" className="w360-btn w360-btn-primary" onClick={asUser}>
            Continue as User
          </button>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={signOut}>
            Sign out
          </button>
        </div>
      </div>
    );
  }
  if (step.kind === "failed") {
    return (
      <div role="alert">
        <h2 className={styles.cardTitle}>That didn&apos;t work</h2>
        <p>Signing you in to the {PORTAL_NAME[portal]} couldn&apos;t be completed.</p>
        <p>
          <a className="w360-btn w360-btn-primary" href={portalHref(origins, portal, "/login")}>
            Try again
          </a>
        </p>
      </div>
    );
  }
  return (
    <p role="status" aria-live="polite">
      Opening the {PORTAL_NAME[portal]}…
    </p>
  );
}
