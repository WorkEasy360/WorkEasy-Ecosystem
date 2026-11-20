"use client";

import { useState } from "react";
import { logout, switchAccount } from "@/lib/api/auth";
import { Button } from "@/features/controlplane/kit";
import { AccountsBrand } from "@/features/signin/AccountsBrand";
import { ACCOUNT_HELP_HREF, CENTER_SECTIONS, type CenterSection } from "./centerConfig";
import styles from "./Account.module.css";

/**
 * The Accounts Control Center (ADR-072; Accounts V3): one person's WorkEasy360 account
 * on WorkEasy360 Accounts, in exactly four sections - My Profile, Login & Security,
 * Devices & Sessions and Settings. Organization administration (members, seats,
 * subscriptions, billing) stays on User Admin and buying in the Store; Settings links
 * there. Every page carries Help & Support.
 */
export function CenterShell({
  current,
  title,
  lead,
  children,
}: {
  current: CenterSection;
  /** The page's heading; omitted when the section renders its own (a reused screen). */
  title?: string;
  lead?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [leaving, setLeaving] = useState<"switch" | "out" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const leave = (how: "switch" | "out") => {
    setLeaving(how);
    setError(null);
    const step =
      how === "switch"
        ? // ADR-066's Switch Account: this sign-in ends (and its products are told),
          // then the sign-in page asks who is next.
          switchAccount().then(({ redirect_url }) => redirect_url)
        : logout().then(() => "/login");
    step
      .then((target) => window.location.assign(target))
      .catch(() => {
        setLeaving(null);
        setError(how === "switch" ? "Could not switch accounts. Try again." : "Could not sign out. Try again.");
      });
  };
  return (
    <div className={styles.shell}>
      <header className={styles.bar}>
        <AccountsBrand />
        <div className={styles.barActions}>
          <Button size="sm" icon="users" loading={leaving === "switch"} disabled={leaving !== null} onClick={() => leave("switch")}>
            Switch account
          </Button>
          <Button size="sm" icon="logout" loading={leaving === "out"} disabled={leaving !== null} onClick={() => leave("out")}>
            Sign out
          </Button>
        </div>
      </header>
      <nav aria-label="Your account" className={styles.centerNav}>
        <ul>
          {CENTER_SECTIONS.map((s) => (
            <li key={s.key}>
              <a href={s.href} aria-current={current === s.key ? "page" : undefined}>
                {s.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <main id="w360-main" className={styles.main}>
        {title && (
          <div className={styles.intro}>
            <h1>{title}</h1>
            {lead && <p>{lead}</p>}
          </div>
        )}
        {error && <p role="alert">{error}</p>}
        {children}
      </main>
      <footer className={styles.footer}>
        <a href={ACCOUNT_HELP_HREF}>Help &amp; Support</a>
        <span className={styles.muted}>Can&apos;t sign in, lost your authenticator, or think someone else is using your account?</span>
      </footer>
    </div>
  );
}
