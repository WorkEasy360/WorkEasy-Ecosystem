"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { NavIcon } from "@/components/sidebar/icons";
import { LoadGate, Status, Table, money, useLoad, when } from "@/features/controlplane/ui";
import {
  panelApi,
  type AffiliateOverview,
  type CurrencyAmount,
  type Dashboard as DashboardData,
  type SplitPart,
  type RangeKey,
} from "@/lib/api/partnerPanel";
import { ConversionTable, LinkBlock } from "./AffiliateScreens";
import { NotificationsPreview, ProductActivity } from "./Dashboard";
import { WORKSPACES, type ExperienceKey } from "./experiences";
import { LEVEL_LEAD, LevelList, LevelMatrix, UpgradeDialog, levelFee } from "./levels";
import { EmptyState, PageHeader, usePanel } from "./PanelKit";
import { UpgradeGrow } from "./UpgradeGrow";
import { WhiteLabelStatusSection } from "./WhiteLabelDashboard";
import styles from "./PartnerPanel.module.css";

/**
 * The unified partner dashboard (2026-11-03, specification figure 1.0 and
 * section 5): one page for every level.
 *
 * - the metric ribbon - Level 1 shows referral metrics, Levels 2 and 3 sales
 *   and margin, Level 3 adds its network - each with its change against the
 *   period of the same length before;
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

const amountIn = (amounts: CurrencyAmount[] | null | undefined, currency: string): number | null => {
  const found = amounts?.find((a) => a.currency === currency);
  return found ? found.amount_minor : amounts && amounts.length === 0 ? 0 : null;
};

/** The change of a figure against the previous period, as words a screen
 * reader says and a badge shows. `null` when there is nothing to compare. */
function delta(now: number | null | undefined, before: number | null | undefined): { text: string; tone: "up" | "down" | "flat" } | null {
  if (now == null || before == null) return null;
  if (before === 0) return now === 0 ? { text: "No change", tone: "flat" } : { text: "New this period", tone: "up" };
  const pct = Math.round(((now - before) / Math.abs(before)) * 100);
  if (pct === 0) return { text: "No change", tone: "flat" };
  return { text: `${pct > 0 ? "↑" : "↓"} ${Math.abs(pct)}%`, tone: pct > 0 ? "up" : "down" };
}

function Metric({
  icon,
  label,
  value,
  change,
  note,
}: {
  icon: string;
  label: string;
  value: React.ReactNode;
  change?: ReturnType<typeof delta>;
  note?: string;
}) {
  return (
    <li className={styles.metric}>
      <span className={styles.metricIcon} aria-hidden="true">
        <NavIcon name={icon} />
      </span>
      <span className={styles.metricBody}>
        <span className={styles.metricLabel}>{label}</span>
        <span className={styles.metricValue}>{value}</span>
        {change && (
          <span className={styles.metricDelta} data-tone={change.tone}>
            {change.text}
            <span className="w360-visually-hidden"> compared with the previous period</span>
          </span>
        )}
        {note && <span className={styles.metricNote}>{note}</span>}
      </span>
    </li>
  );
}

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

  return (
    <div className={styles.page}>
      <PageHeader
        title="Partner Dashboard"
        lead={`Welcome back, ${partner?.display_name ?? "partner"}! Here's your business overview.`}
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
      <div className={styles.dashGrid}>
        <div className={styles.dashMain}>
          {level === "affiliate" && aff && <LinkBlock link={aff.primary_link} createHref={can("referral.create") ? `${base}/referral-links` : null} />}
          {!ready && <p className={styles.muted}>Choose the first and last day of the period, then Apply.</p>}
          {ready && <LoadGate load={load} what="the dashboard">
            {(d) => (
              <>
                <Ribbon d={d} level={level} aff={aff} />
                <div className={styles.dashRow}>
                  <TrendChart d={d} />
                  <ProductSplit d={d} />
                </div>
                <QuickActions />
                <div className={styles.dashRow}>
                  {d.customers ? <RecentCustomers d={d} /> : aff ? <RecentConversions aff={aff} /> : null}
                  <Earnings d={d} />
                </div>
              </>
            )}
          </LoadGate>}
          {level === "white_label" && <WhiteLabelPart />}
          {can("product.view") && level !== "affiliate" && <ProductActivity />}
          <section className={styles.panel} aria-labelledby="matrix">
            <h2 id="matrix">Feature access by partner level</h2>
            <LevelMatrix current={level} caption="What each partner level includes" />
          </section>
          {level !== "white_label" && <UpgradeGrow workspace={level} />}
          <NotificationsPreview />
        </div>
        <aside className={styles.dashSide} aria-label="Your partner level">
          <LevelCard level={level} />
        </aside>
      </div>
    </div>
  );
}

