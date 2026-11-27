"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountApi } from "@/lib/api/console";
import { LoadGate, describeError, useLoad } from "@/features/controlplane/ui";
import { Badge, Button, formatDateTime } from "@/features/controlplane/kit";
import { Panel } from "@/features/console/consoleUi";
import { ChangeEmailForm } from "@/features/console/ChangeEmailForm";
import styles from "./Account.module.css";

/** One line the note asks for (Accounts V3, the note's 8.2): there is no phone sign-in
 * and no SMS provider, so no phone field is offered. */
export const NO_MOBILE_SIGN_IN = "Sign-in with a mobile number is not available.";

/**
 * Login & Security -> the sign-in email (Accounts V3, the note's 8.2): the current
 * address and whether it is verified, a change still waiting for its link (with Resend
 * and Cancel - the old address keeps working throughout), and the existing confirmed
 * change (ADR-066).
 */
export function SignInEmailCard({
  email,
  verified,
  twoStep = false,
  onChanged,
}: {
  email: string;
  verified: boolean;
  /** Two-step verification is on: the change asks for a code too. */
  twoStep?: boolean;
  onChanged?: () => void;
}) {
  const [pending, reload] = useLoad(() => accountApi.pendingEmail(), []);
  const [busy, setBusy] = useState<"resend" | "cancel" | null>(null);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);

  function resend() {
    setBusy("resend");
    setOutcome(null);
    accountApi
      .resendPendingEmail()
      .then((r) => setOutcome({ ok: true, text: `Sent again to ${r.pending?.new_email ?? "the new address"}. Only the newest link works.` }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 429) {
          setOutcome({ ok: false, text: "A link was sent a moment ago. Wait a few minutes before asking for another." });
        } else if (err instanceof ApiError && err.code === "no_pending_email_change") {
          setOutcome({ ok: false, text: "There is no change waiting any more." });
        } else {
          setOutcome({ ok: false, text: describeError(err) });
        }
      })
      .finally(() => {
        setBusy(null);
        reload();
      });
  }

  function cancel() {
    setBusy("cancel");
    setOutcome(null);
    accountApi
      .cancelPendingEmail()
      .then(() => {
        setOutcome({ ok: true, text: `Change cancelled. You keep signing in with ${email}.` });
        onChanged?.();
      })
      .catch((err: unknown) => setOutcome({ ok: false, text: describeError(err) }))
      .finally(() => {
        setBusy(null);
        reload();
      });
  }

  return (
    <Panel title="Sign-in email" description="The address you sign in with, and where security notices are sent.">
      <ChangeEmailForm
        twoStep={twoStep}
        currentEmail={email}
        status={verified ? <Badge tone="success">Verified</Badge> : <Badge tone="warning">Not verified</Badge>}
        onRequested={() => {
          setOutcome(null);
          reload();
          onChanged?.();
        }}
      />
      <LoadGate load={pending} what="a pending change of sign-in email">
        {({ pending: change }) =>
          change ? (
            <div className={styles.pending} role="group" aria-label="Change of sign-in email waiting for confirmation">
              <p className={styles.inline}>
                <Badge tone="info">Pending verification</Badge>
                <span>
                  We sent a link to <strong>{change.new_email}</strong> on {formatDateTime(change.requested_at)}.
                </span>
              </p>
              <p>
                Your sign-in email changes when that link is opened. It expires {formatDateTime(change.expires_at)}. Until then, keep signing in
                with {email}.
              </p>
              <div className={styles.actions}>
                <Button icon="send" loading={busy === "resend"} disabled={busy !== null} onClick={resend}>
                  Resend link
                </Button>
                <Button loading={busy === "cancel"} disabled={busy !== null} onClick={cancel}>
                  Cancel change
                </Button>
              </div>
            </div>
          ) : null
        }
      </LoadGate>
      {outcome && (
        <p role={outcome.ok ? "status" : "alert"} className={outcome.ok ? undefined : "w360-error-text"}>
          {outcome.text}
        </p>
      )}
      <p className={styles.muted}>{NO_MOBILE_SIGN_IN}</p>
    </Panel>
  );
}
