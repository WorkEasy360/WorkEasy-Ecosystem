"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { NavIcon } from "@/components/sidebar/icons";
import { LoadGate, Status, Table, money, useLoad, when } from "@/features/controlplane/ui";
import {
  panelApi,
  type AffiliateOverview,
  type Dashboard as DashboardData,
  type SplitPart,
  type RangeKey,
} from "@/lib/api/partnerPanel";
import { ConversionTable, LinkBlock } from "./AffiliateScreens";
import { NotificationsPreview, ProductActivity } from "./Dashboard";
import { WORKSPACES, type ExperienceKey } from "./experiences";
import { LEVEL_LEAD, LevelList, LevelMatrix, UpgradeDialog, levelFee } from "./levels";
import { onboardingOnly } from "@/lib/api/controlPlane";
import { walletApi, type PartnerSubscriptionRow, type WalletEntry } from "@/lib/api/partnerWallet";
import { StatCard, StatGrid } from "@/features/controlplane/kit";
import { ACCOUNT_WORDS, KYC_WORDS, KycProgress } from "./BusinessKyc";
import { CopyButton } from "./workspaceKit";
import { EmptyState, PageHeader, usePanel } from "./PanelKit";
import { UpgradeGrow } from "./UpgradeGrow";
import { WhiteLabelStatusSection } from "./WhiteLabelDashboard";
import styles from "./PartnerPanel.module.css";

/**
 * The unified partner dashboard (2026-11-03, specification figure 1.0 and
 * section 5): one page for every level.
 *
 * - six overview cards (2026-11-11 brief): account status, referral
 *   conversions, customer renewals, the Platform Wallet, pending earnings and
 *   available commissions - each drawn only when the partner may see it;
 * - pending actions, upcoming renewals, recent wallet transactions and recent
 *   commissions;
 * - date presets (today, yesterday, 7 and 30 days, this and last month, a
 *   custom range) in the partner's own time zone;
 * - the sales and commission trend, the product-wise split, quick actions,
 *   recent customers (or conversions) and the earnings split with a way to
 *   withdraw;
 * - the partner's level, the three levels with their fees and the feature
 *   matrix.
 *
 * Every figure is the server's (the ledger's) and every section follows the
 * member's permissions: a section the server returns as `null` says why
 * instead of showing a zero.
 */

const RANGES: { key: RangeKey; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "7d", label: "Last 7 days" },
  { key: "30d", label: "Last 30 days" },
  { key: "this_month", label: "This month" },
  { key: "last_month", label: "Last month" },
  { key: "custom", label: "Custom range" },
];

/** Categorical slots of the reference palette (dataviz skill), in fixed order. */
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];
const OTHER = "#8a8984";


function RangePicker({
  range,
  onRange,
  onCustom,
}: {
  range: RangeKey;
  onRange: (r: RangeKey) => void;
  onCustom: (from: string, to: string) => void;
}) {
  const selectId = useId();
  const fromId = useId();
  const toId = useId();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  return (
    <div className={styles.rangePicker}>
      <div>
        <label className="w360-label" htmlFor={selectId}>
          Period
        </label>
        <select id={selectId} className="w360-input" value={range} onChange={(e) => onRange(e.target.value as RangeKey)}>
          {RANGES.map((r) => (
            <option key={r.key} value={r.key}>
              {r.label}
            </option>
          ))}
        </select>
      </div>
      {range === "custom" && (
        <form
          className={styles.dates}
          onSubmit={(e) => {
            e.preventDefault();
            onCustom(from, to);
          }}
        >
          <div>
            <label className="w360-label" htmlFor={fromId}>
              From
            </label>
            <input id={fromId} className="w360-input" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label className="w360-label" htmlFor={toId}>
              To
            </label>
            <input id={toId} className="w360-input" type="date" required value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <button type="submit" className="w360-btn w360-btn-secondary" disabled={!from || !to}>
            Apply
          </button>
        </form>
      )}
    </div>
  );
}

