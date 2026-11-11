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
import { destinationAfterSignIn } from "./signInMode";
import adminStyles from "./AdminLogin.module.css";
import styles from "./PartnerLogin.module.css";

/**
 * The Partner portal's sign-in (2026-11-11, the owner's reference image): the partner
 * programme's story on the left, the sign-in card on the right - the Admin sign-in's
 * frame with the partner's words. The request is the same email and password as every
 * other sign-in; what the person may do once inside is the server's decision.
 */

export const PARTNER_SIGN_IN = "/login?mode=partner";
export const PARTNER_SIGN_UP = "/partner/apply";

const FEATURES = [
  { icon: "megaphone", title: "Promote Products", text: "Share links, campaigns and marketing assets" },
  { icon: "layers", title: "Resell & Activate", text: "Assign products and plans to your customers" },
  { icon: "users", title: "Manage Customers", text: "Track renewals, subscriptions and support" },
  { icon: "coins", title: "Earn Commissions", text: "Monitor earnings, payouts and growth" },
];

/** The product family named in the owner's reference image. Words only: no figure,
 * price or customer appears anywhere on this page. */
const ECOSYSTEM = [
  { icon: "chat", name: "OneClickAPI", kind: "WhatsApp", tone: "green" },
  { icon: "layers", name: "SailoAI", kind: "CRM", tone: "purple" },
  { icon: "headset", name: "VyniAI", kind: "Calling", tone: "blue" },
  { icon: "message", name: "RCS / SMS", kind: "Messaging", tone: "pink" },
  { icon: "browser", name: "Website", kind: "Builder", tone: "light" },
  { icon: "apps", name: "and More", kind: "Products", tone: "green" },
];

const BENEFITS = [
  { icon: "megaphone", label: "Promote Products" },
  { icon: "layers", label: "Resell & Activate" },
  { icon: "coins", label: "Earn Commission" },
];

function Ecosystem() {
  return (
    <div className={styles.ecosystem}>
      <p className={styles.ecosystemTitle}>Our Product Ecosystem</p>
      <ul className={styles.ecosystemList}>
        {ECOSYSTEM.map((e) => (
          <li key={e.name} className={styles.product}>
            <span className={styles.productIcon} data-tone={e.tone}>
              <Icon name={e.icon} size={26} />
            </span>
            <strong>{e.name}</strong>
            <span>{e.kind}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PartnerHero() {
  return (
    <aside className={`${adminStyles.hero} ${styles.hero}`}>
      <span className={adminStyles.wave} aria-hidden="true" />
      <div className={adminStyles.heroInner}>
        <Logo size="lg" onBand />
        <p className={adminStyles.portalPill}>
          <Icon name="handshake" size={18} /> Partner Portal
        </p>
        <p className={adminStyles.heroTitle}>
          Grow Your Business
          <br />
          with <span className={adminStyles.heroAccent}>WorkEasy360</span>
        </p>
        <p className={adminStyles.heroText}>
          Promote, resell, manage customers and earn recurring income with the WorkEasy360 partner platform.
        </p>
      </div>
      <div className={styles.showcase}>
        <ul className={`${adminStyles.features} ${styles.features}`}>
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
        <LaptopArt className={styles.laptop} />
      </div>
      <Ecosystem />
    </aside>
  );
}

/** The Partner portal's frame: its sign-in, sign-up and password pages. */
export function PartnerAuthFrame(props: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return <AuthSplitFrame hero={<PartnerHero />} {...props} />;
}

export function PartnerPortalBadge() {
  return (
    <div className={adminStyles.portalCard}>
      <span className={adminStyles.portalIcon}>
        <Icon name="monitor" size={24} />
      </span>
      <span>
        <strong>Partner Portal</strong>
        <span className={adminStyles.portalHint}>For affiliates, resellers and white-label partners</span>
      </span>
    </div>
  );
}

function Benefits() {
  return (
    <section className={styles.benefits} aria-labelledby="w360-partner-benefits">
      <h2 id="w360-partner-benefits" className={styles.divider}>
        <span>Partner Benefits</span>
      </h2>
      <ul className={styles.benefitList}>
        {BENEFITS.map((b) => (
          <li key={b.label}>
            <Icon name={b.icon} size={30} />
            <span>{b.label}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function PartnerLoginScreen() {
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
      router.replace(destinationAfterSignIn("partner", searchParams.get("next")));
    } catch (err) {
      setError(loginErrorMessage(err));
      setUnverified(err instanceof ApiError && err.code === "email_not_verified");
      setBusy(false);
    }
  }

  const described = error ? errorId : undefined;
  return (
    <PartnerAuthFrame
      title="Partner Login"
      description="Access your partner dashboard to promote, resell, manage customers and track earnings."
      footer={<Benefits />}
    >
      <PartnerPortalBadge />
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
          <Link href="/forgot-password?mode=partner" className={adminStyles.forgot}>
            Forgot Password?
          </Link>
        </div>
        {error && (
          <p id={errorId} className={adminStyles.error} role="alert">
            {error}
          </p>
        )}
        {unverified && (
          <p className={styles.resend}>
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
          {busy ? "Signing in…" : "Login to Partner Portal"}
        </button>
      </form>
      <p className={styles.divider}>
        <span>New to WorkEasy360?</span>
      </p>
      <Link href={PARTNER_SIGN_UP} className={styles.outline}>
        <Icon name="userPlus" size={22} />
        Create Partner Account
      </Link>
      {/* 2026-11-10: a read-only preview with example data. A guest is never a
          partner: no account, no session, no partner rights. */}
      <p className={styles.guest}>
        <Link href="/partner/preview">Continue as Guest</Link> - a read-only preview. Not a partner account.
      </p>
    </PartnerAuthFrame>
  );
}
