"use client";

import { useEffect, useId, useRef, useState } from "react";
import { applyAccent, safeColor } from "./useBranding";
import { Tabs } from "./widgets";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";
import css from "./WhiteLabelSetup.module.css";

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

// --- the portal preview (staff white-label wizard) -----------------------------------------------------------

export type PreviewMode = "login" | "dashboard" | "launcher" | "header";

/** Everything the preview draws, taken from the draft in memory. Plain strings
 * and flags: nothing here is markup, a style or an address that is fetched. */
export interface PreviewData {
  brandName: string;
  shortName: string;
  accent: string;
  /** A ``data:`` URL of a file the person just chose, or a same-origin address. */
  logoSrc: string | null;
  welcome: string;
  footer: string;
  supportName: string;
  supportEmail: string;
  supportPhone: string;
  supportUrl: string;
  websiteUrl: string;
  loginShowWelcome: boolean;
  loginShowSupport: boolean;
  /** Names of the products shown in the portal. */
  products: string[];
}

const MODES: { key: PreviewMode; label: string }[] = [
  { key: "login", label: "Login" },
  { key: "dashboard", label: "Dashboard" },
  { key: "launcher", label: "Product launcher" },
  { key: "header", label: "Header" },
];

const supportLines = (d: PreviewData): string[] => [d.supportName, d.supportEmail, d.supportPhone, d.supportUrl].map((v) => v.trim()).filter(Boolean);

function Mark({ src }: { src: string | null }) {
  return src ? (
    // eslint-disable-next-line @next/next/no-img-element -- a local data URL or a same-origin, server-validated logo
    <img className={css.previewLogo} src={src} alt="" />
  ) : (
    <span className={css.previewMark} aria-hidden="true" />
  );
}

function PreviewFooter({ data }: { data: PreviewData }) {
  const lines = supportLines(data);
  const website = data.websiteUrl.trim();
  if (!data.footer.trim() && lines.length === 0 && !website) return null;
  return (
    <div className={css.previewFooter}>
      {data.footer.trim() && <span>{data.footer.trim()}</span>}
      {lines.length > 0 && <span>Support: {lines.join(" · ")}</span>}
      {website && <span>Website: {website}</span>}
    </div>
  );
}

function PreviewPane({ mode, data }: { mode: PreviewMode; data: PreviewData }) {
  const name = data.brandName.trim() || "Your brand";
  const short = data.shortName.trim() || name;
  const welcome = data.welcome.trim();
  const lines = supportLines(data);
  const website = data.websiteUrl.trim();

  if (mode === "login") {
    return (
      <section className={css.previewPane} aria-label="Sign-in page preview">
        <div className={css.previewHeader}>
          <Mark src={data.logoSrc} />
          <strong>{name}</strong>
        </div>
        {data.loginShowWelcome && welcome ? (
          <p>{welcome}</p>
        ) : (
          <p className={css.previewMute}>{welcome ? "The welcome message is turned off for the sign-in page." : "No welcome message is set."}</p>
        )}
        <div aria-hidden="true">
          <p>Sign in to {name}</p>
          <div className={css.previewTile}>Email</div>
          <div className={css.previewTile}>Password</div>
        </div>
        <div aria-hidden="true" className={css.previewButton}>
          Sign in
        </div>
        {data.loginShowSupport && lines.length > 0 && <p className={css.previewMute}>Need help? {lines.join(" · ")}</p>}
        {website && <p className={css.previewMute}>Website: {website}</p>}
      </section>
    );
  }

  if (mode === "dashboard") {
    return (
      <section className={css.previewPane} aria-label="Dashboard preview">
        <div className={css.previewHeader}>
          <Mark src={data.logoSrc} />
          <strong>{short}</strong>
        </div>
        {welcome ? <p>{welcome}</p> : <p className={css.previewMute}>No welcome message is set.</p>}
        <p className={css.previewMute}>The dashboard, its navigation and every security prompt stay Workeasy360&apos;s.</p>
        <PreviewFooter data={data} />
      </section>
    );
  }

  if (mode === "launcher") {
    return (
      <section className={css.previewPane} aria-label="Product launcher preview">
        <div className={css.previewHeader}>
          <Mark src={data.logoSrc} />
          <strong>{short}</strong>
        </div>
        {data.products.length > 0 ? (
          <ul className={css.previewTiles}>
            {data.products.map((product) => (
              <li key={product} className={css.previewTile}>
                {product}
              </li>
            ))}
          </ul>
        ) : (
          <p className={css.previewMute}>No product is shown in the portal yet.</p>
        )}
        <p className={css.previewMute}>Customers open the products they hold; a product is only shown here, never granted.</p>
        <PreviewFooter data={data} />
      </section>
    );
  }

  return (
    <section className={css.previewPane} aria-label="Header preview">
      <div className={css.previewHeader}>
        <Mark src={data.logoSrc} />
        <strong>{short}</strong>
        <span className={css.previewMute}>My products</span>
      </div>
      <p className={css.previewMute}>The header customers see once signed in. It shows the short name when one is set, and the brand name otherwise.</p>
    </section>
  );
}

/**
 * A live preview of the customer portal in four modes - sign-in, dashboard,
 * product launcher and header - drawn from the draft in memory (nothing is saved
 * or served by looking). Built to be inert in the same way as {@link LivePreview}:
 * every value is a React text node or an ``img`` from a local data URL or a
 * same-origin address, and the accent reaches the DOM only through
 * {@link applyAccent}. ``only`` draws one mode without the tabs.
 */
export function PortalPreview({ data, only }: { data: PreviewData; only?: PreviewMode }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  const [mode, setMode] = useState<PreviewMode>(only ?? "login");

  useEffect(() => {
    if (ref.current) applyAccent(ref.current, data.accent);
  }, [data.accent]);

  const pane = <PreviewPane mode={only ?? mode} data={data} />;
  return (
    <div ref={ref} className={styles.stack} role="group" aria-labelledby={titleId}>
      <p id={titleId} className={styles.muted}>
        Preview - nothing here is saved or served. The layout and every security prompt stay Workeasy360&apos;s.
        {safeColor(data.accent.trim()) ? ` Primary colour ${data.accent.trim()}.` : " Default colour."}
      </p>
      {only ? (
        pane
      ) : (
        <Tabs label="Preview of" items={MODES.map((m) => ({ key: m.key, label: m.label }))} selected={mode} onSelect={setMode}>
          {pane}
        </Tabs>
      )}
    </div>
  );
}