export function UnifiedDashboard({ level }: { level: ExperienceKey }) {
  const { id, partner, can, base } = usePanel();
  const [range, setRange] = useState<RangeKey>("this_month");
  const [applied, setApplied] = useState<{ from?: string; to?: string }>({});
  const ready = range !== "custom" || (applied.from && applied.to);
  const [load] = useLoad(
    () => (ready ? panelApi.dashboard(id, range, applied.from, applied.to) : new Promise<DashboardData>(() => {})),
    [id, range, applied.from ?? "", applied.to ?? ""],
  );
  const promotes = can("referral.view");
  const [overview] = useLoad(
    () =>
      promotes && ready
        ? panelApi.affiliateOverview(id, range, applied.from, applied.to)
        : Promise.resolve(null as AffiliateOverview | null),
    [id, range, applied.from ?? "", applied.to ?? "", promotes],
  );
  const aff = overview.status === "ready" ? overview.data : null;

  // ADR-063: before KYC approval and activation, the dashboard is the
  // onboarding status - nothing commercial is offered.
  if (partner && onboardingOnly(partner)) return <OnboardingDashboard />;

  return (
    <div className={styles.page}>
      <PageHeader
        title="Dashboard"
        lead={`Your partner business overview, ${partner?.display_name ?? "partner"}.`}
        actions={
          <RangePicker
            range={range}
            onRange={(r) => {
              setRange(r);
              if (r !== "custom") setApplied({});
            }}
            onCustom={(from, to) => setApplied({ from, to })}
          />
        }
      />
      {level === "affiliate" && aff && <LinkBlock link={aff.primary_link} createHref={can("referral.create") ? `${base}/referral-links` : null} />}
      {!ready && <p className={styles.muted}>Choose the first and last day of the period, then Apply.</p>}
      {ready && (
        <LoadGate load={load} what="the dashboard">
          {(d) => (
            <>
              <Overview d={d} />
              <div className={styles.dashRow}>
                <PendingActions d={d} />
                <QuickActions referralUrl={aff?.primary_link?.url ?? null} />
              </div>
              <div className={styles.dashRow}>
                <TrendChart d={d} />
                <ProductSplit d={d} />
              </div>
              <div className={styles.dashRow}>
                {d.referrals && <ReferralPerformance d={d} />}
                {can("customer.view") && <UpcomingRenewals />}
              </div>
              <div className={styles.dashRow}>
                {d.platform_wallet && <RecentTransactions />}
                {d.recent.commissions && <RecentCommissions d={d} />}
              </div>
              <div className={styles.dashRow}>
                {d.customers ? <RecentCustomers d={d} /> : aff ? <RecentConversions aff={aff} /> : null}
                <Earnings d={d} />
              </div>
            </>
          )}
        </LoadGate>
      )}
      {level === "white_label" && <WhiteLabelPart />}
      {can("product.view") && level !== "affiliate" && <ProductActivity />}
      <div className={styles.dashRow}>
        <LevelCard level={level} />
      </div>
      <section className={styles.panel} aria-labelledby="matrix">
        <h2 id="matrix">Feature access by partner level</h2>
        <LevelMatrix current={level} caption="What each partner level includes" />
      </section>
      {level !== "white_label" && <UpgradeGrow workspace={level} />}
      <NotificationsPreview />
    </div>
  );
}

/** The amount in the partner's own currency, else the first one there is. */
function primary<T extends { currency: string }>(rows: T[] | null | undefined, currency: string): T | null {
  if (!rows || rows.length === 0) return null;
  return rows.find((r) => r.currency === currency) ?? rows[0];
}

/**
 * The six overview cards of the 11 November 2026 brief (section 5): account
 * status, referral conversions, customer renewals, the Platform Wallet, pending
 * earnings and available commissions. Each is the server's figure, and a card
 * whose figure this partner may not see is not drawn - never a zero in its place.
 */
