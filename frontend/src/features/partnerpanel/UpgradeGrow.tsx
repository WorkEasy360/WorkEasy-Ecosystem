"use client";

import Link from "next/link";
import { useId } from "react";
import { useLoad } from "@/features/controlplane/ui";
import { panelApi } from "@/lib/api/partnerPanel";
import type { ExperienceKey } from "./experiences";
import { workspaceHome } from "./experiences";
import { usePanel } from "./PanelKit";
import { STATUS_BADGE, STATUS_LABEL, TARGETS, TARGET_LABEL, TARGET_LEAD, growView, upgradeHref } from "./upgrade";
import styles from "./PartnerPanel.module.css";

/**
 * "Grow Your Partnership" - the one place a dashboard offers an upgrade (ADR-049).
 *
 * **It is a link, never a grant.** The buttons open the upgrade page on the same
 * account; opening it asks nothing and changes nothing. Which cards are drawn is the
 * server's `eligible` list, so an Affiliate is offered Reseller and White-label, a
 * Reseller is offered White-label only, and White-label is offered nothing - and the
 * section then draws nothing at all. A failed or forbidden read draws nothing too: an
 * offer must never get in the way of the dashboard it sits on.
 */
export function UpgradeGrow({ workspace }: { workspace: ExperienceKey }) {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.upgrade(id), [id]);
  const heading = useId();
  if (load.status !== "ready") return null;
  const view = growView(load.data, workspace);
  if (view.kind === "none") return null;

  return (
    <section className={styles.grow} aria-labelledby={heading}>
      <div>
        <h2 id={heading}>Grow Your Partnership</h2>
        <p className={styles.muted}>Upgrade your Workeasy360 partnership. Same sign-in, same account - nothing you have earned is lost.</p>
      </div>

      {view.kind === "open" && (
        <div className={styles.card}>
          <h3 className={styles.cardTitle}>{TARGET_LABEL[view.request.to_experience]} upgrade</h3>
          <p>
            <span className={STATUS_BADGE[view.request.status]}>{STATUS_LABEL[view.request.status]}</span>
          </p>
          {view.request.status === "profile_required" && view.request.info_request_note && (
            <p role="note">Workeasy360 asks: {view.request.info_request_note}</p>
          )}
          <p>
            <Link className="w360-btn w360-btn-primary" href={upgradeHref(base, view.request.to_experience)}>
              {view.request.editable ? "Continue your request" : "View your request"}
            </Link>
          </p>
        </div>
      )}

      {view.kind === "cards" && view.note?.kind === "approved" && (
        <p role="status">
          Your {TARGET_LABEL[view.note.target]} upgrade was approved.{" "}
          <Link href={workspaceHome(view.note.target)}>Open your {TARGET_LABEL[view.note.target]} workspace</Link>
        </p>
      )}
      {view.kind === "cards" && view.note?.kind === "rejected" && (
        <p role="status">
          Your {TARGET_LABEL[view.note.target]} request was not approved{view.note.text ? `: ${view.note.text}` : "."} You can ask again.
        </p>
      )}

      {view.kind === "cards" && view.targets.length > 0 && (
        <ul className={styles.cards}>
          {TARGETS.filter((t) => view.targets.includes(t)).map((target) => (
            <li key={target} className={styles.card}>
              <h3 className={styles.cardTitle}>{TARGET_LABEL[target]}</h3>
              <p>{TARGET_LEAD[target]}</p>
              {view.canAct ? (
                <p>
                  <Link className="w360-btn w360-btn-primary" href={upgradeHref(base, target)}>
                    Upgrade to {TARGET_LABEL[target]}
                  </Link>
                </p>
              ) : (
                <p className={styles.muted}>A partner administrator can request this upgrade.</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
