"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  partnerConsoleApi,
  type ExperienceLabel,
  type OverviewCard,
  type ResellerCommission,
  type ResellerRow,
} from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, MoreButton, Status, Table, money, useLoad, usePaged, when } from "./ui";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * The Partners section's landing page and the reseller list (Phase 10a).
 *
 * Nothing here decides what a partner "is": the labels come from the server,
 * computed from the capabilities the partner holds, and the console only
 * renders them. Nothing here computes money either - every amount is a figure
 * the server summed, formatted for display.
 */

// --- shared bits ----------------------------------------------------------------------

/** The derived labels ("Reseller", "White-label", ...) a partner wears. */
export function ExperienceLabels({ labels }: { labels: ExperienceLabel[] }) {
  if (labels.length === 0) return <span className={styles.muted}>No capabilities yet</span>;
  return (
    <ul className={local.labels} aria-label="Derived from capabilities">
      {labels.map((l) => (
        <li key={l.key} className={local.label}>
          {l.label}
        </li>
      ))}
    </ul>
  );
}

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

// --- overview ---------------------------------------------------------------------------

/** Where each card's number can be inspected. Presentation only: the list it
 * opens re-asks the server, which re-checks the caller's permission. */
const CARD_LINKS: Record<string, string> = {
  active_partners: "/admin/partners/resellers?scope=all&status=active",
  active_resellers: "/admin/partners/resellers?status=active",
  white_label_enabled: "/admin/partners/white-label",
  partner_customers: "/admin/partners/customers",
  active_partner_subscriptions: "/admin/partners/customers?subscription=active",
  pending_commissions: "/admin/commissions?unpaid=1",
  pending_payouts: "/admin/payouts?status=open",
  domains_awaiting_verification: "/admin/partners/white-label?domain_status=awaiting",
};

const CARD_NOTES: Record<string, string> = {
  active_partners: "Partners in the active state.",
  active_resellers: "Active partners that can sell.",
  white_label_enabled: "Active partners that can set a brand or a domain.",
  partner_customers: "Customers with a live partner relationship.",
  active_partner_subscriptions: "Active subscriptions of those customers.",
  pending_commissions: "Ledger entries not yet in a payout.",
  pending_payouts: "Payouts awaiting a decision or a payment.",
  domains_awaiting_verification: "Requested, but no DNS proof seen yet.",
};

function OverviewCardView({ card }: { card: OverviewCard }) {
  const href = CARD_LINKS[card.key] ?? "/admin/partners";
  return (
    <Link
      href={href}
      className={`w360-card ${local.card}`}
      aria-label={`${card.label}: ${card.count}. Open the list.`}
    >
      <span className={local.cardLabel}>{card.label}</span>
      <span className={local.cardCount}>{card.count}</span>
      {card.amounts && card.amounts.length > 0 && (
        <span className={local.cardNote}>
          {card.amounts.map((a) => `${money(a.amount_minor, a.currency)} (${a.entries})`).join(" · ")}
        </span>
      )}
      <span className={local.cardNote}>{CARD_NOTES[card.key]}</span>
    </Link>
  );
}

