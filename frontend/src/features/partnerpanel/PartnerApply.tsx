"use client";

import { useEffect, useId, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { partnerApplicationApi } from "@/lib/api/adminPanel";
import { ApiError } from "@/lib/api/client";
import { Icon } from "@/components/sidebar/icons";
import { PARTNER_SIGN_IN, PartnerAuthFrame, PartnerPortalBadge } from "@/features/auth/PartnerLogin";
import { countryOptions } from "@/features/controlplane/countries";
import { EXPERIENCES, type ExperienceChoice } from "@/features/controlplane/kycParts";
import frame from "@/features/auth/AdminLogin.module.css";
import styles from "@/features/auth/PartnerLogin.module.css";

/**
 * The public partner application (2026-11-09). Applying creates an inactive partner
 * account and grants nothing: rights arrive when a reviewer approves its KYC. The server
 * gives the same answer for every outcome, so nothing here can say whether an address is
 * already registered. Only what a person typed wrong in the form itself is refused.
 */

const MIN_PASSWORD = 12;
const SIGN_IN = PARTNER_SIGN_IN;

function problemsOf(err: unknown): string[] {
  if (err instanceof ApiError) {
    const body = err.body as { detail?: string; problems?: string[] } | undefined;
    if (err.status === 429) return ["Too many attempts. Wait a minute, then try again."];
    if (body?.problems?.length) return body.problems;
    if (body?.detail) return [body.detail];
  }
  return ["Something went wrong. Please try again."];
}

const FIELD_ICONS: Record<string, string> = {
  organization: "building",
  "given-name": "user",
  "family-name": "user",
  email: "mail",
  "new-password": "lock", // pragma: allowlist secret - an autocomplete token, not a password
  tel: "phone",
};

const INTEREST_ICONS: Record<ExperienceChoice, string> = {
  affiliate: "megaphone",
  reseller: "layers",
  white_label: "palette",
};

/** A labelled input in the partner sign-in's style: its icon, and for a password
 * a show / hide button. */
function Field({ label, hint, ...input }: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  const id = useId();
  const hintId = useId();
  const [show, setShow] = useState(false);
  const password = input.type === "password";
  const icon = FIELD_ICONS[input.autoComplete ?? ""];
  return (
    <div>
      <label className={frame.label} htmlFor={id}>
        {label}
      </label>
      <span className={frame.inputWrap}>
        {icon && <Icon name={icon} size={18} />}
        <input
          id={id}
          className={frame.input}
          aria-describedby={hint ? hintId : undefined}
          {...input}
          type={password && show ? "text" : input.type}
        />
        {password && (
          <button
            type="button"
            className={frame.reveal}
            aria-pressed={show}
            aria-label={show ? "Hide password" : "Show password"}
            onClick={() => setShow((v) => !v)}
          >
            <Icon name={show ? "eye" : "eyeOff"} size={18} />
          </button>
        )}
      </span>
      {hint && (
        <p id={hintId} className={styles.hint}>
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

/** The Partner portal's sign-up (2026-11-11): the partner sign-in's frame, the
 * "Create Partner Account" its button leads to. */
export function PartnerApplyScreen() {
  // The frame reads the session, which issues the CSRF cookie this form's POST needs.
  const [sentTo, setSentTo] = useState<string | null>(null);
  return (
    <PartnerAuthFrame
      title={sentTo ? "Check your email" : "Create Partner Account"}
      description={sentTo ? undefined : "Apply as an affiliate, reseller or white-label partner of WorkEasy360."}
    >
      {sentTo ? <Sent email={sentTo} /> : <ApplyForm onSent={setSentTo} />}
    </PartnerAuthFrame>
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
      <p className={styles.below}>
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
  // ADR-065: the Store's Partners page names the programme (`?type=affiliate`),
  // read after hydration so the server's render and the browser's first agree.
  useEffect(() => {
    const asked = new URLSearchParams(window.location.search).get("type");
    const found = EXPERIENCES.find((e) => e.value === asked)?.value;
    if (found) void Promise.resolve().then(() => setInterest((current) => current || found));
  }, []);
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
      <PartnerPortalBadge />
      <p className={styles.lead}>Creating an account does not grant any partner rights: you can promote, resell or earn only once your KYC is approved.</p>
      <form method="post" onSubmit={(e) => void submit(e)} noValidate className={frame.form} aria-describedby={problems.length ? errorId : undefined}>
        <Field label="Business name" autoComplete="organization" placeholder="Your business name" required maxLength={120} value={form.business_name} onChange={set("business_name")} />
        <div className={styles.formRow}>
          <Field label="First name" autoComplete="given-name" required maxLength={100} value={form.first_name} onChange={set("first_name")} />
          <Field label="Last name" autoComplete="family-name" required maxLength={100} value={form.last_name} onChange={set("last_name")} />
        </div>
        <Field label="Email Address" type="email" autoComplete="email" placeholder="Enter your email" required maxLength={254} value={form.email} onChange={set("email")} />
        <Field label="Password" type="password" autoComplete="new-password" placeholder="Choose a password" required value={form.password} onChange={set("password")} hint="At least 12 characters. A few unrelated words make a strong one." />
        <div className={styles.formRow}>
          <div>
            <label className={frame.label} htmlFor={countryId}>
              Country
            </label>
            <span className={frame.inputWrap}>
              <Icon name="globe" size={18} />
              <select id={countryId} className={`${frame.input} ${styles.select}`} required value={form.country} onChange={set("country")}>
                {countries.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </span>
          </div>
          <Field label="Phone (optional)" type="tel" autoComplete="tel" maxLength={32} value={form.phone} onChange={set("phone")} />
        </div>
        <fieldset className={styles.choice}>
          <legend className={frame.label}>How do you want to partner?</legend>
          <div className={styles.choiceList}>
            {EXPERIENCES.map((option) => (
              <label key={option.value} className={styles.choiceTile}>
                <Icon name={INTEREST_ICONS[option.value]} size={26} />
                <span>{option.label}</span>
                <input type="radio" name="interest" value={option.value} checked={interest === option.value} onChange={() => setInterest(option.value)} />
              </label>
            ))}
          </div>
        </fieldset>
        {problems.length > 0 && (
          <div id={errorId} role="alert" className={styles.problems}>
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
        <button type="submit" className={frame.submit} disabled={busy}>
          <Icon name="userPlus" size={20} />
          {busy ? "Creating your account…" : "Create Partner Account"}
        </button>
      </form>
      <p className={styles.below}>
        Already have a partner account? <Link href={SIGN_IN}>Login</Link>
      </p>
    </>
  );
}
