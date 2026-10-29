"use client";

import { useId, useMemo, useState } from "react";
import Link from "next/link";
import { NavIcon } from "@/components/sidebar/icons";
import { firstName, initials } from "@/components/sidebar/SidebarShell";
import { adminConsoleApi } from "@/lib/api/adminConsole";
import { adminApi, type Dashboard as DashboardData, type DashboardOverview } from "@/lib/api/controlPlane";
import { useSession } from "@/lib/auth/useSession";
import { can, useAdmin } from "./ControlShell";
import { ColumnChart, Donut, LineChart, type DayValue } from "./dashboardCharts";
import { LoadGate, Status, money, useLoad } from "./ui";
import styles from "./AdminDashboard.module.css";

/**
 * The Super Admin dashboard (2026-10-26 redesign): figure tiles, revenue and
 * sign-ups per day, orders per product and the latest users, orders and
 * partners - for one month, chosen at the top. Every section is drawn only
 * when the backend sent it (it sends only what the staff member's grants
 * cover). Real figures only: money stays per currency, never converted or
 * added across currencies, and nothing here is estimated.
 */
export function Dashboard() {
  const session = useSession();
  const { permissions } = useAdmin();
  const [month, setMonth] = useState<string>("");
  const [load] = useLoad(() => adminApi.dashboard(month || undefined), [month]);
  const name = session.status === "ready" ? session.session.name || session.session.email || "" : "";
  const greeting = name ? `Welcome back, ${firstName(name)}!` : "Welcome back!";
  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>Dashboard</h1>
          <p className={styles.lead}>{greeting} Here&apos;s what&apos;s happening with your platform.</p>
        </div>
        <MonthPicker value={month} onChange={setMonth} />
      </div>
      <LoadGate load={load} what="the dashboard">
        {(d) => <Overview d={d} permissions={permissions} />}
      </LoadGate>
    </div>
  );
}

// --- the month ---------------------------------------------------------------------

const MONTHS_BACK = 12;

function monthKey(date: Date): string {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

const rangeOf = (key: string) => {
  const [y, m] = key.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const last = new Date(Date.UTC(y, m, 0));
  const f = (d: Date) => d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
  return `${f(first)} - ${f(last)}`;
};

/** This month and the eleven before it; the value "" is this month. */
function MonthPicker({ value, onChange }: { value: string; onChange: (month: string) => void }) {
  const id = useId();
  const options = useMemo(() => {
    const now = new Date();
    return Array.from({ length: MONTHS_BACK }, (_, i) => monthKey(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1))));
  }, []);
  return (
    <div className={styles.monthPicker}>
      <label htmlFor={id} className="w360-visually-hidden">
        Month shown
      </label>
      <span aria-hidden="true" className={styles.calendar}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M3 10h18M8 3v4M16 3v4" />
        </svg>
      </span>
      <select id={id} className={styles.monthSelect} value={value || options[0]} onChange={(e) => onChange(e.target.value === options[0] ? "" : e.target.value)}>
        {options.map((key) => (
          <option key={key} value={key}>
            {rangeOf(key)}
          </option>
        ))}
      </select>
      <span aria-hidden="true" className={styles.selectChevron}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </span>
    </div>
  );
}

// --- the overview --------------------------------------------------------------------

