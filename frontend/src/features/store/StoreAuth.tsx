"use client";

import { Suspense, useEffect, useId, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Icon } from "@/components/sidebar/icons";
import { login } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { publicApi } from "@/lib/api/public";
import { usePortals } from "@/lib/portalContext";
import { storeSignsInThroughAccounts } from "@/lib/portals";
import { fieldProblem } from "@/features/auth/AdminLogin";
import { loginErrorMessage } from "@/features/auth/LoginForm";
import { REFERRAL_TOKEN, SignupForm } from "@/features/auth/SelfService";
import { StoreShell } from "./StoreShell";
import { useStoreAccount } from "./account";
import { rememberStoreReturn, safeStorePath } from "./storeReturn";
import { STORE_HOME } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * Signing in and signing up in the Store (ADR-065): the same WorkEasy360 accounts,
 * the same one sign-in endpoint (rate limits, lockout, CSRF), on the Store's own
 * host - so the session, the referral the visitor followed and the purchase they
 * chose all stay in one place. The forms sit in the checkout's first step and on
 * the Store's own `/login` and `/signup` pages.
 *
 * ADR-073: while WorkEasy360 Accounts hosts identity (ADR-071), the Store signs in
 * and up **through Accounts** instead - the forms become one button each, to the
 * Store's own `/login` (the proxy's hand-off) or `/signup` (Accounts' page), and
 * the buyer comes back to the same Store page. Accounts is then signed in too, so
 * the first product opened after buying asks for no password.
 */

/** Full navigations to the Store's own `/login` and `/signup`, which the proxy
 * answers (a seam for tests, as `checkoutNavigation` is for the payment page). */
export const storeNavigation = { assign: (url: string) => window.location.assign(url) };

/** The Store page this is, to come back to after signing in or up on Accounts. */
function hereInTheStore(): string {
  return `${window.location.pathname}${window.location.search}`;
}

/** ADR-073: sign in on WorkEasy360 Accounts and come back to this Store page. */
export function AccountsSignInButton({ next, compact = false }: { next?: string; compact?: boolean }) {
  return (
    <div className={`${styles.authForm} ${compact ? styles.authCompact : ""}`}>
      <p>Sign in with your WorkEasy360 account. You come straight back here, signed in.</p>
      <button
        type="button"
        className={`${shell.primary} ${styles.wide}`}
        onClick={() => storeNavigation.assign(`/login?next=${encodeURIComponent(next ?? hereInTheStore())}`)}
      >
        <Icon name="login" size={18} /> Sign in with WorkEasy360 Accounts
      </button>
    </div>
  );
}

export function StoreSignInForm({ onSignedIn, compact = false }: { onSignedIn: () => void; compact?: boolean }) {
  const origins = usePortals();
  if (storeSignsInThroughAccounts(origins)) return <AccountsSignInButton compact={compact} />;
  return <StoreOwnSignInForm onSignedIn={onSignedIn} compact={compact} />;
}

function StoreOwnSignInForm({ onSignedIn, compact = false }: { onSignedIn: () => void; compact?: boolean }) {
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unverified, setUnverified] = useState(false);
  const [resent, setResent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const problem = fieldProblem(email, password);
    setError(problem);
    setUnverified(false);
    if (problem) return;
    setBusy(true);
    try {
      await login(email.trim(), password, { remember });
      setPassword("");
      onSignedIn();
    } catch (err) {
      setError(loginErrorMessage(err));
      setUnverified(err instanceof ApiError && err.code === "email_not_verified");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form method="post" onSubmit={submit} noValidate className={`${styles.authForm} ${compact ? styles.authCompact : ""}`} aria-label="Sign in">
      <div className={styles.field}>
        <label htmlFor={emailId}>Email address</label>
        <input
          id={emailId}
          className="w360-input"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
      </div>
      <div className={styles.field}>
        <label htmlFor={passwordId}>Password</label>
        <span className={styles.passwordRow}>
          <input
            id={passwordId}
            className="w360-input"
            type={show ? "text" : "password"}
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
          />
          <button type="button" className={styles.reveal} aria-pressed={show} aria-label={show ? "Hide password" : "Show password"} onClick={() => setShow((v) => !v)}>
            <Icon name={show ? "eye" : "eyeOff"} size={18} />
          </button>
        </span>
      </div>
      <div className={styles.authRow}>
        <label className={styles.check}>
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Remember me
        </label>
        <Link href="/forgot-password">Forgot password?</Link>
      </div>
      {error && (
        <p id={errorId} role="alert" className="w360-error-text">
          {error}
        </p>
      )}
      {unverified && (
        <p>
          <button type="button" className={shell.secondary} onClick={() => publicApi.resendVerification(email.trim()).then(() => setResent(true), () => setResent(true))}>
            Email me the link again
          </button>
          {resent && <span role="status"> Sent - only the newest link works.</span>}
        </p>
      )}
      <button type="submit" className={`${shell.primary} ${styles.wide}`} disabled={busy}>
        <Icon name="login" size={18} /> {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

/** Create an account: the platform's own sign-up form. The emailed link opens on
 * this host and brings the buyer back to `returnPath` (a Store page) once verified. */
export function StoreSignUpForm({ returnPath, onSent }: { returnPath: string; onSent?: () => void }) {
  const origins = usePortals();
  if (storeSignsInThroughAccounts(origins)) return <AccountsSignUpButton returnPath={returnPath} />;
  return <StoreOwnSignUpForm returnPath={returnPath} onSent={onSent} />;
}

function StoreOwnSignUpForm({ returnPath, onSent }: { returnPath: string; onSent?: () => void }) {
  useEffect(() => {
    rememberStoreReturn(returnPath);
  }, [returnPath]);
  return <SignupForm signInHref={`/login?next=${encodeURIComponent(returnPath)}`} onSent={onSent} />;
}

/** ADR-073: create the account on WorkEasy360 Accounts; the confirmation link
 * brings the buyer back to `returnPath`, signed in to the Store and Accounts. */
function AccountsSignUpButton({ returnPath }: { returnPath: string }) {
  return (
    <div className={styles.authForm}>
      <p>Create your WorkEasy360 account. Once you confirm your email you come back to this checkout, with your plan still chosen.</p>
      <button
        type="button"
        className={`${shell.primary} ${styles.wide}`}
        onClick={() => storeNavigation.assign(`/signup?next=${encodeURIComponent(returnPath)}`)}
      >
        <Icon name="userPlus" size={18} /> Create an account on WorkEasy360 Accounts
      </button>
    </div>
  );
}

function SignInPage() {
  const router = useRouter();
  const params = useSearchParams();
  const account = useStoreAccount();
  const next = safeStorePath(params.get("next")) ?? STORE_HOME;
  useEffect(() => {
    if (account.status === "signed_in") router.replace(next);
  }, [account.status, next, router]);
  return (
    <div className={shell.container}>
      <div className={styles.authPage}>
        <section className={`${shell.card} ${styles.authCard}`} aria-labelledby="store-sign-in">
          <h1 id="store-sign-in" className={styles.authTitle}>
            Sign in to the Store
          </h1>
          <p className={styles.authLead}>Use your WorkEasy360 account - the same one you use for User Admin and every product.</p>
          <StoreSignInForm onSignedIn={() => router.replace(next)} />
          <p className={styles.authFoot}>
            New to WorkEasy360? <Link href={`/signup?next=${encodeURIComponent(next)}`}>Create an account</Link>
          </p>
        </section>
      </div>
    </div>
  );
}

function SignUpPage() {
  const params = useSearchParams();
  const next = safeStorePath(params.get("next")) ?? STORE_HOME;
  const [sent, setSent] = useState(false);
  // A partner's referral link may arrive at sign-up (`?ref=`): the same capture the
  // customer portal's sign-up makes, on this host.
  useEffect(() => {
    const code = params.get("ref");
    if (!code || !/^[A-Za-z0-9_-]{3,64}$/.test(code)) return;
    publicApi.captureReferral(code).then(
      ({ token }) => {
        try {
          sessionStorage.setItem(REFERRAL_TOKEN, token);
        } catch {
          // Private mode: the referral rides on its cookie alone.
        }
      },
      () => undefined,
    );
  }, [params]);
  return (
    <div className={shell.container}>
      <div className={styles.authPage}>
        <section className={`${shell.card} ${styles.authCard}`} aria-labelledby="store-sign-up">
          <h1 id="store-sign-up" className={styles.authTitle}>
            {sent ? "Check your email" : "Create your WorkEasy360 account"}
          </h1>
          {!sent && <p className={styles.authLead}>One account for the Store, User Admin and every WorkEasy360 product. Your organization is set up when you confirm your email.</p>}
          <StoreSignUpForm returnPath={next} onSent={() => setSent(true)} />
        </section>
      </div>
    </div>
  );
}

export function StoreSignInScreen() {
  return (
    <StoreShell>
      <Suspense fallback={null}>
        <SignInPage />
      </Suspense>
    </StoreShell>
  );
}

export function StoreSignUpScreen() {
  return (
    <StoreShell>
      <Suspense fallback={null}>
        <SignUpPage />
      </Suspense>
    </StoreShell>
  );
}
