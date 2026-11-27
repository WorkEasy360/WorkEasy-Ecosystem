"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Logo } from "@/components/brand/Logo";
import { Icon } from "@/components/sidebar/icons";
import { login } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { publicApi } from "@/lib/api/public";
import { continueFromAccounts } from "./accountsSignIn";
import { AuthSplitFrame, LaptopArt, fieldProblem } from "./AdminLogin";
import { loginErrorMessage } from "./LoginForm";
import { TwoStepSignIn, twoStepChallenge } from "./TwoStepSignIn";
import adminStyles from "./AdminLogin.module.css";
import partnerStyles from "./PartnerLogin.module.css";
import styles from "./AccountsLogin.module.css";

/**
 * WorkEasy360 Accounts' own sign-in (2026-11-27, the owner's reference image
 * "Account Login"): the personal account's story on the left - its four menus -
 * and the sign-in card on the right, with Log In / Sign Up, Remember me and
 * Forgot Password.
 *
 * Behaviour is the central sign-in's, unchanged: one email and password for
 * every product; a product sign-in (`?resume=`) continues to the product through
 * the server's resume endpoint, which then runs the account chooser and the
 * access checks; otherwise Accounts hands the sign-in on (ADR-071). A partner's
 * branded sign-in (`?brand=`) never comes here - it keeps the plain page, without
 * the WorkEasy360 story.
 */

/** The four menus of the personal account (Accounts V3, the note's section 04) -
 * labels only, nothing here is a link. */
const MENUS = [
  { icon: "user", title: "My Profile", text: "Update personal details." },
  { icon: "shield", title: "Login & Security", text: "Password and verification." },
  { icon: "monitor", title: "Devices & Sessions", text: "Manage signed-in devices." },
  { icon: "settings", title: "Settings", text: "Notifications and account preferences." },
];

const PROMISES = [
  { icon: "user", label: "One Account" },
  { icon: "shield", label: "Secure Access" },
  { icon: "apps", label: "All Products" },
];

export const ACCOUNTS_SIGN_UP = "/signup";

function AccountsPill({ onBand = false }: { onBand?: boolean }) {
  return (
    <span className={onBand ? styles.pillOnBand : styles.pill}>
      <Icon name="user" size={18} /> Accounts
    </span>
  );
}

function AccountsHero() {
  return (
    <aside className={`${adminStyles.hero} ${partnerStyles.hero}`}>
      <span className={adminStyles.wave} aria-hidden="true" />
      <div className={adminStyles.heroInner}>
        <div className={styles.heroBrand}>
          <Logo size="lg" onBand />
          <span className={styles.heroRule} aria-hidden="true" />
          <AccountsPill onBand />
        </div>
        <p className={styles.heroKicker}>Personal User Account</p>
        <p className={adminStyles.heroTitle}>
          One Account.
          <br />
          All <span className={adminStyles.heroAccent}>WorkEasy360</span> Products.
        </p>
        <span className={styles.heroUnderline} aria-hidden="true" />
        <p className={adminStyles.heroText}>Manage your personal profile and security across the WorkEasy360 ecosystem.</p>
      </div>
      <div className={partnerStyles.showcase}>
        <ul className={`${adminStyles.features} ${partnerStyles.features}`}>
          {MENUS.map((m) => (
            <li key={m.title}>
              <span className={`${adminStyles.featureIcon} ${styles.menuIcon}`}>
                <Icon name={m.icon} size={24} />
              </span>
              <span>
                <strong>{m.title}</strong>
                <span className={adminStyles.featureText}>{m.text}</span>
              </span>
            </li>
          ))}
        </ul>
        <LaptopArt className={partnerStyles.laptop} />
      </div>
    </aside>
  );
}

function Promises({ accountsHost }: { accountsHost?: string }) {
  return (
    <>
      <ul className={styles.promises} aria-label="WorkEasy360 Accounts">
        {PROMISES.map((p) => (
          <li key={p.label}>
            <Icon name={p.icon} size={30} />
            <span>{p.label}</span>
          </li>
        ))}
      </ul>
      {accountsHost && (
        <p className={styles.host}>
          <Icon name="globe" size={20} />
          <span>{accountsHost}</span>
        </p>
      )}
    </>
  );
}

/** Log In | Sign Up: two pages, so two links - the current one marked. The current
 * page is not a link, so nothing here can drop `?resume=`. */
export function ModeSwitch({ current }: { current: "login" | "signup" }) {
  return (
    <nav aria-label="Log in or sign up" className={styles.modes}>
      {current === "login" ? (
        <span aria-current="page" className={styles.modeCurrent}>
          Log In
        </span>
      ) : (
        <Link href="/login" className={styles.mode}>
          Log In
        </Link>
      )}
      {current === "signup" ? (
        <span aria-current="page" className={styles.modeCurrent}>
          Sign Up
        </span>
      ) : (
        <Link href={ACCOUNTS_SIGN_UP} className={styles.mode}>
          Sign Up
        </Link>
      )}
    </nav>
  );
}

/** WorkEasy360 Accounts' frame for its other identity pages (sign-up), so Log In and
 * Sign Up look like two tabs of one card. */
export function AccountsAuthFrame({
  title,
  description,
  children,
  accountsHost,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
  accountsHost?: string;
}) {
  return (
    <AuthSplitFrame
      hero={<AccountsHero />}
      badge={<AccountsPill />}
      title={title}
      description={description}
      footer={<Promises accountsHost={accountsHost} />}
    >
      {children}
    </AuthSplitFrame>
  );
}

