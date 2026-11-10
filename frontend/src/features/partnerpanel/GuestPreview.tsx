"use client";

import Link from "next/link";
import { Logo } from "@/components/brand/Logo";
import styles from "./GuestPreview.module.css";

/**
 * Partner portal → Continue as Guest (2026-11-10, the owner's brief).
 *
 * A **static preview** of what a partner portal offers, for someone who has no
 * account yet. It is deliberately not a session and not a tenant:
 *
 * - **No API call of any kind.** Nothing here reads or writes the platform, so a
 *   guest can reach no customer, partner, wallet, KYC document or payout, and no
 *   authorization rule can be bypassed: there is no request to authorize. The
 *   server's own rules (a draft partner holds no permission; every partner route
 *   checks membership) are untouched.
 * - **No real data.** Every figure below is labelled as an example and is a fixed
 *   illustration written into this page, never a record from the platform.
 * - **No rights.** A guest is never a partner, never approved and never active.
 *   The three steps are spelt out so nobody mistakes a preview for an account.
 *
 * "Exit guest preview" simply returns to the sign-in page: there is nothing to end.
 */

const STEPS = [
  {
    title: "Guest preview",
    text: "What you are looking at now. A limited, read-only tour with example figures. No account, no partner rights.",
  },
  {
    title: "Partner login",
    text: "A registered partner signs in with their own email and password, then sends a KYC application. Signing in alone activates nothing.",
  },
  {
    title: "Active partner",
    text: "Once the KYC application is approved and a Workeasy360 Admin activates the account, the partner's affiliate or reseller rights begin.",
  },
];

const FEATURES = [
  {
    title: "Referral links and activity",
    text: "Share product links, see visits and sign-ups they bring, and follow each conversion.",
    example: "Example: 42 visits, 6 sign-ups this month",
  },
  {
    title: "Commission earnings",
    text: "Every commission is an earnings record you can trace to the sale that earned it, cleared after the holding period.",
    example: "Example: ₹ 12,400 earned, ₹ 8,100 cleared",
  },
  {
    title: "Payouts",
    text: "Request a payout of cleared earnings and follow it from approval to payment.",
    example: "Example: 1 payout in review",
  },
  {
    title: "Customers (resellers)",
    text: "Resellers create and manage their customers' organizations and subscriptions.",
    example: "Example: 3 customers, 5 active subscriptions",
  },
  {
    title: "Products you may offer",
    text: "The products Workeasy360 lets you promote or sell, with your partner prices.",
    example: "Example: Easy CRM, EasyBook",
  },
  {
    title: "Support and notifications",
    text: "Raise tickets with the Workeasy360 team and see what needs your attention.",
    example: "Example: 1 open ticket",
  },
];

export function GuestPreviewScreen() {
  return (
    <main id="w360-main" className={styles.page}>
      <header className={styles.top}>
        <Link href="/" className={styles.logo} aria-label="Workeasy360 home">
          <Logo size="lg" />
        </Link>
        <Link href="/login" className="w360-btn w360-btn-secondary">
          Exit guest preview
        </Link>
      </header>

      <section className={styles.banner} role="note" aria-labelledby="guest-title">
        <p className={styles.badge}>Guest preview · example data only</p>
        <h1 id="guest-title" className={styles.title}>
          The Workeasy360 partner portal
        </h1>
        <p className={styles.lead}>
          A read-only tour for visitors. Nothing on this page is a real partner, customer, wallet or payout, and nothing here can be
          changed. To work as a partner you need a registered account, an approved KYC application and an Admin&apos;s activation.
        </p>
        <div className={styles.actions}>
          <Link href="/partner/apply" className="w360-btn w360-btn-primary">
            Apply to become a partner
          </Link>
          <Link href="/login" className="w360-btn w360-btn-secondary">
            Sign in as a partner
          </Link>
        </div>
      </section>

      <section aria-labelledby="guest-steps" className={styles.section}>
        <h2 id="guest-steps" className={styles.heading}>
          Guest, partner, active partner
        </h2>
        <ol className={styles.steps}>
          {STEPS.map((step, i) => (
            <li key={step.title} className={styles.step}>
              <span className={styles.stepNumber} aria-hidden="true">
                {i + 1}
              </span>
              <span>
                <strong>{step.title}</strong>
                <span className={styles.muted}>{step.text}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="guest-features" className={styles.section}>
        <h2 id="guest-features" className={styles.heading}>
          What an active partner works with
        </h2>
        <ul className={styles.grid}>
          {FEATURES.map((f) => (
            <li key={f.title} className={styles.card}>
              <h3 className={styles.cardTitle}>{f.title}</h3>
              <p className={styles.muted}>{f.text}</p>
              <p className={styles.example}>{f.example}</p>
            </li>
          ))}
        </ul>
        <p className={styles.muted}>
          Wallet balances, KYC documents, customer records and payouts are never shown to guests. They exist only inside a signed-in,
          approved partner account.
        </p>
      </section>
    </main>
  );
}