function Ribbon({ d, level, aff }: { d: DashboardData; level: ExperienceKey; aff: AffiliateOverview | null }) {
  const currency = d.partner.currency;
  const gross = amountIn(d.sales?.gross, currency);
  const grossBefore = amountIn(d.previous?.sales?.gross, currency);
  const earned = amountIn(d.sales?.commission_earned, currency);
  const earnedBefore = amountIn(d.previous?.sales?.commission_earned, currency);
  const noMoney = "Needs commission access";
  const tickets = d.tickets ? (
    <Metric
      icon="help"
      label="Open tickets"
      value={d.tickets.open}
      note={d.tickets.breached ? `${d.tickets.breached} past their deadline` : "All within their deadlines"}
    />
  ) : null;
  return (
    <ul className={styles.ribbon} aria-label="Key figures">
      {level === "affiliate" ? (
        <>
          <Metric icon="network" label="Referral clicks" value={aff ? aff.kpis.clicks : "—"} note={aff ? `${aff.kpis.unique_visitors} unique visitors` : undefined} />
          <Metric icon="users" label="Conversions" value={aff ? aff.kpis.conversions : "—"} note={aff ? `${aff.kpis.signups} sign-ups` : undefined} />
        </>
      ) : (
        <>
          <Metric
            icon="users"
            label="Total customers"
            value={d.customers ? d.customers.total : "—"}
            note={d.customers ? `${d.customers.active} active · ${d.customers.new ?? 0} new` : "Not shown for your role"}
            change={d.customers ? delta(d.customers.new, d.previous?.new_customers) : null}
          />
          <Metric icon="apps" label="Active products" value={d.products?.active ?? "—"} />
        </>
      )}
      <Metric
        icon="receipt"
        label="Total sales"
        value={d.sales ? (gross == null ? "—" : money(gross, currency)) : "—"}
        note={d.sales ? `${d.sales.transactions} sales` : noMoney}
        change={d.sales ? delta(gross, grossBefore) : null}
      />
      <Metric
        icon="currency"
        label="My commission"
        value={d.sales ? (earned == null ? "—" : money(earned, currency)) : "—"}
        note={d.sales ? undefined : noMoney}
        change={d.sales ? delta(earned, earnedBefore) : null}
      />
      {level === "white_label" && d.sub_partners ? (
        <Metric icon="handshake" label="Downline partners" value={d.sub_partners.total} note={`${d.sub_partners.active ?? 0} active`} />
      ) : null}
      {tickets}
    </ul>
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

function QuickActions() {
  const { base, can } = usePanel();
  const items = [
    { href: `${base}/users#add-customer`, label: "Create Customer", icon: "users", show: can("customer.provision") },
    { href: `${base}/referral-links`, label: "Generate Referral Link", icon: "key", show: can("referral.create") },
    { href: `${base}/users`, label: "Add Order", icon: "receipt", show: can("subscription.sell") },
    { href: `${base}/payments/commissions`, label: "My Commission", icon: "currency", show: can("commission.view") },
    { href: `${base}/support-manager?new=1`, label: "Raise Support Ticket", icon: "help", show: can("dashboard.view") },
    { href: `${base}/marketing-materials`, label: "Marketing Assets", icon: "sparkles", show: can("referral.view") },
  ].filter((i) => i.show);
  if (items.length === 0) return null;
  return (
    <nav aria-label="Quick actions" className={styles.quickActions}>
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
              <dt>Available balance</dt>
              <dd>{money(m.available_minor, m.currency)}</dd>
            </div>
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
