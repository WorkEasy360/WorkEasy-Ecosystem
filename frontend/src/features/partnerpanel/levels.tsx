"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { NavIcon } from "@/components/sidebar/icons";
import { DialogHead, Modal, dialog } from "@/features/controlplane/Modal";
import { money, useLoad } from "@/features/controlplane/ui";
import { panelApi, type UpgradeState, type UpgradeTarget } from "@/lib/api/partnerPanel";
import { LEVEL_ORDER, WORKSPACES, type ExperienceKey } from "./experiences";
import { usePanel } from "./PanelKit";
import { upgradeHref } from "./upgrade";
import styles from "./PartnerPanel.module.css";

/**
 * The partner levels on the unified dashboard (2026-11-03): the level badge and
 * the persistent "Upgrade Plan" action in the top bar, the upgrade dialog a
 * locked menu entry opens, the "Your Partner Level" card, the level list and
 * the feature-access matrix.
 *
 * **Presentation only.** Which level a partner is at is the server's answer
 * (the workspace its grants open); which upgrades it may ask for, whether this
 * member may ask and what each costs is the upgrade API's answer. Opening the
 * dialog changes nothing - its button opens the existing upgrade request page.
 */

export const LEVEL_LEAD: Record<ExperienceKey, string> = {
  affiliate: "Promote Workeasy360 and earn commission on every sale.",
  reseller: "Sell our products to your own customers and earn higher margins.",
  white_label: "Run the platform under your own brand and domain.",
};

type Mark = "yes" | "no" | string;

/** The specification's "Feature Access by Partner Level" (section 3 and figure 1.0). */
export const FEATURE_MATRIX: { feature: string; icon: string; marks: Record<ExperienceKey, Mark> }[] = [
  { feature: "Dashboard & analytics", icon: "chart", marks: { affiliate: "Referral metrics", reseller: "Sales & margin", white_label: "Network KPIs" } },
  { feature: "Affiliate (referral & commission)", icon: "gift", marks: { affiliate: "yes", reseller: "yes", white_label: "yes" } },
  { feature: "Customer management", icon: "users", marks: { affiliate: "Referred only", reseller: "yes", white_label: "yes" } },
  { feature: "Product store & pricing", icon: "store", marks: { affiliate: "Browse", reseller: "Wholesale", white_label: "yes" } },
  { feature: "Sell & manage subscriptions", icon: "card", marks: { affiliate: "no", reseller: "yes", white_label: "yes" } },
  { feature: "Staff management", icon: "lock", marks: { affiliate: "no", reseller: "Up to 5 staff", white_label: "Unlimited" } },
  { feature: "Helpdesk & support", icon: "help", marks: { affiliate: "Tickets", reseller: "Customer desk", white_label: "Customer desk" } },
  { feature: "Branding & custom domain", icon: "palette", marks: { affiliate: "no", reseller: "no", white_label: "yes" } },
  { feature: "Downline partners", icon: "network", marks: { affiliate: "no", reseller: "yes", white_label: "yes" } },
];

/** What a level costs to start, in the server's words: Level 1 is free; a
 * higher one is the fee Super Admin set, or none. */
export function levelFee(level: ExperienceKey, state: UpgradeState | null): string {
  if (level === "affiliate") return "Free";
  const fee = state?.fees?.[level as UpgradeTarget];
  if (fee === undefined) return "";
  return fee ? `${money(fee.amount_minor, fee.currency)} one-time` : "No set-up fee";
}

function MarkCell({ mark }: { mark: Mark }) {
  if (mark === "yes") {
    return (
      <span className={styles.markYes}>
        <span aria-hidden="true">✓</span>
        <span className="w360-visually-hidden">Included</span>
      </span>
    );
  }
  if (mark === "no") {
    return (
      <span className={styles.markNo}>
        <span aria-hidden="true">–</span>
        <span className="w360-visually-hidden">Not included</span>
      </span>
    );
  }
  return <span className={styles.markText}>{mark}</span>;
}

