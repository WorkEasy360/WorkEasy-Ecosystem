"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountApi } from "@/lib/api/console";
import { DialogHead, Modal, dialog } from "@/features/controlplane/Modal";

/**
 * Change the password (the current one is required). Every other session ends;
 * this one stays signed in. Used on the security page and by the organization
 * gate when a password is below an organization's policy.
 *
 * `popup` (2026-11-05, every form in a popup): a "Change password" button that
 * opens the same fields in a popup - for a settings page. Without it the form
 * is on the page, for the gates whose whole purpose is this change.
 */
export function ChangePasswordForm({ onChanged, popup = false }: { onChanged?: () => void; popup?: boolean }) {
  const currentId = useId();
  const nextId = useId();
  const confirmId = useId();
  const titleId = useId();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [done, setDone] = useState(false);
  const [open, setOpen] = useState(false);

  function clear() {
    setCurrent("");
    setNext("");
    setConfirm("");
  }

  function close() {
    setOpen(false);
    setProblems([]);
    clear();
  }

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
      clear();
      setOpen(false);
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

  const fields = (
    <>
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
    </>
  );

  const problemList = problems.length > 0 && (
    <div role="alert" className="w360-error-text">
      <ul>
        {problems.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  );

  if (popup) {
    return (
      <>
        <div>
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            aria-haspopup="dialog"
            onClick={() => {
              setDone(false);
              setProblems([]);
              clear();
              setOpen(true);
            }}
          >
            Change password
          </button>
          {done && (
            <>
              {" "}
              <span role="status">Password changed. Your other sessions have been signed out.</span>
            </>
          )}
        </div>
        {open && (
          <Modal labelledBy={titleId} onClose={close} size="sm">
            <form method="post" className={dialog.form} onSubmit={submit} noValidate>
              <DialogHead titleId={titleId} title="Change password" onClose={close} />
              <div className={dialog.body}>
                <p className={dialog.intro}>Every other session is signed out; this one stays signed in.</p>
                {problemList}
                {fields}
              </div>
              <div className={dialog.foot}>
                <button type="button" className="w360-btn w360-btn-secondary" onClick={close}>
                  Cancel
                </button>
                <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !current || !next}>
                  {busy ? "Changing…" : "Change password"}
                </button>
              </div>
            </form>
          </Modal>
        )}
      </>
    );
  }

  return (
    <form method="post" className="w360-stack-form" onSubmit={submit} noValidate>
      {fields}
      {problemList}
      {done && <p role="status">Password changed. Your other sessions have been signed out.</p>}
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !current || !next}>
          {busy ? "Changing…" : "Change password"}
        </button>
      </div>
    </form>
  );
}
