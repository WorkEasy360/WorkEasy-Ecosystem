"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Logo } from "@/components/brand/Logo";
import { Icon } from "@/components/sidebar/icons";
import { logout, switchAccount } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { initialsOf } from "@/features/controlplane/kit";
import { ACCOUNT_HELP_HREF, CENTER_SECTIONS, type CenterSection } from "./centerConfig";
import styles from "./AccountShell.module.css";

/**
 * The Accounts Control Center (ADR-072; Accounts V3): one person's WorkEasy360 account
 * on WorkEasy360 Accounts, in exactly four sections - My Profile, Login & Security,
 * Devices & Sessions and Settings. Organization administration (members, seats,
 * subscriptions, billing) stays on User Admin and buying in the Store; Settings links
 * there. Every page carries Help & Support.
 *
 * 2026-11-27 (the owner's reference image "User Account Dashboard"): the deep green
 * sidebar with the "User Account" pill and the Help & Support card, and a light top
 * bar with Help and the account menu (Switch account, Sign out). The reference's
 * "Home" entry is not a fifth menu: My Profile is the page that opens first and
 * carries the overview (V3 section 04 allows exactly four menus).
 */

const SECTION_ICONS: Record<CenterSection, string> = {
  profile: "user",
  security: "shield",
  sessions: "monitor",
  settings: "settings",
};

/** Who is signed in, as the top bar draws them. */
export interface ShellPerson {
  name: string;
  email: string;
  photo_url?: string | null;
}

export function Avatar({ person, size = 40 }: { person: ShellPerson | null; size?: number }) {
  if (person?.photo_url) {
    // eslint-disable-next-line @next/next/no-img-element -- a same-origin capability URL
    return <img className={styles.avatar} src={person.photo_url} alt="" width={size} height={size} />;
  }
  const initials = person ? initialsOf(person.name || person.email) : "";
  return (
    <span className={styles.avatar} style={{ width: size, height: size }} aria-hidden="true">
      {initials || <Icon name="user" size={Math.round(size / 2)} />}
    </span>
  );
}

function AccountMenu({
  person,
  leaving,
  onLeave,
}: {
  person: ShellPerson | null;
  leaving: "switch" | "out" | null;
  onLeave: (how: "switch" | "out") => void;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const wrap = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    const onPointer = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  return (
    <div className={styles.menuWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={styles.menuButton}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label="Account menu"
        onClick={() => setOpen((v) => !v)}
      >
        <Avatar person={person} />
        <Icon name="chevronDown" size={18} />
      </button>
      <div id={panelId} className={styles.menuPanel} hidden={!open}>
        {person && (
          <p className={styles.menuWho}>
            <strong>{person.name || person.email}</strong>
            {person.name && <span>{person.email}</span>}
          </p>
        )}
        <button type="button" className={styles.menuItem} disabled={leaving !== null} onClick={() => onLeave("switch")}>
          <Icon name="users" size={18} />
          {leaving === "switch" ? "Switching…" : "Switch account"}
        </button>
        <button type="button" className={styles.menuItem} disabled={leaving !== null} onClick={() => onLeave("out")}>
          <Icon name="logout" size={18} />
          {leaving === "out" ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </div>
  );
}

export function CenterShell({
  current,
  title,
  lead,
  person,
  children,
}: {
  current: CenterSection;
  /** The page's heading; omitted when the section renders its own (a reused screen). */
  title?: string;
  lead?: React.ReactNode;
  /** The signed-in person with their photo, when the page has loaded the profile;
   * otherwise the top bar uses the session's name and email. */
  person?: ShellPerson | null;
  children: React.ReactNode;
}) {
  // Also issues the CSRF cookie every form here needs (auth_views docstring).
  const session = useSession();
  const fromSession: ShellPerson | null =
    session.status === "ready" && session.session.authenticated
      ? { name: session.session.name ?? "", email: session.session.email ?? "" }
      : null;
  const who = person ?? fromSession;
  const helpId = useId();
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
    <div className={styles.frame}>
      <aside className={styles.sidebar}>
        <div className={styles.brandRow}>
          <a href="/account" className={styles.brand}>
            <Logo onBand />
          </a>
          <p className={styles.pill}>
            <Icon name="user" size={18} /> User Account
          </p>
        </div>
        <nav aria-label="Your account" className={styles.nav}>
          <ul>
            {CENTER_SECTIONS.map((s) => (
              <li key={s.key}>
                <a href={s.href} aria-current={current === s.key ? "page" : undefined}>
                  <Icon name={SECTION_ICONS[s.key]} size={22} />
                  <span>{s.label}</span>
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <section className={styles.helpCard} aria-labelledby={helpId}>
          <span className={styles.helpIcon} aria-hidden="true">
            <Icon name="headset" size={28} />
          </span>
          <div>
            <h2 id={helpId}>Help &amp; Support</h2>
            <p>Can&apos;t sign in, lost your authenticator, or think someone else is using your account?</p>
          </div>
          <a href={ACCOUNT_HELP_HREF} className={styles.helpLink}>
            Get Support <Icon name="chevronRight" size={18} />
          </a>
        </section>
      </aside>
      <div className={styles.content}>
        <header className={styles.topbar}>
          <a href={ACCOUNT_HELP_HREF} className={styles.iconLink} aria-label="Help & Support" title="Help & Support">
            <Icon name="help" size={24} />
          </a>
          <AccountMenu person={who} leaving={leaving} onLeave={leave} />
        </header>
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
      </div>
    </div>
  );
}