function Overview({ d, permissions }: { d: DashboardData; permissions: string[] }) {
  const o = d.overview;
  const monthName = new Date(`${o.first_day}T00:00:00Z`).toLocaleDateString("en-GB", { month: "long", timeZone: "UTC" });
  const current = o.month === monthKey(new Date());
  const period = current ? "this month" : `in ${monthName}`;
  const subscriptions = Object.values(d.subscriptions).reduce((sum, n) => sum + n, 0);
  return (
    <>
      <section aria-label="Figures" className={styles.tiles}>
        {o.users && (
          <Tile tone="blue" icon="users" label="Total Users" value={o.users.total} trend={growth(o.users.new, o.users.total)} note={`+${o.users.new} ${period}`} />
        )}
        {o.partners && (
          <Tile tone="green" icon="handshake" label="Total Partners" value={o.partners.total} trend={growth(o.partners.new, o.partners.total)} note={`+${o.partners.new} ${period}`} />
        )}
        {o.products && (
          <Tile
            tone="purple"
            icon="puzzle"
            label="Total Products"
            value={o.products.total}
            trend={growth(o.products.new, o.products.total)}
            note={`${o.products.available} on sale · +${o.products.new} ${period}`}
          />
        )}
        {o.orders && (
          <Tile tone="orange" icon="receipt" label="Paid Orders" value={o.orders.paid} trend={change(o.orders.paid, o.orders.previous)} note={`${o.orders.previous} in the period before`} />
        )}
        <Tile tone="red" icon="card" label="Active Subscriptions" value={d.subscriptions.active ?? 0} note={`${subscriptions} in all states`} />
      </section>

      <div className={styles.charts}>
        {o.revenue && <Revenue overview={o} monthName={monthName} />}
        {o.users && (
          <section className={styles.card} aria-labelledby="dash-signups">
            <div className={styles.cardHead}>
              <h2 id="dash-signups" className={styles.cardTitle}>
                User Registration
              </h2>
              <span className={styles.chip}>{current ? "This month" : monthName}</span>
            </div>
            <Headline value={String(o.users.new)} trend={change(o.users.new, o.users.previous)} />
            <p className={styles.muted}>New users {period}</p>
            <ColumnChart caption={`New users per day, ${monthName}`} days={series(o, o.users.daily)} format={(n) => compact(n)} />
          </section>
        )}
        {o.orders && (
          <section className={styles.card} aria-labelledby="dash-sales">
            <div className={styles.cardHead}>
              <h2 id="dash-sales" className={styles.cardTitle}>
                Product-wise Sales
              </h2>
              <span className={styles.chip}>{current ? "This month" : monthName}</span>
            </div>
            {o.orders.by_product.length === 0 ? (
              <p className={styles.empty}>No orders were paid {period}.</p>
            ) : (
              <Donut
                caption={`Paid orders per product, ${monthName}`}
                centre="Sales"
                slices={o.orders.by_product.map((p) => ({ key: p.code, label: p.name, value: p.orders }))}
              />
            )}
          </section>
        )}
      </div>

      <div className={styles.lists}>
        {can(permissions, "platform.staff_user.view") && <RecentUsers />}
        {can(permissions, "platform.staff_payment.view") && <RecentOrders />}
        {can(permissions, "platform.staff_partner.view") && <RecentPartners />}
      </div>
    </>
  );
}

const series = (o: DashboardOverview, values: (number | null)[]): DayValue[] => {
  const start = new Date(`${o.first_day}T00:00:00Z`);
  return values.map((value, i) => ({
    day: new Date(start.getTime() + i * 86_400_000).toISOString().slice(0, 10),
    value,
  }));
};

/** Growth of a total over the month: what was added against what was there before. */
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

function Trend({ value }: { value: number | null }) {
  if (value === null) return null;
  const up = value >= 0;
  return (
    <span className={up ? styles.up : styles.down}>
      <span aria-hidden="true">{up ? "↑" : "↓"}</span>
      <span className="w360-visually-hidden">{up ? "up" : "down"}</span> {Math.abs(value)}%
    </span>
  );
}

function Tile({
  tone,
  icon,
  label,
  value,
  trend = null,
  note,
}: {
  tone: "blue" | "green" | "purple" | "orange" | "red";
  icon: string;
  label: string;
  value: number;
  trend?: number | null;
  note: string;
}) {
  return (
    <div className={styles.tile}>
      <span aria-hidden="true" className={`${styles.tileIcon} ${styles[tone]}`}>
        <NavIcon name={icon} />
      </span>
      <div className={styles.tileBody}>
        <p className={styles.tileLabel}>{label}</p>
        <p className={styles.tileValue}>
          {value.toLocaleString("en-IN")} <Trend value={trend} />
        </p>
        <p className={styles.tileNote}>{note}</p>
      </div>
    </div>
  );
}

function Headline({ value, trend }: { value: string; trend: number | null }) {
  return (
    <p className={styles.headline}>
      {value} <Trend value={trend} />
    </p>
  );
}

/** Money collected per day in one currency; a choice when there are several
 * (amounts are never converted or added across currencies). */
function Revenue({ overview, monthName }: { overview: DashboardOverview; monthName: string }) {
  const id = useId();
  const rows = overview.revenue ?? [];
  const largest = [...rows].sort((a, b) => b.amount_minor - a.amount_minor)[0]?.currency ?? "";
  const [picked, setPicked] = useState("");
  const row = rows.find((r) => r.currency === (picked || largest));
  return (
    <section className={styles.card} aria-labelledby="dash-revenue">
      <div className={styles.cardHead}>
        <h2 id="dash-revenue" className={styles.cardTitle}>
          Revenue Overview
        </h2>
        {rows.length <= 1 && <span className={styles.chip}>{monthName}</span>}
        {rows.length > 1 && (
          <>
            <label htmlFor={id} className="w360-visually-hidden">
              Currency
            </label>
            <select id={id} className={styles.chipSelect} value={row?.currency} onChange={(e) => setPicked(e.target.value)}>
              {rows.map((r) => (
                <option key={r.currency} value={r.currency}>
                  {r.currency}
                </option>
              ))}
            </select>
          </>
        )}
      </div>
      {!row ? (
        <p className={styles.empty}>No payments were collected in {monthName}.</p>
      ) : (
        <>
          <Headline value={money(row.amount_minor, row.currency)} trend={change(row.amount_minor, row.previous_minor)} />
          <p className={styles.muted}>
            Collected in {monthName} · {row.payments} {row.payments === 1 ? "payment" : "payments"}
          </p>
          <LineChart
            caption={`Collected per day in ${row.currency}, ${monthName}`}
            days={series(overview, row.daily.map((v) => (v === null ? null : toMajor(v, row.currency))))}
            format={(n) => compact(n, row.currency)}
          />
        </>
      )}
    </section>
  );
}