/** A product asked for the password again (`prompt=login`, ADR-044). */
function reauthenticating(params: URLSearchParams): boolean {
  return Boolean(params.get("resume")) && params.get("reauth") === "1";
}

export function AccountsLoginScreen({ accountsHost }: { accountsHost?: string }) {
  const searchParams = useSearchParams();
  const resume = searchParams.get("resume");
  const reauth = reauthenticating(searchParams);
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
  const [challenge, setChallenge] = useState<string | null>(null);

  /** What a successful sign-in does - after the password, or after its code. */
  function proceed() {
    if (resume) {
      // A full navigation: the server's resume endpoint needs the session cookie
      // just set, and runs the account chooser and the access checks next.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = `/api/v1/integrations/oauth/resume?resume=${encodeURIComponent(resume)}`;
      return;
    }
    // ADR-071: hand the sign-in to the portal that asked, or open the account.
    void continueFromAccounts(searchParams);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const problem = fieldProblem(email, password);
    setError(problem);
    setUnverified(false);
    setResent(false);
    if (problem) return;
    setBusy(true);
    try {
      await login(email.trim(), password, { remember });
      proceed();
    } catch (err) {
      const next = twoStepChallenge(err);
      if (next) {
        setPassword("");
        setChallenge(next);
      } else {
        setError(loginErrorMessage(err));
        setUnverified(err instanceof ApiError && err.code === "email_not_verified");
      }
      setBusy(false);
    }
  }

  const described = error ? errorId : undefined;
  return (
    <AuthSplitFrame
      hero={<AccountsHero />}
      badge={<AccountsPill />}
      title={reauth ? "Confirm it's you" : "Account Login"}
      description={
        reauth
          ? "The product you are opening asked WorkEasy360 to check it is really you. Enter your email and password again - or sign in as someone else."
          : "Sign in once to access your authorized WorkEasy360 products."
      }
      footer={<Promises accountsHost={accountsHost} />}
    >
      {!reauth && !challenge && <ModeSwitch current="login" />}
      {searchParams.get("switched") === "1" && (
        <p role="status" className={styles.note}>
          You&apos;re signed out. Sign in with the account you want to use.
        </p>
      )}
      {searchParams.get("verified") === "1" && (
        // ADR-075: the address is confirmed; with two-step on, the code is still asked for.
        <p role="status" className={styles.note}>
          Your email address is confirmed. Sign in with your password (and the code from your authenticator app, if two-step
          verification is on).
        </p>
      )}
      {searchParams.get("closed") === "1" && (
        <p role="status" className={styles.note}>
          Your account is closed.
        </p>
      )}
      {challenge ? (
        <TwoStepSignIn
          look="split"
          challenge={challenge}
          onSignedIn={proceed}
          onRestart={(message) => {
            setChallenge(null);
            setError(message);
          }}
        />
      ) : (
        <form method="post" onSubmit={submit} noValidate className={adminStyles.form}>
          <div>
            <label className={adminStyles.label} htmlFor={emailId}>
              Email Address
            </label>
            <span className={adminStyles.inputWrap}>
              <Icon name="mail" size={18} />
              <input
                id={emailId}
                className={adminStyles.input}
                type="email"
                name="email"
                autoComplete="username"
                placeholder="Enter your email address"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-describedby={described}
                aria-invalid={error ? true : undefined}
              />
            </span>
          </div>
          <div>
            <label className={adminStyles.label} htmlFor={passwordId}>
              Password
            </label>
            <span className={adminStyles.inputWrap}>
              <Icon name="lock" size={18} />
              <input
                id={passwordId}
                className={adminStyles.input}
                type={show ? "text" : "password"}
                name="password"
                autoComplete="current-password"
                placeholder="Enter your password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-describedby={described}
                aria-invalid={error ? true : undefined}
              />
              <button
                type="button"
                className={adminStyles.reveal}
                aria-pressed={show}
                aria-label={show ? "Hide password" : "Show password"}
                onClick={() => setShow((v) => !v)}
              >
                <Icon name={show ? "eye" : "eyeOff"} size={18} />
              </button>
            </span>
          </div>
          <div className={adminStyles.row}>
            <label className={adminStyles.check}>
              <input type="checkbox" name="remember" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              Remember me
            </label>
            <Link href="/forgot-password" className={adminStyles.forgot}>
              Forgot Password?
            </Link>
          </div>
          {error && (
            <p id={errorId} className={adminStyles.error} role="alert">
              {error}
            </p>
          )}
          {unverified && (
            <p className={partnerStyles.resend}>
              <button
                type="button"
                className="w360-btn w360-btn-secondary"
                onClick={() => publicApi.resendVerification(email.trim()).then(() => setResent(true), () => setResent(true))}
              >
                Email me the link again
              </button>
              {resent && <span role="status"> Sent - only the newest link works.</span>}
            </p>
          )}
          <button type="submit" className={adminStyles.submit} disabled={busy}>
            {busy ? "Signing in…" : reauth ? "Confirm and continue" : "Log In to WorkEasy360"}
          </button>
        </form>
      )}
      {!challenge && (
        <>
          <p className={partnerStyles.divider}>
            <span>OR</span>
          </p>
          <Link href={ACCOUNTS_SIGN_UP} className={partnerStyles.outline}>
            Create WorkEasy360 Account
          </Link>
        </>
      )}
    </AuthSplitFrame>
  );
}
