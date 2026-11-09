"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Logo } from "@/components/brand/Logo";
import { Icon } from "@/components/sidebar/icons";
import { login } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { loginErrorMessage } from "./LoginForm";
import { destinationAfterSignIn } from "./signInMode";
import styles from "./AdminLogin.module.css";

const FEATURES = [
  { icon: "users", title: "Platform & User Management", text: "Customers, organizations, partners & roles" },
  { icon: "box", title: "Product & Plan Management", text: "Products, plans, add-ons & pricing" },
  { icon: "receipt", title: "Orders, Billing & Payments", text: "Orders, invoices, subscriptions & renewals" },
  { icon: "chart", title: "Credits & Usage Control", text: "AI, messaging & API credits with reports" },
];

const ECOSYSTEM = [
  { icon: "box", label: "All Products" },
  { icon: "handshake", label: "Partners" },
  { icon: "users", label: "Customers" },
  { icon: "card", label: "Subscriptions" },
  { icon: "chart", label: "Reports" },
];

/** An abstract console on a laptop. Decoration only: it carries no figures, so
 * nothing on the sign-in page can be read as a real number. */
function LaptopArt() {
  const bars = [46, 62, 54, 78, 70, 92, 84];
  return (
    <svg className={styles.laptop} viewBox="0 0 520 330" aria-hidden="true" focusable="false">
      <rect className={styles.artFrame} x="58" y="8" width="404" height="262" rx="14" />
      <rect className={styles.artScreen} x="70" y="20" width="380" height="238" rx="6" />
      <rect className={styles.artSide} x="70" y="20" width="84" height="238" rx="6" />
      {[44, 62, 80, 98, 116, 134].map((y, i) => (
        <rect key={y} className={i === 0 ? styles.artSideActive : styles.artSideItem} x="80" y={y} width="64" height="10" rx="3" />
      ))}
      {[0, 1, 2, 3].map((i) => (
        <rect key={i} className={styles.artCard} x={166 + i * 70} y="32" width="62" height="38" rx="5" />
      ))}
      <rect className={styles.artCard} x="166" y="80" width="170" height="104" rx="5" />
      {bars.map((h, i) => (
        <rect
          key={i}
          className={i % 2 ? styles.artBarSoft : styles.artBar}
          x={178 + i * 21}
          y={176 - h}
          width="14"
          height={h}
          rx="2"
        />
      ))}
      <rect className={styles.artCard} x="346" y="80" width="92" height="104" rx="5" />
      <circle className={styles.artRing} cx="392" cy="132" r="26" />
      <circle className={styles.artRingSoft} cx="392" cy="132" r="26" pathLength="100" strokeDasharray="38 100" />
      <rect className={styles.artCard} x="166" y="194" width="134" height="54" rx="5" />
      <rect className={styles.artCard} x="306" y="194" width="132" height="54" rx="5" />
      {[206, 218, 230].map((y) => (
        <g key={y}>
          <rect className={styles.artLine} x="176" y={y} width="112" height="5" rx="2" />
          <rect className={styles.artLine} x="316" y={y} width="112" height="5" rx="2" />
        </g>
      ))}
      <path className={styles.artBase} d="M18 276h484l-22 22a18 18 0 0 1-12 6H52a18 18 0 0 1-12-6l-22-22Z" />
      <rect className={styles.artFrame} x="216" y="276" width="88" height="8" rx="4" />
    </svg>
  );
}

