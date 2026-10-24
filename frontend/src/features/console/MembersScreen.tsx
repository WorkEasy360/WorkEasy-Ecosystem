"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { ASSIGNABLE_ROLES, consoleApi, type MemberRow } from "@/lib/api/console";
import { MoreButton, Status, Table, useAction, usePaged, when } from "@/features/controlplane/ui";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { ConfirmAction, PageHeader, PagedState, day, roleLabel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "invited", label: "Invited" },
  { value: "suspended", label: "Suspended" },
];

/**
 * The member directory, and what an administrator may do to each member.
 *
 * Actions are offered only where the backend would allow them - never on the
 * owner (ownership moves by transfer) and never on yourself (the same mistake
 * made on oneself has no one left to undo it) - and every one is authorized
 * again by the server. Role changes and signing a member out are dangerous
 * so they run through `useAction`.
 */
export function MembersScreen() {
  const { slug, access, can } = useOrgAccess();
  const searchId = useId();
  const statusId = useId();
  const [draft, setDraft] = useState("");
  // Applied on submit, not per keystroke: one bounded request per search.
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const paged = usePaged(
    (cursor) => consoleApi.members(slug, { status: status || undefined, q: query || undefined, cursor }),
    [slug, status, query],
  );
  const { act, busy, feedback } = useAction(paged.reload);
  const me = access?.membership.membership_id;
  const orgName = access?.organization.display_name ?? "this organization";

  return (
    <div className={styles.page}>
      <PageHeader
        title="Members"
        lead="Everyone who belongs to this organization, their role and when they last signed in."
        actions={
          can("platform.member.invite") ? (
            <Link className="w360-btn w360-btn-primary" href={`/o/${slug}/settings/invitations`}>
              Invitations
            </Link>
          ) : undefined
        }
      />
      <form
        className={local.toolbar}
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <div className={cp.field}>
          <label className="w360-label" htmlFor={statusId}>
            Status
          </label>
          <select id={statusId} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
        <div className={cp.field}>
          <label className="w360-label" htmlFor={searchId}>
            Search by name or email
          </label>
          <input
            id={searchId}
            className="w360-input"
            type="search"
            maxLength={100}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </div>
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>
      {feedback}
      <PagedState state={paged.state} what="members" />
      {paged.state === "ready" && (
        <>
          <Table
            caption="Organization members"
            head={["Member", "Roles", "Status", "Last sign-in", "Joined", "Actions"]}
            isEmpty={paged.rows.length === 0}
            empty={query || status ? "No members match." : "No members yet."}
          >
            {paged.rows.map((m) => (
              <tr key={m.membership_id}>
                <th scope="row">
                  {m.name || m.email}
                  {m.name && <div className={cp.muted}>{m.email}</div>}
                </th>
                <td>{m.roles.length ? m.roles.map(roleLabel).join(", ") : "—"}</td>
                <td>
                  <Status value={m.status} />
                </td>
                <td>{m.last_sign_in_at ? when(m.last_sign_in_at) : "Never"}</td>
                <td>{day(m.joined_at)}</td>
                <td>
                  {m.is_owner || m.membership_id === me ? (
                    <span className={cp.muted}>{m.is_owner ? "Owner" : "You"}</span>
                  ) : (
                    <MemberActions
                      member={m}
                      orgName={orgName}
                      busy={busy}
                      can={can}
                      run={(action, success) => void act(action, success)}
                      slug={slug}
                    />
                  )}
                </td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

function MemberActions({
  member,
  orgName,
  busy,
  can,
  run,
  slug,
}: {
  member: MemberRow;
  orgName: string;
  busy: boolean;
  can: (permission: string) => boolean;
  run: (action: () => Promise<unknown>, success: string) => void;
  slug: string;
}) {
  const who = member.name || member.email;
  const id = member.membership_id;
  const hasRole = member.status === "active" || member.status === "suspended";
  const current = member.roles.find((r) => ASSIGNABLE_ROLES.some((a) => a.key === r)) ?? "member";

  return (
    <div className={local.actions}>
      {can("platform.role.assign") && hasRole && (
        <RoleForm
          key={current}
          who={who}
          current={current}
          busy={busy}
          onSave={(role) => run(() => consoleApi.changeRole(slug, id, role), `${who} is now ${roleLabel(role)}.`)}
        />
      )}
      {can("platform.member.update") && member.status === "active" && (
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          aria-label={`Suspend ${who}`}
          disabled={busy}
          onClick={() => run(() => consoleApi.suspendMember(slug, id), `${who} is suspended. Their seats were released.`)}
        >
          Suspend
        </button>
      )}
      {can("platform.member.update") && member.status === "suspended" && (
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          aria-label={`Reactivate ${who}`}
          disabled={busy}
          onClick={() =>
            run(() => consoleApi.reactivateMember(slug, id), `${who} is active again. Assign their seats again if they need them.`)
          }
        >
          Reactivate
        </button>
      )}
      {can("platform.member.sign_out") && member.status === "active" && (
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          aria-label={`Sign out of this organization: ${who}`}
          disabled={busy}
          onClick={() => run(() => consoleApi.signOutMember(slug, id), `${who} has been signed out of this organization.`)}
        >
          Sign out of this organization
        </button>
      )}
      {can("platform.member.remove") && (
        <ConfirmAction
          label="Remove"
          accessibleLabel={`Remove ${who}`}
          prompt={`Remove ${who} from ${orgName}? They lose access at once and their seats are released.`}
          confirmLabel="Remove member"
          busy={busy}
          onConfirm={() => run(() => consoleApi.removeMember(slug, id), `${who} was removed from the organization.`)}
        />
      )}
    </div>
  );
}

function RoleForm({
  who,
  current,
  busy,
  onSave,
}: {
  who: string;
  current: string;
  busy: boolean;
  onSave: (role: string) => void;
}) {
  const id = useId();
  const [role, setRole] = useState(current);
  return (
    <form
      className={local.inlineForm}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(role);
      }}
    >
      <label className="w360-visually-hidden" htmlFor={id}>
        Role for {who}
      </label>
      <select id={id} className="w360-input" value={role} onChange={(e) => setRole(e.target.value)}>
        {ASSIGNABLE_ROLES.map((r) => (
          <option key={r.key} value={r.key}>
            {r.label}
          </option>
        ))}
      </select>
      <button
        type="submit"
        className="w360-btn w360-btn-secondary"
        aria-label={`Save role for ${who}`}
        disabled={busy || role === current}
      >
        Save role
      </button>
    </form>
  );
}
