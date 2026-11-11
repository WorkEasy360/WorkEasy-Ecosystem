"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Logo } from "@/components/brand/Logo";
import { Icon } from "@/components/sidebar/icons";
import { login } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { publicApi } from "@/lib/api/public";
import { AuthSplitFrame, LaptopArt, fieldProblem } from "./AdminLogin";
import { loginErrorMessage } from "./LoginForm";
import { ProductEcosystem } from "./PartnerLogin";
import { destinationAfterSignIn } from "./signInMode";
import adminStyles from "./AdminLogin.module.css";
import partnerStyles from "./PartnerLogin.module.css";
import styles from "./UserLogin.module.css";

/**
 * The customer portal's sign-in (2026-11-11, the owner's reference image "User
 * Admin Login"): the User Admin Panel's story on the left, the sign-in card on the
 * right - the Admin and Partner sign-ins' frame with the customer's words. The
 * request is the same email and password as every other sign-in; which
 * organization the person may open is the server's decision once they are in.
 *
 * A product sign-in (`?resume=`) and a partner's branded sign-in (`?brand=`) never
 * come here: they keep the plain sign-in page (LoginScreen.tsx).
 */

export const USER_SIGN_UP = "/signup";

const FEATURES = [
  { icon: "gauge", title: "Business Overview", text: "Customers, subscriptions and key metrics" },
  { icon: "layers", title: "Products & Subscriptions", text: "Manage plans, renewals and usage" },
  { icon: "wallet", title: "Wallets & Credits", text: "Platform wallet, AI credits and usage" },
  { icon: "users", title: "Organization & Team", text: "Company profile, members and access" },
];

/** The card's foot, as in the reference image. Labels only: nothing here is a link
 * or a number. */
const PLATFORM = [
  { icon: "box", label: "All Products" },
  { icon: "users", label: "Customers" },
  { icon: "layers", label: "Subscriptions" },
  { icon: "wallet", label: "Wallets & Credits" },
  { icon: "chart", label: "Reports" },
];

function Platform() {
  return (
    <section className={partnerStyles.benefits} aria-labelledby="w360-user-platform">
      <h2 id="w360-user-platform" className={partnerStyles.divider}>
        <span>WorkEasy360 Platform</span>
      </h2>
      <ul className={`${partnerStyles.benefitList} ${styles.platformList}`}>
        {PLATFORM.map((p) => (
          <li key={p.label}>
            <Icon name={p.icon} size={28} />
            <span>{p.label}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function UserHero() {
  return (
    <aside className={`${adminStyles.hero} ${partnerStyles.hero}`}>
      <span className={adminStyles.wave} aria-hidden="true" />
      <div className={adminStyles.heroInner}>
        <Logo size="lg" onBand />
        <p className={adminStyles.portalPill}>
          <Icon name="monitor" size={18} /> User Admin Panel
        </p>
        <p className={adminStyles.heroTitle}>
          Manage Your Business
          <br />
          with <span className={adminStyles.heroAccent}>WorkEasy360</span>
        </p>
        <p className={adminStyles.heroText}>
          A unified control center to manage customers, products, subscriptions, billing, wallets and team access.
        </p>
      </div>
      <div className={partnerStyles.showcase}>
        <ul className={`${adminStyles.features} ${partnerStyles.features}`}>
          {FEATURES.map((f) => (
            <li key={f.title}>
              <span className={adminStyles.featureIcon}>
                <Icon name={f.icon} size={24} />
              </span>
              <span>
                <strong>{f.title}</strong>
                <span className={adminStyles.featureText}>{f.text}</span>
              </span>
            </li>
          ))}
        </ul>
        <LaptopArt className={partnerStyles.laptop} />
      </div>
      <ProductEcosystem />
    </aside>
  );
}

/** The customer portal's frame: its sign-in and password pages. */
export function UserAuthFrame(props: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return <AuthSplitFrame hero={<UserHero />} {...props} />;
}

export function UserPortalBadge() {
  return (
    <div className={adminStyles.portalCard}>
      <span className={adminStyles.portalIcon}>
        <Icon name="monitor" size={24} />
      </span>
      <span>
        <strong>User Admin Panel</strong>
        <span className={adminStyles.portalHint}>For customers and authorized team members</span>
      </span>
    </div>
  );
}

export function UserLoginScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
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
    setResent(false);
    if (problem) return;
    setBusy(true);
    try {
      await login(email.trim(), password, { remember });
      router.replace(destinationAfterSignIn("user", searchParams.get("next")));
    } catch (err) {
      setError(loginErrorMessage(err));
      setUnverified(err instanceof ApiError && err.code === "email_not_verified");
      setBusy(false);
    }
  }

  const described = error ? errorId : undefined;
  return (
    <UserAuthFrame
      title="User Admin Login"
      description="Access your organization's control center to manage WorkEasy360 products and operations."
      footer={<Platform />}
    >
      <UserPortalBadge />
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
              placeholder="Enter your email"
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
          <Link href="/forgot-password?mode=user" className={adminStyles.forgot}>
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
          <Icon name="login" size={20} />
          {busy ? "Signing in…" : "Login to User Admin Panel"}
        </button>
      </form>
      <p className={partnerStyles.divider}>
        <span>New to WorkEasy360?</span>
      </p>
      <Link href={USER_SIGN_UP} className={partnerStyles.outline}>
        <Icon name="userPlus" size={22} />
        Create Organization Account
      </Link>
    </UserAuthFrame>
  );
}
