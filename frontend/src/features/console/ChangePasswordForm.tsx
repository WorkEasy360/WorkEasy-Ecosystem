"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountApi } from "@/lib/api/console";

/**
 * Change the password (the current one is required). Every other session ends;
 * this one stays signed in. Used on the security page and by the organization
 * gate when a password is below an organization's policy.
 */
export function ChangePasswordForm({ onChanged }: { onChanged?: () => void }) {
  const currentId = useId();
  const nextId = useId();
  const confirmId = useId();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (next !== confirm) {
      setProblems(["The new passwords don't match."]);
      return;
    }
    setBusy(true);
    setProblems([]);
    try {
      await accountApi.changePassword(current, next);
      setDone(true);
      setCurrent("");
      setNext("");
      setConfirm("");
      onChanged?.();
    } catch (err) {
      if (err instanceof ApiError) {
        const body = err.body as { detail?: string; problems?: string[] } | undefined;
        if (err.code === "password_policy_violation" && body?.problems?.length) {
          setProblems(body.problems);
        } else if (err.code === "current_password_incorrect") {
          setProblems(["The current password is not correct."]);
        } else if (err.status === 429) {
          setProblems(["Too many attempts. Wait a minute and try again."]);
        } else {
          setProblems([body?.detail || "The password could not be changed."]);
        }
      } else {
        setProblems(["The password could not be changed."]);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="w360-stack-form" onSubmit={submit} noValidate>
      <div>
        <label className="w360-label" htmlFor={currentId}>
          Current password
        </label>
        <input
          id={currentId}
          className="w360-input"
          type="password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
      </div>
      <div>
        <label className="w360-label" htmlFor={nextId}>
          New password
        </label>
        <input
          id={nextId}
          className="w360-input"
          type="password"
          autoComplete="new-password"
          required
          value={next}
          onChange={(e) => setNext(e.target.value)}
          aria-describedby={`${nextId}-hint`}
        />
        <p id={`${nextId}-hint`} className="w360-hint">
          At least 12 characters. A passphrase of several words is easiest to remember.
        </p>
      </div>
      <div>
        <label className="w360-label" htmlFor={confirmId}>
          Confirm the new password
        </label>
        <input
          id={confirmId}
          className="w360-input"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </div>
      {problems.length > 0 && (
        <div role="alert" className="w360-error-text">
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {done && <p role="status">Password changed. Your other sessions have been signed out.</p>}
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !current || !next}>
          {busy ? "Changing…" : "Change password"}
        </button>
      </div>
    </form>
  );
}