function Overview({ d }: { d: DashboardData }) {
  const { base, can } = usePanel();
  const currency = d.partner.currency;
  const o = d.onboarding;
  const r = d.referrals;
  const wallet = primary(d.platform_wallet, currency);
  const earned = primary(d.money?.commission, currency);
  const others = (n: number) => (n > 1 ? ` · ${n - 1} more ${n - 1 === 1 ? "currency" : "currencies"}` : "");
  const cards = [
    o && (
      <StatCard
        key="status"
        icon="shield"
        tone={o.account_status === "active" && o.cleared ? "green" : "orange"}
        label="Account status"
        value={ACCOUNT_WORDS[o.account_status] ?? o.account_status}
        note={`KYC: ${KYC_WORDS[o.kyc_status] ?? o.kyc_status}`}
        href={`${base}/business-kyc`}
      />
    ),
    r && (
      <StatCard
        key="conversions"
        icon="users"
        tone="blue"
        label="Referral conversions"
        value={r.conversions}
        note={`${r.clicks} clicks · ${r.registrations} registrations in this period`}
        href={can("referral.view") ? `${base}/leads` : undefined}
      />
    ),
    r && <StatCard key="renewals" icon="refresh" tone="purple" label="Customer renewals" value={r.renewals} note="Paid renewals in this period" />,
    d.platform_wallet && (
      <StatCard
        key="wallet"
        icon="wallet"
        tone="blue"
        label="Platform Wallet balance"
        value={wallet ? money(wallet.balance_minor, wallet.currency) : money(0, currency)}
        note={wallet?.is_low ? "Low - add money before the next activation" : `Pays for customer plans${others(d.platform_wallet.length)}`}
        href={`${base}/platform-wallet`}
      />
    ),
    d.money && (
      <StatCard
        key="pending"
        icon="clock"
        tone="orange"
        label="Pending earnings"
        value={earned ? money(earned.pending_minor, earned.currency) : money(0, currency)}
        note="Still in the holding period"
      />
    ),
    d.money && (
      <StatCard
        key="available"
        icon="currency"
        tone="green"
        label="Available commissions"
        value={earned ? money(earned.available_minor, earned.currency) : money(0, currency)}
        note="Ready to withdraw"
        href={can("payout.view") ? `${base}/wallet` : undefined}
      />
    ),
  ].filter(Boolean);
  if (cards.length === 0) return null;
  return <StatGrid label="Overview">{cards}</StatGrid>;
}

