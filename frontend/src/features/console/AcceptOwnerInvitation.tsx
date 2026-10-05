"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { login, logout } from "@/lib/api/auth";
import { ownerInvitationApi, type OwnerInvitationPreview } from "@/lib/api/partnerConsole";
import { useSession } from "@/lib/auth/useSession";
import { takeFragmentToken } from "@/lib/auth/fragmentToken";
import { describeError, when } from "@/features/controlplane/ui";
import { NewAccountForm } from "./AcceptInvitation";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";

type View =
  | { status: "loading" }
  | { status: "invalid" }
  | { status: "error"; message: string }
  | { status: "ready"; preview: OwnerInvitationPreview; token: string };

/**
 * A customer owner's activation page (Phase 10e, closing D-020): the page the
 * emailed link opens after a partner has set up their organization.
 *
 * Nothing here is new authentication. The link (in the URL fragment, so no
 * server ever sees it) proves the mailbox; what the person does next is the
 * platform's own:
 *
 * - **a new person** chooses a password (and a name if none is on record),
 *   which creates their credentials and signs them in;
 * - **someone whose address already has an account** signs in with it - holding
 *   a link never lets anyone set a password on an account that can already
 *   sign in - and then accepts, so there is never a second account;
 * - **someone signed in as the wrong account** is asked to sign out.
 *
 * The password is never in the email and never in the URL.
 */
