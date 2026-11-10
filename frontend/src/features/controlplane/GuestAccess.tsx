"use client";

import { useId, useState } from "react";
import { adminApi, type GuestSessionRow, type GuestSessionStatus, type GuestStart } from "@/lib/api/controlPlane";
import { usePortals } from "@/lib/portalContext";
import { portalHref, type PortalOrigins } from "@/lib/portals";
import { Alert, Badge, Button, exactTime, formatDateTime, formatRelative, type Tone } from "./kit";
import { DialogHead, Modal, dialog } from "./Modal";
import { LoadGate, autoReason, describeError, useLoad } from "./ui";
import styles from "./ControlPlane.module.css";

/**
 * Login as Guest (ADR-062): a Super Admin opens one partner's own dashboard,
 * read-only, for a limited time, as themselves - never with the partner's
 * password or any secret of the partner's.
 *
 * The server decides everything: who may (`platform.staff_partner.guest`), which
 * partner, for how long, what the guest may read, and where the dashboard is.
 * This file only carries the browser there. In multi-portal mode the partner
 * portal is another host with its own session, so the answer carries a
 * one-time ticket that is posted there in a form body - never put in a URL.
 */

export const GUEST_PERMISSION = "platform.staff_partner.guest";

/** Whether a partner in `status` has a dashboard to look at (a draft has none
 * yet). The server refuses a draft too; this only says so beside the action. */
export const guestAvailable = (status: string) => status !== "draft";

/** Send the browser to the guest view the server opened. */
export function openGuestView(view: GuestStart, portals: PortalOrigins | null): void {
  if (view.entry) {
    // Only ever to this deployment's own partner portal, whatever the answer says.
    if (!portals || new URL(view.entry.action).origin !== new URL(portals.partner).origin) {
      throw new Error("Login as Guest could not be opened here. Reload the page and try again.");
    }
    const form = document.createElement("form");
    form.method = "POST";
    form.action = view.entry.action;
    form.hidden = true;
    const ticket = document.createElement("input");
    ticket.type = "hidden";
    ticket.name = "ticket";
    ticket.value = view.entry.ticket;
    form.appendChild(ticket);
    document.body.appendChild(form);
    form.submit();
    return;
  }
  window.location.assign(portalHref(portals, "partner", view.portal_path));
}

/** Start a guest view of `partnerId` and go there. `returnPath` is the Admin
 * page "Exit Guest Mode" comes back to (the server accepts partner pages only). */
export async function loginAsGuest(partnerId: string, where: string, returnPath: string, portals: PortalOrigins | null): Promise<void> {
  const view = await adminApi.startGuestView(partnerId, autoReason("Login as Guest", where), returnPath);
  openGuestView(view, portals);
}

/** The Admin page the browser was on, when it is one "Exit Guest Mode" may return to. */
export function currentReturnPath(): string {
  if (typeof window === "undefined") return "/admin/partners/all";
  return window.location.pathname;
}

/** What `?guest=` on an Admin page says happened to a Login as Guest that came back. */
export function guestOutcomeMessage(raw: string | null): string | null {
  if (raw === "expired") return "That guest login link was used or expired before it opened. Choose Login as Guest again.";
  if (raw === "denied") return "Login as Guest was refused: your access to it has changed. Ask a security administrator.";
  return null;
}

const STATUS_TEXT: Record<GuestSessionStatus, string> = {
  active: "Active",
  exited: "Exited",
  revoked: "Revoked",
  signed_out: "Signed out",
  replaced: "Replaced",
  expired: "Expired",
};

const STATUS_TONE: Record<GuestSessionStatus, Tone> = {
  active: "success",
  exited: "neutral",
  revoked: "danger",
  signed_out: "neutral",
  replaced: "neutral",
  expired: "neutral",
};

function who(person: GuestSessionRow["staff"]): string {
  if (!person) return "WorkEasy360";
  return person.name ? `${person.name} (${person.email})` : person.email;
}

