"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { adminApi, type PartnerDetail, type PartnerMemberRow } from "@/lib/api/controlPlane";
import { partnerConsoleApi } from "@/lib/api/partnerConsole";
import { Section } from "./PartnerSection";
import { Table, when } from "./ui";
import { Segmented, SelectField, TextField } from "./widgets";
import styles from "./ControlPlane.module.css";
import ps from "./PartnerProfileTabs.module.css";

/**
 * The people who can sign in and act for one partner: who they are, whether
 * they have taken up the invitation and
 * when they last used it - and the four things staff do about them.
 *
 * - **Invite** names a person (first and last name, used only when the address
 *   has no account yet) and says when the setup link goes out. There is no
 *   password field anywhere: a person chooses their own from the emailed link.
 * - **Change role** moves a person between Administrator and Viewer. The server
 *   refuses to take the last administrator off an active partner, and its
 *   refusal is shown as written.
 * - **Send or resend the setup link** for someone who has not chosen a password.
 * - **Remove** ends this person's *partner* access at once. It does not delete
 *   their Workeasy360 account, and nothing they belong to elsewhere changes.
 *
 * Every change goes through the same staff commands as before; nothing here is
 * a second way to change a member.
 */

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

const ROLE_TEXT: Record<string, string> = { owner: "Owner", admin: "Administrator", viewer: "Viewer" };
const roleText = (role: string) => ROLE_TEXT[role] ?? role;

function MemberStatus({ member: m }: { member: PartnerMemberRow }) {
  if (m.password_set) return <span className="w360-badge w360-badge-success">Active</span>;
  if (m.invitation_deferred) {
    return <span className="w360-badge w360-badge-warning">Invitation not sent yet (sent when the partner is activated)</span>;
  }
  if (m.invitation_sent_at) return <span className="w360-badge w360-badge-neutral">Invitation sent {when(m.invitation_sent_at)}</span>;
  // Attached before invitations were tracked: no record of one being sent.
  return <span className="w360-badge w360-badge-neutral">Invitation pending</span>;
}

export function MembersSection({ partner, manage, act, busy }: { partner: PartnerDetail; manage: boolean; act: Act; busy: boolean }) {
  return (
    <Section title="Partner users">
      <p className={styles.muted}>
        The people who can sign in and act for this partner. Someone invited by email chooses their own password from the link they are
        sent. The administrator set up through Add Partner signs in with the password typed there; it is never shown again, and “Send
        password reset link” lets them choose a new one. The contacts on the Contacts tab are not users.
      </p>
      <Table
        caption="Members"
        head={["Name", "Email", "Role", "Status", "Last activity", ...(manage ? ["Actions"] : [])]}
        isEmpty={partner.members.length === 0}
        empty="No users yet."
      >
        {partner.members.map((m) => (
          <MemberRow key={m.id} partnerId={partner.id} member={m} manage={manage} act={act} busy={busy} />
        ))}
      </Table>
      {manage &&
        (partner.status === "terminated" ? (
          <p className={styles.muted}>This partner is terminated, so it takes on no new users.</p>
        ) : (
          <InviteForm partner={partner} act={act} busy={busy} />
        ))}
    </Section>
  );
}

// --- one person -----------------------------------------------------------------------------

type Panel = "role" | "remove";

function MemberRow({ partnerId, member: m, manage, act, busy }: { partnerId: string; member: PartnerMemberRow; manage: boolean; act: Act; busy: boolean }) {
  const [panel, setPanel] = useState<Panel | null>(null);
  // Closing a confirmation without acting returns keyboard focus to the button
  // that opened it; acting moves it to the announced outcome (useAction).
  const restore = useRef<Panel | null>(null);
  const triggers = useRef<Record<Panel, HTMLButtonElement | null>>({ role: null, remove: null });

  useEffect(() => {
    if (panel === null && restore.current) {
      triggers.current[restore.current]?.focus();
      restore.current = null;
    }
  }, [panel]);

  const cancel = () => {
    restore.current = panel;
    setPanel(null);
  };

  function resend() {
    return act(async () => {
      const result = await partnerConsoleApi.resendSetup(partnerId, m.id);
      if (!result.sent) {
        throw new ApiError(409, { detail: "No link was sent: this person has already chosen a password." });
      }
    }, m.invitation_deferred ? "The setup link was sent now, instead of when the partner is activated." : "A new setup link was sent. The old one no longer works.");
  }

  return (
    <tr>
      <td>{m.full_name || "-"}</td>
      <th scope="row">{m.email}</th>
      <td>{roleText(m.role)}</td>
      <td>
        <MemberStatus member={m} />
      </td>
      <td>{m.last_login_at ? when(m.last_login_at) : "Never signed in"}</td>
      {manage && (
        <td>
          {panel === "role" ? (
            <RolePanel
              member={m}
              busy={busy}
              onCancel={cancel}
              onConfirm={(role) => {
                setPanel(null);
                void act(() => partnerConsoleApi.changeMemberRole(partnerId, m.id, role), "Role changed. It takes effect on this person's next request.");
              }}
            />
          ) : panel === "remove" ? (
            <RemovePanel
              member={m}
              busy={busy}
              onCancel={cancel}
              onConfirm={() => {
                setPanel(null);
                void act(
                  () => adminApi.removeMember(partnerId, m.id),
                  "User removed. Their partner access ended immediately; their Workeasy360 account was not deleted.",
                );
              }}
            />
          ) : (
            <div className={ps.memberActions}>
              <button
                type="button"
                ref={(el) => {
                  triggers.current.role = el;
                }}
                className="w360-btn w360-btn-secondary"
                disabled={busy}
                onClick={() => setPanel("role")}
              >
                Change role{" "}
                <span className="w360-visually-hidden">for {m.email}</span>
              </button>
              {!m.password_set && (
                <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void resend()}>
                  Resend setup link{" "}
                  <span className="w360-visually-hidden">to {m.email}</span>
                </button>
              )}
              {m.password_set && (
                <button
                  type="button"
                  className="w360-btn w360-btn-secondary"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const result = await partnerConsoleApi.sendPasswordReset(partnerId, m.id);
                      if (!result.sent) throw new ApiError(409, { detail: "No link was sent: this account cannot use a reset link." });
                    }, "A password reset link was sent. Their current password keeps working until they use it.")
                  }
                >
                  Send password reset link{" "}
                  <span className="w360-visually-hidden">to {m.email}</span>
                </button>
              )}
              <button
                type="button"
                ref={(el) => {
                  triggers.current.remove = el;
                }}
                className="w360-btn w360-btn-secondary"
                disabled={busy}
                onClick={() => setPanel("remove")}
              >
                Remove{" "}
                <span className="w360-visually-hidden">user {m.email}</span>
              </button>
            </div>
          )}
        </td>
      )}
    </tr>
  );
}

