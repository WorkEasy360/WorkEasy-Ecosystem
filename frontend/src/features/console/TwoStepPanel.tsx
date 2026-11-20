"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountApi, type TwoStepSetup, type TwoStepStatus } from "@/lib/api/console";
import { LoadGate, describeError, useLoad } from "@/features/controlplane/ui";
import { Alert, Badge, Button, CopyButton, formatDate, formatDateTime } from "@/features/controlplane/kit";
import { normalizeCode } from "@/features/auth/TwoStepSignIn";
import { Panel } from "./consoleUi";
import styles from "./TwoStep.module.css";

/**
 * Two-step verification (Accounts V3, the note's 8.3): an authenticator app's code as
 * well as the password at every sign-in, with ten single-use recovery codes. Nothing is
 * switched on until a first code from the app is confirmed; every later change (new
 * recovery codes, another authenticator, turning it off) asks for the password and a
 * current code again.
 *
 * The secret, the QR picture and the recovery codes are held in this component's state
 * only: never in a URL, a log or browser storage, and gone when the step closes.
 */

type Purpose = "setup" | "replace" | "codes" | "disable";

type Flow =
  | { step: "idle" }
  | { step: "verify"; purpose: Purpose }
  | { step: "scan"; setup: TwoStepSetup; replacing: boolean }
  | { step: "codes"; codes: string[]; reason: "enabled" | "replaced" | "regenerated"; otherSessionsEnded: boolean };

const VERIFY_COPY: Record<Purpose, { title: string; lead: string; submit: string }> = {
  setup: {
    title: "Set up two-step verification",
    lead: "Enter your password to start. Nothing changes until you confirm a code from your authenticator app.",
    submit: "Continue",
  },
  replace: {
    title: "Replace your authenticator app",
    lead: "Use this when you move to a new phone or app. Your current app keeps working until the new one is confirmed.",
    submit: "Continue",
  },
  codes: {
    title: "Get new recovery codes",
    lead: "Your current recovery codes stop working as soon as the new ones are made.",
    submit: "Make new codes",
  },
  disable: {
    title: "Turn off two-step verification",
    lead: "Signing in will need only your password. Your recovery codes stop working.",
    submit: "Turn off",
  },
};

const DONE_COPY: Record<"enabled" | "replaced" | "regenerated", string> = {
  enabled: "Two-step verification is on.",
  replaced: "Your new authenticator app is set up. The old one no longer works.",
  regenerated: "New recovery codes are ready. The old ones no longer work.",
};

/** A server refusal, in words, and which field it is about. */
function refusal(err: unknown): { text: string; field?: "password" | "code" } {
  if (err instanceof ApiError) {
    if (err.code === "password_invalid" || err.code === "current_password_incorrect") {
      return { text: "The password is not correct.", field: "password" };
    }
    if (err.code === "two_step_code_invalid") {
      return { text: "That code isn't right. Check your authenticator app and try again, or use a recovery code.", field: "code" };
    }
    if (err.status === 429) return { text: "Too many attempts. Wait a minute, then try again." };
    if (err.code === "two_step_unavailable") return { text: "Two-step verification is not available right now. Try again later." };
    if (err.code === "two_step_setup_missing") return { text: "This setup has expired. Start again." };
    if (err.code === "two_step_not_enabled") return { text: "Two-step verification is already off." };
  }
  return { text: describeError(err) };
}

/** Moves focus to a step's heading when it appears (the button pressed has gone). */
function useStepFocus() {
  const ref = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return ref;
}

export function TwoStepPanel() {
  const [load, reload] = useLoad(() => accountApi.twoStep(), []);
  const [flow, setFlow] = useState<Flow>({ step: "idle" });
  const [outcome, setOutcome] = useState<string | null>(null);
  const finish = (message: string) => {
    setFlow({ step: "idle" });
    setOutcome(message);
    reload();
  };
  const start = (purpose: Purpose) => {
    setOutcome(null);
    setFlow({ step: "verify", purpose });
  };
  return (
    <Panel
      title="Two-step verification"
      description="A code from an authenticator app on your phone, as well as your password, every time you sign in."
    >
      <LoadGate load={load} what="your two-step verification">
        {(status) => {
          if (flow.step === "verify") {
            return (
              <VerifyStep
                purpose={flow.purpose}
                needsCode={status.enabled}
                onCancel={() => setFlow({ step: "idle" })}
                onSetup={(setup) => setFlow({ step: "scan", setup, replacing: flow.purpose === "replace" })}
                onCodes={(codes) => setFlow({ step: "codes", codes, reason: "regenerated", otherSessionsEnded: false })}
                onDisabled={() => finish("Two-step verification is off. Signing in needs only your password.")}
              />
            );
          }
          if (flow.step === "scan") {
            return (
              <ScanStep
                setup={flow.setup}
                onCancel={() => setFlow({ step: "idle" })}
                onExpired={(message) => {
                  setFlow({ step: "idle" });
                  setOutcome(message);
                }}
                onConfirmed={(codes, otherSessionsEnded) =>
                  setFlow({ step: "codes", codes, reason: flow.replacing ? "replaced" : "enabled", otherSessionsEnded })
                }
              />
            );
          }
          if (flow.step === "codes") {
            const { reason, otherSessionsEnded } = flow;
            return (
              <RecoveryCodes
                codes={flow.codes}
                onDone={() =>
                  finish(`${DONE_COPY[reason]}${otherSessionsEnded ? " Your other devices were signed out." : ""}`)
                }
              />
            );
          }
          return <StatusView status={status} outcome={outcome} onStart={start} />;
        }}
      </LoadGate>
    </Panel>
  );
}