/** What needs the partner's attention, from the server's own facts only. */
function PendingActions({ d }: { d: DashboardData }) {
  const { base, can } = usePanel();
  const titleId = useId();
  const currency = d.partner.currency;
  const earned = primary(d.money?.commission, currency);
  const low = (d.platform_wallet ?? []).filter((w) => w.is_low);
  const items: { text: string; href: string; action: string }[] = [];
  if (d.onboarding && !d.onboarding.cleared) {
    items.push({ text: "Your KYC is not approved yet.", href: `${base}/business-kyc`, action: "Complete KYC" });
  }
  for (const w of low) {
    items.push({
      text: `Your Platform Wallet is low (${money(w.balance_minor, w.currency)}).`,
      href: `${base}/platform-wallet`,
      action: "Add money",
    });
  }
  if (d.tickets && d.tickets.breached > 0) {
    items.push({
      text: `${d.tickets.breached} support ${d.tickets.breached === 1 ? "ticket is" : "tickets are"} past the deadline.`,
      href: `${base}/support-manager`,
      action: "Open tickets",
    });
  }
  if (earned && earned.available_minor > 0 && can("payout.view")) {
    items.push({
      text: `${money(earned.available_minor, earned.currency)} is available to withdraw.`,
      href: `${base}/wallet`,
      action: "Withdraw",
    });
  }
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <h2 id={titleId}>Pending actions</h2>
      {items.length === 0 ? (
        <p className={styles.muted}>Nothing needs your attention right now.</p>
      ) : (
        <ul className={styles.pendingList}>
          {items.map((i) => (
            <li key={i.text}>
              <span>{i.text}</span>
              <Link href={i.href} className="w360-btn w360-btn-secondary w360-btn-sm">
                {i.action}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Customer plans that renew or end within 30 days (the server's "expiring"). */
function UpcomingRenewals() {
  const { id, base } = usePanel();
  const titleId = useId();
  const [load] = useLoad(() => walletApi.subscriptions(id, "expiring"), [id]);
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId}>Upcoming renewals</h2>
        <Link href={`${base}/users/subscriptions`}>View all</Link>
      </div>
      <LoadGate load={load} what="the renewals">
        {(r) => <RenewalTable rows={r.items.slice(0, 5)} />}
      </LoadGate>
    </section>
  );
}

function RenewalTable({ rows }: { rows: PartnerSubscriptionRow[] }) {
  return (
    <Table
      caption="Customer plans that renew soon"
      head={["Customer", "Plan", "Renews", "Paid by"]}
      isEmpty={rows.length === 0}
      empty="No plan renews in the next 30 days."
    >
      {rows.map((row) => (
        <tr key={row.subscription_id}>
          <th scope="row">{row.customer || "Customer"}</th>
          <td>{row.lines.map((l) => `${l.product_name} ${l.plan_name}`).join(", ") || "—"}</td>
          <td>{when(row.renewal_date ?? row.current_period_end)}</td>
          <td>{row.paid_by === "partner_wallet" ? "Your Platform Wallet" : "The customer"}</td>
        </tr>
      ))}
    </Table>
  );
}

/** The Platform Wallet's latest movements - spendable money, never earnings. */
function RecentTransactions() {
  const { id, base, can } = usePanel();
  const titleId = useId();
  const [load] = useLoad(() => walletApi.wallet(id), [id]);
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId}>Recent transactions</h2>
        <span className={styles.panelLinks}>
          <Link href={`${base}/platform-wallet/transactions`}>View all</Link>
          {can("wallet.manage") && (
            <Link href={`${base}/platform-wallet`} className="w360-btn w360-btn-primary w360-btn-sm">
              Add Money
            </Link>
          )}
        </span>
      </div>
      <LoadGate load={load} what="the wallet">
        {(w) => <TransactionTable rows={w.recent.slice(0, 5)} />}
      </LoadGate>
    </section>
  );
}

function TransactionTable({ rows }: { rows: WalletEntry[] }) {
  return (
    <Table
      caption="Your Platform Wallet's latest movements"
      head={["Date", "Type", "Details", "Amount"]}
      numeric={["Amount"]}
      isEmpty={rows.length === 0}
      empty="No wallet movements yet."
    >
      {rows.map((e) => (
        <tr key={e.id}>
          <th scope="row">{when(e.created_at)}</th>
          <td>{e.entry_type_label}</td>
          <td>{e.customer?.name ? `${e.customer.name}${e.order ? ` · ${e.order.number}` : ""}` : e.reason || "—"}</td>
          <td className={styles.num}>{money(e.amount_minor, e.currency)}</td>
        </tr>
      ))}
    </Table>
  );
}

/** The commission ledger's three kinds of row (contracts AccrualKind). */
const COMMISSION_KIND: Record<string, string> = {
  accrual: "Commission earned",
  reversal: "Reversed",
  adjustment: "Adjustment",
};

/** The commission ledger's latest entries (the server's `recent.commissions`). */
function RecentCommissions({ d }: { d: DashboardData }) {
  const { base, can } = usePanel();
  const titleId = useId();
  const rows = (d.recent.commissions ?? []).slice(0, 5);
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId}>Recent commissions</h2>
        {can("commission.view") && <Link href={`${base}/payments/commissions`}>View all</Link>}
      </div>
      <Table
        caption="Your latest commission entries"
        head={["Date", "Entry", "Amount"]}
        numeric={["Amount"]}
        isEmpty={rows.length === 0}
        empty="No commission yet."
      >
        {rows.map((c) => (
          <tr key={c.id}>
            <th scope="row">{when(c.event_at)}</th>
            <td>{COMMISSION_KIND[c.kind] ?? c.kind}</td>
            <td className={styles.num}>{money(c.amount_minor, c.currency)}</td>
          </tr>
        ))}
      </Table>
    </section>
  );
}

const W = 480;
const H = 240;
const PAD = { top: 16, right: 12, bottom: 28, left: 12 };

function days(from: string, to: string): string[] {
  const out: string[] = [];
  const start = new Date(from);
  const end = new Date(to);
  const day = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
  while (day < end && out.length < 400) {
    out.push(day.toISOString().slice(0, 10));
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return out;
}

/** Sales and commission per day on one money axis (same currency, so one
 * scale - never two y-axes), with a crosshair tooltip and a table view. */
function TrendChart({ d }: { d: DashboardData }) {
  const [table, setTable] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const charts = d.charts;
  const currency = d.partner.currency;
  if (!charts) {
    return (
      <section className={styles.panel} aria-labelledby={titleId}>
        <h2 id={titleId}>Sales &amp; commission trend</h2>
        <p className={styles.muted}>Needs commission access.</p>
      </section>
    );
  }
  const byDay = (series: { date: string; amount_minor: number }[] | undefined) =>
    Object.fromEntries((series ?? []).map((p) => [p.date, p.amount_minor]));
  const sales = byDay(charts.gross[currency]);
  const commission = byDay(charts.commission[currency]);
  const axis = days(d.range.from, d.range.to);
  const points = axis.map((date) => ({ date, sales: sales[date] ?? 0, commission: commission[date] ?? 0 }));
  const max = Math.max(1, ...points.map((p) => Math.max(p.sales, p.commission)));
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (points.length <= 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
  const y = (v: number) => PAD.top + innerH - (v / max) * innerH;
  const line = (key: "sales" | "commission") => points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const empty = points.every((p) => p.sales === 0 && p.commission === 0);
  const shown = hover !== null ? points[hover] : null;

  return (
    <section className={`${styles.panel} ${styles.trend}`} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId}>Sales &amp; commission trend</h2>
        <button type="button" className="w360-btn w360-btn-secondary" aria-pressed={table} onClick={() => setTable((t) => !t)}>
          {table ? "Show chart" : "Show as table"}
        </button>
      </div>
      <ul className={styles.legend} aria-label="Series">
        <li>
          <Swatch color={SERIES[0]} /> Sales ({currency})
        </li>
        <li>
          <Swatch color={SERIES[1]} /> Commission ({currency})
        </li>
      </ul>
      {empty ? (
        <EmptyState title="No sales in this period" icon="chart">
          <span>Sales you make, and the commission they earn, appear here as soon as they are recorded.</span>
        </EmptyState>
      ) : table ? (
        <Table caption={`Sales and commission by day (${currency})`} head={["Date", "Sales", "Commission"]}>
          {points
            .filter((p) => p.sales || p.commission)
            .map((p) => (
              <tr key={p.date}>
                <th scope="row">{p.date}</th>
                <td className={styles.num}>{money(p.sales, currency)}</td>
                <td className={styles.num}>{money(p.commission, currency)}</td>
              </tr>
            ))}
        </Table>
      ) : (
        <figure className={styles.chartFrame}>
          <svg
            className={styles.chart}
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={`Sales and commission by day, ${points.length} days, highest ${money(max, currency)}`}
            onMouseLeave={() => setHover(null)}
          >
            <line className={styles.chartGrid} x1={PAD.left} x2={W - PAD.right} y1={PAD.top + innerH} y2={PAD.top + innerH} />
            <line className={styles.chartGrid} x1={PAD.left} x2={W - PAD.right} y1={PAD.top} y2={PAD.top} strokeDasharray="3 4" />
            <text className={styles.chartAxis} x={PAD.left} y={PAD.top - 4}>
              {money(max, currency)}
            </text>
            <path d={line("sales")} fill="none" stroke={SERIES[0]} strokeWidth={2} strokeLinejoin="round" />
            <path d={line("commission")} fill="none" stroke={SERIES[1]} strokeWidth={2} strokeLinejoin="round" />
            {shown && hover !== null && (
              <g>
                <line className={styles.crosshair} x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} />
                <circle cx={x(hover)} cy={y(shown.sales)} r={4} fill={SERIES[0]} stroke="#fff" strokeWidth={2} />
                <circle cx={x(hover)} cy={y(shown.commission)} r={4} fill={SERIES[1]} stroke="#fff" strokeWidth={2} />
              </g>
            )}
            {points.map((p, i) => (
              <rect
                key={p.date}
                x={x(i) - innerW / Math.max(points.length, 1) / 2}
                y={PAD.top}
                width={innerW / Math.max(points.length, 1)}
                height={innerH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
              />
            ))}
            <text className={styles.chartAxis} x={PAD.left} y={H - 8}>
              {points[0]?.date}
            </text>
            <text className={styles.chartAxis} x={W - PAD.right} y={H - 8} textAnchor="end">
              {points[points.length - 1]?.date}
            </text>
          </svg>
          {shown && (
            <figcaption className={styles.tooltip} aria-live="polite">
              <strong>{shown.date}</strong>
              <span>Sales: {money(shown.sales, currency)}</span>
              <span>Commission: {money(shown.commission, currency)}</span>
            </figcaption>
          )}
        </figure>
      )}
    </section>
  );
}

/** A legend swatch: an SVG square (no inline style - colours are
 * attributes, the same as the chart's marks). */
function Swatch({ color }: { color: string }) {
  return (
    <svg className={styles.swatch} viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      <rect width="12" height="12" rx="3" fill={color} />
    </svg>
  );
}

/** One bar split into the products' shares, a 2px surface gap between parts.
 * Geometry only; the numbers are in the labelled legend below it. */
function SegmentedBar({ parts, currency }: { parts: (SplitPart & { color: string })[]; currency: string }) {
  const GAP = 0.4;
  const widths = parts.map((p) => Math.max(0.6, p.share_bp / 100 - GAP));
  const starts = widths.map((_, i) => widths.slice(0, i).reduce((at, w) => at + w + GAP, 0));
  return (
    <svg className={styles.segmented} viewBox="0 0 100 4" preserveAspectRatio="none" role="img" aria-label="Share of sales by product, listed below">
      {parts.map((p, i) => (
        <rect key={p.product_code || "others"} x={starts[i]} y={0} width={widths[i]} height={4} fill={p.color}>
          <title>{`${p.product_name}: ${money(p.gross_minor, currency)}`}</title>
        </rect>
      ))}
    </svg>
  );
}

/** The product-wise split as one segmented bar (part-to-whole), the five
 * largest products and "Other", each labelled with its share and amount. */
function ProductSplit({ d }: { d: DashboardData }) {
  const titleId = useId();
  const currency = d.partner.currency;
  // The server folds the long tail into "Others" and works out each share.
  const split = d.products?.split?.[currency];
  const parts = (split?.parts ?? []).map((p, i) => ({ ...p, color: p.product_code ? SERIES[i] ?? OTHER : OTHER }));
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <h2 id={titleId}>Product-wise sales</h2>
      {d.products?.sales == null ? (
        <p className={styles.muted}>Needs commission access.</p>
      ) : !split ? (
        <EmptyState title="No product sales yet" icon="apps">
          <span>Each product&apos;s share of your sales shows here.</span>
        </EmptyState>
      ) : (
        <>
          <p className={styles.splitTotal}>
            <strong>{money(split.total_minor, currency)}</strong> <span className={styles.muted}>across {split.products} products</span>
          </p>
          <SegmentedBar parts={parts} currency={currency} />
          <ul className={styles.splitLegend}>
            {parts.map((p) => (
              <li key={p.product_code || "others"}>
                <Swatch color={p.color} />
                <span className={styles.splitName}>{p.product_name}</span>
                <span className={styles.num}>{Math.round(p.share_bp / 100)}%</span>
                <span className={`${styles.num} ${styles.muted}`}>{money(p.gross_minor, currency)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** The specification's quick actions (4.2), each only for the permission it
 * needs: referral links for a promoter, plan activation and Add Money for a
 * reseller, support for everyone. */
function QuickActions({ referralUrl }: { referralUrl: string | null }) {
  const { base, can } = usePanel();
  const items = [
    {
      href: `${base}/customers/activate`,
      label: "Activate Customer Plan",
      icon: "receipt",
      show: can("subscription.sell") && can("customer.view"),
    },
    { href: `${base}/platform-wallet`, label: "Add Money", icon: "card", show: can("wallet.manage") },
    { href: `${base}/users#add-customer`, label: "Create Customer", icon: "users", show: can("customer.provision") },
    {
      href: `${base}/referral-links`,
      label: "Generate Referral Link",
      icon: "key",
      show: can("referral.create") && !referralUrl,
    },
    { href: `${base}/support-manager?new=1`, label: "Contact Support", icon: "help", show: can("dashboard.view") },
  ].filter((i) => i.show);
  if (items.length === 0 && !referralUrl) return null;
  return (
    <nav aria-label="Quick actions" className={styles.quickActions}>
      {referralUrl && can("referral.view") && (
        <span className={styles.quickAction}>
          <span className={styles.quickIcon} aria-hidden="true">
            <NavIcon name="key" />
          </span>
          <CopyButton text={referralUrl} label="Referral Link" />
        </span>
      )}
      {items.map((i) => (
        <Link key={i.label} href={i.href} className={styles.quickAction}>
          <span className={styles.quickIcon} aria-hidden="true">
            <NavIcon name={i.icon} />
          </span>
          {i.label}
        </Link>
      ))}
    </nav>
  );
}

/** Referral performance (4.1.2), from the tracked platform events. */
function ReferralPerformance({ d }: { d: DashboardData }) {
  const { base } = usePanel();
  const titleId = useId();
  const r = d.referrals;
  if (!r) return null;
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId}>Referral performance</h2>
        <Link href={`${base}/leads`}>Referral results</Link>
      </div>
      <dl className={styles.earnings}>
        <div>
          <dt>Referral clicks</dt>
          <dd>{r.clicks}</dd>
        </div>
        <div>
          <dt>Registrations</dt>
          <dd>{r.registrations}</dd>
        </div>
        <div>
          <dt>Eligible conversions</dt>
          <dd>{r.conversions}</dd>
        </div>
        <div>
          <dt>Customer renewals</dt>
          <dd>{r.renewals}</dd>
        </div>
      </dl>
    </section>
  );
}

/** What a partner sees before it may trade (ADR-063, the specification's 2.2):
 * where it stands and the next step - business profile, KYC, support. */
function OnboardingDashboard() {
  const { partner, base } = usePanel();
  const o = partner?.onboarding;
  const kyc = o?.kyc_status ?? "draft";
  return (
    <div className={styles.page}>
      <PageHeader
        title="Dashboard"
        lead={`Welcome, ${partner?.display_name ?? "partner"}. Your account opens fully once your KYC is approved and your account is active.`}
      />
      <section className={styles.panel} aria-labelledby="onboarding-status">
        <h2 id="onboarding-status">Onboarding status</h2>
        <KycProgress kyc={kyc} account={o?.account_status ?? partner?.status ?? "draft"} />
        <p className={styles.actionsRow}>
          <Link className="w360-btn w360-btn-primary" href={`${base}/business-kyc`}>
            Business & KYC
          </Link>
          <Link className="w360-btn w360-btn-secondary" href={`${base}/support-manager?new=1`}>
            Contact Support
          </Link>
          <Link className="w360-btn w360-btn-secondary" href={`${base}/account`}>
            My Account & Security
          </Link>
        </p>
        <p className={styles.muted}>
          Until then, referral links, customers, plan activation, the Platform Wallet and payouts are not available.
        </p>
      </section>
    </div>
  );
}

function RecentCustomers({ d }: { d: DashboardData }) {
  const { base } = usePanel();
  const titleId = useId();
  const rows = d.recent.customers ?? [];
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId}>Recent customers</h2>
        <Link href={`${base}/users`}>View all</Link>
      </div>
      <Table caption="Your newest customers" head={["Name", "Products", "Status", "Since"]} isEmpty={rows.length === 0} empty="No customers yet.">
        {rows.map((c) => {
          const row = c as typeof c & { products?: string[] };
          return (
            <tr key={c.organization_id}>
              <th scope="row">{c.name || "Customer"}</th>
              <td>{row.products?.length ? row.products.join(", ") : "—"}</td>
              <td>
                <Status value={c.link_status} />
              </td>
              <td>{when(c.started_at)}</td>
            </tr>
          );
        })}
      </Table>
    </section>
  );
}

function RecentConversions({ aff }: { aff: AffiliateOverview }) {
  const { base } = usePanel();
  const titleId = useId();
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId}>Recent conversions</h2>
        <Link href={`${base}/conversions`}>View all</Link>
      </div>
      <ConversionTable rows={aff.recent.conversions} fallbackCurrency={aff.partner.currency} caption="Your latest conversions" />
    </section>
  );
}

/** The specification's earnings card: total, approved (cleared the holding
 * period), pending, available - and the way to withdraw it. */
const paid = (d: DashboardData, currency: string): number | null =>
  d.money?.balances.find((b) => b.currency === currency)?.paid_minor ?? null;

function Earnings({ d }: { d: DashboardData }) {
  const { base, can } = usePanel();
  const titleId = useId();
  const split = d.money?.commission ?? [];
  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId}>My earnings</h2>
        {can("commission.view") && <Link href={`${base}/payments/commissions`}>View all</Link>}
      </div>
      {!d.money ? (
        <p className={styles.muted}>Needs commission access.</p>
      ) : split.length === 0 ? (
        <p className={styles.muted}>No commission yet. What you earn appears here.</p>
      ) : (
        split.map((m) => (
          <dl key={m.currency} className={styles.earnings}>
            <div>
              <dt>Total commission</dt>
              <dd>{money(m.total_minor, m.currency)}</dd>
            </div>
            <div>
              <dt>Approved commission</dt>
              <dd>{money(m.approved_minor, m.currency)}</dd>
            </div>
            <div>
              <dt>Pending commission</dt>
              <dd>{money(m.pending_minor, m.currency)}</dd>
            </div>
            <div data-strong>
              <dt>Available earnings</dt>
              <dd>{money(m.available_minor, m.currency)}</dd>
            </div>
            {paid(d, m.currency) !== null && (
              <div>
                <dt>Paid commissions</dt>
                <dd>{money(paid(d, m.currency) ?? 0, m.currency)}</dd>
              </div>
            )}
          </dl>
        ))
      )}
      {can("payout.view") && (
        <p>
          <Link className={`w360-btn w360-btn-primary ${styles.wide}`} href={`${base}/wallet`}>
            Withdraw earnings
          </Link>
        </p>
      )}
    </section>
  );
}

function WhiteLabelPart() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.whiteLabelStatus(id), [id]);
  const status = load.status === "ready" && (load.data.brand || load.data.domain) ? load.data : null;
  return status ? <WhiteLabelStatusSection s={status} base={base} /> : null;
}

