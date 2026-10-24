"use client";

import { useId } from "react";
import { accountApi, type AccountEvent, type SessionRow } from "@/lib/api/console";
import { LoadGate, Table, useAction, useLoad, when } from "@/features/controlplane/ui";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { describePreferenceChange } from "@/features/signin/signInCopy";
import { ChangePasswordForm } from "./ChangePasswordForm";
import { PageHeader, Panel } from "./consoleUi";

const FAILURE_REASONS: Record<string, string> = {
  invalid_credentials: "wrong password",
  account_locked: "account locked",
  account_disabled: "account disabled",
};

/** An account-trail entry as a sentence. An unknown kind is shown as itself. */
export function describeAccountEvent(event: Pick<AccountEvent, "kind" | "reason">): string {
  switch (event.kind) {
    case "sign_in":
      return "Signed in";
    case "sign_in_failed": {
      const reason = FAILURE_REASONS[event.reason] ?? event.reason.replace(/_/g, " ");
      return reason ? `Failed sign-in attempt (${reason})` : "Failed sign-in attempt";
    }
    case "password_changed":
      return "Password changed";
    case "session_revoked":
      return "A session was signed out";
    case "other_sessions_revoked":
      return "All other sessions were signed out";
    case "signed_out_everywhere":
      return "Signed out everywhere";
    case "account_disabled":
      return "Account disabled";
    case "account_enabled":
      return "Account enabled";
    case "account_unlocked":
      return "Account unlocked";
    case "sign_in_preference_changed":
      return describePreferenceChange(event.reason);
    default:
      return event.kind.replace(/_/g, " ");
  }
}

/**
 * "My security": the person's own account, in every organization - the
 * password, where they are signed in and what has happened to the account
 * recently. Signing a session out ends it at once; changing the password ends
 * every other session, and the page says so.
 */
export function MySecurityScreen() {
  const [sessions, reloadSessions] = useLoad(() => accountApi.sessions(), []);
  const [activity, reloadActivity] = useLoad(() => accountApi.activity(), []);
  const refresh = () => {
    reloadSessions();
    reloadActivity();
  };
  const { act, busy, feedback } = useAction(refresh);
  const noteId = useId();

  return (
    <div className={styles.page}>
      <PageHeader
        title="My security"
        lead="How you sign in and where you're signed in. These settings belong to your account, in every organization."
      />

      <Panel title="Password">
        <ChangePasswordForm onChanged={refresh} />
      </Panel>

      <Panel title="Where you're signed in">
        <p id={noteId} className={cp.muted}>
          Signing a session out ends it at once. Changing your password also signs out every other session.
        </p>
        {feedback}
        <LoadGate load={sessions} what="your sessions">
          {(d) => (
            <SessionsTable
              sessions={d.sessions}
              busy={busy}
              describedBy={noteId}
              onRevoke={(s) =>
                void act(() => accountApi.revokeSession(s.session_id), `Signed out the session on ${s.device_label}.`)
              }
              onRevokeOthers={() =>
                void act(() => accountApi.revokeOtherSessions(), "Every other session has been signed out.")
              }
            />
          )}
        </LoadGate>
      </Panel>

      <Panel title="Recent security activity">
        <LoadGate load={activity} what="your recent activity">
          {(d) => (
            <Table
              caption="Sign-ins, password changes and signed-out sessions, newest first"
              head={["When", "What happened", "Device", "Network"]}
              isEmpty={d.events.length === 0}
              empty="No recent activity."
            >
              {d.events.map((e, i) => (
                <tr key={`${e.occurred_at}-${i}`}>
                  <td className={local.nowrap}>{when(e.occurred_at)}</td>
                  <th scope="row">{describeAccountEvent(e)}</th>
                  <td>{e.device_label || "—"}</td>
                  <td className={cp.mono}>{e.ip_prefix || "—"}</td>
                </tr>
              ))}
            </Table>
          )}
        </LoadGate>
      </Panel>
    </div>
  );
}

function SessionsTable({
  sessions,
  busy,
  describedBy,
  onRevoke,
  onRevokeOthers,
}: {
  sessions: SessionRow[];
  busy: boolean;
  describedBy: string;
  onRevoke: (session: SessionRow) => void;
  onRevokeOthers: () => void;
}) {
  const others = sessions.filter((s) => !s.current);
  return (
    <>
      <Table
        caption="Your sessions, most recently active first"
        head={["Device", "Network", "Signed in", "Last active", "Actions"]}
        isEmpty={sessions.length === 0}
        empty="No sessions to show."
      >
        {sessions.map((s) => (
          <tr key={s.session_id}>
            <th scope="row">
              {s.device_label}
              {s.current && <span className={`w360-badge w360-badge-success ${local.marker}`}>This device</span>}
            </th>
            <td className={cp.mono}>{s.ip_prefix || "—"}</td>
            <td>{when(s.signed_in_at)}</td>
            <td>{when(s.last_active_at)}</td>
            <td>
              {s.current ? (
                <span className={cp.muted}>Current session</span>
              ) : (
                <button
                  type="button"
                  className="w360-btn w360-btn-secondary"
                  aria-label={`Sign out ${s.device_label}, signed in ${when(s.signed_in_at)}`}
                  aria-describedby={describedBy}
                  disabled={busy}
                  onClick={() => onRevoke(s)}
                >
                  Sign out
                </button>
              )}
            </td>
          </tr>
        ))}
      </Table>
      {others.length > 0 && (
        <div>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            aria-describedby={describedBy}
            disabled={busy}
            onClick={onRevokeOthers}
          >
            Sign out all other sessions
          </button>
        </div>
      )}
    </>
  );
}
