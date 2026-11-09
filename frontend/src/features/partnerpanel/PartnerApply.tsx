"use client";

import { useId, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { partnerApplicationApi } from "@/lib/api/adminPanel";
import { ApiError } from "@/lib/api/client";
import { AuthFrame } from "@/features/auth/SelfService";
import { countryOptions } from "@/features/controlplane/countries";
import { EXPERIENCES, type ExperienceChoice } from "@/features/controlplane/kycParts";
import siteStyles from "@/features/site/Site.module.css";
import styles from "./KycApplication.module.css";

/**
 * The public partner application (2026-11-09). Applying creates an inactive partner
 * account and grants nothing: rights arrive when a reviewer approves its KYC. The server
 * gives the same answer for every outcome, so nothing here can say whether an address is
 * already registered. Only what a person typed wrong in the form itself is refused.
 */

const MIN_PASSWORD = 12;
const SIGN_IN = "/login?mode=partner";

function problemsOf(err: unknown): string[] {
  if (err instanceof ApiError) {
    const body = err.body as { detail?: string; problems?: string[] } | undefined;
    if (err.status === 429) return ["Too many attempts. Wait a minute, then try again."];
    if (body?.problems?.length) return body.problems;
    if (body?.detail) return [body.detail];
  }
  return ["Something went wrong. Please try again."];
}

function Field({ label, hint, ...input }: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  const id = useId();
  const hintId = useId();
  return (
    <div className={siteStyles.field}>
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

// The names come from the runtime's own locale data, and Node's and Chrome's differ. Reading
// them only in the browser keeps the first render the same on both sides.
const neverChanges = () => () => {};
const NO_COUNTRIES: ReturnType<typeof countryOptions> = [];
const noCountries = () => NO_COUNTRIES;
let cachedCountries: ReturnType<typeof countryOptions> | null = null;
const browserCountries = () => (cachedCountries ??= countryOptions());

const EMPTY_FORM = { business_name: "", first_name: "", last_name: "", email: "", password: "", country: "", phone: "" };

export function PartnerApplyScreen() {
  const [sentTo, setSentTo] = useState<string | null>(null);
  return (
    // AuthFrame reads the session, which issues the CSRF cookie this form's POST needs.
    <AuthFrame title={sentTo ? "Check your email" : "Apply to become a partner"}>
      {sentTo ? <Sent email={sentTo} /> : <ApplyForm onSent={setSentTo} />}
    </AuthFrame>
  );
}

function Sent({ email }: { email: string }) {
  return (
    <>
      <p role="status">
        If <strong>{email}</strong> can receive email, a message with your next step is on its way.
      </p>
      <p className="w360-hint">
        After that, sign in on the partner portal and complete your KYC application. Applying does not grant any partner rights: those start only once your KYC is approved.
      </p>
      <p className={siteStyles.below}>
        <Link href={SIGN_IN}>Go to partner sign-in</Link>
      </p>
    </>
  );
}

function ApplyForm({ onSent }: { onSent: (email: string) => void }) {
  const errorId = useId();
  const countryId = useId();
  const [form, setForm] = useState(EMPTY_FORM);
  const [interest, setInterest] = useState<ExperienceChoice | "">("");
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const countries = useSyncExternalStore(neverChanges, browserCountries, noCountries);

  const set = (key: keyof typeof EMPTY_FORM) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  function formProblems(): string[] {
    const found: string[] = [];
    if (!form.business_name.trim()) found.push("Enter your business name.");
    if (!form.first_name.trim()) found.push("Enter your first name.");
    if (!form.last_name.trim()) found.push("Enter your last name.");
    if (!/^[^\s@]+@[^\s@]+$/.test(form.email.trim())) found.push("Enter your email address.");
    if (form.password.length < MIN_PASSWORD) found.push(`Choose a password of at least ${MIN_PASSWORD} characters.`);
    if (!form.country) found.push("Choose your country.");
    if (!interest) found.push("Choose how you want to partner.");
    return found;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    const found = formProblems();
    setProblems(found);
    if (found.length > 0 || !interest) return;
    setBusy(true);
    try {
      const email = form.email.trim();
      await partnerApplicationApi.apply({
        business_name: form.business_name.trim(),
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        email,
        password: form.password,
        country: form.country,
        phone: form.phone.trim() || undefined,
        interest,
      });
      onSent(email);
    } catch (err) {
      setProblems(problemsOf(err));
      setBusy(false);
    }
  }

  return (
    <>
      <p className={styles.applyLead}>Applying does not grant any partner rights. You can start selling or promoting only once your KYC is approved.</p>
      <form method="post" onSubmit={(e) => void submit(e)} noValidate aria-describedby={problems.length ? errorId : undefined}>
        <Field label="Business name" autoComplete="organization" required maxLength={120} value={form.business_name} onChange={set("business_name")} />
        <div className={siteStyles.formRow}>
          <Field label="First name" autoComplete="given-name" required maxLength={100} value={form.first_name} onChange={set("first_name")} />
          <Field label="Last name" autoComplete="family-name" required maxLength={100} value={form.last_name} onChange={set("last_name")} />
        </div>
        <Field label="Email" type="email" autoComplete="email" required maxLength={254} value={form.email} onChange={set("email")} />
        <Field label="Password" type="password" autoComplete="new-password" required value={form.password} onChange={set("password")} hint="At least 12 characters. A few unrelated words make a strong one." />
        <div className={siteStyles.field}>
          <label className="w360-label" htmlFor={countryId}>
            Country
          </label>
          <select id={countryId} className="w360-input" required value={form.country} onChange={set("country")}>
            {countries.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <Field label="Phone (optional)" type="tel" autoComplete="tel" maxLength={32} value={form.phone} onChange={set("phone")} />
        <fieldset className={styles.choice}>
          <legend className="w360-label">How do you want to partner?</legend>
          {EXPERIENCES.map((option) => (
            <label key={option.value} className={styles.check}>
              <input type="radio" name="interest" value={option.value} checked={interest === option.value} onChange={() => setInterest(option.value)} />
              {option.label}
            </label>
          ))}
        </fieldset>
        {problems.length > 0 && (
          <div id={errorId} role="alert" className="w360-error-text">
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
        )}
        <button type="submit" className={`w360-btn w360-btn-primary ${siteStyles.fullWidth}`} disabled={busy}>
          {busy ? "Sending your application…" : "Send application"}
        </button>
      </form>
      <p className={siteStyles.below}>
        Already a partner? <Link href={SIGN_IN}>Sign in</Link>
      </p>
    </>
  );
}
