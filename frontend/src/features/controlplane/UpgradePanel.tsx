"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import {
  partnerConsoleApi,
  type PartnerUpgrades,
  type UpgradeAction,
  type UpgradeProposal,
  type UpgradeRequestRow,
} from "@/lib/api/partnerConsole";
import { LoadGate, Status, money, useAction, when, type Load } from "./ui";
import { Segmented, TextAreaField, TextField } from "./widgets";
import styles from "./ControlPlane.module.css";
import local from "./UpgradePanel.module.css";

/**
 * A partner's upgrade standing and its review (ADR-049).
 *
 * An Affiliate that wants to resell, or a Reseller that wants its own brand, asks
 * from its own dashboard; this is where staff see the request and decide it. The
 * panel shows the partner's level, where the request stands, what the partner
 * proposed (as plain text - nothing it typed is interpreted) and what the server
 * says is still missing, and offers three decisions: approve, ask for more, reject.
 *
 * **The browser asks; the server decides.** A decision body carries a choice, a
 * note, the product codes staff ticked and a commercial reference - never a
 * capability, a price or an amount. Approving grants the approved bundle on the
 * partner's existing account only if every server rule passes (active partner,
 * complete profile for everything it will hold); a refusal is shown in the
 * server's own words, with the list of what is missing, and nothing changed.
 * Set-up fees and other onboarding prices are not handled here at all.
 */

const LEVEL: Record<string, string> = { reseller: "Reseller", white_label: "White-label", affiliate: "Affiliate" };

/** What the server calls a missing item, in words a person reads. */
const MISSING: Record<string, string> = {
  business: "Business details",
  capabilities: "Partner type",
  products: "Products",
  pricing: "Pricing",
  commission: "Commission",
  brand: "Brand",
  domain: "Custom domain",
  referral: "Referral program",
  administrator: "Partner administrator",
};

const level = (key: string | null | undefined): string => (key ? (LEVEL[key] ?? key) : "—");

/** The state shown for the partner: the open request's, else how the last
 * decided one ended; a withdrawn request leaves nothing to show. */
function shownStatus(data: PartnerUpgrades): { status: string; row: UpgradeRequestRow | null } {
  if (data.open) return { status: data.open.status, row: data.open };
  const last = data.history[0];
  if (last && (last.status === "approved" || last.status === "rejected")) return { status: last.status, row: last };
  return { status: "none", row: null };
}

type Tab = "profile" | "products" | "commission";
const POINTERS: { tab: Tab; label: string }[] = [
  { tab: "profile", label: "Profile" },
  { tab: "products", label: "Products" },
  { tab: "commission", label: "Commission" },
];

// --- the header line --------------------------------------------------------------------------

/** One line above the partner's tabs while a request is open, so a pending
 * upgrade is not missed on the Overview. Nothing when there is none. */
export function UpgradeNotice({ load, onOpen }: { load: Load<PartnerUpgrades>; onOpen: () => void }) {
  if (load.status !== "ready" || !load.data.open) return null;
  const open = load.data.open;
  return (
    <section className={`w360-card ${local.notice}`} aria-label="Upgrade request">
      <p>
        Upgrade to <strong>{level(open.to_experience)}</strong> requested: <Status value={open.status} />
      </p>
      <button type="button" className="w360-btn w360-btn-secondary" onClick={onOpen}>
        Review the upgrade request
      </button>
    </section>
  );
}

// --- the panel ------------------------------------------------------------------------------------

export function UpgradePanel({
  partnerId,
  load,
  manage,
  onChanged,
  onOpenTab,
  available = [],
}: {
  partnerId: string;
  load: Load<PartnerUpgrades>;
  /** Whether this staff member may decide (the partner-manage permission). */
  manage: boolean;
  onChanged: () => void;
  /** Open one of the partner's own tabs, to check what a decision depends on. */
  onOpenTab?: (tab: Tab) => void;
  /** The tabs this partner has, so a pointer is drawn only for one that exists. */
  available?: string[];
}) {
  const heading = useId();
  return (
    <section id="upgrade" className={styles.stack} aria-labelledby={heading}>
      <h2 id={heading} className={styles.cardTitle}>
        Upgrade
      </h2>
      <LoadGate load={load} what="the upgrade status">
        {(data) => <Body partnerId={partnerId} data={data} manage={manage} onChanged={onChanged} onOpenTab={onOpenTab} available={available} />}
      </LoadGate>
    </section>
  );
}