/** Manage Guest Access: who opened this partner's portal as a guest, why, when,
 * and how each view ended; an active one can be revoked (it ends on that
 * guest's next request). A dialog over the page it was opened from. */
export function ManageGuestAccess({
  partnerId,
  partnerName,
  partnerStatus,
  canGuest,
  onClose,
}: {
  partnerId: string;
  partnerName: string;
  partnerStatus: string;
  canGuest: boolean;
  onClose: () => void;
}) {
  const titleId = useId();
  const portals = usePortals();
  const [load, reload] = useLoad(() => adminApi.guestSessions(partnerId), [partnerId]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function revoke(row: GuestSessionRow) {
    setBusy(row.id);
    setError(null);
    setNotice(null);
    try {
      await adminApi.revokeGuestSession(partnerId, row.id, autoReason("Revoke guest access", "Manage Guest Access"));
      setNotice(`Guest access of ${who(row.staff)} revoked. It ends on their next request.`);
      reload();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  }

  async function open() {
    setBusy("open");
    setError(null);
    try {
      await loginAsGuest(partnerId, "Manage Guest Access", currentReturnPath(), portals);
    } catch (err) {
      setError(describeError(err));
      setBusy(null);
    }
  }

  return (
    <Modal labelledBy={titleId} onClose={onClose} size="lg">
      <DialogHead titleId={titleId} title={`Guest access · ${partnerName}`} onClose={onClose} />
      <div className={dialog.body}>
        <p className={dialog.intro}>
          A guest view opens this partner&apos;s own dashboard read-only, as you, never with the partner&apos;s password. It ends by itself, when
          the guest exits or signs out, or when it is revoked here. Each one is recorded in the partner&apos;s activity.
        </p>
        {notice && <Alert tone="success" title={notice} />}
        {error && <Alert tone="danger" title={error} />}
        <LoadGate load={load} what="guest access">
          {(data) =>
            data.sessions.length === 0 ? (
              <p className={styles.muted}>Nobody has opened this partner&apos;s portal as a guest yet.</p>
            ) : (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <caption className="w360-visually-hidden">Guest views of {partnerName}, newest first</caption>
                  <thead>
                    <tr>
                      <th scope="col">Staff member</th>
                      <th scope="col">Reason</th>
                      <th scope="col">Started</th>
                      <th scope="col">Status</th>
                      <th scope="col">
                        <span className="w360-visually-hidden">Action</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sessions.map((row) => (
                      <tr key={row.id}>
                        <td>
                          {who(row.staff)}
                          {row.mine && <span className={styles.muted}> · you</span>}
                        </td>
                        <td>{row.reason}</td>
                        <td>
                          <span title={exactTime(row.started_at)}>{formatDateTime(row.started_at)}</span>
                        </td>
                        <td>
                          <Badge tone={STATUS_TONE[row.status]} dot>
                            {STATUS_TEXT[row.status]}
                          </Badge>{" "}
                          <span className={styles.muted}>
                            {row.status === "active"
                              ? `ends ${formatRelative(row.expires_at)}`
                              : row.status === "revoked" && row.ended_by
                                ? `by ${who(row.ended_by)}`
                                : row.ended_at
                                  ? formatRelative(row.ended_at)
                                  : ""}
                          </span>
                        </td>
                        <td>
                          {row.status === "active" && canGuest && (
                            <Button size="sm" variant="danger" loading={busy === row.id} disabled={busy !== null} onClick={() => void revoke(row)}>
                              Revoke
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </LoadGate>
      </div>
      <div className={dialog.foot}>
        <Button onClick={onClose}>Close</Button>
        {canGuest && guestAvailable(partnerStatus) && (
          <Button variant="primary" icon="login" loading={busy === "open"} disabled={busy !== null} onClick={() => void open()}>
            Login as Guest
          </Button>
        )}
      </div>
    </Modal>
  );
}
