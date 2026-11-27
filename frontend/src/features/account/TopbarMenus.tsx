"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import { accountApi, type AccountEvent } from "@/lib/api/console";
import { when } from "@/features/controlplane/ui";
import { describeAccountEvent } from "@/features/console/MySecurityScreen";
import { ACCOUNT_HELP_HREF } from "./centerConfig";
import styles from "./AccountShell.module.css";

/**
 * The top bar's AI Assistant and bell (2026-11-27, the owner's reference image
 * "User Account Dashboard", to be matched exactly).
 *
 * - AI Assistant: guided answers about this account, each with a link to the page
 *   that does it (the owner's choice). No AI model is called and nothing the person
 *   does here leaves the page; a real assistant can replace the answers later.
 * - The bell: this account's recent security activity (sign-ins, password, two-step,
 *   email changes) - the account's own events, the same feed as Settings. The dot
 *   shows activity newer than the last time the person opened the bell on this
 *   browser; without browser storage, activity of the last seven days.
 */

/** A top-bar popover: opens and closes on its button, closes on Escape (focus back on
 * the button) or a pointer outside it. */
function usePopover() {
  const [open, setOpen] = useState(false);
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
  return { open, setOpen, wrap, button };
}

/** The guided answers: what a person most often needs on their own account. */
export const ASSISTANT_TOPICS: readonly { q: string; a: string; href: string; go: string }[] = [
  {
    q: "How do I turn on two-step verification?",
    a: "Open Login & Security and choose “Set up two-step verification”. Scan the code with an authenticator app, enter the 6-digit code, and keep your recovery codes somewhere safe.",
    href: "/account/security",
    go: "Open Login & Security",
  },
  {
    q: "How do I change my password?",
    a: "Open Login & Security and choose “Change password”. Changing it signs out every other device; this one stays signed in.",
    href: "/account/security",
    go: "Open Login & Security",
  },
  {
    q: "I forgot my password",
    a: "Reset it by email: a link goes to your sign-in email. Resetting it signs you out everywhere.",
    href: "/forgot-password",
    go: "Reset my password",
  },
  {
    q: "How do I change my sign-in email?",
    a: "Open Login & Security, then Sign-in email. A link goes to the new address; the change happens when you open it. With two-step verification on, you also enter a code.",
    href: "/account/security",
    go: "Open Login & Security",
  },
  {
    q: "Where am I signed in?",
    a: "Devices & Sessions lists every device and browser where this account is signed in, and the products you signed in to. You can sign out any one of them.",
    href: "/account/sessions",
    go: "Open Devices & Sessions",
  },
  {
    q: "I think someone else is using my account",
    a: "Change your password, then use “Sign out everywhere” in Devices & Sessions, and turn on two-step verification. Help & Support can help if you can no longer sign in.",
    href: "/account/sessions",
    go: "Open Devices & Sessions",
  },
  {
    q: "How do I update my name, photo or time zone?",
    a: "Open My Profile and choose Edit. Products show your new details the next time you open them.",
    href: "/account/profile?edit=1",
    go: "Edit my profile",
  },
  {
    q: "Which emails will I get?",
    a: "Choose them in Settings, under Notifications. Security and recovery notices are always sent.",
    href: "/account/settings",
    go: "Open Settings",
  },
  {
    q: "How do I close my account?",
    a: "Settings, then Close account. Reopening it later is a request to Help & Support.",
    href: "/account/settings",
    go: "Open Settings",
  },
];

