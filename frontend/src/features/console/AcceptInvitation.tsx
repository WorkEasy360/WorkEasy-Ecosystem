"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { logout } from "@/lib/api/auth";
import { accountApi, type InvitationPreview } from "@/lib/api/console";
import { useSession } from "@/lib/auth/useSession";
import { takeFragmentToken } from "@/lib/auth/fragmentToken";
import { describeError, when } from "@/features/controlplane/ui";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";


type View =
  | { status: "loading" }
  | { status: "invalid" }
  | { status: "error"; message: string }
  | { status: "ready"; preview: InvitationPreview; token: string };

/**
 * The invitee's side of an invitation (public: no organization exists for
 * them yet). Previewed first so the person sees what they are joining, then:
 *
 * - signed in as the invited address: one button joins;
 * - signed in as someone else: sign out first (acceptance is by the invited
 *   address only);
 * - the address already has an account: sign in first, then open the link
 *   again;
 * - otherwise: choose a name and password, which creates the account and
 *   joins in one step (holding the emailed link verifies the address).
 */
export function AcceptInvitation() {
  const session = useSession();
  const token = useRef<string | null | undefined>(undefined);
  const [view, setView] = useState<View>({ status: "loading" });
  const [signedOut, setSignedOut] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    // Read once per page: React may run this effect twice in development.
    if (token.current === undefined) token.current = takeFragmentToken();
    // The session call also issues the CSRF cookie the POST below needs.
    if (session.status === "loading") return;
    const value = token.current;
    let cancelled = false;
    const run = async () => {
      if (!value) {
        setView({ status: "invalid" });
        return;
      }
      try {
        const preview = await accountApi.previewInvitation(value);
        if (!cancelled) setView({ status: "ready", preview, token: value });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.code === "invitation_invalid") setView({ status: "invalid" });
        else setView({ status: "error", message: describeError(err) });
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [session.status, tick]);

  const signedInAs =
    !signedOut && session.status === "ready" && session.session.authenticated ? session.session.email ?? "" : null;

  return (
    <main id="w360-main" className={styles.gate}>
      <div className={`w360-card ${styles.gateCard}`}>
        {view.status === "loading" && (
          <>
            <h1 className={styles.gateTitle}>Invitation</h1>
            <p aria-live="polite">Checking your invitation…</p>
          </>
        )}
        {view.status === "invalid" && (
          <>
            <h1 className={styles.gateTitle}>This invitation can&apos;t be used</h1>
            <p role="alert">
              The link isn&apos;t valid. It may have expired, been withdrawn, or already been used. Ask the person who
              invited you to send a new invitation.
            </p>
            <p>
              <Link href="/login">Go to sign in</Link>
            </p>
          </>
        )}
        {view.status === "error" && (
          <>
            <h1 className={styles.gateTitle}>Invitation</h1>
            <p role="alert">{view.message}</p>
            <div>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setTick((n) => n + 1)}>
                Try again
              </button>
            </div>
          </>
        )}
        {view.status === "ready" && (
          <Invitation
            token={view.token}
            preview={view.preview}
            signedInAs={signedInAs}
            onInvalid={() => setView({ status: "invalid" })}
            onSignedOut={() => {
              setSignedOut(true);
              setTick((n) => n + 1);
            }}
          />
        )}
      </div>
    </main>
  );
}

