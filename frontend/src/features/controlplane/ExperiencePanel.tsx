"use client";

import { useId, useMemo, useState } from "react";
import type { PartnerDetail } from "@/lib/api/controlPlane";
import { partnerConsoleApi } from "@/lib/api/partnerConsole";
import { useAction, useLoad } from "./ui";
import { Segmented } from "./widgets";
import styles from "./ControlPlane.module.css";
import wizard from "./AddReseller.module.css";

/**
 * A partner's experience, and changing it later (ADR-047).
 *
 * The experience is what the partner's capabilities earn - Reseller, White-label,
 * Affiliate - plus the one the portal opens first. Changing it here is the explicit
 * capability transition: **adding** gives the partner that experience's capabilities
 * (and, for a selling one, products to sell) and removes nothing; **retiring**
 * withdraws only what no remaining experience needs, never the money views; the
 * **primary** choice only selects among the workspaces the grants already open.
 * Referrals, commissions, payouts and customers are untouched by all three, and
 * each is audited with the reason typed here.
 */

type Action = "default" | "grant" | "retire";

const LABEL: Record<string, string> = { reseller: "Reseller", white_label: "White-label", affiliate: "Affiliate" };

export function ExperiencePanel({ partner, manage, onChanged }: { partner: PartnerDetail; manage: boolean; onChanged: () => void }) {
  const id = useId();
  const [catalog] = useLoad(() => partnerConsoleApi.capabilityCatalog(), []);
  const [products] = useLoad(() => partnerConsoleApi.products().catch(() => ({ results: [] })), []);
  const [action, setAction] = useState<Action>("grant");
  const [experience, setExperience] = useState("");
  const [reason, setReason] = useState("");
  const [makePrimary, setMakePrimary] = useState(true);
  const [chosen, setChosen] = useState<string[]>([]);
  // Only a change that went through clears the form; a refusal keeps what was typed.
  const { act, busy, feedback } = useAction(() => {
    setReason("");
    setChosen([]);
    setExperience("");
    onChanged();
  });

  // An answer from before workspaces existed (a release overlapping this one) has
  // none: the panel then offers only what it can offer safely.
  const workspaces = useMemo(() => partner.workspaces ?? [], [partner.workspaces]);
  const held = useMemo(() => workspaces.map((w) => w.key), [workspaces]);
  const experiences = catalog.status === "ready" ? (catalog.data.experiences ?? []) : [];
  // White-label contains the reseller workspace, so a white-label partner already
  // holds everything Reseller would add.
  const addable = experiences.filter((e) => !held.includes(e.key) && !(e.key === "reseller" && held.includes("white_label")));
  const options =
    action === "grant" ? addable.map((e) => ({ key: e.key, label: e.label })) : workspaces.map((w) => ({ key: w.key, label: w.label }));
  const selected = experiences.find((e) => e.key === experience);
  const sells = action === "grant" && Boolean(selected?.sells);
  const offered = products.status === "ready" ? products.data.results.filter((p) => p.status === "available") : [];
  const canRetire = held.length > 1;
  const ended = partner.status === "terminated" || !manage;

  const valid = Boolean(experience) && reason.trim().length >= 10 && (action !== "retire" || canRetire);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid || busy) return;
    const why = reason.trim();
    const run = () =>
      partnerConsoleApi.changeExperience(partner.id, {
        action,
        experience,
        reason: why,
        ...(action === "grant" ? { make_default: makePrimary, products: sells ? chosen : [] } : {}),
      });
    void act(run, SUCCESS[action]);
  }

  return (
    <section className={wizard.section} aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className={styles.cardTitle}>
        Experience
      </h2>
      <p>
        Primary experience: <strong>{partner.landing ? (LABEL[partner.landing] ?? partner.landing) : "none yet"}</strong>
        {workspaces.length > 1 && (
          <span className={styles.muted}> · workspaces: {workspaces.map((w) => w.label).join(", ")}</span>
        )}
      </p>
      <p className={styles.muted}>
        The experience decides which workspace the partner lands in. What it may do always comes from its capabilities, listed on the
        Users &amp; Permissions tab.
      </p>
      {feedback}
      {!ended && (
        <form onSubmit={submit} className={styles.form}>
          <Segmented<Action>
            legend="Change"
            options={[
              { value: "grant", label: "Add an experience" },
              { value: "default", label: "Set the primary experience", disabled: workspaces.length < 2 },
              { value: "retire", label: "Retire an experience", disabled: !canRetire },
            ]}
            value={action}
            onChange={(v) => {
              setAction(v);
              setExperience("");
            }}
            hint={
              action === "grant"
                ? "Gives the partner the capabilities of that experience. Nothing it holds is removed, and its history is untouched."
                : action === "retire"
                  ? "Withdraws only what no remaining experience needs. Its commission, referral and payout history stays, and so does its view of the money it is owed."
                  : "Chooses which of its workspaces the partner opens first."
            }
          />
          <div className={styles.field}>
            <label className="w360-label" htmlFor={`${id}-exp`}>
              Experience
            </label>
            <select id={`${id}-exp`} className="w360-input" value={experience} onChange={(e) => setExperience(e.target.value)}>
              <option value="">Choose…</option>
              {options.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
            {action === "grant" && addable.length === 0 && catalog.status === "ready" && (
              <p className={styles.muted}>This partner already holds every experience.</p>
            )}
          </div>
          {action === "grant" && (
            <label>
              <input type="checkbox" checked={makePrimary} onChange={(e) => setMakePrimary(e.target.checked)} /> Make it the primary experience
            </label>
          )}
          {sells && (
            <fieldset className={wizard.section}>
              <legend className="w360-label">Products it may sell (optional)</legend>
              <ul className={wizard.plain}>
                {offered.map((p) => (
                  <li key={p.code}>
                    <label>
                      <input
                        type="checkbox"
                        checked={chosen.includes(p.code)}
                        onChange={(e) => setChosen(e.target.checked ? [...chosen, p.code] : chosen.filter((c) => c !== p.code))}
                      />{" "}
                      {p.name}
                    </label>
                  </li>
                ))}
              </ul>
            </fieldset>
          )}
          <div className={styles.field}>
            <label className="w360-label" htmlFor={`${id}-why`}>
              Why the experience is changing (recorded)
            </label>
            <input
              id={`${id}-why`}
              className="w360-input"
              required
              minLength={10}
              maxLength={200}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !valid}>
              Apply
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

const SUCCESS: Record<Action, string> = {
  grant: "Experience added. The partner's existing capabilities and history are unchanged.",
  retire: "Experience retired. Its history and the partner's view of its money are unchanged.",
  default: "Primary experience changed.",
};
