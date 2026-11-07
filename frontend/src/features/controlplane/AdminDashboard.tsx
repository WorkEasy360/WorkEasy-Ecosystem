"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { firstName } from "@/components/sidebar/SidebarShell";
import { adminConsoleApi } from "@/lib/api/adminConsole";
import { adminApi, type Dashboard as DashboardData, type DashboardOverview } from "@/lib/api/controlPlane";
import { useSession } from "@/lib/auth/useSession";
import { can, useAdmin } from "./ControlShell";
import { ColumnChart, Donut, LineChart, type DayValue } from "./dashboardCharts";
import {
  Avatar,
  Delta,
  EmptyState,
  Page,
  PageHeader,
  SectionHeader,
  Select,
  StatCard,
  StatGrid,
  StatusBadge,
  Trunc,
  formatDate,
  formatMoney,
  formatNumber,
  kit,
  type SelectOption,
} from "./kit";
import { LoadGate, useLoad } from "./ui";
import styles from "./AdminDashboard.module.css";

/**
 * The Super Admin dashboard (2026-11-07 design system): KPI cards, revenue and
 * sign-ups per day, orders per product and the latest users, orders and
 * partners - for one period, chosen once in the page header. The server
 * reports by calendar month, so the periods offered are this month (up to
 * today), last month and the ten before; nothing finer is offered because
 * nothing finer exists to show. Every section is drawn only when the backend
 * sent it (it sends only what the staff member's grants cover). Real figures
 * only: money stays per currency, never converted or added across currencies.
 */
export function Dashboard() {
  const session = useSession();
  const { permissions } = useAdmin();
  const [month, setMonth] = useState<string>("");
  const [load] = useLoad(() => adminApi.dashboard(month || undefined), [month]);
  const name = session.status === "ready" ? session.session.name || session.session.email || "" : "";
  const greeting = name ? `Welcome back, ${firstName(name)}.` : "Welcome back.";
  return (
    <Page>
      <PageHeader
        title="Dashboard"
        subtitle={`${greeting} Here's what's happening across the platform.`}
        primary={<PeriodPicker value={month} onChange={setMonth} />}
      />
      <LoadGate load={load} what="the dashboard">
        {(d) => <Overview d={d} permissions={permissions} />}
      </LoadGate>
    </Page>
  );
}

// --- the period ----------------------------------------------------------------------

const MONTHS_BACK = 12;
const MONTH_SHORT = new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" });
const MONTH_LONG = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });

function monthKey(date: Date): string {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** The days a month covers, never past today: "1 Nov – 7 Nov 2026". */
export function rangeLabel(key: string, today: Date = new Date()): string {
  const [y, m] = key.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const end = new Date(Date.UTC(y, m, 0));
  const last = monthKey(today) === key ? today : end;
  const mon = MONTH_SHORT.format(first).replace("Sept", "Sep");
  return `1 ${mon} – ${last.getUTCDate()} ${mon} ${y}`;
}

/** This month (up to today), last month and the ten before; "" is this month. */
function PeriodPicker({ value, onChange }: { value: string; onChange: (month: string) => void }) {
  const options = useMemo<SelectOption[]>(() => {
    const now = new Date();
    return Array.from({ length: MONTHS_BACK }, (_, i) => {
      const key = monthKey(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1)));
      const label =
        i === 0
          ? `This month · ${rangeLabel(key, now)}`
          : i === 1
            ? `Last month · ${rangeLabel(key, now)}`
            : MONTH_LONG.format(new Date(`${key}-01T00:00:00Z`));
      return { value: i === 0 ? "" : key, label };
    });
  }, []);
  return <Select label="Period" value={value} options={options} onChange={onChange} />;
}

// --- the overview --------------------------------------------------------------------

