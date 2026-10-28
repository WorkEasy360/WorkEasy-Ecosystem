"use client";

import { useEffect, useId, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { publicApi } from "@/lib/api/public";
import { useSession } from "@/lib/auth/useSession";
import { takeFragmentToken } from "@/lib/auth/fragmentToken";
import { rememberIntent, useSiteSettings } from "@/features/site/SiteShell";
import styles from "@/features/site/Site.module.css";
import { usePortalRouter } from "@/lib/portalRouter";

/**
 * Self-service accounts (Phase 9): sign up, confirm the address, recover a
 * forgotten password. Every answer the server gives here is deliberately the
 * same for registered and unregistered addresses, so these screens never say
 * whether an account exists either. Tokens from emailed links are read from
 * the URL *fragment* (never sent to a server by the browser) and posted in a
 * request body.
 */

export const REFERRAL_TOKEN = "w360.referral-token";

/** The token from `#token=...`: `null` while server-rendering, then the token
 * (or `""` when the link carried none), taken out of the address bar once. */
let taken: { path: string; token: string } | undefined;
function takeOnce(): string {
  // One reading per page: a second emailed link opened in the same tab (a
  // reset after a verification) must not reuse the first one's token.
  if (taken?.path !== window.location.pathname) {
    taken = { path: window.location.pathname, token: takeFragmentToken() ?? "" };
  }
  return taken.token;
}
const noSubscription = () => () => undefined;

function useFragmentToken(): string | null {
  return useSyncExternalStore(noSubscription, takeOnce, () => null);
}

function problemsOf(err: unknown): string[] {
  if (err instanceof ApiError) {
    const body = err.body as { detail?: string; problems?: string[] } | undefined;
    if (err.code === "password_policy_violation" && body?.problems?.length) return body.problems;
    if (err.status === 429) return ["Too many attempts. Wait a minute, then try again."];
    if (body?.detail) return [body.detail];
  }
  return ["Something went wrong. Please try again."];
}

function Problems({ id, problems }: { id: string; problems: string[] }) {
  if (problems.length === 0) return null;
  return (
    <div id={id} role="alert" className="w360-error-text">
      {problems.length === 1 ? (
        <p>{problems[0]}</p>
      ) : (
        <ul>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AuthFrame({ title, children }: { title: string; children: React.ReactNode }) {
  // Issues the CSRF cookie every form below needs (auth_views docstring).
  useSession();
  return (
    <main id="w360-main" className={styles.authPage}>
      <div className={styles.authColumn}>
        <div className={styles.authHeading}>
          <Link href="/" className={styles.logo} aria-label="Workeasy360 home">
            <span className={styles.logoMark} aria-hidden="true">
              W
            </span>
            Workeasy360
          </Link>
          <h1 className={styles.authTitle}>{title}</h1>
        </div>
        <div className={`w360-card ${styles.authCard}`}>{children}</div>
      </div>
    </main>
  );
}

function Field({
  label,
  hint,
  ...input
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  const id = useId();
  const hintId = useId();
  return (
    <div className={styles.field}>
      <label className="w360-label" htmlFor={id}>
        {label}
      </label>
      <input id={id} className="w360-input" aria-describedby={hint ? hintId : undefined} {...input} />
      {hint && (
        <p id={hintId} className="w360-hint">
          {hint}
        </p>
      )}
    </div>
  );
}

// --- sign up -------------------------------------------------------------------------------

export function SignupScreen() {
  const params = useSearchParams();
  const site = useSiteSettings();
  const errorId = useId();
  const [form, setForm] = useState({ first_name: "", last_name: "", email: "", password: "", company_name: "" });
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  // A partner's referral link (`/signup?ref=CODE`): turned into an opaque
  // token now, attributed once the organization exists. Best effort.
  useEffect(() => {
    const code = params.get("ref");
    if (!code || !/^[A-Za-z0-9_-]{3,64}$/.test(code)) return;
    publicApi.captureReferral(code).then(
      ({ token }) => {
        try {
          sessionStorage.setItem(REFERRAL_TOKEN, token);
        } catch {
          // Private mode: the referral is simply not attributed.
        }
      },
      () => undefined,
    );
  }, [params]);

  // A plan chosen on the public site before signing up (`?plan=`), kept
  // for the checkout that opens after verification.
  useEffect(() => {
    const plan = params.get("plan");
    if (plan) rememberIntent({ plan, interval: params.get("interval") ?? undefined });
  }, [params]);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setProblems([]);
    try {
      await publicApi.signup({ ...form, email: form.email.trim(), company_name: form.company_name.trim() || undefined });
      setSentTo(form.email.trim());
    } catch (err) {
      setProblems(err instanceof ApiError && err.code === "signup_closed"
        ? ["Signing up is closed at the moment. Please try again later."]
        : problemsOf(err));
    } finally {
      setBusy(false);
    }
  }

  if (sentTo) {
    return (
      <AuthFrame title="Check your email">
        <CheckYourEmail email={sentTo} />
      </AuthFrame>
    );
  }

  return (
    <AuthFrame title="Create your account">
      {site && !site.signup_enabled ? (
        <p role="status">Signing up is closed at the moment. Please try again later.</p>
      ) : (
        <form onSubmit={submit} noValidate aria-describedby={problems.length ? errorId : undefined}>
          <div className={styles.formRow}>
            <Field label="First name" autoComplete="given-name" required maxLength={100} value={form.first_name} onChange={set("first_name")} />
            <Field label="Last name" autoComplete="family-name" required maxLength={100} value={form.last_name} onChange={set("last_name")} />
          </div>
          <Field label="Work email" type="email" autoComplete="email" required maxLength={254} value={form.email} onChange={set("email")} />
          <Field
            label="Password"
            type="password"
            autoComplete="new-password"
            required
            value={form.password}
            onChange={set("password")}
            hint="At least 12 characters. A few unrelated words make a strong one."
          />
          <Field
            label="Company name (optional)"
            autoComplete="organization"
            maxLength={150}
            value={form.company_name}
            onChange={set("company_name")}
            hint="We create your organization with this name once you confirm your email."
          />
          <Problems id={errorId} problems={problems} />
          <button type="submit" className={`w360-btn w360-btn-primary ${styles.fullWidth}`} disabled={busy}>
            {busy ? "Creating your account…" : "Create account"}
          </button>
        </form>
      )}
      <p className={styles.below}>
        Already have an account? <Link href="/login">Sign in</Link>
      </p>
    </AuthFrame>
  );
}

function CheckYourEmail({ email }: { email: string }) {
  const [state, setState] = useState<"idle" | "busy" | "sent" | "error">("idle");
  return (
    <>
      <p role="status">
        If <strong>{email}</strong> can receive it, a link to confirm your address is on its way. Open it on this
        device and enter your password to finish.
      </p>
      <p className="w360-hint">The link expires in 24 hours. Check your spam folder if it hasn&apos;t arrived.</p>
      <button
        type="button"
        className="w360-btn w360-btn-secondary"
        disabled={state === "busy"}
        onClick={() => {
          setState("busy");
          publicApi.resendVerification(email).then(
            () => setState("sent"),
            () => setState("error"),
          );
        }}
      >
        Send the link again
      </button>
      {state === "sent" && <p role="status">Sent again - only the newest link works.</p>}
      {state === "error" && (
        <p role="alert" className="w360-error-text">
          That didn&apos;t work. Wait a minute and try again.
        </p>
      )}
    </>
  );
}

// --- verify ---------------------------------------------------------------------------------

/** Where a finished verification or sign-in goes. Only internal paths. */
async function finishSignIn(router: ReturnType<typeof usePortalRouter>, organizationId: string | null) {
  let referral: string | null = null;
  try {
    referral = sessionStorage.getItem(REFERRAL_TOKEN);
    sessionStorage.removeItem(REFERRAL_TOKEN);
  } catch {
    referral = null;
  }
  // Asked even with no token in this tab: the verification link usually opens
  // in a new tab, and the server then uses the referral cookie (ADR-052).
  if (organizationId) {
    await publicApi.convertReferral(referral ?? "", organizationId).catch(() => undefined);
  }
  router.replace("/dashboard");
}

export function VerifyEmailScreen() {
  const router = usePortalRouter();
  const token = useFragmentToken();
  const errorId = useId();
  const [password, setPassword] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  if (token === null) {
    return (
      <AuthFrame title="Confirm your email">
        <p aria-live="polite">Loading…</p>
      </AuthFrame>
    );
  }
  if (token === "") {
    return (
      <AuthFrame title="Confirm your email">
        <p role="alert">This link is not valid. Open the newest email we sent you, or sign up again.</p>
        <p className={styles.below}>
          <Link href="/signup">Sign up</Link> · <Link href="/login">Sign in</Link>
        </p>
      </AuthFrame>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setProblems([]);
    try {
      const result = await publicApi.verifyEmail(token ?? "", password);
      await finishSignIn(router, result.organization_id);
    } catch (err) {
      if (err instanceof ApiError && err.code === "link_invalid") {
        setProblems(["This link is not valid or has expired. Sign in to have a new one sent."]);
      } else if (err instanceof ApiError && err.code === "invalid_credentials") {
        setProblems(["That is not the password you chose when you signed up."]);
      } else {
        setProblems(problemsOf(err));
      }
      setBusy(false);
    }
  }

  return (
    <AuthFrame title="Confirm your email">
      <form onSubmit={submit} noValidate aria-describedby={problems.length ? errorId : undefined}>
        <p>Enter the password you chose when you signed up to confirm your address and sign in.</p>
        <Field label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        <Problems id={errorId} problems={problems} />
        <button type="submit" className={`w360-btn w360-btn-primary ${styles.fullWidth}`} disabled={busy || !password}>
          {busy ? "Confirming…" : "Confirm and sign in"}
        </button>
      </form>
      <p className={styles.below}>
        Didn&apos;t sign up? You can ignore the email. <Link href="/forgot-password">Forgotten the password?</Link>
      </p>
    </AuthFrame>
  );
}

// --- forgot / reset ---------------------------------------------------------------------------

export function ForgotPasswordScreen() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "sent">("idle");
  const [problems, setProblems] = useState<string[]>([]);
  const errorId = useId();

  if (state === "sent") {
    return (
      <AuthFrame title="Check your email">
        <p role="status">
          If an account uses <strong>{email}</strong>, a link to reset its password is on its way. It works once, for 60
          minutes.
        </p>
        <p className={styles.below}>
          <Link href="/login">Back to sign in</Link>
        </p>
      </AuthFrame>
    );
  }

  return (
    <AuthFrame title="Reset your password">
      <form
        noValidate
        aria-describedby={problems.length ? errorId : undefined}
        onSubmit={(e) => {
          e.preventDefault();
          setState("busy");
          setProblems([]);
          publicApi.forgotPassword(email.trim()).then(
            () => setState("sent"),
            (err) => {
              setProblems(problemsOf(err));
              setState("idle");
            },
          );
        }}
      >
        <Field label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Problems id={errorId} problems={problems} />
        <button type="submit" className={`w360-btn w360-btn-primary ${styles.fullWidth}`} disabled={state === "busy" || !email.trim()}>
          Email me a reset link
        </button>
      </form>
      <p className={styles.below}>
        <Link href="/login">Back to sign in</Link>
      </p>
    </AuthFrame>
  );
}

export function ResetPasswordScreen() {
  const token = useFragmentToken();
  const errorId = useId();
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");

  if (token === null) {
    return (
      <AuthFrame title="Choose a new password">
        <p aria-live="polite">Loading…</p>
      </AuthFrame>
    );
  }
  if (state === "done") {
    return (
      <AuthFrame title="Password changed">
        <p role="status">Your password was changed and every session was signed out.</p>
        <Link href="/login" className={`w360-btn w360-btn-primary ${styles.fullWidth}`}>
          Sign in
        </Link>
      </AuthFrame>
    );
  }
  if (token === "") {
    return (
      <AuthFrame title="Choose a new password">
        <p role="alert">This link is not valid. Ask for a new one.</p>
        <p className={styles.below}>
          <Link href="/forgot-password">Reset your password</Link>
        </p>
      </AuthFrame>
    );
  }

  return (
    <AuthFrame title="Choose a new password">
      <form
        noValidate
        aria-describedby={problems.length ? errorId : undefined}
        onSubmit={(e) => {
          e.preventDefault();
          if (password !== again) {
            setProblems(["The two passwords don't match."]);
            return;
          }
          setState("busy");
          setProblems([]);
          publicApi.resetPassword(token, password).then(
            () => setState("done"),
            (err) => {
              setProblems(
                err instanceof ApiError && err.code === "link_invalid"
                  ? ["This link is not valid or has expired. Ask for a new one."]
                  : problemsOf(err),
              );
              setState("idle");
            },
          );
        }}
      >
        <Field label="New password" type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} hint="At least 12 characters." />
        <Field label="Repeat the new password" type="password" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)} />
        <Problems id={errorId} problems={problems} />
        <button type="submit" className={`w360-btn w360-btn-primary ${styles.fullWidth}`} disabled={state === "busy" || !password}>
          Change password
        </button>
      </form>
    </AuthFrame>
  );
}
