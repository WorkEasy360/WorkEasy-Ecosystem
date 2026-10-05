"use client";

import { useEffect, useId, useRef } from "react";
import { applyAccent, safeColor } from "./useBranding";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * A live preview of a partner's brand on the three surfaces a customer sees:
 * the sign-in page, the header and the launcher (Phase 10d).
 *
 * Built to be inert. Every value is drawn as a React text node or an ``img``
 * whose source is a local data URL or a same-origin address - never markup, a
 * style string or a remote URL - and the accent reaches the DOM only through
 * the CSSOM (``applyAccent`` accepts a strict six-digit hex colour and ignores
 * everything else), so a hostile value cannot leak into the page. The preview
 * is drawn from the draft in memory: showing it stores nothing.
 */
export function LivePreview({
  brandName,
  accent,
  logoSrc,
  supportEmail,
}: {
  brandName: string;
  accent: string;
  /** A ``data:`` URL of a file the person just chose, or a same-origin address. */
  logoSrc: string | null;
  supportEmail: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  const name = brandName.trim() || "Your brand";

  useEffect(() => {
    if (ref.current) applyAccent(ref.current, accent);
  }, [accent]);

  const mark = logoSrc ? (
    // eslint-disable-next-line @next/next/no-img-element -- a local data URL or a same-origin, server-validated logo
    <img className={local.simLogo} src={logoSrc} alt="" />
  ) : (
    <span className={local.simMark} aria-hidden="true" />
  );

  return (
    <div ref={ref} className={styles.stack} role="group" aria-labelledby={titleId}>
      <p id={titleId} className={styles.muted}>
        Preview - the layout and every security prompt stay Workeasy360&apos;s.
        {safeColor(accent) ? ` Accent ${accent}.` : " Default accent."}
      </p>
      <div className={local.split}>
        <section className={local.simulated} aria-label="Sign-in page preview">
          <div className={local.simHeader}>
            {mark}
            <strong>{name}</strong>
          </div>
          <div aria-hidden="true">
            <p>Sign in to {name}</p>
            <div className={local.simTile}>Email</div>
            <div className={local.simTile}>Password</div>
          </div>
          <div aria-hidden="true" className={local.simButton}>
            Sign in
          </div>
          {supportEmail && <p className={styles.muted}>Need help? {supportEmail}</p>}
        </section>

        <section className={local.simulated} aria-label="Header preview">
          <div className={local.simHeader}>
            {mark}
            <strong>{name}</strong>
            <span className={styles.muted}>My products</span>
          </div>
          <p className={styles.muted}>The header customers see once signed in.</p>
        </section>

        <section className={local.simulated} aria-label="Launcher preview">
          <div className={local.simHeader}>
            {mark}
            <strong>{name}</strong>
          </div>
          <div aria-hidden="true">
            <div className={local.simTile}>Product one</div>
            <div className={local.simTile}>Product two</div>
          </div>
          <p className={styles.muted}>Where customers open the products they hold.</p>
        </section>
      </div>
    </div>
  );
}
