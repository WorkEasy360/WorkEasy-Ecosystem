"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { adminApi } from "@/lib/api/controlPlane";
import {
  partnerConsoleApi,
  type ListCompletion,
  type OverviewCard,
  type ResellerCommission,
  type ResellerRow,
  type SetupStatus,
} from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { ExperienceLabels, PrimaryType, TypeBadges } from "./labels";
import { LoadGate, MoreButton, Status, Table, money, useAction, useLoad, usePaged, when } from "./ui";
import { NavIcon } from "@/components/sidebar/icons";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * The Partners area (ADR-048): the Dashboard and All Partners.
 *
 * Nothing here decides what a partner "is": the type badges come from the
 * server, computed from the capabilities the partner holds, and a partner
 * holding several shows several. The Partner type filter is a server-side filter
 * on those same grants, so a combined partner is in every view it belongs to.
 * Nothing here computes money or completion either - every figure is the
 * server's.
 */

// --- shared bits ----------------------------------------------------------------------

export { ExperienceLabels };

/** Per-currency ledger figures: what the server summed, never added across currencies. */
export function CommissionSummary({ rows }: { rows: ResellerCommission[] }) {
  if (rows.length === 0) return <span className={styles.muted}>No commission yet</span>;
  return (
    <div className={local.stackTight}>
      {rows.map((r) => (
        <span key={r.currency}>
          {money(r.balance_minor, r.currency)} earned
          <span className={styles.muted}> · {money(r.unpaid_minor, r.currency)} unpaid</span>
        </span>
      ))}
    </div>
  );
}

const SETUP_TEXT: Record<SetupStatus, string> = {
  setup_required: "Setup required",
  ready: "Ready",
  active: "Active",
  suspended: "Suspended",
  terminated: "Terminated",
};

const SETUP_BADGE: Partial<Record<SetupStatus, string>> = {
  setup_required: "w360-badge w360-badge-warning",
  ready: "w360-badge w360-badge-info",
};

/** A partner's state as the console names it: a draft is "Setup required" until
 * its profile is complete, then "Ready" (for staff to activate). */
export function SetupStatusBadge({ value }: { value: SetupStatus }) {
  const badge = SETUP_BADGE[value];
  if (!badge) return <Status value={value} />;
  return <span className={badge}>{SETUP_TEXT[value]}</span>;
}

/** The profile completion as words first ("35% · Incomplete") with a bar that
 * only reinforces them. */
export function CompletionMeter({ value, label }: { value: Pick<ListCompletion, "percent" | "complete">; label?: string }) {
  return (
    <div className={local.meter}>
      <span>
        {value.percent}% <span className={styles.muted}>· {value.complete ? "Complete" : "Incomplete"}</span>
        {label && <span className="w360-visually-hidden"> {label}</span>}
      </span>
      <span className={local.meterTrack} aria-hidden="true">
        <span className={`${value.complete ? local.meterDone : local.meterFill} ${local[`w${Math.round(Math.max(0, Math.min(100, value.percent)) / 10) * 10}`]}`} />
      </span>
    </div>
  );
}

/** The open upgrade request's state, in words (ADR-049). */
const UPGRADE_TEXT: Record<string, string> = {
  requested: "Requested",
  profile_required: "Profile required",
  pending_approval: "Pending approval",
};
const upgradeText = (status: string): string => UPGRADE_TEXT[status] ?? status.replace(/_/g, " ");
const upgradeTarget = (key: string): string => (key === "white_label" ? "White-label" : key === "reseller" ? "Reseller" : key);

// --- the dashboard -----------------------------------------------------------------------

/** Where each card's number can be inspected: All Partners, filtered. */
const CARD_LINKS: Record<string, string> = {
  total_partners: "/admin/partners/all",
  active_partners: "/admin/partners/all?status=active",
  resellers: "/admin/partners/all?type=reseller",
  white_label: "/admin/partners/all?type=white_label",
  affiliates: "/admin/partners/all?type=affiliate",
  suspended_partners: "/admin/partners/all?status=suspended",
  profiles_incomplete: "/admin/partners/all?profile=incomplete",
};

