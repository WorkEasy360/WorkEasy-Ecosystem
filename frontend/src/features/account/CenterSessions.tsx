"use client";

import { useId, useState } from "react";
import { accountApi, accountCenterApi, type SignInRow, type SignOutEverywhere } from "@/lib/api/console";
import { LoadGate, MoreButton, Table, useAction, useLoad, usePaged, when } from "@/features/controlplane/ui";
import { Badge, Button, useConfirm } from "@/features/controlplane/kit";
import { Panel } from "@/features/console/consoleUi";
import { describeAccountEvent } from "@/features/console/MySecurityScreen";
import styles from "./Account.module.css";

/** Devices & Sessions -> Active Sessions: one row per WorkEasy360 sign-in (a device),
 * never one per portal. Signing one out ends it everywhere it was signed in, and tells
 * the products it opened. */
export function ActiveSessions() {
  const confirm = useConfirm();
  const [load, reload] = useLoad(() => accountCenterApi.sessions(), []);
  const { act, busy, feedback } = useAction(reload);
  const noteId = useId();
  const [everywhere, setEverywhere] = useState<SignOutEverywhere | null>(null);
  const [ending, setEnding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revoke = (s: SignInRow) =>
    void act(() => accountApi.revokeSession(s.session_id), `Signed out ${s.device_label}, on every host it was signed in to.`);
  return (
    <>
      {confirm.dialog}
      <Panel title="Where you're signed in">
        <p id={noteId} className={styles.muted}>
          Network is a coarse prefix, never your full address. A device signed in to User Admin and the Store from one sign-in is one row.
        </p>
        {feedback}
        <LoadGate load={load} what="your sessions">
          {(d) => {
            const others = d.sessions.filter((s) => !s.current);
            return (
              <>
                <Table
                  caption="Your sign-ins, most recently active first"
                  head={["Device", "Network", "Signed in", "Last activity", "Actions"]}
                  isEmpty={d.sessions.length === 0}
                  empty="No sessions to show."
                >
                  {d.sessions.map((s) => (
                    <tr key={s.session_id}>
                      <th scope="row">
                        {s.device_label}
                        {s.current && (
                          <span className={styles.marker}>
                            <Badge tone="success">This device</Badge>
                          </span>
                        )}
                      </th>
                      <td className={styles.mono}>{s.ip_prefix || "—"}</td>
                      <td>{when(s.signed_in_at)}</td>
                      <td>{when(s.last_active_at)}</td>
                      <td>
                        {s.current ? (
                          <span className={styles.muted}>Current sign-in</span>
                        ) : (
                          <Button
                            size="sm"
                            aria-label={`Sign out ${s.device_label}, signed in ${when(s.signed_in_at)}`}
                            aria-describedby={noteId}
                            disabled={busy}
                            onClick={() => revoke(s)}
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
                    <Button
                      icon="logout"
                      disabled={busy}
                      onClick={() =>
                        confirm.ask({
                          title: "Sign out every other device?",
                          message: "Every other device and browser is signed out at once. You stay signed in here.",
                          confirmLabel: "Sign out other devices",
                          danger: true,
                          onConfirm: () => void act(() => accountApi.revokeOtherSessions(), "Every other device has been signed out."),
                        })
                      }
                    >
                      Sign out all other devices
                    </Button>
                  </div>
                )}
              </>
            );
          }}
        </LoadGate>
      </Panel>

      <Panel title="Sign out everywhere">
        {everywhere ? (
          <div role="status">
            <p>
              You are signed out of WorkEasy360 everywhere ({everywhere.workeasy360.sessions_ended} ended).{" "}
              {everywhere.products.length === 0
                ? "No product needed to be told."
                : everywhere.complete
                  ? "Every product you were signed in to confirmed it."
                  : "Not every product has confirmed it yet:"}
            </p>
            {everywhere.products.length > 0 && (
              <ul>
                {everywhere.products.map((p) => (
                  <li key={p.product_code}>
                    <strong>{p.name || p.product_code}</strong>: {p.status === "delivered" ? "signed out" : p.status === "pending" ? "still being told" : "could not be told"}
                  </li>
                ))}
              </ul>
            )}
            <a className="w360-btn w360-btn-primary" href="/login">
              Sign in again
            </a>
          </div>
        ) : (
          <>
            <p className={styles.muted}>Ends every WorkEasy360 session of this account, this device included, and tells each product that supports it.</p>
            {error && <p role="alert">{error}</p>}
            <div>
              <Button
                variant="danger"
                icon="logout"
                loading={ending}
                onClick={() =>
                  confirm.ask({
                    title: "Sign out everywhere?",
                    message: "Every browser and device is signed out of WorkEasy360 at once, this one included.",
                    confirmLabel: "Sign out everywhere",
                    danger: true,
                    onConfirm: () => {
                      setEnding(true);
                      setError(null);
                      accountApi
                        .signOutEverywhere()
                        .then(setEverywhere)
                        .catch(() => setError("Could not sign out everywhere. Nothing was changed; try again."))
                        .finally(() => setEnding(false));
                    },
                  })
                }
              >
                Sign out everywhere
              </Button>
            </div>
          </>
        )}
      </Panel>
    </>
  );
}

/** Devices & Sessions -> Login History: every sign-in, refused or kept, to WorkEasy360
 * and to products. */
export function LoginHistory() {
  const [scope, setScope] = useState<"sign_ins" | "all">("sign_ins");
  const page = usePaged((cursor) => accountCenterApi.history(cursor, scope), [scope]);
  return (
    <Panel
      title="Login History"
      description="Successful and failed sign-ins, and the products you signed in to with WorkEasy360. Kept for about 13 months."
    >
      <div role="group" aria-label="Show" className={styles.filters}>
        <button type="button" className="w360-btn w360-btn-secondary" aria-pressed={scope === "sign_ins"} onClick={() => setScope("sign_ins")}>
          Sign-ins
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" aria-pressed={scope === "all"} onClick={() => setScope("all")}>
          All security events
        </button>
      </div>
      <div className={styles.section}>
        {page.state === "loading" ? (
          <p className={styles.muted}>Loading your history…</p>
        ) : page.state !== "ready" ? (
          <p role="alert">Your history could not be loaded. Try again.</p>
        ) : (
          <>
            <Table
              caption="Newest first"
              head={["When", "What happened", "Product", "Device", "Network"]}
              isEmpty={page.rows.length === 0}
              empty="Nothing yet."
            >
              {page.rows.map((e, i) => (
                <tr key={`${e.occurred_at}-${i}`}>
                  <td className={styles.nowrap}>{when(e.occurred_at)}</td>
                  <th scope="row">{describeAccountEvent(e)}</th>
                  <td>{e.product_name ?? "—"}</td>
                  <td>{e.device_label || "—"}</td>
                  <td className={styles.mono}>{e.ip_prefix || "—"}</td>
                </tr>
              ))}
            </Table>
            <MoreButton hasMore={page.hasMore} onMore={() => void page.more()} />
          </>
        )}
      </div>
    </Panel>
  );
}
