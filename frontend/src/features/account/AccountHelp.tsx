"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { publicApi, type AccountSupportTopic } from "@/lib/api/public";
import { useSession } from "@/lib/auth/useSession";
import { Button } from "@/features/controlplane/kit";
import { Panel } from "@/features/console/consoleUi";
import { AccountsBrand } from "@/features/signin/AccountsBrand";
import styles from "./Account.module.css";

/**
 * Help & Support for a personal account (Accounts V3, the note's 10.3): a signed-out
 * or signed-in person asks WorkEasy360's own, restricted account-security support -
 * never a partner, and never an automation. The same answer whether or not an account
 * exists for the address, and a request is never proof of who owns an account.
 */

export const ACCOUNT_SUPPORT_TOPICS: { value: AccountSupportTopic; label: string }[] = [
  { value: "cannot_sign_in", label: "I cannot sign in" },
  { value: "lost_two_step", label: "I lost my authenticator app and recovery codes" },
  { value: "account_compromised", label: "Someone else may be using my account" },
  { value: "closure", label: "Closing my account" },
  { value: "other", label: "Something else" },
];

export function AccountHelpScreen() {
  const session = useSession();
  const signedIn = session.status === "ready" && session.session.authenticated ? session.session : null;
  return (
    <div className={styles.shell}>
      <header className={styles.bar}>
        <AccountsBrand />
        <div className={styles.barActions}>
          <a href={signedIn ? "/account" : "/login"}>{signedIn ? "Back to your account" : "Sign in"}</a>
        </div>
      </header>
      <main id="w360-main" className={styles.main}>
        <div className={styles.intro}>
          <h1>Help with your account</h1>
          <p>For problems signing in or keeping your WorkEasy360 account safe.</p>
        </div>
        <Panel title="Before you ask">
          <ul className={styles.list}>
            <li>
              Forgotten your password? <a href="/forgot-password">Reset it by email</a> - it is the quickest way back in.
            </li>
            <li>Lost your phone? Sign in with one of the recovery codes you saved when you set up two-step verification.</li>
            <li>Need help with an organization, a product or a bill? Ask in User Admin: your organization&apos;s owner or your partner can help there.</li>
          </ul>
        </Panel>
        {/* Keyed by who is signed in: a signed-in person's own details fill the form. */}
        <SupportForm key={signedIn?.user_id ?? "signed-out"} email={signedIn?.email ?? ""} name={signedIn?.name ?? ""} />
        <Panel title="What happens next">
          <p>
            Your request goes to WorkEasy360&apos;s own account-security team. Partners and organization administrators do not see it.
          </p>
          <p>
            A request does not prove who owns an account by itself. Before anything about your account changes, support confirms it with you
            through the account&apos;s own sign-in email. WorkEasy360 never asks for your password, a code from your authenticator app, or a
            recovery code.
          </p>
        </Panel>
      </main>
    </div>
  );
}

function SupportForm({ email: knownEmail, name: knownName }: { email: string; name: string }) {
  const id = useId();
  const [email, setEmail] = useState(knownEmail);
  const [name, setName] = useState(knownName);
  const [topic, setTopic] = useState<AccountSupportTopic | "">("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ text: string; field?: string } | null>(null);
  const [reference, setReference] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const address = email.trim();
    if (!/^[^\s@]+@[^\s@]+$/.test(address)) {
      setProblem({ text: "Enter the email address of the account.", field: "email" });
      return;
    }
    if (!topic) {
      setProblem({ text: "Choose what you need help with.", field: "topic" });
      return;
    }
    if (!message.trim()) {
      setProblem({ text: "Tell us what happened.", field: "message" });
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const r = await publicApi.accountSupport({ email: address, name: name.trim(), topic, message: message.trim() });
      setReference(r.reference);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setProblem({ text: "Too many requests from here. Wait a while, then try again." });
      } else {
        const body = err instanceof ApiError ? (err.body as { detail?: string; field?: string } | undefined) : undefined;
        setProblem({ text: body?.detail || "Your request could not be sent. Try again.", field: body?.field });
      }
    } finally {
      setBusy(false);
    }
  }

  if (reference) {
    return (
      <Panel title="Request sent">
        <div role="status">
          <p>
            Your reference is <strong className={styles.mono}>{reference}</strong>. Keep it: support will mention it when they reply to the
            address you gave.
          </p>
        </div>
      </Panel>
    );
  }

  const errorId = `${id}-error`;
  const mark = (field: string) => (problem?.field === field ? { "aria-invalid": true as const, "aria-describedby": errorId } : {});
  return (
    <Panel title="Ask account support">
      <form method="post" className={styles.form} onSubmit={submit} noValidate>
        <div className={styles.fields}>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={`${id}-email`}>
              Email address of the account
            </label>
            <input
              id={`${id}-email`}
              className="w360-input"
              type="email"
              autoComplete="email"
              maxLength={254}
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              {...mark("email")}
            />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={`${id}-name`}>
              Your name
            </label>
            <input
              id={`${id}-name`}
              className="w360-input"
              autoComplete="name"
              maxLength={201}
              value={name}
              onChange={(e) => setName(e.target.value)}
              {...mark("name")}
            />
          </div>
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={`${id}-topic`}>
            What do you need help with?
          </label>
          <select
            id={`${id}-topic`}
            className="w360-input"
            required
            value={topic}
            onChange={(e) => setTopic(e.target.value as AccountSupportTopic | "")}
            {...mark("topic")}
          >
            <option value="">Choose one</option>
            {ACCOUNT_SUPPORT_TOPICS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={`${id}-message`}>
            What happened?
          </label>
          <textarea
            id={`${id}-message`}
            className="w360-input"
            rows={5}
            maxLength={4000}
            required
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            aria-describedby={problem?.field === "message" ? errorId : `${id}-message-hint`}
            aria-invalid={problem?.field === "message" ? true : undefined}
          />
          <p id={`${id}-message-hint`} className="w360-hint">
            Never write your password or a code here.
          </p>
        </div>
        {problem && (
          <p id={errorId} role="alert" className="w360-error-text">
            {problem.text}
          </p>
        )}
        <div className={styles.actions}>
          <Button type="submit" variant="primary" icon="send" loading={busy}>
            Send request
          </Button>
        </div>
      </form>
    </Panel>
  );
}