function RolePanel({ member, busy, onCancel, onConfirm }: { member: PartnerMemberRow; busy: boolean; onCancel: () => void; onConfirm: (role: string) => void }) {
  const id = useId();
  const [role, setRole] = useState(member.role);
  return (
    <div role="group" aria-labelledby={`${id}-title`} className={ps.panel}>
      <p id={`${id}-title`}>
        <strong>Change role for {member.email}</strong>
      </p>
      <label className="w360-label" htmlFor={`${id}-role`}>
        New role for {member.email}
      </label>
      <select id={`${id}-role`} className="w360-input" autoFocus value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="admin">Administrator</option>
        <option value="viewer">Viewer (read-only)</option>
      </select>
      <p className={styles.muted}>It takes effect on their next request. An active partner must always keep one administrator.</p>
      <div className={styles.row}>
        <button type="button" className="w360-btn w360-btn-primary" disabled={busy || role === member.role} onClick={() => onConfirm(role)}>
          Confirm role change
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function RemovePanel({ member, busy, onCancel, onConfirm }: { member: PartnerMemberRow; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={`${id}-title`} className={ps.panel}>
      <p id={`${id}-title`}>
        <strong>Remove {member.email}?</strong>
      </p>
      <p id={`${id}-why`}>
        This ends this person&apos;s partner access immediately. It does not delete their Workeasy360 account, and anything they belong
        to outside this partner is untouched.
      </p>
      <div className={styles.row}>
        <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={onConfirm}>
          Yes, remove access
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" autoFocus aria-describedby={`${id}-why`} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// --- inviting -------------------------------------------------------------------------------------

type Timing = "now" | "on_activation";

function InviteForm({ partner, act, busy }: { partner: PartnerDetail; act: Act; busy: boolean }) {
  const titleId = useId();
  // Deferring only means something while the partner is a draft: the people
  // named then are emailed when it is first activated, and never otherwise.
  const draft = partner.status === "draft";
  const [email, setEmail] = useState("");
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [role, setRole] = useState("admin");
  const [timing, setTiming] = useState<Timing>("now");

  return (
    <form
      className={ps.inviteForm}
      aria-labelledby={titleId}
      onSubmit={(e) => {
        e.preventDefault();
        const extra: { first_name?: string; last_name?: string; send_invitation?: Timing } = {};
        if (first.trim()) extra.first_name = first.trim();
        if (last.trim()) extra.last_name = last.trim();
        if (draft) extra.send_invitation = timing;
        void act(
          async () => {
            await partnerConsoleApi.inviteAdministrator(partner.id, email.trim(), role, extra);
            setEmail("");
            setFirst("");
            setLast("");
          },
          draft && timing === "on_activation"
            ? "Added. The invitation will be emailed when this partner is activated."
            : "Added. If this person has no password yet, a link to choose one was emailed.",
        );
      }}
    >
      <h4 id={titleId} className={styles.cardTitle}>
        Invite a person
      </h4>
      <TextField label="Email address" type="email" value={email} onChange={setEmail} maxLength={254} autoComplete="off" required showRequired />
      <div className={ps.nameRow}>
        <TextField label="First name (optional)" value={first} onChange={setFirst} maxLength={120} autoComplete="off" />
        <TextField label="Last name (optional)" value={last} onChange={setLast} maxLength={120} autoComplete="off" />
      </div>
      <p className="w360-hint">
        Names are used only if this address has no Workeasy360 account yet; an existing account is attached as it is and never renamed.
      </p>
      <SelectField
        label="Role"
        value={role}
        onChange={setRole}
        options={[
          { value: "admin", label: "Administrator" },
          { value: "viewer", label: "Viewer (read-only)" },
        ]}
      />
      {draft ? (
        <Segmented<Timing>
          legend="Send the invitation"
          options={[
            { value: "now", label: "Now" },
            { value: "on_activation", label: "When the partner is activated" },
          ]}
          value={timing}
          onChange={setTiming}
          hint="The email contains a link to choose a password. Nobody is sent a password."
        />
      ) : (
        <p className={styles.muted}>A link to choose a password is emailed now, unless the person already has one. Nobody is sent a password.</p>
      )}
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !email.trim()}>
          Invite
        </button>
      </div>
    </form>
  );
}