/** The feature-access matrix, the current level's column marked. */
export function LevelMatrix({ current, caption = "Feature access by partner level" }: { current: ExperienceKey; caption?: string }) {
  return (
    <div className={styles.matrixWrap} role="region" aria-label={caption} tabIndex={0}>
      <table className={styles.matrix}>
        <caption className="w360-visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Feature</th>
            {LEVEL_ORDER.map((level) => (
              <th key={level} scope="col" aria-current={level === current ? "true" : undefined} data-current={level === current || undefined}>
                {WORKSPACES[level].label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {FEATURE_MATRIX.map((row) => (
            <tr key={row.feature}>
              <th scope="row">
                <span className={styles.matrixFeature}>
                  <NavIcon name={row.icon} />
                  {row.feature}
                </span>
              </th>
              {LEVEL_ORDER.map((level) => (
                <td key={level} data-current={level === current || undefined}>
                  <MarkCell mark={row.marks[level]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The three levels, the current one marked, each with what it costs. */
export function LevelList({ current, state }: { current: ExperienceKey; state: UpgradeState | null }) {
  return (
    <ol className={styles.levelList} aria-label="Partner levels">
      {LEVEL_ORDER.map((level) => {
        const def = WORKSPACES[level];
        const fee = levelFee(level, state);
        return (
          <li key={level} className={styles.levelItem} data-current={level === current || undefined} aria-current={level === current ? "true" : undefined}>
            <span className={styles.levelNumber}>Level {def.level.number}</span>
            <span className={styles.levelName}>{def.label}</span>
            <span className={styles.levelLead}>{LEVEL_LEAD[level]}</span>
            {fee && <span className={styles.levelFee}>{fee}</span>}
            {level === current && <span className="w360-badge w360-badge-success">Your level</span>}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The upgrade dialog: what the next levels add, what they cost and a way to ask.
 * `feature` names the locked entry that opened it, when one did.
 */
export function UpgradeDialog({
  current,
  feature,
  onClose,
}: {
  current: ExperienceKey;
  feature?: { label: string; pitch?: string; level: ExperienceKey };
  onClose: () => void;
}) {
  const { id, base } = usePanel();
  const titleId = useId();
  const [load] = useLoad(() => panelApi.upgrade(id), [id]);
  const state = load.status === "ready" ? load.data : null;
  const eligible = state?.eligible ?? [];
  const targets = LEVEL_ORDER.filter((l) => l !== "affiliate" && LEVEL_ORDER.indexOf(l) > LEVEL_ORDER.indexOf(current)) as UpgradeTarget[];
  return (
    <Modal labelledBy={titleId} onClose={onClose} wide>
      <DialogHead titleId={titleId} title={feature ? `Unlock ${feature.label}` : "Upgrade your partnership"} onClose={onClose} />
      <div className={dialog.body}>
        {feature && (
          <p>
            <strong>{feature.label}</strong> is part of the {WORKSPACES[feature.level].label} level
            {feature.pitch ? `: ${feature.pitch}` : "."}
          </p>
        )}
        <p className={styles.muted}>
          You are a {WORKSPACES[current].level.name} (Level {WORKSPACES[current].level.number}). Upgrading keeps your sign-in, your account and
          everything you have earned.
        </p>
        {load.status === "loading" && <p aria-live="polite">Loading the upgrade options…</p>}
        {state?.open && (
          <p role="status">
            Your request to become a {WORKSPACES[state.open.to_experience].label} partner is in progress.{" "}
            <Link href={upgradeHref(base, state.open.to_experience)} onClick={onClose}>
              View your request
            </Link>
          </p>
        )}
        {targets.length === 0 ? (
          <p>You are at the highest partner level.</p>
        ) : (
          <ul className={styles.cards}>
            {targets.map((target) => (
              <li key={target} className={styles.card}>
                <h3 className={styles.cardTitle}>
                  Level {WORKSPACES[target].level.number}: {WORKSPACES[target].label}
                </h3>
                <p>{LEVEL_LEAD[target]}</p>
                {levelFee(target, state) && <p className={styles.levelFee}>{levelFee(target, state)}</p>}
                {!state?.open && eligible.includes(target) && state?.can_request ? (
                  <p>
                    <Link className="w360-btn w360-btn-primary" href={upgradeHref(base, target)} onClick={onClose}>
                      Upgrade to {WORKSPACES[target].label}
                    </Link>
                  </p>
                ) : !state?.open && state && !state.can_request ? (
                  <p className={styles.muted}>A partner administrator can request this upgrade.</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <LevelMatrix current={current} />
      </div>
      <div className={dialog.foot}>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}

/** The top bar's level badge and the persistent "Upgrade Plan" action. */
export function LevelTools({ level }: { level: ExperienceKey }) {
  const [open, setOpen] = useState(false);
  const def = WORKSPACES[level];
  return (
    <div className={styles.levelTools}>
      <span className={styles.levelBadge}>
        <span className="w360-visually-hidden">Partner level: </span>
        <span aria-hidden="true">Partner Level:</span> <strong>{def.level.name}</strong>
      </span>
      {level !== "white_label" && (
        <button type="button" aria-label="Upgrade Plan" className={`w360-btn w360-btn-secondary ${styles.upgradeButton}`} onClick={() => setOpen(true)}>
          <span aria-hidden="true">↑</span>
          <span>
            Upgrade<span className={styles.upgradeWord}> Plan</span>
          </span>
        </button>
      )}
      {open && <UpgradeDialog current={level} onClose={() => setOpen(false)} />}
    </div>
  );
}
