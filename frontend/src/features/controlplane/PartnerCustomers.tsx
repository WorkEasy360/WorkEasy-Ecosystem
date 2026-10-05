"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { partnerConsoleApi, type OwnerActivation, type PartnerCustomerAdminRow } from "@/lib/api/partnerConsole";
import { MoreButton, Status, Table, useAction, usePaged, when } from "./ui";
import { can, useAdmin } from "./ControlShell";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * Partner customers (Phase 10c): one partner's, on its own page, and every
 * partner's, under Partners → Customers.
 *
 * The owner column is the **factual** state of the activation invitation, as
 * the server derived it from the row and the clock. A customer whose owner has
 * not claimed the account is never shown as onboarded: it says "Owner
 * activation pending". No token, no link and nothing that could redeem one is
 * ever in the data this screen receives.
 */

const ACTIVATION_TEXT: Record<OwnerActivation["activation"], string> = {
  pending: "Owner activation pending",
  activated: "Owner activated",
  expired: "Invitation expired",
  revoked: "Invitation revoked",
  declined: "Invitation declined",
  not_applicable: "Not applicable",
};

const ACTIVATION_BADGE: Record<OwnerActivation["activation"], string> = {
  pending: "w360-badge w360-badge-warning",
  activated: "w360-badge w360-badge-success",
  expired: "w360-badge w360-badge-danger",
  revoked: "w360-badge w360-badge-danger",
  declined: "w360-badge w360-badge-danger",
  not_applicable: "w360-badge w360-badge-neutral",
};

/** Text first; colour only reinforces it. */
export function ActivationBadge({ owner }: { owner: Pick<OwnerActivation, "activation"> }) {
  return <span className={ACTIVATION_BADGE[owner.activation]}>{ACTIVATION_TEXT[owner.activation]}</span>;
}

/**
 * Send a customer's owner a new activation link, or withdraw the current one.
 * Shown only where it can do something: a customer whose owner has not
 * activated yet, on a relationship that is still live. Both are dangerous
 * actions on the server, so a missing recent verification prompts inline
 * (``useAction``) and the same click then completes. The link itself is never
 * in what comes back - only the state - so there is nothing here to show.
 */
export function OwnerInvitationActions({
  activation,
  live,
  subject,
  resend,
  revoke,
  onDone,
}: {
  activation: OwnerActivation["activation"];
  live: boolean;
  /** The customer's name, so each button is unambiguous to a screen reader. */
  subject: string;
  resend: () => Promise<unknown>;
  revoke: () => Promise<unknown>;
  onDone: () => void;
}) {
  const { act, busy, feedback } = useAction(onDone);
  const canResend = live && activation !== "activated" && activation !== "not_applicable";
  const canWithdraw = live && activation === "pending";
  if (!canResend) return <>{feedback}</>;
  return (
    <div className={local.stackTight}>
      <div className={styles.row}>
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          disabled={busy}
          aria-label={`${activation === "pending" ? "Resend" : "Send a new"} activation link for ${subject}`}
          onClick={() => void act(resend, "A new activation link was emailed to the owner. The previous link no longer works.")}
        >
          {activation === "pending" ? "Resend link" : "Send new link"}
        </button>
        {canWithdraw && (
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            disabled={busy}
            aria-label={`Withdraw the activation link for ${subject}`}
            onClick={() => void act(revoke, "The activation link was withdrawn. The customer is unchanged; send a new link when ready.")}
          >
            Withdraw link
          </button>
        )}
      </div>
      {feedback}
    </div>
  );
}

