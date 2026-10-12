"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  partnerConsoleApi,
  type ExperienceLabel,
  type OverviewCard,
  type ResellerCommission,
  type ResellerPaid,
  type ResellerRow,
} from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { ExperienceLabels } from "./labels";
import { QuickActions, RecentActivity } from "./PartnerActivity";
import { LoadGate, MoreButton, Status, Table, money, useLoad, usePaged, when } from "./ui";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * The Partner Control Center's dashboard and its partner lists (Phase 10a,
 * reorganized for the Control Center).
 *
 * Nothing here decides what a partner "is": the labels come from the server,
 * computed from the capabilities the partner holds, and the console only
 * renders them. All Partners, Resellers and Affiliates are one list over the
 * same partner accounts with a different server-side `scope` - three views,
 * never three kinds of record. Nothing here computes money either - every
 * amount is a figure the server summed, formatted for display.
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

function PaidSummary({ rows }: { rows: ResellerPaid[] }) {
  if (rows.length === 0) return <span className={styles.muted}>Nothing paid yet</span>;
  return (
    <div className={local.stackTight}>
      {rows.map((r) => (
        <span key={r.currency}>
          {money(r.amount_minor, r.currency)}
          <span className={styles.muted}>
            {" "}
            · {r.payouts} {r.payouts === 1 ? "payout" : "payouts"}
          </span>
        </span>
      ))}
    </div>
  );
}

/** Whether a partner wears the White-label label - read from the server's labels. */
const wearsWhiteLabel = (labels: ExperienceLabel[]) => labels.some((l) => l.key === "white_label");

// --- the dashboard -----------------------------------------------------------------------

/** Where each card's number can be inspected. Presentation only: the list it
 * opens re-asks the server, which re-checks the caller's permission. */
const CARD_LINKS: Record<string, string> = {
  total_partners: "/admin/partners/all",
  active_partners: "/admin/partners/all?status=active",
  active_resellers: "/admin/partners/resellers?status=active",
  white_label_enabled: "/admin/partners/white-label",
  active_affiliates: "/admin/partners/affiliates?status=active",
  partner_customers: "/admin/partners/customers",
  active_partner_subscriptions: "/admin/partners/customers?subscription=active",
  pending_commissions: "/admin/partners/commissions?unpaid=1",
  pending_payouts: "/admin/partners/payouts?status=open",
  domains_awaiting_verification: "/admin/partners/white-label?domain_status=awaiting",
};

const CARD_NOTES: Record<string, string> = {
  total_partners: "Every partner account, in any state.",
  active_partners: "Partners in the active state.",
  active_resellers: "Active partners that can sell.",
  white_label_enabled: "Active partners that can set a brand or a domain.",
  active_affiliates: "Active partners that can refer customers.",
  partner_customers: "Customers with a live partner relationship.",
  active_partner_subscriptions: "Active subscriptions of those customers - what partners have sold and is live.",
  pending_commissions: "Ledger entries not yet in a payout.",
  pending_payouts: "Payouts awaiting a decision or a payment.",
  domains_awaiting_verification: "Requested, but no DNS proof seen yet.",
};