const CARD_NOTES: Record<string, string> = {
  total_partners: "Every partner account, in any state.",
  active_partners: "Partners in the active state.",
  resellers: "Partners that can sell and manage customers, in any state.",
  white_label: "Partners that can use their own brand and domain, in any state.",
  affiliates: "Partners that can refer customers, in any state.",
  suspended_partners: "Partners paused by Workeasy360.",
  profiles_incomplete: "Partners, not terminated, whose profile still needs something.",
};

function OverviewCardView({ card }: { card: OverviewCard }) {
  const href = CARD_LINKS[card.key] ?? "/admin/partners/all";
  return (
    <Link href={href} className={`w360-card ${local.card}`} aria-label={`${card.label}: ${card.count}. Open the list.`}>
      <span className={local.cardLabel}>{card.label}</span>
      <span className={local.cardCount}>{card.count}</span>
      <span className={local.cardNote}>{CARD_NOTES[card.key]}</span>
    </Link>
  );
}

function AddPartnerButton() {
  const { permissions } = useAdmin();
  if (!can(permissions, "platform.staff_partner.manage")) return null;
  return (
    <Link className="w360-btn w360-btn-primary" href="/admin/partners/new">
      + Add Partner
    </Link>
  );
}

/** Partners → Dashboard: factual counts and the newest partners. */
export function PartnersOverview() {
  const [load] = useLoad(() => partnerConsoleApi.overview(), []);

  return (
    <div className={styles.stack}>
      <div className={local.headerRow}>
        <h1>Partners</h1>
        <AddPartnerButton />
      </div>
      <p className={styles.lead}>
        Counts of what exists right now. Revenue, recurring-revenue and growth figures are not shown: they would need currency
        conversion this platform does not perform.
      </p>
      <LoadGate load={load} what="the partner dashboard">
        {(d) => (
          <>
            {(d.upgrades_pending ?? 0) > 0 && (
              <section className={`w360-card ${local.completion}`} aria-label="Upgrade requests">
                <p className={local.completionTitle}>
                  {d.upgrades_pending} upgrade {d.upgrades_pending === 1 ? "request is" : "requests are"} waiting for a decision.
                </p>
                <p>
                  <Link className="w360-btn w360-btn-primary" href="/admin/partners/all">
                    Open All Partners
                  </Link>{" "}
                  <span className={styles.muted}>Partners with a request carry an &ldquo;Upgrade&rdquo; badge.</span>
                </p>
              </section>
            )}
            <section aria-labelledby="partner-kpis" className={styles.stack}>
              <h2 id="partner-kpis" className="w360-visually-hidden">
                Key figures
              </h2>
              <div className={local.cards}>
                {d.cards.map((card) => (
                  <OverviewCardView key={card.key} card={card} />
                ))}
              </div>
            </section>
            <section aria-labelledby="recent-partners" className={styles.stack}>
              <div className={local.headerRow}>
                <h2 id="recent-partners">Recent partners</h2>
                <Link href="/admin/partners/all">View all partners</Link>
              </div>
              <Table
                caption="Recent partners"
                head={["Partner", "Type", "Profile", "Status", "Created"]}
                isEmpty={d.recent_partners.length === 0}
                empty="No partners yet. Add the first one with + Add Partner."
              >
                {d.recent_partners.map((p) => (
                  <tr key={p.id}>
                    <th scope="row">
                      <Link href={`/admin/partners/${p.id}`}>{p.display_name}</Link>
                    </th>
                    <td>
                      <TypeBadges labels={p.experience} />
                    </td>
                    <td>
                      <CompletionMeter value={p.profile_completion} label={`profile of ${p.display_name}`} />
                    </td>
                    <td>
                      <SetupStatusBadge value={p.profile_completion.setup_status} />
                    </td>
                    <td>{when(p.created_at)}</td>
                  </tr>
                ))}
              </Table>
              <p className={styles.muted}>As of {when(d.as_of)}.</p>
            </section>
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- All Partners ------------------------------------------------------------------------------

const TYPE_OPTIONS = [
  { value: "", label: "All Partners" },
  { value: "reseller", label: "Reseller" },
  { value: "white_label", label: "White-label" },
  { value: "affiliate", label: "Affiliate" },
];
const STATUS_OPTIONS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "suspended", label: "Suspended" },
  { value: "draft", label: "Draft" },
  { value: "terminated", label: "Terminated" },
];
const PROFILE_OPTIONS = [
  { value: "", label: "All" },
  { value: "complete", label: "Complete" },
  { value: "incomplete", label: "Incomplete" },
];

const pick = (raw: string | null, options: { value: string }[]) => (options.some((o) => o.value === raw) ? (raw as string) : "");

/** Partners → All Partners: one table, filtered by the server (search, partner
 * type, status, profile). The filters are kept in the address so a filtered
 * view can be linked to and survives a reload. */
export function AllPartnersList() {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/admin/partners/all";
  const searchId = useId();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  const [filters, setFilters] = useState({
    q: (params.get("q") ?? "").slice(0, 80),
    type: pick(params.get("type"), TYPE_OPTIONS),
    status: pick(params.get("status"), STATUS_OPTIONS),
    profile: pick(params.get("profile"), PROFILE_OPTIONS),
  });
  const manage = can(permissions, "platform.staff_partner.manage");
  const portals = usePortals();
  // Add Partner lands here with the new partner's id: the row is marked and
  // the banner says where its setup continues.
  const createdParam = params.get("created") ?? "";
  const [created] = useState(UUID_RE.test(createdParam) ? createdParam : "");

  const paged = usePaged(
    (cursor) =>
      partnerConsoleApi.resellers({
        scope: "all",
        q: filters.q || undefined,
        type: filters.type || undefined,
        status: filters.status || undefined,
        profile: filters.profile || undefined,
        cursor,
      }),
    [filters.q, filters.type, filters.status, filters.profile],
  );
  const { act, busy, feedback } = useAction(() => paged.reload());

  function apply(next: typeof filters) {
    setFilters(next);
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(next)) if (value) query.set(key, value);
    const text = query.toString();
    router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
  }

  const select = (key: "type" | "status" | "profile", label: string, options: { value: string; label: string }[]) => (
    <div>
      <label className="w360-label" htmlFor={`${searchId}-${key}`}>
        {label}
      </label>
      <select id={`${searchId}-${key}`} className="w360-input" value={filters[key]} onChange={(e) => apply({ ...filters, [key]: e.target.value })}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );

  return (
    <div className={styles.stack}>
      <div className={local.headerRow}>
        <h1>All Partners</h1>
        <AddPartnerButton />
      </div>
      <form
        className={local.filters}
        role="search"
        aria-label="Filter partners"
        onSubmit={(e) => {
          e.preventDefault();
          apply({ ...filters, q: draft.trim().slice(0, 80) });
        }}
      >
        <div className={local.searchField}>
          <label className="w360-label" htmlFor={searchId}>
            Search by partner name or email
          </label>
          <input id={searchId} type="search" className="w360-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={80} />
        </div>
        {select("type", "Partner type", TYPE_OPTIONS)}
        {select("status", "Status", STATUS_OPTIONS)}
        {select("profile", "Profile", PROFILE_OPTIONS)}
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>
      {created && (
        <div className={local.createdBanner} role="status">
          <p>
            <strong>Partner created successfully.</strong> Its profile is incomplete: use{" "}
            <Link href={`/admin/partners/${created}/profile`}>Complete setup</Link> (the gear in its row) to finish it,
            then activate it with the switch in its row.
          </p>
        </div>
      )}
      {feedback}

      {paged.state === "loading" && <p aria-live="polite">Loading partners…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to partners.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load partners.</p>}
      {paged.state === "ready" && (
        <>
          <Table
            caption="Partners"
            head={[
              "Status",
              "Action",
              "Panel",
              "Partner ID",
              "Name",
              "Partner Type",
              "Email",
              "Phone",
              "Country",
              "Currency",
              "Wallet",
              "Last Login",
              "Joining Date",
            ]}
            isEmpty={paged.rows.length === 0}
            empty="No partners match these filters."
          >
            {paged.rows.map((r) => (
              <PartnerRow
                key={r.id}
                row={r}
                manage={manage}
                busy={busy}
                highlighted={r.id === created}
                onStatus={(target, reason) =>
                  act(
                    () => adminApi.setPartnerStatus(r.id, target, reason, r.version),
                    target === "suspended"
                      ? `${r.display_name} suspended.`
                      : r.status === "draft"
                        ? `${r.display_name} activated.`
                        : `${r.display_name} reactivated.`,
                  )
                }
                onGuest={(reason) =>
                  act(async () => {
                    const view = await adminApi.startGuestView(r.id, reason);
                    window.location.assign(portalHref(portals, "partner", view.portal_path));
                  }, `Opening the portal of ${r.display_name} as a guest…`)
                }
              />
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The partner's balance: its commission ledger per currency, as the server
 * summed it (never added across currencies). */
function wallet(r: ResellerRow) {
  if (r.commission.length === 0) return money(0, r.payout_currency);
  return r.commission.map((c) => money(c.balance_minor, c.currency)).join(" · ");
}

function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(undefined, { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** A value with a copy button, as the list shows a panel link, an id or an email. */
function Copyable({ value, shown, what }: { value: string; shown?: string; what: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className={local.copyCell}>
      <span className={local.copyText} title={value}>
        {shown ?? value}
      </span>
      <button
        type="button"
        className={local.iconButton}
        aria-label={copied ? `Copied ${what}` : `Copy ${what}`}
        title={copied ? "Copied" : "Copy"}
        onClick={() =>
          void navigator.clipboard?.writeText(value).then(
            () => setCopied(true),
            () => setCopied(false),
          )
        }
      >
        <NavIcon name="copy" />
      </button>
    </span>
  );
}

type Prompt = null | "status" | "guest";

/** The reason recorded when the switch turns a partner on. Turning one on asks
 * nothing (the owner's call, 2026-10-27); the server still requires a reason, so
 * the switch states where the action came from, and the audit entry records
 * who and when. Turning a partner off still asks why. */
const SWITCH_ON_REASON = { activate: "Activated from All Partners", reactivate: "Reactivated from All Partners" };

function PartnerRow({
  row: r,
  manage,
  busy,
  highlighted,
  onStatus,
  onGuest,
}: {
  row: ResellerRow;
  manage: boolean;
  busy: boolean;
  highlighted: boolean;
  onStatus: (target: "active" | "suspended", reason: string) => void;
  onGuest: (reason: string) => void;
}) {
  const portals = usePortals();
  const reasonId = useId();
  const [prompt, setPrompt] = useState<Prompt>(null);
  const [reason, setReason] = useState("");
  const href = `/admin/partners/${r.id}`;
  const setupHref = `/admin/partners/${r.id}/profile`;
  const panelPath = `/partner/${r.id}`;
  const panelUrl = portals ? portalHref(portals, "partner", panelPath) : `${window.location.origin}${panelPath}`;
  const on = r.status === "active";
  const draft = r.status === "draft";
  // The server refuses to activate a partner nobody can act for, so a draft
  // without an administrator is not offered the switch.
  const canToggle = manage && (r.status === "active" || r.status === "suspended" || (draft && r.administrator !== null));
  const toggleNote =
    r.status === "terminated"
      ? "A terminated partner cannot be reactivated."
      : !manage
        ? "You may not change a partner's status."
        : draft && r.administrator === null
          ? "Add an administrator before activating this partner."
          : on
            ? "Suspend this partner"
            : draft
              ? "Activate this partner"
              : "Reactivate this partner";
  const complete = r.profile_completion.complete;
  const target = on ? "suspended" : "active";
  const verb = on ? "Suspend" : draft ? "Activate" : "Reactivate";
  const minLength = prompt === "guest" ? 3 : 10;

  function open(next: Prompt) {
    setPrompt((current) => (current === next ? null : next));
    setReason("");
  }

  return (
    <>
      <tr className={highlighted ? local.highlightRow : undefined}>
        <td>
          <button
            type="button"
            role="switch"
            aria-checked={on}
            aria-label={`${r.display_name} active`}
            aria-expanded={on ? prompt === "status" : undefined}
            title={toggleNote}
            className={local.switch}
            disabled={!canToggle || busy}
            onClick={() =>
              on ? open("status") : onStatus("active", draft ? SWITCH_ON_REASON.activate : SWITCH_ON_REASON.reactivate)
            }
          >
            <span className={local.switchKnob} />
          </button>
        </td>
        <td>
          <div className={local.iconRow}>
            <Link
              href={setupHref}
              className={local.iconButton}
              aria-label={`${complete ? "Edit setup of" : "Complete setup of"} ${r.display_name}`}
              title={complete ? "Edit setup" : "Complete setup"}
            >
              <NavIcon name="settings" />
            </Link>
            {manage && (
              <button
                type="button"
                className={local.iconButton}
                aria-label={`Guest login to ${r.display_name}`}
                aria-expanded={prompt === "guest"}
                title="Guest login (read-only)"
                disabled={busy}
                onClick={() => open("guest")}
              >
                <NavIcon name="login" />
              </button>
            )}
          </div>
        </td>
        <td>
          <Copyable value={panelUrl} what={`panel link of ${r.display_name}`} />
        </td>
        <td>
          <Copyable value={r.id} shown={r.id.slice(0, 8)} what={`partner ID of ${r.display_name}`} />
        </td>
        <th scope="row">
          <Link href={href}>{r.display_name}</Link>
          <div className={local.nameMeta}>
            <SetupStatusBadge value={r.profile_completion.setup_status} />
            {r.upgrade && (
              <Link
                href={`${href}?tab=users`}
                className={`w360-badge ${r.upgrade.status === "pending_approval" ? "w360-badge-warning" : "w360-badge-neutral"}`}
                aria-label={`Upgrade: ${upgradeText(r.upgrade.status)}, to ${upgradeTarget(r.upgrade.to)} - ${r.display_name}`}
              >
                Upgrade: {upgradeText(r.upgrade.status)}
              </Link>
            )}
          </div>
        </th>
        <td>
          <PrimaryType value={r.primary_experience} />
        </td>
        <td>
          {r.administrator?.email ? (
            <Copyable value={r.administrator.email} what={`email of ${r.display_name}`} />
          ) : (
            <span className={styles.muted}>No administrator</span>
          )}
        </td>
        <td>{r.phone ? <Copyable value={r.phone} what={`phone of ${r.display_name}`} /> : <span className={styles.muted}>—</span>}</td>
        <td>{r.country ? <abbr title={countryName(r.country)}>{r.country}</abbr> : <span className={styles.muted}>—</span>}</td>
        <td>{r.payout_currency}</td>
        <td className={styles.num}>{wallet(r)}</td>
        <td>{r.administrator?.last_login_at ? when(r.administrator.last_login_at) : <span className={styles.muted}>Never</span>}</td>
        <td>{when(r.created_at)}</td>
      </tr>
      {prompt !== null && (
        <tr className={local.promptRow}>
          <td colSpan={13}>
            <form
              className={local.promptForm}
              onSubmit={(e) => {
                e.preventDefault();
                const text = reason.trim();
                setPrompt(null);
                setReason("");
                if (prompt === "guest") onGuest(text);
                else onStatus(target, text);
              }}
            >
              <label className="w360-label" htmlFor={reasonId}>
                {prompt === "guest"
                  ? `Why are you opening the portal of ${r.display_name}? (recorded, and shown in the partner's activity)`
                  : `Reason to ${verb.toLowerCase()} ${r.display_name} (recorded in the audit log)`}
              </label>
              <input
                id={reasonId}
                className="w360-input"
                autoFocus
                required
                minLength={minLength}
                maxLength={200}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              {prompt === "guest" && (
                <p className={styles.muted}>
                  Read-only: you see the portal as the partner does but cannot change anything. It ends after 30 minutes.
                </p>
              )}
              <div className={styles.row}>
                <button
                  type="submit"
                  className={`w360-btn ${prompt === "status" && on ? "w360-btn-danger" : "w360-btn-primary"}`}
                  disabled={busy || reason.trim().length < minLength}
                >
                  {prompt === "guest" ? "Open guest view" : verb}
                </button>
                <button type="button" className="w360-btn w360-btn-secondary" onClick={() => open(null)}>
                  Cancel
                </button>
              </div>
            </form>
          </td>
        </tr>
      )}
    </>
  );
}