/** The days of the period up to today (a month still running is not drawn ahead). */
function series(o: DashboardOverview, values: (number | null)[]): DayValue[] {
  const start = new Date(`${o.first_day}T00:00:00Z`);
  const today = new Date().toISOString().slice(0, 10);
  return values
    .map((value, i) => ({ day: new Date(start.getTime() + i * 86_400_000).toISOString().slice(0, 10), value }))
    .filter((d) => d.day <= today || d.value !== null);
}

/** Growth of a total over the period: what was added against what was there before. */
function growth(added: number, total: number): number | null {
  const before = total - added;
  return before > 0 ? Math.round((added / before) * 100) : null;
}

/** Change against the period before; none when there was nothing before. */
function change(now: number, before: number): number | null {
  return before > 0 ? Math.round(((now - before) / before) * 100) : null;
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

const toMajor = (minor: number, currency: string) => minor / 10 ** (currency === "JPY" ? 0 : 2);

/** A row of cards of equal height, its columns set by how many cards it holds,
 * so no card is left alone on a row. */
function CardRow({ children, label }: { children: React.ReactNode[]; label: string }) {
  const shown = children.filter(Boolean);
  if (shown.length === 0) return null;
  return (
    <div className={styles.row} data-count={Math.min(shown.length, 3)} role="group" aria-label={label}>
      {shown}
    </div>
  );
}

/** A dashboard card: its header, then its content filling the card's height. */
function Panel({
  id,
  title,
  description,
  actions,
  children,
}: {
  id: string;
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className={`${kit.card} ${styles.panel}`} aria-labelledby={id}>
      <div className={styles.panelBody}>
        <SectionHeader id={id} title={title} description={description} actions={actions} />
        <div className={styles.panelContent}>{children}</div>
      </div>
    </section>
  );
}

function Overview({ d, permissions }: { d: DashboardData; permissions: string[] }) {
  const o = d.overview;
  const range = rangeLabel(o.month);
  const current = o.month === monthKey(new Date());
  const period = current ? "this month" : `in ${MONTH_LONG.format(new Date(`${o.first_day}T00:00:00Z`))}`;
  const subscriptions = Object.values(d.subscriptions).reduce((sum, n) => sum + n, 0);
  const organizations = d.organizations.active;

  const cards = [
    o.users && (
      <StatCard
        key="users"
        icon="users"
        tone="blue"
        label="Total users"
        value={formatNumber(o.users.total)}
        delta={growth(o.users.new, o.users.total)}
        note={`+${formatNumber(o.users.new)} ${period}`}
        href={can(permissions, "platform.staff_user.view") ? "/admin/users" : undefined}
      />
    ),
    o.partners && (
      <StatCard
        key="partners"
        icon="handshake"
        tone="green"
        label="Total partners"
        value={formatNumber(o.partners.total)}
        delta={growth(o.partners.new, o.partners.total)}
        note={`+${formatNumber(o.partners.new)} ${period}`}
        href={can(permissions, "platform.staff_partner.view") ? "/admin/partners/all" : undefined}
      />
    ),
    o.products && (
      <StatCard
        key="products"
        icon="box"
        tone="purple"
        label="Total products"
        value={formatNumber(o.products.total)}
        delta={growth(o.products.new, o.products.total)}
        note={`${formatNumber(o.products.available)} on sale · +${formatNumber(o.products.new)} ${period}`}
      />
    ),
    o.orders && (
      <StatCard
        key="orders"
        icon="receipt"
        tone="orange"
        label="Paid orders"
        value={formatNumber(o.orders.paid)}
        delta={change(o.orders.paid, o.orders.previous)}
        note={`${formatNumber(o.orders.previous)} in the period before`}
      />
    ),
    <StatCard
      key="subscriptions"
      icon="card"
      tone="red"
      label="Active subscriptions"
      value={formatNumber(d.subscriptions.active ?? 0)}
      note={`${formatNumber(subscriptions)} in all states`}
      href={can(permissions, "platform.staff_customer.view") ? "/admin/subscriptions" : undefined}
    />,
    organizations !== undefined && (
      <StatCard
        key="organizations"
        icon="building"
        tone="blue"
        label="Active organizations"
        value={formatNumber(organizations)}
        note="Customer workspaces in use"
      />
    ),
  ].filter(Boolean);

  return (
    <>
      <StatGrid label="Figures">{cards}</StatGrid>

      <CardRow label="Charts">
        {[
          o.revenue && <Revenue key="revenue" overview={o} range={range} />,
          o.users && (
            <Panel key="signups" id="dash-signups" title="User registrations" description={range}>
              {o.users.new === 0 ? (
                <EmptyState icon="users" title="No sign-ups yet" hint={`Nobody signed up ${period}.`} compact />
              ) : (
                <>
                  <Headline value={formatNumber(o.users.new)} trend={change(o.users.new, o.users.previous)} />
                  <p className={styles.muted}>New users {period}</p>
                  <ColumnChart caption={`New users per day, ${range}`} days={series(o, o.users.daily)} format={(n) => compact(n)} />
                </>
              )}
            </Panel>
          ),
          o.orders && (
            <Panel key="sales" id="dash-sales" title="Product-wise sales" description={range}>
              {o.orders.by_product.length === 0 ? (
                <EmptyState icon="cart" title="No sales yet" hint={`No orders were paid ${period}.`} compact />
              ) : (
                <Donut
                  caption={`Paid orders per product, ${range}`}
                  centre="Sales"
                  slices={o.orders.by_product.map((p) => ({ key: p.code, label: p.name, value: p.orders }))}
                />
              )}
            </Panel>
          ),
        ]}
      </CardRow>

      <CardRow label="Latest activity">
        {[
          can(permissions, "platform.staff_user.view") && <RecentUsers key="users" />,
          can(permissions, "platform.staff_payment.view") && <RecentOrders key="orders" />,
          can(permissions, "platform.staff_partner.view") && <RecentPartners key="partners" />,
        ]}
      </CardRow>
    </>
  );
}

function Headline({ value, trend }: { value: string; trend: number | null }) {
  return (
    <p className={styles.headline}>
      <span>{value}</span>
      {trend !== null && (
        <span className={styles.headlineDelta}>
          <Delta value={trend} />
          <span className={styles.vs}>vs period before</span>
        </span>
      )}
    </p>
  );
}

/** Money collected per day in one currency; a choice when there are several
 * (amounts are never converted or added across currencies). */
function Revenue({ overview, range }: { overview: DashboardOverview; range: string }) {
  const rows = overview.revenue ?? [];
  const largest = [...rows].sort((a, b) => b.amount_minor - a.amount_minor)[0]?.currency ?? "";
  const [picked, setPicked] = useState("");
  const row = rows.find((r) => r.currency === (picked || largest));
  return (
    <Panel
      id="dash-revenue"
      title="Revenue overview"
      description={range}
      actions={
        rows.length > 1 && row ? (
          <Select
            label="Currency"
            size="sm"
            value={row.currency}
            options={rows.map((r) => ({ value: r.currency, label: r.currency }))}
            onChange={setPicked}
          />
        ) : undefined
      }
    >
      {!row ? (
        <EmptyState icon="currency" title="No payments yet" hint={`No payments were collected in ${range}.`} compact />
      ) : (
        <>
          <Headline value={formatMoney(row.amount_minor, row.currency)} trend={change(row.amount_minor, row.previous_minor)} />
          <p className={styles.muted}>
            {formatNumber(row.payments)} {row.payments === 1 ? "payment" : "payments"} collected
          </p>
          <LineChart
            caption={`Collected per day in ${row.currency}, ${range}`}
            days={series(overview, row.daily.map((v) => (v === null ? null : toMajor(v, row.currency))))}
            format={(n) => compact(n, row.currency)}
          />
        </>
      )}
    </Panel>
  );
}

// --- the latest rows ---------------------------------------------------------------------

function ListPanel({ id, title, href, children }: { id: string; title: string; href: string; children: React.ReactNode }) {
  return (
    <Panel
      id={id}
      title={title}
      actions={
        <Link href={href} className={styles.viewAll}>
          View all<span className="w360-visually-hidden"> {title.toLowerCase().replace("recent ", "")}</span>
        </Link>
      }
    >
      {children}
    </Panel>
  );
}

/** One row of a latest-items list: avatar, name (a link) and a muted second
 * line cut to fit, then the status and date on the right. */
function Item({
  name,
  sub,
  href,
  avatar = true,
  side,
  date,
}: {
  name: string;
  sub?: string | null;
  href: string;
  avatar?: boolean;
  side?: React.ReactNode;
  date?: string;
}) {
  return (
    <li className={styles.item}>
      {avatar && <Avatar name={name} />}
      <span className={styles.itemText}>
        <Link href={href} className={styles.itemName}>
          {name}
        </Link>
        {sub && (
          <span className={styles.itemSub}>
            <Trunc>{sub}</Trunc>
          </span>
        )}
      </span>
      <span className={styles.itemSide}>
        {side}
        {date && <span className={styles.itemDate}>{date}</span>}
      </span>
    </li>
  );
}

function Items({ empty, icon, children }: { empty: string; icon: string; children: React.ReactNode[] }) {
  if (children.length === 0) return <EmptyState icon={icon} title={empty} compact />;
  return <ul className={styles.items}>{children}</ul>;
}

function RecentUsers() {
  const [load] = useLoad(() => adminConsoleApi.users({ limit: 5 }), []);
  return (
    <ListPanel id="dash-recent-users" title="Recent users" href="/admin/users">
      <LoadGate load={load} what="the latest users">
        {(page) => (
          <Items empty="No users yet." icon="users">
            {page.results.map((u) => (
              <Item
                key={u.user_id}
                name={u.name || u.email}
                sub={u.name ? u.email : u.is_staff ? "Staff" : null}
                href={`/admin/users/${u.user_id}`}
                side={<StatusBadge value={u.status} />}
                date={formatDate(u.created_at)}
              />
            ))}
          </Items>
        )}
      </LoadGate>
    </ListPanel>
  );
}

function RecentOrders() {
  const [load] = useLoad(() => adminConsoleApi.orders({ limit: 5 }), []);
  return (
    <ListPanel id="dash-recent-orders" title="Recent orders" href="/admin/orders">
      <LoadGate load={load} what="the latest orders">
        {(page) => (
          <Items empty="No orders yet." icon="receipt">
            {page.results.map((o) => (
              <Item
                key={o.order_id}
                avatar={false}
                name={o.number}
                sub={[o.organization_name, o.description].filter(Boolean).join(" · ")}
                href={`/admin/orders?q=${encodeURIComponent(o.number)}`}
                side={
                  <>
                    <span className={styles.amount}>{formatMoney(o.amount_due_minor, o.currency)}</span>
                    <StatusBadge value={o.status} />
                  </>
                }
              />
            ))}
          </Items>
        )}
      </LoadGate>
    </ListPanel>
  );
}

function RecentPartners() {
  const [load] = useLoad(() => adminApi.partners({ limit: 5 }), []);
  return (
    <ListPanel id="dash-recent-partners" title="Recent partners" href="/admin/partners/all">
      <LoadGate load={load} what="the latest partners">
        {(page) => (
          <Items empty="No partners yet." icon="handshake">
            {page.results.map((p) => (
              <Item
                key={p.id}
                name={p.display_name}
                sub={p.contact_email || p.code}
                href={`/admin/partners/${p.id}`}
                side={<StatusBadge value={p.status} />}
                date={formatDate(p.created_at)}
              />
            ))}
          </Items>
        )}
      </LoadGate>
    </ListPanel>
  );
}
