"use client";

import { useId, useState } from "react";
import { RequiredMark } from "../widgets";
import styles from "./AddPartner.module.css";
import cp from "../ControlPlane.module.css";
import { passwordChecks, passwordStrength } from "./model";

/**
 * A password input for a password someone else will use (ADR-047).
 *
 * `type="password"` with `autoComplete="new-password"`, so a browser or a
 * password manager treats it as a password being *created* (and never offers to
 * fill it from another site). The value is controlled by the parent's React
 * state and goes nowhere else: no storage, no URL, no log. Show/Hide changes how
 * it is drawn and nothing else; it starts hidden.
 */
export function PasswordField({
  label,
  value,
  onChange,
  error,
  hint,
  describedBy,
  required = true,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: React.ReactNode;
  /** Ids of other text that describes this field (the requirements list). */
  describedBy?: string;
  required?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const hintId = useId();
  const errorId = useId();
  const [visible, setVisible] = useState(false);
  const described = [hint ? hintId : "", error ? errorId : "", describedBy ?? ""].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cp.field}>
      <label className="w360-label" htmlFor={id}>
        {label}
        {required && <RequiredMark />}
      </label>
      <div className={styles.passwordRow}>
        <input
          id={id}
          className="w360-input"
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="new-password"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          maxLength={128}
          placeholder={placeholder}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={described}
        />
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          aria-pressed={visible}
          aria-controls={id}
          // The visible word is "Show"/"Hide"; the name adds which field, and
          // starts with the visible word (WCAG 2.5.3).
          aria-label={`${visible ? "Hide" : "Show"} ${label.toLowerCase()}`}
          onClick={() => setVisible((v) => !v)}
        >
          {visible ? "Hide" : "Show"}
        </button>
      </div>
      {hint && (
        <p id={hintId} className="w360-hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="w360-error-text">
          {error}
        </p>
      )}
    </div>
  );
}

/** What the person typing can see being met, and an indicative strength. The
 * list is the courtesy checks (length, both match); the rest of the policy is the
 * server's and is said in words, never faked here. */
export function PasswordGuidance({
  id,
  password,
  confirm,
  minLength,
  compact = false,
}: {
  id: string;
  password: string;
  confirm: string;
  minLength: number;
  /** Smaller, without the box - inside the Add Partner dialog. */
  compact?: boolean;
}) {
  const checks = passwordChecks(password, confirm, minLength);
  const strength = passwordStrength(password, minLength);
  return (
    <div id={id} className={compact ? styles.guidanceCompact : styles.guidance}>
      <p className={styles.strength}>
        Strength: <strong>{strength.label}</strong>{" "}
        <span className={styles.strengthBar} aria-hidden="true">
          {[1, 2, 3, 4].map((n) => (
            <span key={n} className={n <= strength.score ? styles.strengthOn : styles.strengthOff} />
          ))}
        </span>{" "}
        <span className={compact ? "w360-visually-hidden" : styles.muted}>(a hint only - the platform&apos;s password policy decides)</span>
      </p>
      <ul className={styles.requirements} aria-label="Password requirements">
        {checks.map((c) => (
          <li key={c.key} className={c.ok ? styles.met : styles.unmet}>
            <span aria-hidden="true">{c.ok ? "✓" : "○"}</span> {c.label}
            <span className="w360-visually-hidden">{c.ok ? " - met" : " - not met yet"}</span>
          </li>
        ))}
        <li className={styles.unmet}>
          <span aria-hidden="true">○</span> Not a common password, not only digits, not like the email address
          <span className="w360-visually-hidden"> - checked when you continue</span>
        </li>
      </ul>
    </div>
  );
}