export function CustomerTable({
  rows,
  showPartner = false,
  caption = "Customers",
  empty = "No customers match.",
  actions,
}: {
  rows: PartnerCustomerAdminRow[];
  showPartner?: boolean;
  caption?: string;
  empty?: string;
  /** An extra column (a row's own actions), when the page has any. */
  actions?: (row: PartnerCustomerAdminRow) => React.ReactNode;
}) {
  const head = [
    "Customer",
    ...(showPartner ? ["Partner"] : []),
    "Status",
    "Owner activation",
    "Products",
    "Subscription",
    "Relationship",
    ...(actions ? ["Actions"] : []),
  ];
  return (
    <Table caption={caption} head={head} isEmpty={rows.length === 0} empty={empty}>
      {rows.map((c) => {
        const ended = c.link_status === "ended";
        return (
          <tr key={c.link_id}>
            <th scope="row">
              <Link href={`/admin/customers/${c.organization_id}`}>{c.name}</Link>
              {c.owner.email && <div className={styles.muted}>{c.owner.email}</div>}
            </th>
            {showPartner && (
              <td>
                <Link href={`/admin/partners/${c.partner_id}`}>{c.partner_name}</Link>
              </td>
            )}
            <td>{ended ? <span className={styles.muted}>—</span> : <Status value={c.organization_status} />}</td>
            <td>
              <div className={local.stackTight}>
                <ActivationBadge owner={c.owner} />
                {c.owner.activation === "pending" && c.owner.expires_at && (
                  <span className={styles.muted}>Expires {when(c.owner.expires_at)}</span>
                )}
                {c.owner.activation === "activated" && c.owner.resolved_at && (
                  <span className={styles.muted}>{when(c.owner.resolved_at)}</span>
                )}
              </div>
            </td>
            <td>{ended ? "—" : c.products.join(", ") || "—"}</td>
            <td>{ended ? "—" : <Status value={c.subscription_status} />}</td>
            <td>
              <div className={local.stackTight}>
                <Status value={c.link_status} />
                <span className={styles.muted}>
                  {ended ? `ended ${when(c.ended_at)}` : `since ${when(c.started_at)}`}
                </span>
              </div>
            </td>
            {actions && <td>{actions(c)}</td>}
          </tr>
        );
      })}
    </Table>
  );
}

const ACTIVATION_FILTER = ["", "pending", "activated", "expired", "revoked", "declined", "not_applicable"];
const SUBSCRIPTION_FILTER = ["", "active", "trialing", "past_due", "grace", "suspended", "cancelled", "expired", "none"];

/** Partners → Customers: the customers of every partner. */
export function AllPartnerCustomers() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const params = useSearchParams();
  const searchId = useId();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const initialActivation = params.get("activation") ?? "";
  const [activation, setActivation] = useState(ACTIVATION_FILTER.includes(initialActivation) ? initialActivation : "");
  const initialSubscription = params.get("subscription") ?? "";
  const [subscription, setSubscription] = useState(SUBSCRIPTION_FILTER.includes(initialSubscription) ? initialSubscription : "");
  const paged = usePaged(
    (cursor) =>
      partnerConsoleApi.customers(undefined, {
        q: query || undefined,
        status: status || undefined,
        activation: activation || undefined,
        subscription: subscription || undefined,
        cursor,
      }),
    [query, status, activation, subscription],
  );
  return (
    <div className={styles.stack}>
      <h1>Partner customers</h1>
      <p className={styles.lead}>Customers with a partner relationship, and whether their owner has activated the account.</p>
      <form
        className={local.filters}
        role="search"
        aria-label="Filter partner customers"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <div>
          <label className="w360-label" htmlFor={searchId}>
            Search by customer name
          </label>
          <input id={searchId} className="w360-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={80} />
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-status`}>
            Relationship
          </label>
          <select id={`${searchId}-status`} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            <option value="live">Live (active or suspended)</option>
            <option value="active">Active</option>
            <option value="suspended">Suspended</option>
            <option value="ended">Ended</option>
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-act`}>
            Owner activation
          </label>
          <select id={`${searchId}-act`} className="w360-input" value={activation} onChange={(e) => setActivation(e.target.value)}>
            {ACTIVATION_FILTER.map((a) => (
              <option key={a} value={a}>
                {a === "" ? "Any" : ACTIVATION_TEXT[a as OwnerActivation["activation"]]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-sub`}>
            Subscription
          </label>
          <select id={`${searchId}-sub`} className="w360-input" value={subscription} onChange={(e) => setSubscription(e.target.value)}>
            {SUBSCRIPTION_FILTER.map((s) => (
              <option key={s} value={s}>
                {s === "" ? "Any" : s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>
      {paged.state === "loading" && <p aria-live="polite">Loading customers…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to partner customers.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load customers.</p>}
      {paged.state === "ready" && (
        <>
          <CustomerTable
            rows={paged.rows}
            showPartner
            caption="Partner customers"
            actions={manage ? (row) => <StaffOwnerActions row={row} onDone={paged.reload} /> : undefined}
          />
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

/** The owner-invitation actions for one row of a staff customer table. */
export function StaffOwnerActions({ row, onDone }: { row: PartnerCustomerAdminRow; onDone: () => void }) {
  return (
    <OwnerInvitationActions
      activation={row.owner.activation}
      live={row.link_status !== "ended"}
      subject={row.name}
      resend={() => partnerConsoleApi.resendOwnerInvitation(row.partner_id, row.organization_id)}
      revoke={() => partnerConsoleApi.revokeOwnerInvitation(row.partner_id, row.organization_id)}
      onDone={onDone}
    />
  );
}