function Invitation({
  token,
  preview,
  signedInAs,
  onInvalid,
  onSignedOut,
}: {
  token: string;
  preview: InvitationPreview;
  signedInAs: string | null;
  onInvalid: () => void;
  onSignedOut: () => void;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [needsSignIn, setNeedsSignIn] = useState(false);
  const org = preview.organization_name;

  async function accept(body?: { full_name: string; password: string }) {
    setBusy(true);
    setProblems([]);
    try {
      const result = await accountApi.acceptInvitation(token, body);
      router.replace(`/o/${encodeURIComponent(result.organization_slug)}`);
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError) {
        const detail = (err.body as { detail?: string; problems?: string[] } | undefined) ?? {};
        if (err.code === "invitation_invalid") return onInvalid();
        if (err.code === "sign_in_required") {
          setNeedsSignIn(true);
          return;
        }
        if (err.code === "password_policy_violation" && detail.problems?.length) {
          setProblems(detail.problems);
          return;
        }
        if (err.code === "invitation_email_mismatch") {
          setProblems([
            detail.detail || "This invitation was sent to a different email address. Sign out and open the link again.",
          ]);
          return;
        }
      }
      setProblems([describeError(err)]);
    }
  }

  const intro = (
    <>
      <h1 className={styles.gateTitle}>Join {org}</h1>
      <p>
        You&apos;ve been invited to join <strong>{org}</strong> as <strong>{preview.role_name}</strong>, using{" "}
        <strong>{preview.email}</strong>. This invitation expires {when(preview.expires_at)}.
      </p>
    </>
  );

  const errors =
    problems.length > 0 ? (
      <div role="alert" className="w360-error-text">
        <ul>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </div>
    ) : null;

  if (preview.signed_in_as_invitee && signedInAs !== null) {
    return (
      <>
        {intro}
        {errors}
        <div>
          <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={() => void accept()}>
            {busy ? "Joining…" : `Join ${org}`}
          </button>
        </div>
      </>
    );
  }

  if (signedInAs !== null) {
    return (
      <>
        {intro}
        <p role="alert">
          You&apos;re signed in as {signedInAs || "another account"}, but this invitation was sent to {preview.email}.
          Sign out to accept it as {preview.email}.
        </p>
        {errors}
        <div>
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              logout()
                .then(onSignedOut, () => setProblems(["Signing out didn't complete. Try again."]))
                .finally(() => setBusy(false));
            }}
          >
            Sign out
          </button>
        </div>
      </>
    );
  }

  if (preview.account_exists || needsSignIn) {
    return (
      <>
        {intro}
        <p role={needsSignIn ? "alert" : undefined}>
          An account already exists for {preview.email}. Sign in first, then open the invitation link from your email
          again.
        </p>
        <p>
          <Link className="w360-btn w360-btn-primary" href="/login">
            Sign in
          </Link>
        </p>
      </>
    );
  }

  return (
    <>
      {intro}
      <p>Create your account to join. You&apos;ll use this email address and password to sign in.</p>
      <NewAccountForm busy={busy} errors={errors} onSubmit={(body) => void accept(body)} onMismatch={setProblems} />
    </>
  );
}

/** Name, password and confirmation, for someone whose emailed link is about to
 * create or complete their account. Shared by every one-time-link screen that
 * does this (a member invitation, a customer owner's activation). */
export function NewAccountForm({
  busy,
  errors,
  onSubmit,
  onMismatch,
  askName = true,
  submitLabel = "Create account and join",
  busyLabel = "Joining…",
}: {
  busy: boolean;
  errors: React.ReactNode;
  onSubmit: (body: { full_name: string; password: string }) => void;
  onMismatch: (problems: string[]) => void;
  /** False when the account already has a name (the field is then not shown and no name is sent). */
  askName?: boolean;
  submitLabel?: string;
  busyLabel?: string;
}) {
  const nameId = useId();
  const passwordId = useId();
  const hintId = useId();
  const confirmId = useId();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  return (
    <form
      className="w360-stack-form"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (askName && !name.trim()) return onMismatch(["Enter your name."]);
        if (password !== confirm) return onMismatch(["The passwords don't match."]);
        onSubmit({ full_name: name.trim(), password });
      }}
    >
      {askName && (
        <div>
          <label className="w360-label" htmlFor={nameId}>
            Your name
          </label>
          <input
            id={nameId}
            className="w360-input"
            autoComplete="name"
            required
            maxLength={255}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
      )}
      <div>
        <label className="w360-label" htmlFor={passwordId}>
          Choose a password
        </label>
        <input
          id={passwordId}
          className="w360-input"
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-describedby={hintId}
        />
        <p id={hintId} className="w360-hint">
          At least 12 characters. A passphrase of several words is easiest to remember.
        </p>
      </div>
      <div>
        <label className="w360-label" htmlFor={confirmId}>
          Confirm the password
        </label>
        <input
          id={confirmId}
          className="w360-input"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </div>
      {errors}
      <div className={local.actions}>
        <button
          type="submit"
          className="w360-btn w360-btn-primary"
          disabled={busy || (askName && !name.trim()) || !password}
        >
          {busy ? busyLabel : submitLabel}
        </button>
      </div>
    </form>
  );
}