export function AcceptOwnerInvitation() {
  const session = useSession();
  const token = useRef<string | null | undefined>(undefined);
  const [view, setView] = useState<View>({ status: "loading" });
  // Who is signed in: the session as first read, until this page signs someone
  // in or out itself (then the page owns the answer).
  const [changedTo, setChangedTo] = useState<string | null | undefined>(undefined);
  const [tick, setTick] = useState(0);
  const signedInAs =
    changedTo !== undefined
      ? changedTo
      : session.status === "ready" && session.session.authenticated
        ? (session.session.email ?? "")
        : null;

  useEffect(() => {
    // Read once per page: React may run this effect twice in development.
    if (token.current === undefined) token.current = takeFragmentToken();
    // The session call also issues the CSRF cookie the POSTs below need.
    if (session.status === "loading") return;
    const value = token.current;
    let cancelled = false;
    const run = async () => {
      if (!value) {
        setView({ status: "invalid" });
        return;
      }
      try {
        const preview = await ownerInvitationApi.preview(value);
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

  return (
    <main id="w360-main" className={styles.gate}>
      <div className={`w360-card ${styles.gateCard}`}>
        {view.status === "loading" && (
          <>
            <h1 className={styles.gateTitle}>Activate your account</h1>
            <p aria-live="polite">Checking your link…</p>
          </>
        )}
        {view.status === "invalid" && (
          <>
            <h1 className={styles.gateTitle}>This link can&apos;t be used</h1>
            <p role="alert">
              The link isn&apos;t valid. It may have expired, been replaced by a newer one, been withdrawn, or already been used.
              Ask the person who set up your account to send you a new one.
            </p>
            <p>
              Already activated? <Link href="/login">Sign in</Link>.
            </p>
          </>
        )}
        {view.status === "error" && (
          <>
            <h1 className={styles.gateTitle}>Activate your account</h1>
            <p role="alert">{view.message}</p>
            <div>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setTick((n) => n + 1)}>
                Try again
              </button>
            </div>
          </>
        )}
        {view.status === "ready" && (
          <Activation
            token={view.token}
            preview={view.preview}
            signedInAs={signedInAs}
            onInvalid={() => setView({ status: "invalid" })}
            // The preview is asked for again as the new visitor, and shown as
            // loading until it answers - never as the old visitor's view.
            onSignedIn={(email) => {
              setChangedTo(email);
              setView({ status: "loading" });
              setTick((n) => n + 1);
            }}
            onSignedOut={() => {
              setChangedTo(null);
              setView({ status: "loading" });
              setTick((n) => n + 1);
            }}
          />
        )}
      </div>
    </main>
  );
}

function Activation({
  token,
  preview,
  signedInAs,
  onInvalid,
  onSignedIn,
  onSignedOut,
}: {
  token: string;
  preview: OwnerInvitationPreview;
  signedInAs: string | null;
  onInvalid: () => void;
  onSignedIn: (email: string) => void;
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
      const payload = body ? { password: body.password, ...(body.full_name ? { full_name: body.full_name } : {}) } : {};
      const result = await ownerInvitationApi.accept(token, payload);
      router.replace(`/o/${encodeURIComponent(result.organization_slug)}`);
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError) {
        const detail = (err.body as { detail?: string; problems?: string[]; field?: string } | undefined) ?? {};
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
          setProblems([detail.detail || `This link was sent to ${preview.email}. Sign out and open it again.`]);
          return;
        }
        if (err.code === "organization_not_writable") {
          setProblems([`${org} isn't accepting changes at the moment. Contact ${preview.partner_name}.`]);
          return;
        }
        if (err.code === "membership_invalid" && detail.field === "full_name") {
          setProblems(["Enter your name."]);
          return;
        }
      }
      setProblems([describeError(err)]);
    }
  }

  const intro = (
    <>
      <h1 className={styles.gateTitle}>Activate {org}</h1>
      <p>
        <strong>{preview.partner_name}</strong> has set up <strong>{org}</strong> for you, and named{" "}
        <strong>{preview.email}</strong> as its owner. This link expires {when(preview.expires_at)}.
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

  // Signed in as the owner named on the link: one deliberate step.
  if (preview.signed_in_as_invitee && signedInAs !== null) {
    return (
      <>
        {intro}
        <p>You&apos;re signed in as {preview.email}. Accept to become the owner of {org}.</p>
        {errors}
        <div>
          <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={() => void accept()}>
            {busy ? "Opening…" : `Become the owner of ${org}`}
          </button>
        </div>
      </>
    );
  }

  // Signed in as somebody else.
  if (signedInAs !== null) {
    return (
      <>
        {intro}
        <p role="alert">
          You&apos;re signed in as {signedInAs || "another account"}, but this link was sent to {preview.email}. Sign out to
          continue as {preview.email}.
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

  // The address already has an account: sign in with it; no second account is made.
  if (preview.account_exists || needsSignIn) {
    return (
      <>
        {intro}
        <p role={needsSignIn ? "alert" : undefined}>
          You already have an account for {preview.email}. Sign in with it to take ownership of {org}.
        </p>
        <SignIn email={preview.email} onSignedIn={onSignedIn} />
      </>
    );
  }

  return (
    <>
      {intro}
      <p>Choose a password to activate your account. You&apos;ll use {preview.email} and this password to sign in.</p>
      <NewAccountForm
        busy={busy}
        errors={errors}
        askName={preview.needs_name}
        submitLabel="Set password and continue"
        busyLabel="Setting up…"
        onSubmit={(body) => void accept(body)}
        onMismatch={setProblems}
      />
    </>
  );
}

const LOGIN_PROBLEMS: Record<string, string> = {
  invalid_credentials: "That email and password don't match.",
  account_locked: "This account is locked for a while after too many attempts. Try again later, or reset your password.",
  account_disabled: "This account can't be used to sign in.",
  email_not_verified: "This address hasn't been verified yet.",
};

function SignIn({ email, onSignedIn }: { email: string; onSignedIn: (email: string) => void }) {
  const passwordId = useId();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  return (
    <form
      className="w360-stack-form"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setProblem(null);
        login(email, password).then(
          (result) => onSignedIn(result.email || email),
          (err: unknown) => {
            setBusy(false);
            setProblem(err instanceof ApiError ? (LOGIN_PROBLEMS[err.code] ?? describeError(err)) : describeError(err));
          },
        );
      }}
    >
      <div>
        <span className="w360-label">Email</span>
        <p>{email}</p>
      </div>
      <div>
        <label className="w360-label" htmlFor={passwordId}>
          Password
        </label>
        <input
          id={passwordId}
          className="w360-input"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      {problem && (
        <p role="alert" className="w360-error-text">
          {problem}
        </p>
      )}
      <div className={local.actions}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <Link href="/forgot-password">Forgot your password?</Link>
      </div>
    </form>
  );
}