function Body({
  partnerId,
  data,
  manage,
  onChanged,
  onOpenTab,
  available,
}: {
  partnerId: string;
  data: PartnerUpgrades;
  manage: boolean;
  onChanged: () => void;
  onOpenTab?: (tab: Tab) => void;
  available: string[];
}) {
  const { status, row } = shownStatus(data);
  const names = Object.fromEntries(data.products.map((p) => [p.code, p.name]));
  return (
    <>
      <dl className={local.facts}>
        <dt>Current partner level</dt>
        <dd>{level(data.current)}</dd>
        <dt>Upgrade status</dt>
        <dd>
          <Status value={status} />
        </dd>
        <dt>Requested upgrade</dt>
        <dd>{row ? level(row.to_experience) : "—"}</dd>
      </dl>

      {!data.open && status === "none" && (
        <p className={styles.muted}>
          {data.eligible.length > 0
            ? `No upgrade requested. The partner can ask for ${data.eligible.map(level).join(" or ")} from its own dashboard.`
            : "No upgrade requested, and none is available from this level."}
        </p>
      )}

      {data.open && (
        <OpenRequest
          key={`${data.open.id}:${data.open.version}`}
          partnerId={partnerId}
          data={data}
          open={data.open}
          names={names}
          manage={manage}
          onChanged={onChanged}
          onOpenTab={onOpenTab}
          available={available}
        />
      )}

      {data.history.length > 0 && (
        <section className={styles.stack} aria-label="Upgrade history">
          <h3>History</h3>
          <ol className={local.history}>
            {data.history.map((h) => (
              <li key={h.id}>
                <p>
                  <strong>
                    {level(h.from_experience)} → {level(h.to_experience)}
                  </strong>{" "}
                  <Status value={h.status} />
                </p>
                <p className={styles.muted}>
                  Requested {when(h.requested_at)}
                  {h.requested_by ? ` by ${h.requested_by}` : ""}.
                  {h.decided_at ? ` Decided ${when(h.decided_at)}${h.decided_by ? ` by ${h.decided_by}` : ""}.` : ""}
                </p>
                {h.decision_note && <p className={local.note}>Note: {h.decision_note}</p>}
                {h.commercial_reference && <p>Commercial reference: {h.commercial_reference}</p>}
                {h.status === "approved" && (
                  <p className={styles.muted}>
                    Added {h.capabilities_added.length} {h.capabilities_added.length === 1 ? "capability" : "capabilities"}
                    {h.capabilities_added.length > 0 && ` (${h.capabilities_added.join(", ")})`}
                    {h.products_granted.length > 0 && `; products: ${h.products_granted.map((c) => names[c] ?? c).join(", ")}`}. Nothing was removed.
                  </p>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}

// --- one open request ---------------------------------------------------------------------------------

function OpenRequest({
  partnerId,
  data,
  open,
  names,
  manage,
  onChanged,
  onOpenTab,
  available,
}: {
  partnerId: string;
  data: PartnerUpgrades;
  open: UpgradeRequestRow;
  names: Record<string, string>;
  manage: boolean;
  onChanged: () => void;
  onOpenTab?: (tab: Tab) => void;
  available: string[];
}) {
  const waiting = open.status === "pending_approval";
  // A request the partner is still filling in (or was sent back to) cannot be
  // approved; it can still be turned down once it has been submitted.
  const canReject = open.status === "pending_approval" || open.status === "profile_required";
  return (
    <>
      <p className={styles.muted}>
        Requested {when(open.requested_at)}
        {open.requested_by ? ` by ${open.requested_by}` : ""}
        {open.submitted_at ? `; submitted ${when(open.submitted_at)}` : "; not submitted yet"}.
      </p>
      {open.status === "requested" && <p>The partner has started the request but has not submitted it. There is nothing to decide yet.</p>}
      {open.status === "profile_required" && (
        <p className={local.note}>
          Sent back to the partner{open.info_request_note ? `: ${open.info_request_note}` : "."}
        </p>
      )}

      <section className={styles.stack} aria-label="What the request still needs">
        <h3>Checklist</h3>
        <ul className={local.checklist}>
          {data.checklist.map((item) => (
            <li key={item.key}>
              <strong>{item.label}</strong>: {item.done ? "done" : item.required ? "needed" : "optional, not provided"}
              {!item.done && item.detail && <span className={styles.muted}> - {item.detail}</span>}
            </li>
          ))}
        </ul>
      </section>

      <section className={styles.stack} aria-label="What the partner asked for">
        <h3>What the partner asked for</h3>
        <ProposalView proposal={open.proposal} names={names} />
      </section>

      {manage && canReject && (
        <Decision
          partnerId={partnerId}
          open={open}
          data={data}
          waiting={waiting}
          onChanged={onChanged}
          onOpenTab={onOpenTab}
          available={available}
        />
      )}
      {!manage && <p className={styles.muted}>You can read this request but not decide it.</p>}
    </>
  );
}

function ProposalView({ proposal, names }: { proposal: UpgradeProposal; names: Record<string, string> }) {
  const rows: [string, React.ReactNode][] = [];
  if (proposal.products) rows.push(["Products", proposal.products.length ? proposal.products.map((c) => names[c] ?? c).join(", ") : "None chosen"]);
  if (proposal.pricing) {
    rows.push(["Pricing preference", proposal.pricing.preference === "negotiated" ? "Negotiated" : "Standard prices"]);
    if (proposal.pricing.notes) rows.push(["Pricing notes", <p key="pn" className={local.note}>{proposal.pricing.notes}</p>]);
  }
  const c = proposal.commercial;
  if (c) {
    if (c.expected_customers !== null) rows.push(["Expected customers", String(c.expected_customers)]);
    if (c.team_size !== null) rows.push(["Team size", String(c.team_size)]);
    if (c.customer_management) rows.push(["Customer management", <p key="cm" className={local.note}>{c.customer_management}</p>]);
    if (c.notes) rows.push(["Commercial notes", <p key="cn" className={local.note}>{c.notes}</p>]);
  }
  const b = proposal.brand;
  if (b) {
    rows.push(["Brand name", b.brand_name]);
    if (b.primary_color) rows.push(["Primary colour", b.primary_color]);
    if (b.secondary_color) rows.push(["Secondary colour", b.secondary_color]);
    if (b.support_email) rows.push(["Brand support email", b.support_email]);
  }
  if (proposal.domain?.hostname) rows.push(["Proposed domain", proposal.domain.hostname]);
  if (rows.length === 0) return <p className={styles.muted}>The partner has not filled anything in yet.</p>;
  return (
    <dl className={local.facts}>
      {rows.map(([label, value]) => (
        <div key={label} className={local.pair}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// --- deciding ---------------------------------------------------------------------------------------------

const SUCCESS: Record<UpgradeAction, string> = {
  approve: "Upgrade approved. The partner keeps its login and everything it earned, and now lands in its new workspace.",
  request_info: "Sent back to the partner with your note.",
  reject: "Upgrade rejected. Nothing the partner holds has changed.",
};

function Decision({
  partnerId,
  open,
  data,
  waiting,
  onChanged,
  onOpenTab,
  available,
}: {
  partnerId: string;
  open: UpgradeRequestRow;
  data: PartnerUpgrades;
  waiting: boolean;
  onChanged: () => void;
  onOpenTab?: (tab: Tab) => void;
  available: string[];
}) {
  const id = useId();
  const [action, setAction] = useState<UpgradeAction>(waiting ? "approve" : "reject");
  const [note, setNote] = useState("");
  const [reference, setReference] = useState("");
  // Ticked: what the partner asked for. The offered list is the catalogue; a
  // code the partner asked for that is not on it is still shown, so nothing it
  // asked for is hidden, and the server refuses what cannot be granted.
  const asked = open.proposal.products ?? [];
  const [chosen, setChosen] = useState<string[]>(asked);
  const [missing, setMissing] = useState<string[]>([]);
  const { act, busy, feedback } = useAction(() => {
    setNote("");
    setReference("");
    onChanged();
  });

  const offered = [...data.products, ...asked.filter((c) => !data.products.some((p) => p.code === c)).map((c) => ({ code: c, name: c }))];
  const needsNote = action !== "approve";
  const valid = !needsNote || note.trim().length >= 3;
  const target = level(open.to_experience);
  const pointers = POINTERS.filter((p) => onOpenTab && available.includes(p.tab));

  async function decide() {
    setMissing([]);
    try {
      return await partnerConsoleApi.decideUpgrade(partnerId, open.id, {
        action,
        note: note.trim(),
        ...(action === "approve" ? { products: chosen, commercial_reference: reference.trim() } : {}),
      });
    } catch (err) {
      // The refusal text itself is shown by the action; the list of what is
      // missing is the server's own, given words here.
      const found = err instanceof ApiError ? (err.body as { missing?: unknown } | undefined)?.missing : undefined;
      if (Array.isArray(found)) setMissing(found.filter((m): m is string => typeof m === "string"));
      throw err;
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid || busy) return;
    void act(decide, SUCCESS[action]);
  }

  // The outcome sits beside the decision form, not inside it.
  return (
    <div className={styles.stack}>
      <form className={styles.form} onSubmit={submit} aria-labelledby={`${id}-h`}>
        <h3 id={`${id}-h`}>Decision</h3>
        <Segmented<UpgradeAction>
          legend="What do you want to do?"
          options={[
            { value: "approve", label: "Approve", disabled: !waiting },
            { value: "request_info", label: "Ask for more information", disabled: !waiting },
            { value: "reject", label: "Reject" },
          ]}
          value={action}
          onChange={setAction}
          hint={
            action === "approve"
              ? `Gives the partner the ${target} capabilities on its existing login and account. Nothing it holds is removed and its history is untouched. The server refuses if the partner is not active or its profile is not complete for everything it will hold.`
              : action === "request_info"
                ? "Sends the request back to the partner with your note. It can edit and submit again."
                : "Closes the request. The partner keeps exactly what it had and may ask again."
          }
        />
        {!waiting && <p className={styles.muted}>The partner is still completing this request, so it can only be rejected for now.</p>}

        {action === "approve" && (
          <>
            {pointers.length > 0 && (
              <p className={styles.muted}>
                Before approving, check the partner&apos;s{" "}
                {pointers.map((p, i) => (
                  <span key={p.tab}>
                    {i > 0 && (i === pointers.length - 1 ? " and " : ", ")}
                    <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpenTab?.(p.tab)}>
                      {p.label}
                    </button>
                  </span>
                ))}
                . Prices are set on the Pricing tab once the partner sells.
              </p>
            )}
            <fieldset className={styles.field}>
              <legend className="w360-label">Products the partner may sell</legend>
              {offered.length === 0 ? (
                <p className={styles.muted}>No products are on sale to choose from.</p>
              ) : (
                <ul className={local.products}>
                  {offered.map((p) => (
                    <li key={p.code}>
                      <label>
                        <input
                          type="checkbox"
                          checked={chosen.includes(p.code)}
                          onChange={(e) => setChosen(e.target.checked ? [...chosen, p.code] : chosen.filter((c) => c !== p.code))}
                        />{" "}
                        {p.name}
                        {asked.includes(p.code) && <span className={styles.muted}> (asked for)</span>}
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </fieldset>
            <TextField
              label={open.setup_fee ? "Set-up fee payment reference (required)" : "Commercial reference (optional)"}
              value={reference}
              onChange={setReference}
              maxLength={120}
              hint={
                open.setup_fee
                  ? `This upgrade carries a set-up fee of ${money(open.setup_fee.amount_minor, open.setup_fee.currency)}. Enter the invoice or transfer reference once it is paid; approval is refused without it.`
                  : "A pointer to the terms agreed, such as a contract number. It is recorded; no amount is stored or charged here."
              }
            />
          </>
        )}

        <TextAreaField
          label={needsNote ? "Note to the partner (required)" : "Note (optional)"}
          value={note}
          onChange={setNote}
          maxLength={500}
          rows={3}
          hint={needsNote ? "The partner reads this." : "Recorded with the decision."}
        />

        <div>
          <button type="submit" className={`w360-btn ${action === "reject" ? "w360-btn-danger" : "w360-btn-primary"}`} disabled={busy || !valid}>
            {action === "approve" ? "Approve upgrade" : action === "request_info" ? "Send back to the partner" : "Reject upgrade"}
          </button>
        </div>
      </form>
      {feedback}
      <div role="status" aria-live="polite">
        {missing.length > 0 && (
          <>
            <p>Still needed before this can be approved:</p>
            <ul>
              {missing.map((m) => (
                <li key={m}>{MISSING[m] ?? m}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