/** "Your Partner Level", the three levels and the feature matrix. */
function LevelCard({ level }: { level: ExperienceKey }) {
  const { id } = usePanel();
  const [open, setOpen] = useState(false);
  const [load] = useLoad(() => panelApi.upgrade(id), [id]);
  const state = load.status === "ready" ? load.data : null;
  const def = WORKSPACES[level];
  const fee = levelFee(level, state);
  return (
    <>
      <section className={`${styles.panel} ${styles.levelCard}`} aria-labelledby="your-level">
        <h2 id="your-level">Your partner level</h2>
        <p className={styles.levelHead}>
          <strong>{def.level.name}</strong> <span className="w360-badge w360-badge-success">Level {def.level.number}</span>
        </p>
        <p className={styles.muted}>{LEVEL_LEAD[level]}</p>
        {fee && <p className={styles.levelFee}>Set-up: {fee}</p>}
        {level !== "white_label" && (
          <button type="button" className="w360-btn w360-btn-primary" onClick={() => setOpen(true)}>
            Upgrade plan
          </button>
        )}
      </section>
      <section className={styles.panel} aria-labelledby="levels">
        <h2 id="levels">Partner levels</h2>
        <LevelList current={level} state={state} />
      </section>
      {open && <UpgradeDialog current={level} onClose={() => setOpen(false)} />}
    </>
  );
}