function OverviewCardView({ card }: { card: OverviewCard }) {
  const href = CARD_LINKS[card.key] ?? "/admin/partners";
  return (
    <Link href={href} className={`w360-card ${local.card}`} aria-label={`${card.label}: ${card.count}. Open the list.`}>
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

/** Partners → Dashboard: quick actions, the factual counts, and what changed lately. */
export function PartnersOverview() {
  const { permissions } = useAdmin();
  const [load] = useLoad(() => partnerConsoleApi.overview(), []);

  return (
    <div className={styles.stack}>
      <div className={local.headerRow}>
        <h1>Partners</h1>
        <QuickActions permissions={permissions} />
      </div>
      <p className={styles.lead}>
        Counts of what exists right now. Revenue, growth and recurring-revenue figures are not shown: they would need currency
        conversion this platform does not perform.
      </p>
      <LoadGate load={load} what="the partner overview">
        {(d) => (
          <section aria-labelledby="partner-kpis" className={styles.stack}>
            <h2 id="partner-kpis" className="w360-visually-hidden">
              Key figures
            </h2>
            <div className={local.cards}>
              {d.cards.map((card) => (
                <OverviewCardView key={card.key} card={card} />
              ))}
            </div>
            <p className={styles.muted}>As of {when(d.as_of)}.</p>
          </section>
        )}
      </LoadGate>
      {can(permissions, "platform.staff_partner.view") && <RecentActivity />}
    </div>
  );
}

// --- the partner lists ------------------------------------------------------------------------

const STATUSES = ["draft", "active", "suspended", "terminated"];

export type ListScope = "all" | "resellers" | "affiliates";

const SCOPE_TEXT: Record<
  ListScope,
  { heading: string; caption: string; lead: string; empty: string; loading: string; add: { label: string; href: string } }
> = {
  all: {
    heading: "All partners",
    caption: "Partner accounts",
    lead: "Every partner account, whatever it can do. The labels next to a name are derived from the capabilities it holds - one account can wear several.",
    empty: "No partners match.",
    loading: "Loading partners…",
    add: { label: "Add partner", href: "/admin/partners/new" },
  },
  resellers: {
    heading: "Resellers",
    caption: "Resellers",
    lead: "Partners that hold a selling or customer-management capability.",
    empty: "No resellers match. Try All partners, or add one.",
    loading: "Loading resellers…",
    add: { label: "Add reseller", href: "/admin/partners/resellers/new" },
  },
  affiliates: {
    heading: "Affiliates",
    caption: "Affiliates",
    lead: "Partners that hold a referral capability. Referral figures are counts only: nothing identifies who was referred.",
    empty: "No affiliates match. Create one from Add partner.",
    loading: "Loading affiliates…",
    add: { label: "Add affiliate", href: "/admin/partners/new?purpose=affiliate" },
  },
};

const HEADS: Record<ListScope, string[]> = {
  all: ["Partner", "Labels", "Status", "Customers", "Products", "Subscriptions", "Commission", "White-label", "Created", "Last activity"],
  resellers: [
    "Reseller",
    "Status",
    "Customers",
    "Products allowed",
    "Active subscriptions",
    "Commission",
    "Payouts",
    "White-label",
    "Last activity",
    "Actions",
  ],
  affiliates: ["Affiliate", "Status", "Referral codes", "Conversions", "Commission", "Pending payout", "Paid", "Last activity"],
};

/** All Partners, Resellers or Affiliates: the same partner accounts, filtered by
 * the server on the capabilities they hold (`scope`). */
export function PartnerList({ scope }: { scope: ListScope }) {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const searchId = useId();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  // The search is applied on submit, not per keystroke: one bounded request per search.
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [product, setProduct] = useState(params.get("product") ?? "");
  const [capability, setCapability] = useState(params.get("capability") ?? "");
  const manage = can(permissions, "platform.staff_partner.manage");
  const text = SCOPE_TEXT[scope];

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
        <h1>{text.heading}</h1>
        {manage && (
          <Link className="w360-btn w360-btn-primary" href={text.add.href}>
            {text.add.label}
          </Link>
        )}
      </div>
      <p className={styles.lead}>{text.lead}</p>
      <form
        className={local.filters}
        role="search"
        aria-label={`Filter ${text.heading.toLowerCase()}`}
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
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>

      {paged.state === "loading" && <p aria-live="polite">{text.loading}</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to partners.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load {text.heading.toLowerCase()}.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption={text.caption} head={HEADS[scope]} isEmpty={paged.rows.length === 0} empty={text.empty}>
            {paged.rows.map((r) =>
              scope === "affiliates" ? (
                <AffiliateTableRow key={r.id} row={r} />
              ) : scope === "all" ? (
                <AllPartnersTableRow key={r.id} row={r} />
              ) : (
                <ResellerTableRow key={r.id} row={r} />
              ),
            )}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

export function ResellerList() {
  return <PartnerList scope="resellers" />;
}

export function AllPartnersList() {
  return <PartnerList scope="all" />;
}

export function AffiliateList() {
  return <PartnerList scope="affiliates" />;
}

function products(r: ResellerRow) {
  if (r.products.length === 0) return <span className={styles.muted}>None</span>;
  return r.products.map((p) => (p.plan_code ? `${p.product_code} (${p.plan_code})` : p.product_code)).join(", ");
}

function WhiteLabelCell({ row }: { row: ResellerRow }) {
  return wearsWhiteLabel(row.experience) ? (
    <span className="w360-badge w360-badge-success">Enabled</span>
  ) : (
    <span className={styles.muted}>Not enabled</span>
  );
}

function PartnerName({ row, labels = false }: { row: ResellerRow; labels?: boolean }) {
  return (
    <th scope="row">
      <Link href={`/admin/partners/${row.id}`}>{row.display_name}</Link>
      <div className={`${styles.muted} ${styles.mono}`}>{row.code}</div>
      {labels && <ExperienceLabels labels={row.experience} />}
    </th>
  );
}

function ResellerTableRow({ row: r }: { row: ResellerRow }) {
  return (
    <tr>
      <PartnerName row={r} labels />
      <td>
        <Status value={r.status} />
      </td>
      <td className={styles.num}>{r.customers}</td>
      <td>{products(r)}</td>
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
      <td>
        <WhiteLabelCell row={r} />
      </td>
      <td>{when(r.last_activity_at)}</td>
      <td>
        <div className={local.stackTight}>
          <Link href={`/admin/partners/${r.id}`} aria-label={`Manage ${r.display_name}`}>
            Manage
          </Link>
          <Link href={`/admin/partners/${r.id}?tab=customers`} aria-label={`Customers of ${r.display_name}`}>
            Customers
          </Link>
        </div>
      </td>
    </tr>
  );
}

function AllPartnersTableRow({ row: r }: { row: ResellerRow }) {
  return (
    <tr>
      <PartnerName row={r} />
      <td>
        <ExperienceLabels labels={r.experience} />
      </td>
      <td>
        <Status value={r.status} />
      </td>
      <td className={styles.num}>{r.customers}</td>
      <td>{products(r)}</td>
      <td className={styles.num}>{r.active_subscriptions}</td>
      <td>
        <CommissionSummary rows={r.commission} />
      </td>
      <td>
        <WhiteLabelCell row={r} />
      </td>
      <td>{when(r.created_at)}</td>
      <td>{when(r.last_activity_at)}</td>
    </tr>
  );
}

function AffiliateTableRow({ row: r }: { row: ResellerRow }) {
  const { codes, active_codes: activeCodes, captured, converted } = r.referrals;
  const more = activeCodes - codes.length;
  return (
    <tr>
      <PartnerName row={r} labels />
      <td>
        <Status value={r.status} />
      </td>
      <td>
        {codes.length === 0 ? (
          <span className={styles.muted}>No active code</span>
        ) : (
          <div className={local.stackTight}>
            {codes.map((c) => (
              <span key={c} className={styles.mono}>
                {c}
              </span>
            ))}
            {more > 0 && <span className={styles.muted}>and {more} more</span>}
          </div>
        )}
      </td>
      <td>
        <span className={styles.num}>{converted}</span>
        <span className={styles.muted}> of {captured} referrals</span>
      </td>
      <td>
        {r.commission.length === 0 ? (
          <span className={styles.muted}>No commission yet</span>
        ) : (
          <div className={local.stackTight}>
            {r.commission.map((c) => (
              <span key={c.currency}>{money(c.balance_minor, c.currency)}</span>
            ))}
          </div>
        )}
      </td>
      <td>
        <div className={local.stackTight}>
          {r.commission.filter((c) => c.unpaid_minor !== 0).map((c) => (
            <span key={c.currency}>{money(c.unpaid_minor, c.currency)} unpaid</span>
          ))}
          {r.payouts.open > 0 && <span className={styles.muted}>{r.payouts.open} open payout{r.payouts.open === 1 ? "" : "s"}</span>}
          {r.payouts.open === 0 && r.commission.every((c) => c.unpaid_minor === 0) && <span className={styles.muted}>None</span>}
        </div>
      </td>
      <td>
        <PaidSummary rows={r.payouts.paid} />
      </td>
      <td>{when(r.last_activity_at)}</td>
    </tr>
  );
}
