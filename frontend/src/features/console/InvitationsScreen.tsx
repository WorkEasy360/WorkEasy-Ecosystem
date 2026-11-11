"use client";

import { useId, useState } from "react";
import { ASSIGNABLE_ROLES, consoleApi } from "@/lib/api/console";
import { MoreButton, Status, Table, useAction, usePaged, when } from "@/features/controlplane/ui";
import { FormPopup } from "@/features/controlplane/Modal";
import { Card, Page, useConfirm } from "@/features/controlplane/kit";
import cp from "@/features/controlplane/ControlPlane.module.css";
import local from "./ConsoleScreens.module.css";
import { PageHeader, PagedState, roleLabel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

const STATES = [
  { value: "pending", label: "Pending" },
  { value: "expired", label: "Expired" },
  { value: "accepted", label: "Accepted" },
  { value: "revoked", label: "Revoked" },
  { value: "all", label: "All" },
];

/** Roles whose holder can change who holds what: inviting someone into one
 * also needs `platform.role.assign` (and, being dangerous, a verified code). */
const PRIVILEGED = new Set(["admin", "billing_manager"]);

/**
 * "Invite Member": an email and a role, in a popup (2026-11-05). Offered on Members
 * and on Invitations. Administrator and Billing Manager are offered only to someone
 * who may assign roles; the server checks again.
 */
export function InviteMemberPopup({
  slug,
  onDone,
  label = "+ Invite someone",
}: {
  slug: string;
  onDone?: () => void;
  label?: string;
}) {
  const { can } = useOrgAccess();
  const emailId = useId();
  const roleId = useId();
  const roleHintId = useId();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const canPrivileged = can("platform.role.assign");
  const roles = ASSIGNABLE_ROLES.filter((r) => canPrivileged || !PRIVILEGED.has(r.key));
  return (
    <FormPopup
      label={label}
      title="Invite someone"
      size="sm"
      submitLabel="Send invitation"
      canSubmit={!!email.trim()}
      onOpen={() => {
        setEmail("");
        setRole("member");
      }}
      onSubmit={async () => {
        const address = email.trim();
        await consoleApi.invite(slug, address, role);
        return `Invitation sent to ${address}.`;
      }}
      onDone={onDone}
    >
      <div className={cp.field}>
        <label className="w360-label" htmlFor={emailId}>
          Email address
        </label>
        <input
          id={emailId}
          className="w360-input"
          type="email"
          autoComplete="off"
          required
          maxLength={254}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={roleId}>
          Role
        </label>
        <select id={roleId} className="w360-input" value={role} onChange={(e) => setRole(e.target.value)} aria-describedby={roleHintId}>
          {roles.map((r) => (
            <option key={r.key} value={r.key}>
              {r.label}
            </option>
          ))}
        </select>
        <p id={roleHintId} className="w360-hint">
          {canPrivileged
            ? "You can invite Members, Read Only users, Administrators and Billing Managers."
            : "You can invite Members and Read Only users. An administrator who can assign roles can invite administrators and billing managers."}
        </p>
      </div>
    </FormPopup>
  );
}

/**
 * Invite people by email, and follow up on the invitations already sent. The link
 * travels in the email only; nothing here ever shows or sends a token. Revoking asks
 * first: the person's link stops working at once.
 */
export function InvitationsScreen() {
  const { slug, can } = useOrgAccess();
  const stateId = useId();
  const [state, setState] = useState("pending");
  const paged = usePaged((cursor) => consoleApi.invitations(slug, state, cursor), [slug, state]);
  const { act, busy, feedback } = useAction(paged.reload);
  const confirm = useConfirm();
  const canInvite = can("platform.member.invite");

  return (
    <Page>
      {/* Members and Invitations are tabs of Organization & Team (2026-11-03): no header link between them. */}
      <PageHeader
        title="Invitations"
        lead="Invite people by email. An invitation lasts a week; resending it sends a new link and the old one stops working."
        primary={canInvite ? <InviteMemberPopup slug={slug} onDone={paged.reload} /> : undefined}
      />
      {feedback}
      {confirm.dialog}

      <Card title="Sent invitations">
        <div className={local.section}>
        <div className={local.toolbar}>
          <div className={cp.field}>
            <label className="w360-label" htmlFor={stateId}>
              Show
            </label>
            <select id={stateId} className="w360-input" value={state} onChange={(e) => setState(e.target.value)}>
              {STATES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <PagedState state={paged.state} what="invitations" />
        {paged.state === "ready" && (
          <>
            <Table
              caption="Invitations"
              head={["Email", "Role", "State", "Invited by", "Sent", "Expires", "Actions"]}
              isEmpty={paged.rows.length === 0}
              empty="No invitations to show."
            >
              {paged.rows.map((inv) => {
                const open = inv.state === "pending" || inv.state === "expired";
                return (
                  <tr key={inv.invitation_id}>
                    <th scope="row">{inv.email}</th>
                    <td>{roleLabel(inv.role)}</td>
                    <td>
                      <Status value={inv.state} />
                    </td>
                    <td>{inv.invited_by ?? "—"}</td>
                    <td>
                      {when(inv.last_sent_at ?? inv.created_at)}
                      {inv.send_count > 1 && <div className={cp.muted}>Sent {inv.send_count} times</div>}
                    </td>
                    <td>{inv.state === "accepted" ? "—" : when(inv.expires_at)}</td>
                    <td>
                      {open && canInvite ? (
                        <div className={local.actions}>
                          <button
                            type="button"
                            className="w360-btn w360-btn-secondary"
                            aria-label={`Resend invitation to ${inv.email}`}
                            disabled={busy}
                            onClick={() =>
                              void act(
                                () => consoleApi.resendInvitation(slug, inv.invitation_id),
                                `A new invitation was sent to ${inv.email}. The previous link no longer works.`,
                              )
                            }
                          >
                            Resend
                          </button>
                          <button
                            type="button"
                            className="w360-btn w360-btn-secondary"
                            aria-label={`Revoke invitation to ${inv.email}`}
                            disabled={busy}
                            onClick={() =>
                              confirm.ask({
                                title: `Revoke the invitation to ${inv.email}?`,
                                message: "Their link stops working at once. You can invite them again later.",
                                confirmLabel: "Revoke invitation",
                                danger: true,
                                onConfirm: () => {
                                  void act(
                                    () => consoleApi.revokeInvitation(slug, inv.invitation_id),
                                    `The invitation to ${inv.email} was revoked.`,
                                  );
                                },
                              })
                            }
                          >
                            Revoke
                          </button>
                        </div>
                      ) : (
                        <span className={cp.muted}>—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </Table>
            <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
          </>
        )}
        </div>
      </Card>
    </Page>
  );
}
