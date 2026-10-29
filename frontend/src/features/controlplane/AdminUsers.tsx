"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { USER_SEGMENTS, adminConsoleApi, type UserAction } from "@/lib/api/adminConsole";
import { useSession } from "@/lib/auth/useSession";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, MoreButton, ReasonAction, Status, Table, money, useAction, useLoad, usePaged, when } from "./ui";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";

const LIFECYCLE_LABELS: Record<string, string> = {
  explorer: "Explorer",
  trial: "Trial",
  customer: "Customer",
  unverified: "Unverified",
};

/**
 * Super Admin → Users (Phase 8 extension, Phase 9): every account on the
 * platform, segmented by what its organizations hold - Explorers (nothing
 * yet), Trial, Customers - and by account state (unverified, suspended =
 * temporarily locked out, deactivated). One identity each: a person moves
 * between segments as their organizations buy, never between tables. Never a
 * credential. Managing an account (``platform.staff_user.manage``)
 * reaches every organization the person belongs to at once, so each action
 * asks for an audited reason. Nobody is offered "Deactivate" on their own
 * account (the server refuses it too).
 */
/** `initialQuery`: a search arriving in the address (`/admin/users?q=...`, from
 * the console's search box). */
export function Users({ initialQuery = "" }: { initialQuery?: string } = {}) {
  const searchId = useId();
  const [draft, setDraft] = useState(initialQuery);
  // Applied on submit, not per keystroke: one bounded request per search.
  const [query, setQuery] = useState(initialQuery);
  const [segment, setSegment] = useState("");
  const paged = usePaged(
    (cursor) => adminConsoleApi.users({ q: query || undefined, segment: segment || undefined, cursor }),
    [query, segment],
  );

  return (
    <div className={cp.stack}>
      <div>
        <h1>Users</h1>
        <p className={cp.lead}>
          Every account on the platform, with their organizations and subscriptions. Subscribed people pay for a
          product; the free tier is everyone else - on a trial (Trial) or with no product yet (Explorers).
        </p>
      </div>
      <div className={cp.row} role="group" aria-label="Segment">
        {USER_SEGMENTS.map((s) => (
          <button
            key={s.key || "all"}
            type="button"
            className={segment === s.key ? "w360-btn w360-btn-primary" : "w360-btn w360-btn-secondary"}
            aria-pressed={segment === s.key}
            onClick={() => setSegment(s.key)}
          >
            {s.label}
          </button>
        ))}
      </div>
      <form
        className={cp.row}
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <label className="w360-label" htmlFor={searchId}>
          Search by name or email
        </label>
        <input id={searchId} className="w360-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={100} />
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>
      {paged.state === "loading" && <p aria-live="polite">Loading users…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to the user directory.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load users.</p>}
      {paged.state === "ready" && (
        <>
          <Table
            caption="User accounts"
            head={[
              "User",
              "Account",
              "Email verified",
              "Signed up",
              "Last sign-in",
              "Lifecycle",
              "Organizations",
              "Products",
              "Subscription",
            ]}
            isEmpty={paged.rows.length === 0}
            empty="No users match."
          >
            {paged.rows.map((u) => (
              <tr key={u.user_id}>
                <th scope="row">
                  <Link href={`/admin/users/${u.user_id}`}>{u.name || u.email}</Link>
                  {u.name && <div className={cp.muted}>{u.email}</div>}
                  {u.is_staff && <div className={cp.muted}>Staff</div>}
                </th>
                <td>
                  <Status value={u.status} />
                  {u.locked && <div className={cp.muted}>locked out</div>}
                </td>
                <td>{u.email_verified ? "Yes" : "No"}</td>
                <td>{when(u.created_at)}</td>
                <td>{u.last_sign_in_at ? when(u.last_sign_in_at) : "Never"}</td>
                <td>{LIFECYCLE_LABELS[u.lifecycle] ?? u.lifecycle}</td>
                <td>{u.organization_names.length ? u.organization_names.join(", ") : "—"}</td>
                <td>{u.products.length ? u.products.join(", ") : "—"}</td>
                <td>{u.subscription_status ? <Status value={u.subscription_status} /> : "—"}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

export function UserDetailScreen({ userId }: { userId: string }) {
  const { permissions } = useAdmin();
  const session = useSession();
  const [load, reload] = useLoad(() => adminConsoleApi.user(userId), [userId]);
  const { act, busy, feedback } = useAction(reload);
  const manage = can(permissions, "platform.staff_user.manage");
  // Until the session is known, "is this me?" is unknown - so Disable waits.
  const me = session.status === "ready" ? session.session.user_id : undefined;
  const isSelf = me === userId;
  const mayDisable = session.status === "ready" && !isSelf;

  const run = (action: UserAction, reason: string, success: string) =>
    act(() => adminConsoleApi.userAction(userId, action, reason), success);

  return (
    <div className={cp.stack}>
      <LoadGate load={load} what="this user">
        {(u) => (
          <>
            <div>
              <h1>
                {u.name || u.email} <Status value={u.status} />
              </h1>
              <p className={cp.muted}>
                {u.email} · created {when(u.created_at)} · last sign-in {u.last_sign_in_at ? when(u.last_sign_in_at) : "never"}
              </p>
              <p className={cp.muted}>
                {LIFECYCLE_LABELS[u.lifecycle] ?? u.lifecycle} · email {u.email_verified ? "verified" : "not verified"}
                {u.acquisition_source ? ` · joined by ${u.acquisition_source.replace(/_/g, " ")}` : ""}
              </p>
            </div>
            <ul className={`${styles.actions} ${styles.plainList}`} aria-label="Account state">
              <li>
                <span className={u.is_staff ? "w360-badge w360-badge-warning" : "w360-badge w360-badge-neutral"}>
                  {u.is_staff ? "Staff member" : "Not staff"}
                </span>
              </li>
              <li>
                <span className={u.locked ? "w360-badge w360-badge-danger" : "w360-badge w360-badge-neutral"}>
                  {u.locked ? "Locked (too many failed sign-ins)" : "Not locked"}
                </span>
              </li>
            </ul>
            {feedback}
            {manage && (
              <div className={cp.row}>
                <ReasonAction
                  label="Sign out everywhere"
                  busy={busy}
                  onConfirm={(reason) => run("sign-out", reason, "Every session of this account has ended.")}
                />
                {u.locked && (
                  <ReasonAction label="Unlock" busy={busy} onConfirm={(reason) => run("unlock", reason, "The account is unlocked.")} />
                )}
                {u.status === "disabled" ? (
                  <ReasonAction label="Reactivate" busy={busy} onConfirm={(reason) => run("enable", reason, "The account is active again.")} />
                ) : (
                  mayDisable && (
                    <ReasonAction
                      label="Deactivate"
                      busy={busy}
                      onConfirm={(reason) => run("disable", reason, "The account is deactivated and signed out everywhere.")}
                    />
                  )
                )}
                {isSelf && <p className={cp.muted}>This is your own account, so it can&apos;t be deactivated here.</p>}
              </div>
            )}

            <section className={`w360-card ${cp.card}`} aria-labelledby="user-orgs">
              <h2 id="user-orgs" className={cp.cardTitle}>
                Organizations
              </h2>
              {u.memberships.length === 0 ? (
                <p className={cp.muted}>Not a member of any organization.</p>
              ) : (
                <ul className={styles.plainList}>
                  {u.memberships.map((m) => (
                    <li key={m.organization_id}>
                      <Link href={`/admin/customers/${m.organization_id}`}>{m.name}</Link>{" "}
                      <span className={cp.muted}>({m.slug})</span>
                      {m.is_owner && <span className="w360-badge w360-badge-neutral"> Owner</span>}
                      <div className={cp.muted}>
                        {m.subscription_status ? `Subscription ${m.subscription_status.replace(/_/g, " ")}` : "No subscription"}
                        {m.products.length ? ` · ${m.products.join(", ")}` : ""}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {u.orders.length > 0 && (
              <section className={`w360-card ${cp.card}`} aria-labelledby="user-orders">
                <h2 id="user-orders" className={cp.cardTitle}>
                  Orders
                </h2>
                <Table caption="Recent orders of this person's organizations" head={["Order", "Item", "Amount", "Status", "Placed"]} isEmpty={false}>
                  {u.orders.map((o) => (
                    <tr key={o.order_id}>
                      <th scope="row">{o.number}</th>
                      <td>{o.description}</td>
                      <td className={cp.num}>{o.amount_due_minor ? money(o.amount_due_minor, o.currency) : "—"}</td>
                      <td>
                        <Status value={o.status.replace(/_/g, " ")} />
                      </td>
                      <td>{when(o.created_at)}</td>
                    </tr>
                  ))}
                </Table>
              </section>
            )}

            <section className={`w360-card ${cp.card}`} aria-labelledby="user-sessions">
              <h2 id="user-sessions" className={cp.cardTitle}>
                Sessions
              </h2>
              <Table
                caption="Signed-in sessions"
                head={["Signed in", "Last active", "Expires", "Network", "Device"]}
                isEmpty={u.sessions.length === 0}
                empty="No active sessions."
              >
                {u.sessions.map((s) => (
                  <tr key={s.session_id}>
                    <td>{when(s.signed_in_at)}</td>
                    <td>{when(s.last_active_at)}</td>
                    <td>{when(s.expires_at)}</td>
                    <td className={cp.mono}>{s.ip_prefix || "—"}</td>
                    <td>{s.device_label}</td>
                  </tr>
                ))}
              </Table>
            </section>

            <section className={`w360-card ${cp.card}`} aria-labelledby="user-activity">
              <h2 id="user-activity" className={cp.cardTitle}>
                Recent activity
              </h2>
              <Table
                caption="Recent account events"
                head={["When", "Event", "Detail", "Network", "Device"]}
                isEmpty={u.activity.length === 0}
                empty="No recent account events."
              >
                {u.activity.map((a, i) => (
                  <tr key={`${a.occurred_at}-${i}`}>
                    <td>{when(a.occurred_at)}</td>
                    <td>{a.kind.replace(/_/g, " ")}</td>
                    <td>{a.reason ? a.reason.replace(/_/g, " ") : "—"}</td>
                    <td className={cp.mono}>{a.ip_prefix || "—"}</td>
                    <td>{a.device_label || "—"}</td>
                  </tr>
                ))}
              </Table>
            </section>
          </>
        )}
      </LoadGate>
    </div>
  );
}
