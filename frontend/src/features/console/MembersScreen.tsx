"use client";

import { useId, useState } from "react";
import { ASSIGNABLE_ROLES, consoleApi, type MemberRow } from "@/lib/api/console";
import { MoreButton, Status, Table, useAction, usePaged, when } from "@/features/controlplane/ui";
import { DialogHead, FormPopup, Modal, dialog } from "@/features/controlplane/Modal";
import { Button, Card, FilterBar, Page, SearchInput, StatusChips } from "@/features/controlplane/kit";
import cp from "@/features/controlplane/ControlPlane.module.css";
import local from "./ConsoleScreens.module.css";
import { ConfirmAction, PageHeader, PagedState, ROLE_DESCRIPTIONS, day, roleLabel } from "./consoleUi";
import { InviteMemberPopup } from "./InvitationsScreen";
import { useOrgAccess } from "./OrgAccess";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "invited", label: "Invited" },
  { value: "suspended", label: "Suspended" },
];

/** "View permissions": what each role may do, as the access-control model defines it. */
function RolesDialog({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  return (
    <Modal labelledBy={titleId} onClose={onClose} size="md">
      <DialogHead titleId={titleId} title="Roles and permissions" onClose={onClose} />
      <div className={dialog.body}>
        <dl className={local.details}>
          {Object.entries(ROLE_DESCRIPTIONS).map(([key, text]) => (
            <div key={key}>
              <dt>{roleLabel(key)}</dt>
              <dd>{text}</dd>
            </div>
          ))}
        </dl>
        <p className={local.muted}>
          Paying invoices, topping up wallets and buying plans need the Owner or a Billing Manager. Everyone else sees
          &quot;Contact Organization Admin&quot; instead. Every action is checked again by WorkEasy360.
        </p>
      </div>
      <div className={dialog.foot}>
        <Button onClick={onClose}>Close</Button>
      </div>
    </Modal>
  );
}

/**
 * The member directory, and what an administrator may do to each member.
 *
 * Actions are offered only where the backend would allow them - never on the
 * owner (ownership moves by transfer) and never on yourself (the same mistake
 * made on oneself has no one left to undo it) - and every one is authorized
 * again by the server. Signing a member out is dangerous so it runs through
 * `useAction`; a role is changed in a small popup from the row.
 */
export function MembersScreen() {
  const { slug, access, can } = useOrgAccess();
  // Applied as the search settles or on Enter: one bounded request per search.
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [roles, setRoles] = useState(false);
  const paged = usePaged(
    (cursor) => consoleApi.members(slug, { status: status || undefined, q: query || undefined, cursor }),
    [slug, status, query],
  );
  const { act, busy, feedback } = useAction(paged.reload);
  const me = access?.membership.membership_id;
  const orgName = access?.organization.display_name ?? "this organization";

  return (
    <Page>
      {/* Members and Invitations are tabs of Organization & Team (2026-11-03): no header link between them. */}
      <PageHeader
        title="Members"
        lead="Everyone who belongs to this organization, their role and when they last signed in."
        actions={
          <Button icon="key" aria-haspopup="dialog" onClick={() => setRoles(true)}>
            View permissions
          </Button>
        }
        primary={can("platform.member.invite") ? <InviteMemberPopup slug={slug} label="Invite Member" /> : undefined}
      />
      {roles && <RolesDialog onClose={() => setRoles(false)} />}
      <Card title="Team members">
        <div className={local.section}>
          <FilterBar
            label="Filter members"
            onClear={
              query || status
                ? () => {
                    setQuery("");
                    setStatus("");
                  }
                : undefined
            }
          >
            <SearchInput label="Search by name or email" value={query} onSearch={setQuery} />
            <StatusChips label="Status" value={status} onChange={setStatus} chips={STATUS_FILTERS} />
          </FilterBar>
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
                          reload={paged.reload}
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
      </Card>
    </Page>
  );
}

function MemberActions({
  member,
  orgName,
  busy,
  can,
  run,
  reload,
  slug,
}: {
  member: MemberRow;
  orgName: string;
  busy: boolean;
  can: (permission: string) => boolean;
  run: (action: () => Promise<unknown>, success: string) => void;
  reload: () => void;
  slug: string;
}) {
  const who = member.name || member.email;
  const id = member.membership_id;
  const hasRole = member.status === "active" || member.status === "suspended";
  const current = member.roles.find((r) => ASSIGNABLE_ROLES.some((a) => a.key === r)) ?? "member";

  return (
    <div className={local.actions}>
      {can("platform.role.assign") && hasRole && (
        <RolePopup
          who={who}
          current={current}
          busy={busy}
          onSave={async (role) => {
            await consoleApi.changeRole(slug, id, role);
            return `${who} is now ${roleLabel(role)}.`;
          }}
          onDone={reload}
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

/** A member's role, changed in a small popup from the row (2026-11-05). */
function RolePopup({
  who,
  current,
  busy,
  onSave,
  onDone,
}: {
  who: string;
  current: string;
  busy: boolean;
  onSave: (role: string) => Promise<string>;
  onDone: () => void;
}) {
  const id = useId();
  const [role, setRole] = useState(current);
  return (
    <FormPopup
      label="Change role"
      buttonClassName="w360-btn w360-btn-secondary"
      buttonAriaLabel={`Change role for ${who}`}
      disabled={busy}
      title={`Change role for ${who}`}
      size="sm"
      submitLabel="Save role"
      canSubmit={role !== current}
      onOpen={() => setRole(current)}
      onSubmit={() => onSave(role)}
      onDone={onDone}
    >
      <div className={cp.field}>
        <label className="w360-label" htmlFor={id}>
          Role for {who}
        </label>
        <select id={id} className="w360-input" value={role} onChange={(e) => setRole(e.target.value)}>
          {ASSIGNABLE_ROLES.map((r) => (
            <option key={r.key} value={r.key}>
              {r.label}
            </option>
          ))}
        </select>
      </div>
    </FormPopup>
  );
}