function StatusView({ status, outcome, onStart }: { status: TwoStepStatus; outcome: string | null; onStart: (p: Purpose) => void }) {
  const feedback = outcome && <p role="status">{outcome}</p>;
  if (!status.available) {
    return (
      <div className={styles.step}>
        <p className={styles.status}>
          <Badge tone="neutral">Not available yet</Badge>
        </p>
        <p className={styles.muted}>Two-step verification is not available on WorkEasy360 yet. Your password still protects your account.</p>
        {feedback}
      </div>
    );
  }
  if (!status.enabled) {
    return (
      <div className={styles.step}>
        <p className={styles.status}>
          <Badge tone="warning">Off</Badge>
          <span>Only your password protects your account.</span>
        </p>
        <p className={styles.muted}>
          You need an authenticator app, such as Google Authenticator, Microsoft Authenticator or 1Password. You also get ten recovery codes, in
          case you lose your phone.
        </p>
        {feedback}
        <div className={styles.actions}>
          <Button variant="primary" icon="shield" onClick={() => onStart("setup")}>
            Set up two-step verification
          </Button>
        </div>
      </div>
    );
  }
  const left = status.recovery_codes_remaining;
  return (
    <div className={styles.step}>
      <p className={styles.status}>
        <Badge tone="success">On</Badge>
        <span>
          Authenticator app{status.enabled_at ? `, since ${formatDate(status.enabled_at)}` : ""}.
        </span>
      </p>
      <p>
        {left} {left === 1 ? "recovery code" : "recovery codes"} left.
        {left <= 2 && <span className={styles.muted}> Get new ones before you run out.</span>}
      </p>
      {feedback}
      <div className={styles.actions}>
        <Button icon="refresh" onClick={() => onStart("codes")}>
          New recovery codes
        </Button>
        <Button icon="phone" onClick={() => onStart("replace")}>
          Replace authenticator app
        </Button>
        <Button variant="danger" onClick={() => onStart("disable")}>
          Turn off
        </Button>
      </div>
    </div>
  );
}

