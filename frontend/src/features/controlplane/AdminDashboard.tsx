"use client";

import { useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import {
  adminPanelApi,
  type ActivityEvent,
  type DashboardOverviewV2,
  type PendingAction,
  type RunningTotal,
  type TopProduct,
  type WalletTotal,
} from "@/lib/api/adminPanel";
import { can, useAdmin } from "./ControlShell";
import {
  Delta,
  EmptyState,
  InfoTip,
  Page,
  PageHeader,
  SectionHeader,
  Select,
  formatDate,
  formatMoney,
  formatNumber,
  formatRelative,
  kit,
  type SelectOption,
  type StatTone,
} from "./kit";
import { GroupedBars, MultiLineChart, monthLabel } from "./trendCharts";
import { LoadGate, useLoad } from "./ui";
import styles from "./AdminDashboard.module.css";

/**
 * The Admin dashboard (2026-11-09, the owner's reference image): six figures for the
 * period chosen once in the header, two month-by-month charts each with its own range,
 * the three balances, what is waiting, what just happened and the top products.
 *
 * Real figures only, from `commercial/controlplane/overview.py`, which also states what
 * Sales and Collections mean. Every section is drawn only when the backend sent it (it
 * sends only what the staff member's grants cover). Money stays per currency.
 */
export function Dashboard() {
  const [period, setPeriod] = useState<Period>({ days: 30 });
  const [currency, setCurrency] = useState<string | undefined>(undefined);
  const [load] = useLoad(() => adminPanelApi.overview({ ...period, currency }), [period.days, period.from, period.to, currency]);
  return (
    <Page>
      <PageHeader
        title="Dashboard"
        subtitle="Overview of platform performance, collections, subscriptions and pending actions."
        primary={<PeriodPicker value={period} onChange={setPeriod} />}
        actions={
          load.status === "ready" && (load.data.currencies?.length ?? 0) > 1 ? (
            <Select
              label="Currency"
              value={load.data.currency ?? ""}
              options={(load.data.currencies ?? []).map((c) => ({ value: c, label: c }))}
              onChange={setCurrency}
            />
          ) : undefined
        }
      />
      <LoadGate load={load} what="the dashboard">
        {(d) => <Overview d={d} />}
      </LoadGate>
    </Page>
  );
}

// --- the period ----------------------------------------------------------------------

type Period = { days: number; from?: undefined; to?: undefined } | { days?: undefined; from: string; to: string };

const PRESETS: SelectOption[] = [
  { value: "7", label: "Last 7 Days" },
  { value: "30", label: "Last 30 Days" },
  { value: "90", label: "Last 90 Days" },
  { value: "180", label: "Last 180 Days" },
  { value: "365", label: "Last 365 Days" },
  { value: "custom", label: "Custom range" },
];

const iso = (date: Date) => date.toISOString().slice(0, 10);

function PeriodPicker({ value, onChange }: { value: Period; onChange: (next: Period) => void }) {
  const custom = value.days === undefined;
  const today = iso(new Date());
  return (
    <div className={styles.period}>
      <Select
        label="Period"
        value={custom ? "custom" : String(value.days)}
        options={PRESETS}
        onChange={(next) => {
          if (next === "custom") onChange({ from: iso(new Date(Date.now() - 29 * 86_400_000)), to: today });
          else onChange({ days: Number(next) });
        }}
      />
      {custom && (
        <>
          <label className={styles.dateField}>
            <span className="w360-visually-hidden">From</span>
            <input
              type="date"
              className="w360-input"
              value={value.from}
              max={value.to}
              onChange={(e) => e.target.value && onChange({ from: e.target.value, to: value.to })}
            />
          </label>
          <label className={styles.dateField}>
            <span className="w360-visually-hidden">To</span>
            <input
              type="date"
              className="w360-input"
              value={value.to}
              min={value.from}
              max={today}
              onChange={(e) => e.target.value && onChange({ from: value.from, to: e.target.value })}
            />
          </label>
        </>
      )}
    </div>
  );
}

// --- the figures ---------------------------------------------------------------------

function Figure({
  label,
  value,
  icon,
  tone,
  change,
  note,
  href,
  info,
}: {
  label: string;
  value: string;
  icon: string;
  tone: StatTone;
  change?: number | null;
  note?: string;
  href?: string;
  info?: string;
}) {
  const body = (
    <>
      <span className={styles.figureIcon} data-tone={tone}>
        <Icon name={icon} size={22} />
      </span>
      <span className={styles.figureText}>
        <span className={styles.figureLabel}>
          {label}
          {info && <InfoTip label={`What ${label} means`}>{info}</InfoTip>}
        </span>
        <strong className={styles.figureValue}>{value}</strong>
        <span className={styles.figureFoot}>
          {change !== undefined && change !== null ? <Delta value={change} /> : null}
          <span>{change === null || change === undefined ? (note ?? "No earlier figure") : "vs previous period"}</span>
        </span>
      </span>
    </>
  );
  return href ? (
    <Link href={href} className={styles.figureCard}>
      {body}
    </Link>
  ) : (
    <div className={styles.figureCard}>{body}</div>
  );
}

const walletMoney = (wallets: NonNullable<DashboardOverviewV2["wallets"]>, currency: string) =>
  wallets.platform.balance + wallets.consumption.balance > 0 || currency
    ? formatMoney(wallets.platform.balance + wallets.consumption.balance, currency)
    : "—";

function Overview({ d }: { d: DashboardOverviewV2 }) {
  const { permissions } = useAdmin();
  const c = d.cards;
  const currency = d.currency ?? "";
  const runningNote = (r: RunningTotal) => `+${formatNumber(r.added)} this period`;
  return (
    <>
      <div className={styles.figures} role="group" aria-label="Figures">
        <Figure
          label="Total Customers"
          value={formatNumber(c.customers.total)}
          icon="users"
          tone="green"
          change={c.customers.change_percent}
          note={runningNote(c.customers)}
          href="/admin/customers"
        />
        {c.partners && (
          <Figure
            label="Total Partners"
            value={formatNumber(c.partners.total)}
            icon="handshake"
            tone="blue"
            change={c.partners.change_percent}
            note={runningNote(c.partners)}
            href={can(permissions, "platform.staff_partner.view") ? "/admin/partners/all" : undefined}
          />
        )}
        <Figure
          label="Active Subscriptions"
          value={formatNumber(c.active_subscriptions.total)}
          icon="card"
          tone="purple"
          change={c.active_subscriptions.change_percent}
          note={runningNote(c.active_subscriptions)}
          href="/admin/subscriptions"
        />
        {c.collections && (
          <Figure
            label="Collections"
            value={formatMoney(c.collections.amount_minor, currency)}
            icon="currency"
            tone="orange"
            change={c.collections.change_percent}
            href="/admin/payments"
            info={d.definitions.collections}
          />
        )}
        {c.sales && (
          <Figure
            label="Sales"
            value={formatMoney(c.sales.amount_minor, currency)}
            icon="chart"
            tone="green"
            change={c.sales.change_percent}
            href="/admin/invoices"
            info={d.definitions.sales}
          />
        )}
        {d.wallets && (
          <Figure
            label="Wallet Summary"
            value={walletMoney(d.wallets, currency)}
            icon="wallet"
            tone="green"
            note="Platform + Consumption, now"
            href="/admin/wallets"
            info="What customers and partners hold now in their Platform and Consumption wallets. AI Credits are counted separately. Partner commissions are earnings, not a wallet."
          />
        )}
      </div>

      <div className={styles.chartRow}>
        <GrowthPanel showPartners={Boolean(c.partners)} />
        {c.sales && <MoneyPanel currency={currency} />}
        {d.wallets && <WalletPanel wallets={d.wallets} />}
      </div>

      <div className={styles.bottomRow}>
        <PendingPanel items={d.pending} />
        <ActivityPanel events={d.activity} />
        {d.top_products && <TopProductsPanel rows={d.top_products} period={d.period} />}
      </div>
    </>
  );
}

// --- panels --------------------------------------------------------------------------

function Panel({
  id,
  title,
  description,
  icon,
  actions,
  children,
}: {
  id: string;
  title: string;
  description?: React.ReactNode;
  icon?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className={`${kit.card} ${styles.panel}`} aria-labelledby={id}>
      <div className={styles.panelBody}>
        <div className={styles.panelHead}>
          {icon && (
            <span className={styles.panelIcon}>
              <Icon name={icon} size={20} />
            </span>
          )}
          <SectionHeader id={id} title={title} description={description} actions={actions} />
        </div>
        <div className={styles.panelContent}>{children}</div>
      </div>
    </section>
  );
}

const RANGE_OPTIONS: SelectOption[] = [
  { value: "3", label: "Last 3 Months" },
  { value: "6", label: "Last 6 Months" },
  { value: "12", label: "Last 12 Months" },
];

function Legend({ items }: { items: { key: string; label: string; tone: string; value?: string; change?: number | null }[] }) {
  return (
    <ul className={styles.seriesLegend}>
      {items.map((item) => (
        <li key={item.key}>
          <span aria-hidden="true" className={styles.swatch} data-tone={item.tone} />
          <span className={styles.seriesLabel}>{item.label}</span>
          {item.value && <strong>{item.value}</strong>}
          {item.change !== undefined && item.change !== null && <Delta value={item.change} />}
        </li>
      ))}
    </ul>
  );
}

function GrowthPanel({ showPartners }: { showPartners: boolean }) {
  const [months, setMonths] = useState("6");
  const [load] = useLoad(() => adminPanelApi.growth(Number(months)), [months]);
  return (
    <Panel
      id="dash-growth"
      title={showPartners ? "Customer, Partner & Subscription Trend" : "Customer & Subscription Trend"}
      description="Growth over time"
      actions={<Select label="Trend range" size="sm" value={months} options={RANGE_OPTIONS} onChange={setMonths} />}
    >
      <LoadGate load={load} what="the trend">
        {(t) => {
          const series = [
            { key: "customers", label: "Customers", tone: "green" as const, values: t.customers },
            { key: "subscriptions", label: "Subscriptions", tone: "blue" as const, values: t.subscriptions },
            ...(t.partners ? [{ key: "partners", label: "Partners", tone: "orange" as const, values: t.partners }] : []),
          ];
          const empty = series.every((s) => s.values.every((v) => v === 0));
          return empty ? (
            <EmptyState icon="chart" title="Nothing to chart yet" hint="Growth appears here as customers join." compact />
          ) : (
            <div className={styles.chartWithLegend}>
              <MultiLineChart
                caption={`Customers, subscriptions and partners at each month end, ${monthLabel(t.months[0])} to ${monthLabel(t.months[t.months.length - 1])}`}
                months={t.months}
                series={series}
                format={compact}
              />
              <Legend
                items={series.map((s) => {
                  const last = s.values[s.values.length - 1] ?? 0;
                  const first = s.values[0] ?? 0;
                  return {
                    key: s.key,
                    label: s.label,
                    tone: s.tone,
                    value: formatNumber(last),
                    change: first > 0 ? ((last - first) * 100) / first : null,
                  };
                })}
              />
            </div>
          );
        }}
      </LoadGate>
    </Panel>
  );
}

function MoneyPanel({ currency }: { currency: string }) {
  const [months, setMonths] = useState("6");
  const [load] = useLoad(() => adminPanelApi.money(Number(months), currency || undefined), [months, currency]);
  return (
    <Panel
      id="dash-money"
      title="Collections vs Sales"
      description={`Amount (${currency || "per currency"}) by month`}
      actions={<Select label="Collections range" size="sm" value={months} options={RANGE_OPTIONS} onChange={setMonths} />}
    >
      <LoadGate load={load} what="collections and sales">
        {(t) => {
          const series = [
            { key: "collections", label: "Collections", tone: "deep" as const, values: t.collections },
            { key: "sales", label: "Sales", tone: "light" as const, values: t.sales },
          ];
          const empty = series.every((s) => s.values.every((v) => v === 0));
          const major = (minor: number) => minor / 10 ** (t.currency === "JPY" ? 0 : 2);
          return empty ? (
            <EmptyState icon="currency" title="No money movement yet" hint={`Nothing was billed or received in ${t.currency}.`} compact />
          ) : (
            <>
              <GroupedBars
                caption={`Collections and sales per month in ${t.currency}`}
                months={t.months}
                series={series.map((s) => ({ ...s, values: s.values.map(major) }))}
                format={(n) => compact(n, t.currency)}
              />
              <Legend items={series.map((s) => ({ key: s.key, label: s.label, tone: s.tone }))} />
            </>
          );
        }}
      </LoadGate>
    </Panel>
  );
}

/** 1.2K, 3.4L (lakh) and 1.1Cr (crore) for rupees; 1.2K, 3.4M elsewhere. */
function compact(n: number, currency?: string): string {
  const trim = (v: number) => (Math.round(v * 10) / 10).toString();
  if (currency === "INR") {
    if (n >= 1e7) return `${trim(n / 1e7)}Cr`;
    if (n >= 1e5) return `${trim(n / 1e5)}L`;
  }
  if (n >= 1e9) return `${trim(n / 1e9)}B`;
  if (n >= 1e6) return `${trim(n / 1e6)}M`;
  if (n >= 1e3) return `${trim(n / 1e3)}K`;
  return trim(n);
}

function WalletRow({ wallet, icon, credits = false }: { wallet: WalletTotal; icon: string; credits?: boolean }) {
  return (
    <li>
      <Link href={credits ? "/admin/wallets/ai-credits" : wallet.kind === "platform" ? "/admin/wallets" : "/admin/wallets/consumption"} className={styles.walletRow}>
        <span className={styles.panelIcon}>
          <Icon name={icon} size={20} />
        </span>
        <span className={styles.walletText}>
          <span>{wallet.label}</span>
          <strong>
            {credits
              ? `${formatNumber(wallet.balance)} credits`
              : wallet.currency
                ? formatMoney(wallet.balance, wallet.currency)
                : formatNumber(wallet.balance)}
          </strong>
        </span>
        <span className={styles.walletCount}>
          {formatNumber(wallet.wallets)} {wallet.wallets === 1 ? "wallet" : "wallets"}
        </span>
      </Link>
    </li>
  );
}

function WalletPanel({ wallets }: { wallets: NonNullable<DashboardOverviewV2["wallets"]> }) {
  return (
    <Panel id="dash-wallets" title="Wallet Balance Summary" description="Current platform wallet balances">
      <ul className={styles.walletList}>
        <WalletRow wallet={wallets.platform} icon="wallet" />
        <WalletRow wallet={wallets.ai_credits} icon="sparkles" credits />
        <WalletRow wallet={wallets.consumption} icon="bolt" />
      </ul>
    </Panel>
  );
}

const PENDING_ICON: Record<string, { icon: string; tone: StatTone }> = {
  kyc_reviews: { icon: "shield", tone: "red" },
  failed_payments: { icon: "card", tone: "orange" },
  low_balance: { icon: "wallet", tone: "orange" },
};

function PendingPanel({ items }: { items: PendingAction[] }) {
  return (
    <Panel id="dash-pending" title="Pending Actions" description="Items that need your attention" icon="alert">
      {items.length === 0 ? (
        <EmptyState icon="checkCircle" title="Nothing for you here" hint="Your access has no queues to work." compact />
      ) : (
        <ul className={styles.pendingList}>
          {items.map((item) => {
            const look = PENDING_ICON[item.key] ?? { icon: "alert", tone: "orange" as StatTone };
            return (
              <li key={item.key} className={styles.pendingItem} data-tone={item.count > 0 ? look.tone : undefined}>
                <span className={styles.figureIcon} data-tone={look.tone}>
                  <Icon name={look.icon} size={18} />
                </span>
                <span className={styles.pendingText}>
                  <strong>{item.label}</strong>
                  <span>{item.hint}</span>
                </span>
                <span className={styles.pendingCount}>{formatNumber(item.count)}</span>
                <Link href={item.href} className={styles.pendingAction}>
                  {item.action}
                  <span className="w360-visually-hidden"> {item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

const ACTIVITY_ICON: Record<string, string> = {
  customer_registered: "users",
  partner_approved: "handshake",
  partner_rejected: "xCircle",
  partner_correction_required: "edit",
  payment_succeeded: "currency",
  payment_failed: "alert",
  subscription_activated: "card",
};

function ActivityPanel({ events }: { events: ActivityEvent[] }) {
  return (
    <Panel id="dash-activity" title="Recent Activities" description="Latest platform activities" icon="diagnostics">
      {events.length === 0 ? (
        <EmptyState icon="diagnostics" title="No activity yet" hint="Registrations, approvals and payments appear here." compact />
      ) : (
        <ul className={styles.activityList}>
          {events.map((event, i) => (
            <li key={`${event.kind}-${event.at}-${i}`}>
              <span className={styles.activityIcon}>
                <Icon name={ACTIVITY_ICON[event.kind] ?? "info"} size={16} />
              </span>
              <span className={styles.activityText}>
                <Link href={event.href}>
                  {event.title}
                  {event.amount_minor !== undefined && event.currency
                    ? ` (${formatMoney(event.amount_minor, event.currency)})`
                    : ""}
                </Link>
                {event.detail && <span>{event.detail}</span>}
              </span>
              <time className={styles.activityTime} dateTime={event.at}>
                {formatRelative(event.at)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function TopProductsPanel({ rows, period }: { rows: TopProduct[]; period: DashboardOverviewV2["period"] }) {
  return (
    <Panel
      id="dash-products"
      title="Top Products"
      description="By active subscriptions"
      icon="box"
      actions={
        <Link href="/admin/products" className={styles.viewAll}>
          View all<span className="w360-visually-hidden"> products</span>
        </Link>
      }
    >
      {rows.length === 0 ? (
        <EmptyState icon="box" title="No products sold yet" hint="Products appear once customers subscribe." compact />
      ) : (
        <div className={styles.tableScroll} tabIndex={0} role="group" aria-label="Top products table, scrolls sideways on a small screen">
          <table className={styles.productTable}>
            <caption className="w360-visually-hidden">
              Top products, revenue from {formatDate(period.from)} to {formatDate(period.to)}
            </caption>
            <thead>
              <tr>
                <th scope="col">Product</th>
                <th scope="col">Active Subscriptions</th>
                <th scope="col">Revenue</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.code}>
                  <th scope="row">{row.name}</th>
                  <td>{formatNumber(row.active_subscriptions)}</td>
                  <td>{formatMoney(row.revenue_minor, row.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
