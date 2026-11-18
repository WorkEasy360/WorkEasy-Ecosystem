"use client";

import { useId } from "react";
import Link from "next/link";
import { accountApi, type AccountEvent, type SessionRow } from "@/lib/api/console";
import { LoadGate, Table, useAction, useLoad, when } from "@/features/controlplane/ui";
import { Badge, Button, Page, useConfirm } from "@/features/controlplane/kit";
import cp from "@/features/controlplane/ControlPlane.module.css";
import local from "./ConsoleScreens.module.css";
import { useOrgAccess } from "./OrgAccess";
import { describePreferenceChange } from "@/features/signin/signInCopy";
import { useSession } from "@/lib/auth/useSession";
import { ChangeEmailForm } from "./ChangeEmailForm";
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
    case "email_change_requested":
      return "Change of sign-in email requested (link sent to the new address)";
    case "email_changed":
      return "Sign-in email changed";
    case "email_verified":
      return "Email address verified";
    case "password_reset":
      return "Password reset by email";
    default:
      return event.kind.replace(/_/g, " ");
  }
}

/**
 * Account & Security (2026-11-11, the User Admin brief Part 2 section 07): the
 * person's own account, in every organization - the password, where they are signed
 * in (and signing the others out), and what has happened to the account recently.
 * Signing a session out ends it at once; changing the password ends every other
 * session, and the page says so. Organization-wide security (policies, the audit log)
 * is on its own pages, shown only to those who may see it.
 */
export function MySecurityScreen() {
  const { slug, can } = useOrgAccess();
  const confirm = useConfirm();
  const [sessions, reloadSessions] = useLoad(() => accountApi.sessions(), []);
  const [activity, reloadActivity] = useLoad(() => accountApi.activity(), []);
  const refresh = () => {
    reloadSessions();
    reloadActivity();
  };
  const { act, busy, feedback } = useAction(refresh);
  const noteId = useId();
  const session = useSession();
  const email = session.status === "ready" ? session.session.email : "";

  return (
    <Page>
      <PageHeader
        title="Account & Security"
        lead="Your sign-in email and password, where you're signed in, and recent activity. These belong to your account, in every organization."
        actions={
          slug ? (
            <Link className="w360-btn w360-btn-secondary" href={`/o/${slug}/settings/sign-in-preferences`}>
              Sign-in preferences
            </Link>
          ) : undefined
        }
      />
      {confirm.dialog}

      <Panel title="Sign-in email">
        {email ? <ChangeEmailForm currentEmail={email} onRequested={refresh} /> : <p className={cp.muted}>Loading…</p>}
      </Panel>

      <Panel title="Password">
        <ChangePasswordForm popup onChanged={refresh} />
      </Panel>

      <Panel title="Sign-in methods">
        <ul className={local.methods}>
          <li>
            <strong>WorkEasy360 password</strong> - signs you in to User Admin, the Store and every product you can use.
          </li>
          <li>
            <strong>Products</strong> - open with this account through WorkEasy360 single sign-on. The first time you open each product
            after signing in, you choose to continue with this account or use a different one.{" "}
            {slug && <Link href={`/o/${slug}/settings/sign-in-preferences`}>Change how products sign you in</Link>}
          </li>
        </ul>
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
                confirm.ask({
                  title: "Sign out every other session?",
                  message: "Every browser and device except this one is signed out at once. You stay signed in here.",
                  confirmLabel: "Sign out other sessions",
                  danger: true,
                  onConfirm: () => {
                    void act(() => accountApi.revokeOtherSessions(), "Every other session has been signed out.");
                  },
                })
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

      {(can("platform.audit.view") || can("platform.security.view")) && slug && (
        <Panel title="Organization security">
          <p className={local.muted}>Visible to you as an administrator of this organization.</p>
          <div className={local.actions}>
            {can("platform.audit.view") && (
              <Link className="w360-btn w360-btn-secondary" href={`/o/${slug}/settings/audit`}>
                Admin activity history
              </Link>
            )}
            {can("platform.security.view") && (
              <Link className="w360-btn w360-btn-secondary" href={`/o/${slug}/settings/security/authentication`}>
                Organization sign-in policy
              </Link>
            )}
          </div>
        </Panel>
      )}
    </Page>
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
              {s.current && (
                <span className={local.marker}>
                  <Badge tone="success">This device</Badge>
                </span>
              )}
            </th>
            <td className={cp.mono}>{s.ip_prefix || "—"}</td>
            <td>{when(s.signed_in_at)}</td>
            <td>{when(s.last_active_at)}</td>
            <td>
              {s.current ? (
                <span className={cp.muted}>Current session</span>
              ) : (
                <Button
                  size="sm"
                  aria-label={`Sign out ${s.device_label}, signed in ${when(s.signed_in_at)}`}
                  aria-describedby={describedBy}
                  disabled={busy}
                  onClick={() => onRevoke(s)}
                >
                  Sign out
                </Button>
              )}
            </td>
          </tr>
        ))}
      </Table>
      {others.length > 0 && (
        <div>
          <Button icon="logout" aria-describedby={describedBy} disabled={busy} onClick={onRevokeOthers}>
            Sign out all other sessions
          </Button>
        </div>
      )}
    </>
  );
}