function VerifyStep({
  purpose,
  needsCode,
  onCancel,
  onSetup,
  onCodes,
  onDisabled,
}: {
  purpose: Purpose;
  needsCode: boolean;
  onCancel: () => void;
  onSetup: (setup: TwoStepSetup) => void;
  onCodes: (codes: string[]) => void;
  onDisabled: () => void;
}) {
  const heading = useStepFocus();
  const passwordId = useId();
  const codeId = useId();
  const errorId = useId();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ text: string; field?: "password" | "code" } | null>(null);
  const copy = VERIFY_COPY[purpose];
  const askCode = needsCode && purpose !== "setup";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!password || (askCode && !normalizeCode(code))) {
      setProblem({ text: askCode ? "Enter your password and a code from your authenticator app." : "Enter your password." });
      return;
    }
    setBusy(true);
    setProblem(null);
    const value = normalizeCode(code);
    try {
      if (purpose === "setup" || purpose === "replace") {
        onSetup(await accountApi.twoStepSetup(password, askCode ? value : undefined));
      } else if (purpose === "codes") {
        onCodes((await accountApi.twoStepRecoveryCodes(password, value)).recovery_codes);
      } else {
        await accountApi.twoStepDisable(password, value);
        onDisabled();
      }
    } catch (err) {
      setProblem(refusal(err));
      setBusy(false);
    }
  }

  return (
    <form method="post" className={styles.step} onSubmit={submit} noValidate>
      <h3 ref={heading} tabIndex={-1}>
        {copy.title}
      </h3>
      <p>{copy.lead}</p>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={passwordId}>
          Password
        </label>
        <input
          id={passwordId}
          className="w360-input"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={problem?.field === "password" ? true : undefined}
          aria-describedby={problem ? errorId : undefined}
        />
      </div>
      {askCode && (
        <div className={styles.field}>
          <label className="w360-label" htmlFor={codeId}>
            6-digit code or a recovery code
          </label>
          <input
            id={codeId}
            className="w360-input"
            type="text"
            inputMode="text"
            autoComplete="one-time-code"
            autoCapitalize="off"
            spellCheck={false}
            maxLength={32}
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            aria-invalid={problem?.field === "code" ? true : undefined}
            aria-describedby={problem ? errorId : undefined}
          />
        </div>
      )}
      {problem && (
        <p id={errorId} role="alert" className="w360-error-text">
          {problem.text}
        </p>
      )}
      <div className={styles.actions}>
        <Button type="submit" variant={purpose === "disable" ? "danger" : "primary"} loading={busy}>
          {copy.submit}
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** The secret in groups of four, as authenticator apps print it. */
function grouped(secret: string): string {
  return secret.replace(/\s+/g, "").replace(/(.{4})/g, "$1 ").trim();
}

function ScanStep({
  setup,
  onCancel,
  onExpired,
  onConfirmed,
}: {
  setup: TwoStepSetup;
  onCancel: () => void;
  onExpired: (message: string) => void;
  onConfirmed: (codes: string[], otherSessionsEnded: boolean) => void;
}) {
  const heading = useStepFocus();
  const codeId = useId();
  const errorId = useId();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const value = normalizeCode(code);
    if (!/^\d{6}$/.test(value)) {
      setProblem("Enter the 6-digit code your authenticator app shows now.");
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const done = await accountApi.twoStepConfirm(value);
      onConfirmed(done.recovery_codes, done.other_sessions_ended);
    } catch (err) {
      if (err instanceof ApiError && err.code === "two_step_setup_missing") {
        onExpired("That setup expired before it was confirmed. Nothing was changed; start again.");
        return;
      }
      setProblem(refusal(err).text);
      setCode("");
      setBusy(false);
    }
  }

  return (
    <form method="post" className={styles.step} onSubmit={submit} noValidate>
      <h3 ref={heading} tabIndex={-1}>
        Scan this code with your authenticator app
      </h3>
      <p>Open the app, add an account, and scan the picture.</p>
      {/* eslint-disable-next-line @next/next/no-img-element -- a data: URI the server drew; nothing to optimize */}
      <img className={styles.qr} src={setup.qr_svg_data_uri} alt="QR code to add WorkEasy360 to your authenticator app" width={200} height={200} />
      <p>
        Can&apos;t scan it? Enter this key in the app instead: <span className={styles.secret}>{grouped(setup.secret)}</span>{" "}
        <CopyButton value={setup.secret} label="Copy the key" />
      </p>
      <p>
        On this phone? <a href={setup.otpauth_uri}>Add it to an authenticator app on this device</a>.
      </p>
      <p className={styles.muted}>This setup expires at {formatDateTime(setup.expires_at)}.</p>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={codeId}>
          6-digit code from the app
        </label>
        <input
          id={codeId}
          className="w360-input"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          required
          value={code}
          onChange={(e) => setCode(e.target.value)}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? errorId : undefined}
        />
      </div>
      {problem && (
        <p id={errorId} role="alert" className="w360-error-text">
          {problem}
        </p>
      )}
      <div className={styles.actions}>
        <Button type="submit" variant="primary" loading={busy}>
          Confirm and turn on
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** Downloads the codes as a text file, made in the browser (never fetched). */
function downloadCodes(codes: string[]): boolean {
  if (typeof URL.createObjectURL !== "function") return false;
  const text = `WorkEasy360 recovery codes\nEach code works once. Keep them somewhere safe.\n\n${codes.join("\n")}\n`;
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "workeasy360-recovery-codes.txt";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return true;
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const heading = useStepFocus();
  const checkId = useId();
  const [saved, setSaved] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  return (
    <div className={styles.step}>
      <h3 ref={heading} tabIndex={-1}>
        Save your recovery codes
      </h3>
      <Alert tone="warning" title="These codes are shown only once.">
        If you lose your phone, sign in with one of these instead of a code from the app. Each code works once.
      </Alert>
      <ol className={styles.codes} aria-label="Recovery codes">
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ol>
      <div className={styles.actions}>
        <Button
          icon="copy"
          onClick={() => {
            const done = () => setNote("Copied.");
            if (navigator.clipboard?.writeText) navigator.clipboard.writeText(codes.join("\n")).then(done, () => setNote("Could not copy. Write them down instead."));
            else setNote("Could not copy. Write them down instead.");
          }}
        >
          Copy codes
        </Button>
        <Button icon="download" onClick={() => setNote(downloadCodes(codes) ? "Downloaded." : "Could not download. Copy them instead.")}>
          Download codes
        </Button>
        {note && <span role="status">{note}</span>}
      </div>
      <label className={styles.check} htmlFor={checkId}>
        <input id={checkId} type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        <span>I saved these codes somewhere safe</span>
      </label>
      <div className={styles.actions}>
        <Button variant="primary" disabled={!saved} onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}