// --- the latest rows ---------------------------------------------------------------------

const joined = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—";

function ListCard({ id, title, href, children }: { id: string; title: string; href: string; children: React.ReactNode }) {
  return (
    <section className={styles.card} aria-labelledby={id}>
      <div className={styles.cardHead}>
        <h2 id={id} className={styles.cardTitle}>
          {title}
        </h2>
        <Link href={href} className={styles.viewAll}>
          View all<span className="w360-visually-hidden"> {title.toLowerCase().replace("recent ", "")}</span>
        </Link>
      </div>
      {children}
    </section>
  );
}

function MiniTable({ caption, head, empty, rows }: { caption: string; head: string[]; empty: string; rows: React.ReactNode[] }) {
  if (rows.length === 0) return <p className={styles.empty}>{empty}</p>;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className="w360-visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );
}

function RecentUsers() {
  const [load] = useLoad(() => adminConsoleApi.users({ limit: 5 }), []);
  return (
    <ListCard id="dash-recent-users" title="Recent Users" href="/admin/users">
      <LoadGate load={load} what="the latest users">
        {(page) => (
          <MiniTable
            caption="The latest users"
            head={["Name", "Role", "Status", "Joined"]}
            empty="No users yet."
            rows={page.results.map((u) => (
              <tr key={u.user_id}>
                <td>
                  <span className={styles.person}>
                    <span aria-hidden="true" className={styles.initials}>
                      {initials(u.name || u.email)}
                    </span>
                    <span className={styles.personText}>
                      <Link href={`/admin/users/${u.user_id}`}>{u.name || u.email}</Link>
                      {u.name && <span className={styles.sub}>{u.email}</span>}
                    </span>
                  </span>
                </td>
                <td>
                  <span className={u.is_staff ? styles.roleStaff : styles.roleUser}>{u.is_staff ? "Staff" : "User"}</span>
                </td>
                <td>
                  <Status value={u.status} />
                </td>
                <td className={styles.nowrap}>{joined(u.created_at)}</td>
              </tr>
            ))}
          />
        )}
      </LoadGate>
    </ListCard>
  );
}

function RecentOrders() {
  const [load] = useLoad(() => adminConsoleApi.orders({ limit: 5 }), []);
  return (
    <ListCard id="dash-recent-orders" title="Recent Orders" href="/admin/orders">
      <LoadGate load={load} what="the latest orders">
        {(page) => (
          <MiniTable
            caption="The latest orders"
            head={["Order", "Customer", "Amount", "Status"]}
            empty="No orders yet."
            rows={page.results.map((o) => (
              <tr key={o.order_id}>
                <td>
                  <span className={styles.personText}>
                    <span className={`${styles.strong} ${styles.nowrap}`}>{o.number}</span>
                    <span className={styles.sub}>{o.description}</span>
                  </span>
                </td>
                <td>{o.organization_name || "—"}</td>
                <td className={styles.nowrap}>{money(o.amount_due_minor, o.currency)}</td>
                <td>
                  <Status value={o.status} />
                </td>
              </tr>
            ))}
          />
        )}
      </LoadGate>
    </ListCard>
  );
}

function RecentPartners() {
  const [load] = useLoad(() => adminApi.partners({ limit: 5 }), []);
  return (
    <ListCard id="dash-recent-partners" title="Recent Partners" href="/admin/partners/all">
      <LoadGate load={load} what="the latest partners">
        {(page) => (
          <MiniTable
            caption="The latest partners"
            head={["Partner", "Status", "Joined"]}
            empty="No partners yet."
            rows={page.results.map((p) => (
              <tr key={p.id}>
                <td>
                  <span className={styles.personText}>
                    <Link href={`/admin/partners/${p.id}`}>{p.display_name}</Link>
                    {p.contact_email && <span className={styles.sub}>{p.contact_email}</span>}
                  </span>
                </td>
                <td>
                  <Status value={p.status} />
                </td>
                <td className={styles.nowrap}>{joined(p.created_at)}</td>
              </tr>
            ))}
          />
        )}
      </LoadGate>
    </ListCard>
  );
}
