"use client";

import { invitationApi } from "@/lib/api/controlPlane";
import { useAction, useLoad, when } from "./ui";
import styles from "./ControlPlane.module.css";
import { usePortalRouter } from "@/lib/portalRouter";

/** Ownership invitations for the signed-in account - e.g. a customer a
 * partner created on their behalf. Accepting is the owner's own act; it is
 * never done for them. Renders nothing when there is nothing to accept. */
export function Invitations() {
  const router = usePortalRouter();
  const [load] = useLoad(() => invitationApi.list(), []);
  const { act, busy, feedback } = useAction(() => router.refresh());

  // Invitations are optional: while loading, or on any failure, show nothing.
  if (load.status !== "ready" || load.data.results.length === 0) return null;
  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby="invites-h">
      <h2 id="invites-h" className={styles.cardTitle}>
        Invitations
      </h2>
      {feedback}
      <ul className={styles.chain}>
        {load.data.results.map((i) => (
          <li key={i.organization_id} className={styles.chainItem}>
            <span>
              {i.organization_name} <span className={styles.muted}>· expires {when(i.expires_at)}</span>
            </span>
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  await invitationApi.accept(i.organization_id);
                  router.push("/organizations/select");
                  router.refresh();
                }, "Invitation accepted.")
              }
            >
              Accept ownership
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