export function AssistantMenu() {
  const { open, setOpen, wrap, button } = usePopover();
  const panelId = useId();
  const titleId = useId();
  const [picked, setPicked] = useState<number | null>(null);
  const topic = picked === null ? null : ASSISTANT_TOPICS[picked];
  return (
    <div className={styles.popWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={styles.assistantButton}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => {
          setPicked(null);
          setOpen((v) => !v);
        }}
      >
        <Icon name="sparkles" size={20} />
        AI Assistant
      </button>
      <div id={panelId} className={`${styles.popPanel} ${styles.assistantPanel}`} hidden={!open} role="region" aria-labelledby={titleId}>
        <p id={titleId} className={styles.popTitle}>
          <Icon name="sparkles" size={18} /> AI Assistant
        </p>
        {!open ? null : topic ? (
          <div className={styles.answer}>
            <p className={styles.answerQ}>{topic.q}</p>
            <p>{topic.a}</p>
            <a href={topic.href} className={styles.answerGo}>
              {topic.go} <Icon name="arrowRight" size={16} />
            </a>
            <button type="button" className={styles.backButton} onClick={() => setPicked(null)}>
              <Icon name="chevronLeft" size={16} /> Other questions
            </button>
          </div>
        ) : (
          <>
            <p className={styles.popLead}>Quick answers about your WorkEasy360 account.</p>
            <ul className={styles.topics}>
              {ASSISTANT_TOPICS.map((t, i) => (
                <li key={t.q}>
                  <button type="button" onClick={() => setPicked(i)}>
                    {t.q}
                    <Icon name="chevronRight" size={16} />
                  </button>
                </li>
              ))}
            </ul>
            <p className={styles.popFoot}>
              Not here? <a href={ACCOUNT_HELP_HREF}>Help &amp; Support</a>
            </p>
          </>
        )}
      </div>
    </div>
  );
}

const SEEN_KEY = "w360.accounts.alertsSeen";
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

function readSeen(): string | null {
  try {
    return window.localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

function writeSeen(at: string) {
  try {
    window.localStorage.setItem(SEEN_KEY, at);
  } catch {
    /* no storage: the dot falls back to the last seven days */
  }
}

/** Whether the bell shows its dot: activity newer than the last look. */
export function hasUnseen(events: AccountEvent[], seen: string | null, now: number = Date.now()): boolean {
  const newest = events.reduce<string | null>((m, e) => (m === null || e.occurred_at > m ? e.occurred_at : m), null);
  if (!newest) return false;
  if (seen) return newest > seen;
  return now - Date.parse(newest) < WEEK_MS;
}

export function AlertsMenu() {
  const { open, setOpen, wrap, button } = usePopover();
  const panelId = useId();
  const titleId = useId();
  const [events, setEvents] = useState<AccountEvent[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [unseen, setUnseen] = useState(false);
  useEffect(() => {
    let live = true;
    accountApi
      .activity()
      .then((d) => {
        if (!live) return;
        const sorted = [...d.events].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
        setEvents(sorted);
        setUnseen(hasUnseen(sorted, readSeen()));
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, []);
  const toggle = () => {
    setOpen((v) => !v);
    if (events && events.length > 0) {
      writeSeen(events[0].occurred_at);
      setUnseen(false);
    }
  };
  return (
    <div className={styles.popWrap} ref={wrap}>
      <button
        ref={button}
        type="button"
        className={styles.iconLink}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={unseen ? "Security alerts (new activity)" : "Security alerts"}
        title="Security alerts"
        onClick={toggle}
      >
        <Icon name="bell" size={24} />
        {unseen && <span className={styles.bellDot} aria-hidden="true" />}
      </button>
      <div id={panelId} className={`${styles.popPanel} ${styles.alertsPanel}`} hidden={!open} role="region" aria-labelledby={titleId}>
        <p id={titleId} className={styles.popTitle}>
          <Icon name="bell" size={18} /> Security alerts
        </p>
        {!open ? null : failed ? (
          <p className={styles.popLead}>Could not load your recent activity.</p>
        ) : events === null ? (
          <p className={styles.popLead}>Loading…</p>
        ) : events.length === 0 ? (
          <p className={styles.popLead}>No recent activity.</p>
        ) : (
          <ul className={styles.alerts}>
            {events.slice(0, 5).map((e, i) => (
              <li key={`${e.occurred_at}-${i}`}>
                <span>
                  {describeAccountEvent(e)}
                  {e.device_label && <span className={styles.alertMuted}> - {e.device_label}</span>}
                </span>
                <time dateTime={e.occurred_at} className={styles.alertMuted}>
                  {when(e.occurred_at)}
                </time>
              </li>
            ))}
          </ul>
        )}
        <p className={styles.popFoot}>
          <a href="/account/settings#security-activity">
            View All <Icon name="arrowRight" size={14} />
          </a>
        </p>
      </div>
    </div>
  );
}
