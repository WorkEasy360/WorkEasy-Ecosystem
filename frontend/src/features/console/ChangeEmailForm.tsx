"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountApi } from "@/lib/api/console";
import { DialogHead, Modal, dialog } from "@/features/controlplane/Modal";

/**
 * Change the sign-in email (ADR-066; the unified-login brief 5.6). Not the
 * same as using a different account: this account - its organizations,
 * products, wallets and history - stays exactly as it is, and only the address
 * it signs in with moves, once the link mailed to the new address is used.
 * The current password is required - and, with two-step verification on, a
 * current code (2026-11-27, the V2/V3 alignment: reauthenticate before a
 * sensitive identifier change). A button that opens the fields in a popup
 * (every form in a popup, 2026-11-05).
 */
export function ChangeEmailForm({
  currentEmail,
  onRequested,
  status,
  twoStep = false,
}: {
  currentEmail: string;
  onRequested?: () => void;
  /** Two-step verification is on: ask for the code with the password. When the
   * caller does not know, the field appears once the server asks for it. */
  twoStep?: boolean;
  /** Accounts V3: shown after the address (its Verified badge). */
  status?: React.ReactNode;
}) {
  const emailId = useId();
  const passwordId = useId();
  const codeId = useId();
  const titleId = useId();
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [askCode, setAskCode] = useState(twoStep);
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [sentTo, setSentTo] = useState("");
  const [open, setOpen] = useState(false);

  function close() {
    setOpen(false);
    setProblems([]);
    setAddress("");
    setPassword("");
    setCode("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setProblems([]);
    try {
      await accountApi.changeEmail(address.trim(), password, askCode ? code.trim() : undefined);
      setSentTo(address.trim());
      close();
      onRequested?.();
    } catch (err) {
      if (err instanceof ApiError) {
        const body = err.body as { detail?: string } | undefined;
        if (err.code === "current_password_incorrect") setProblems(["The current password is not correct."]);
        else if (err.code === "two_step_code_invalid") {
          setProblems([
            askCode
              ? "That code isn't right. Use the current code from your authenticator app, or a recovery code."
              : "Two-step verification is on: also enter the code from your authenticator app.",
          ]);
          setAskCode(true);
          setCode("");
        }
        else if (err.status === 429) setProblems(["Too many requests. Wait a while and try again."]);
        else setProblems([body?.detail || "The email could not be changed."]);
      } else {
        setProblems(["The email could not be changed."]);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p>
        You sign in with <strong>{currentEmail}</strong>.{status ? <> {status}</> : null}
      </p>
      <div>
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          aria-haspopup="dialog"
          onClick={() => {
            setSentTo("");
            setOpen(true);
          }}
        >
          Change sign-in email
        </button>
        {sentTo && (
          <>
            {" "}
            <span role="status">
              We sent a confirmation link to {sentTo}. Your sign-in email changes when you open it; until then, keep using {currentEmail}.
            </span>
          </>
        )}
      </div>
      {open && (
        <Modal labelledBy={titleId} onClose={close} size="sm">
          <form method="post" className={dialog.form} onSubmit={submit} noValidate>
            <DialogHead titleId={titleId} title="Change sign-in email" onClose={close} />
            <div className={dialog.body}>
              <p className={dialog.intro}>
                Your account, organizations and products stay the same - only the address you sign in with changes. We email a link to the new
                address to confirm it.
              </p>
              {problems.length > 0 && (
                <div role="alert" className="w360-error-text">
                  <ul>
                    {problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div>
                <label className="w360-label" htmlFor={emailId}>
                  New sign-in email
                </label>
                <input
                  id={emailId}
                  className="w360-input"
                  type="email"
                  autoComplete="email"
                  required
                  value={address}
                  onChange={(e) => setAddress(e.target.value)}
                />
              </div>
              <div>
                <label className="w360-label" htmlFor={passwordId}>
                  Current password
                </label>
                <input
                  id={passwordId}
                  className="w360-input"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              {askCode && (
                <div>
                  <label className="w360-label" htmlFor={codeId}>
                    Two-step code
                  </label>
                  <input
                    id={codeId}
                    className="w360-input"
                    inputMode="text"
                    autoComplete="one-time-code"
                    required
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    aria-describedby={`${codeId}-hint`}
                  />
                  <p id={`${codeId}-hint`} className="w360-hint">
                    The 6-digit code from your authenticator app, or one of your recovery codes.
                  </p>
                </div>
              )}
            </div>
            <div className={dialog.foot}>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={close}>
                Cancel
              </button>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !address.trim() || !password || (askCode && !code.trim())}>
                {busy ? "Sending…" : "Send confirmation link"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