function Hero() {
  return (
    <aside className={styles.hero}>
      <span className={styles.wave} aria-hidden="true" />
      <div className={styles.heroInner}>
        <Logo size="lg" onBand />
        <p className={styles.portalPill}>
          <Icon name="shield" size={18} /> Admin Portal
        </p>
        <p className={styles.heroTitle}>
          WorkEasy<span className={styles.heroAccent}>360</span>
          <br />
          Platform Control Center
        </p>
        <p className={styles.heroText}>
          Manage the entire platform, partners, customers, products, plans, credits and business operations from a single
          console.
        </p>
        <ul className={styles.features}>
          {FEATURES.map((f) => (
            <li key={f.title}>
              <span className={styles.featureIcon}>
                <Icon name={f.icon} size={24} />
              </span>
              <span>
                <strong>{f.title}</strong>
                <span className={styles.featureText}>{f.text}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
      <LaptopArt />
    </aside>
  );
}

/**
 * The Admin sign-in's frame (the owner's reference image): the platform's story
 * on the left, a card on the right. The forgotten-password and reset pages use
 * it too, so the Admin host never drops back to the public site's look.
 */
export function AdminAuthFrame({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  // Issues the CSRF cookie every form here needs (auth_views docstring).
  useSession();
  return (
    <main id="w360-main" className={styles.page}>
      <Hero />
      <section className={styles.side}>
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <Logo size="lg" />
            <h1 className={styles.title}>{title}</h1>
            {description && <p className={styles.description}>{description}</p>}
          </div>
          {children}
          {footer}
        </div>
      </section>
    </main>
  );
}

export function PortalBadge() {
  return (
    <div className={styles.portalCard}>
      <span className={styles.portalIcon}>
        <Icon name="shield" size={22} />
      </span>
      <span>
        <strong>Admin Portal</strong>
        <span className={styles.portalHint}>Full platform access and control</span>
      </span>
    </div>
  );
}

function Ecosystem() {
  return (
    <div className={styles.ecosystem}>
      <p className={styles.ecosystemTitle}>WorkEasy360 Ecosystem</p>
      <ul className={styles.ecosystemList}>
        {ECOSYSTEM.map((e) => (
          <li key={e.label}>
            <Icon name={e.icon} size={26} />
            <span>{e.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Validation the browser can do before asking the server. The server's answer
 * still decides everything; it just never hears an obviously empty form. */
function fieldProblem(email: string, password: string): string | null {
  if (!email.trim()) return "Enter your email address.";
  if (!/^[^\s@]+@[^\s@]+$/.test(email.trim())) return "Enter a valid email address.";
  if (!password) return "Enter your password.";
  return null;
}

export function AdminLoginScreen() {
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
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const problem = fieldProblem(email, password);
    setError(problem);
    if (problem) return;
    setBusy(true);
    try {
      await login(email.trim(), password, { remember });
      router.replace(destinationAfterSignIn("admin", searchParams.get("next")));
    } catch (err) {
      setError(loginErrorMessage(err));
      setBusy(false);
    }
  }

  const described = error ? errorId : undefined;
  return (
    <AdminAuthFrame
      title="Admin Login"
      description="Access the platform control center to manage WorkEasy360 operations."
      footer={<Ecosystem />}
    >
      <PortalBadge />
      <form method="post" onSubmit={submit} noValidate className={styles.form}>
        <div>
          <label className={styles.label} htmlFor={emailId}>
            Email Address
          </label>
          <span className={styles.inputWrap}>
            <Icon name="mail" size={18} />
            <input
              id={emailId}
              className={styles.input}
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
          <label className={styles.label} htmlFor={passwordId}>
            Password
          </label>
          <span className={styles.inputWrap}>
            <Icon name="lock" size={18} />
            <input
              id={passwordId}
              className={styles.input}
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
              className={styles.reveal}
              aria-pressed={show}
              aria-label={show ? "Hide password" : "Show password"}
              onClick={() => setShow((v) => !v)}
            >
              <Icon name={show ? "eye" : "eyeOff"} size={18} />
            </button>
          </span>
        </div>
        <div className={styles.row}>
          <label className={styles.check}>
            <input type="checkbox" name="remember" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Remember Me
          </label>
          <Link href="/forgot-password?mode=admin" className={styles.forgot}>
            Forgot Password?
          </Link>
        </div>
        {error && (
          <p id={errorId} className={styles.error} role="alert">
            {error}
          </p>
        )}
        <button type="submit" className={styles.submit} disabled={busy}>
          <Icon name="login" size={20} />
          {busy ? "Signing in…" : "Sign In to Admin"}
        </button>
      </form>
    </AdminAuthFrame>
  );
}