export function PartnersOverview() {
  const { permissions } = useAdmin();
  const [load] = useLoad(() => partnerConsoleApi.overview(), []);
  const manage = can(permissions, "platform.staff_partner.manage");

  return (
    <div className={styles.stack}>
      <div className={local.headerRow}>
        <h1>Partners</h1>
        {manage && (
          <Link className="w360-btn w360-btn-primary" href="/admin/partners/resellers/new">
            Add reseller
          </Link>
        )}
      </div>
      <p className={styles.lead}>
        Counts of what exists right now. Revenue, growth and recurring-revenue figures are not shown: they would need
        currency conversion this platform does not perform.
      </p>
      <LoadGate load={load} what="the partner overview">
        {(d) => (
          <>
            <div className={local.cards}>
              {d.cards.map((card) => (
                <OverviewCardView key={card.key} card={card} />
              ))}
            </div>
            <p className={styles.muted}>As of {when(d.as_of)}.</p>
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- the reseller list ---------------------------------------------------------------------------

const STATUSES = ["draft", "active", "suspended", "terminated"];

export function ResellerList() {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const searchId = useId();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  // The search is applied on submit, not per keystroke: one bounded request per search.
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [product, setProduct] = useState(params.get("product") ?? "");
  const [capability, setCapability] = useState(params.get("capability") ?? "");
  const [scope, setScope] = useState(params.get("scope") === "all" ? "all" : "resellers");
  const manage = can(permissions, "platform.staff_partner.manage");

  const paged = usePaged(
    (cursor) =>
      partnerConsoleApi.resellers({
        q: query || undefined,
        status: status || undefined,
        product: product || undefined,
        capability: capability || undefined,
        scope,
        cursor,
      }),
    [query, status, product, capability, scope],
  );
  // The filter choices are read from the server. A failure just leaves the
  // menus empty - the list itself does not depend on them.
  const [products] = useLoad(() => partnerConsoleApi.products().catch(() => ({ results: [] })), []);
  const [catalog] = useLoad(() => partnerConsoleApi.capabilityCatalog().catch(() => null), []);

  return (
    <div className={styles.stack}>
      <div className={local.headerRow}>
        <h1>{scope === "all" ? "All partners" : "Resellers"}</h1>
        {manage && (
          <Link className="w360-btn w360-btn-primary" href="/admin/partners/resellers/new">
            Add reseller
          </Link>
        )}
      </div>
      <form
        className={local.filters}
        role="search"
        aria-label="Filter resellers"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <div>
          <label className="w360-label" htmlFor={searchId}>
            Search by name or code
          </label>
          <input id={searchId} className="w360-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={80} />
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-status`}>
            Status
          </label>
          <select id={`${searchId}-status`} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s[0].toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-product`}>
            Product
          </label>
          <select id={`${searchId}-product`} className="w360-input" value={product} onChange={(e) => setProduct(e.target.value)}>
            <option value="">Any</option>
            {products.status === "ready" &&
              products.data.results.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.name}
                </option>
              ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-cap`}>
            Capability
          </label>
          <select id={`${searchId}-cap`} className="w360-input" value={capability} onChange={(e) => setCapability(e.target.value)}>
            <option value="">Any</option>
            {catalog.status === "ready" &&
              catalog.data?.results.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-scope`}>
            Show
          </label>
          <select id={`${searchId}-scope`} className="w360-input" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="resellers">Resellers</option>
            <option value="all">All partners</option>
          </select>
        </div>
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>

      {paged.state === "loading" && <p aria-live="polite">Loading resellers…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to partners.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load resellers.</p>}
      {paged.state === "ready" && (
        <>
          <Table
            caption={scope === "all" ? "Partner accounts" : "Resellers"}
            head={[
              "Reseller",
              "Status",
              "Customers",
              "Products allowed",
              "Active subscriptions",
              "Commission",
              "Payouts",
              "Last activity",
            ]}
            isEmpty={paged.rows.length === 0}
            empty={scope === "all" ? "No partners match." : "No resellers match. Try “All partners”, or add one."}
          >
            {paged.rows.map((r) => (
              <ResellerTableRow key={r.id} row={r} />
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

function ResellerTableRow({ row: r }: { row: ResellerRow }) {
  return (
    <tr>
      <th scope="row">
        <Link href={`/admin/partners/${r.id}`}>{r.display_name}</Link>
        <div className={`${styles.muted} ${styles.mono}`}>{r.code}</div>
        <ExperienceLabels labels={r.experience} />
      </th>
      <td>
        <Status value={r.status} />
      </td>
      <td className={styles.num}>{r.customers}</td>
      <td>
        {r.products.length === 0 ? (
          <span className={styles.muted}>None</span>
        ) : (
          r.products.map((p) => (p.plan_code ? `${p.product_code} (${p.plan_code})` : p.product_code)).join(", ")
        )}
      </td>
      <td className={styles.num}>{r.active_subscriptions}</td>
      <td>
        <CommissionSummary rows={r.commission} />
      </td>
      <td>
        {r.payouts.latest ? (
          <div className={local.stackTight}>
            <Status value={r.payouts.latest.status} />
            {r.payouts.open > 0 && <span className={styles.muted}>{r.payouts.open} open</span>}
          </div>
        ) : (
          <span className={styles.muted}>None</span>
        )}
      </td>
      <td>{when(r.last_activity_at)}</td>
    </tr>
  );
}
