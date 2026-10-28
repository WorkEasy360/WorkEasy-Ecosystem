"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { LoadGate, Status, Table, money, useLoad, when } from "@/features/controlplane/ui";
import { panelApi, type Dashboard as DashboardData, type RangeKey } from "@/lib/api/partnerPanel";
import { Amounts, EmptyState, Kpi, PageHeader, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

const RANGES: { key: RangeKey; label: string }[] = [
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "90d", label: "90 days" },
  { key: "custom", label: "Custom" },
];

type Metric = "gross" | "commission" | "transactions";

/** `extra` is a workspace's own section (the white-label status), drawn right
 * after the key figures; the page is otherwise the same in every workspace. */
export function PartnerDashboard({ extra }: { extra?: React.ReactNode } = {}) {
  const { id, partner, can, base } = usePanel();
  const [range, setRange] = useState<RangeKey>("30d");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [applied, setApplied] = useState<{ from?: string; to?: string }>({});
  const fromId = useId();
  const toId = useId();
  const [load] = useLoad(
    () => panelApi.dashboard(id, range, applied.from, applied.to),
    [id, range, applied.from ?? "", applied.to ?? ""],
  );

  return (
    <div className={styles.page}>
      <PageHeader
        title={`Welcome, ${partner?.display_name ?? ""}`}
        lead="Your customers, sales and earnings at a glance. Every figure is calculated by Workeasy360 from your ledger."
        actions={
          <div className={styles.range}>
            <fieldset>
              <legend>Date range</legend>
              {RANGES.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  className={styles.rangeButton}
                  aria-pressed={range === r.key}
                  onClick={() => {
                    setRange(r.key);
                    if (r.key !== "custom") setApplied({});
                  }}
                >
                  {r.label}
                </button>
              ))}
            </fieldset>
            {range === "custom" && (
              <form
                className={styles.dates}
                onSubmit={(e) => {
                  e.preventDefault();
                  setApplied({ from, to });
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
        }
      />
      <LoadGate load={load} what="the dashboard">
        {(d) => (
          <>
            <Kpis d={d} />
            {extra}
            {d.charts && <SalesOverview d={d} />}
            <div className={styles.twoCol}>
              <QuickLinks base={base} can={can} />
              <Recent d={d} base={base} />
            </div>
            {can("product.view") && <ProductActivity />}
            <NotificationsPreview />
          </>
        )}
      </LoadGate>
    </div>
  );
}

function Kpis({ d }: { d: DashboardData }) {
  const noCustomers = "Not shown for your role.";
  const noMoney = "Needs commission access.";
  return (
    <ul className={styles.kpis} aria-label="Key figures">
      <Kpi icon="users" label="Total customers" value={d.customers?.total} note={d.customers ? `${d.customers.active} active` : undefined} unavailable={d.customers ? undefined : noCustomers} />
      <Kpi
        icon="card"
        label="Active subscriptions"
        value={d.customers?.active_subscriptions}
        unavailable={d.customers ? undefined : noCustomers}
      />
      <Kpi icon="receipt" label="Total transactions" value={d.sales?.transactions} note="Sales on which you earn" unavailable={d.sales ? undefined : noMoney} />
      <Kpi icon="chart" label="Gross sales" value={<Amounts amounts={d.sales?.gross} />} unavailable={d.sales ? undefined : noMoney} />
      <Kpi
        icon="tag"
        label="How you earn"
        value={d.earning?.model === "margin" ? "Margin" : "Commission"}
        note={
          d.earning?.model === "margin"
            ? `Your cost is list price less ${((d.earning.discount_bp ?? 0) / 100).toFixed(2)}%`
            : "A commission on each paid sale"
        }
      />
      <Kpi icon="currency" label="Commission earned" value={<Amounts amounts={d.sales?.commission_earned} />} unavailable={d.sales ? undefined : noMoney} />
      <Kpi
        icon="history"
        label="Pending commission"
        value={<Amounts amounts={d.money?.pending_commission} />}
        note="Still in the holding period"
        unavailable={d.money ? undefined : noMoney}
      />
      <Kpi
        icon="handshake"
        label="Pending payout"
        value={<Amounts amounts={d.money?.pending_payout} />}
        note="Cleared, awaiting payment"
        unavailable={d.money ? undefined : noMoney}
      />
      <Kpi icon="building" label="Customer limit" unavailable={d.not_implemented.customer_limit} />
      {d.sub_partners && <Kpi icon="network" label="Sub-partners" value={d.sub_partners.total} />}
    </ul>
  );
}

function SalesOverview({ d }: { d: DashboardData }) {
  const charts = d.charts!;
  const [metric, setMetric] = useState<Metric>("gross");
  const [table, setTable] = useState(false);
  const currencyId = useId();
  const currencies = Object.keys(metric === "commission" ? charts.commission : charts.gross);
  const [currency, setCurrency] = useState(d.partner.currency);
  const chosen = currencies.includes(currency) ? currency : (currencies[0] ?? d.partner.currency);
  const points: { date: string; value: number }[] =
    metric === "transactions"
      ? charts.transactions.map((p) => ({ date: p.date, value: p.count }))
      : (metric === "gross" ? charts.gross : charts.commission)[chosen]?.map((p) => ({ date: p.date, value: p.amount_minor })) ?? [];
  const label = (v: number) => (metric === "transactions" ? String(v) : money(v, chosen));
  const title = { gross: "Gross sales", commission: "Commission", transactions: "Transactions" }[metric];

  return (
    <section className={styles.panel} aria-labelledby="sales-overview">
      <h2 id="sales-overview">Sales overview</h2>
      <div className={styles.chartControls}>
        <div className={styles.range}>
          <fieldset>
            <legend>Measure</legend>
            {(["gross", "commission", "transactions"] as Metric[]).map((m) => (
              <button key={m} type="button" className={styles.rangeButton} aria-pressed={metric === m} onClick={() => setMetric(m)}>
                {{ gross: "Gross sales", commission: "Commission", transactions: "Transactions" }[m]}
              </button>
            ))}
          </fieldset>
          {metric !== "transactions" && currencies.length > 1 && (
            <div>
              <label className="w360-label" htmlFor={currencyId}>
                Currency
              </label>
              <select id={currencyId} className="w360-input" value={chosen} onChange={(e) => setCurrency(e.target.value)}>
                {currencies.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        <button type="button" className="w360-btn w360-btn-secondary" aria-pressed={table} onClick={() => setTable((t) => !t)}>
          {table ? "Show chart" : "Show as table"}
        </button>
      </div>
      {points.length === 0 ? (
        <EmptyState title="No sales in this period" icon="chart">
          <span>Sales you make, and the commission they earn, appear here as soon as they are recorded.</span>
        </EmptyState>
      ) : table ? (
        <Table caption={`${title} by day`} head={["Date", title]}>
          {points.map((p) => (
            <tr key={p.date}>
              <th scope="row">{p.date}</th>
              <td>{label(p.value)}</td>
            </tr>
          ))}
        </Table>
      ) : (
        <BarChart points={points} label={label} title={`${title} by day`} />
      )}
    </section>
  );
}

const W = 720;
const H = 220;
const PAD = { top: 12, right: 8, bottom: 26, left: 8 };

/** Thin bars on a zero baseline. The bar heights are geometry; every number a
 * reader sees (the axis maximum and each bar's tooltip) is the server's amount,
 * formatted. A table view of the same data is one button away. */
function BarChart({ points, label, title }: { points: { date: string; value: number }[]; label: (v: number) => string; title: string }) {
  const max = Math.max(...points.map((p) => p.value), 1);
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const slot = innerW / points.length;
  const bar = Math.max(2, Math.min(22, slot * 0.62));
  const base = PAD.top + innerH;
  const first = points[0]!.date;
  const last = points[points.length - 1]!.date;
  return (
    <figure className={styles.chartFrame}>
      <svg className={styles.chart} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}: ${points.length} days, highest ${label(max)}`}>
        <line className={styles.chartGrid} x1={PAD.left} x2={W - PAD.right} y1={base} y2={base} />
        <line className={styles.chartGrid} x1={PAD.left} x2={W - PAD.right} y1={PAD.top} y2={PAD.top} strokeDasharray="3 4" />
        <text className={styles.chartAxis} x={PAD.left} y={PAD.top - 2}>
          {label(max)}
        </text>
        {points.map((p, i) => {
          const h = Math.max(0, (p.value / max) * innerH);
          return (
            <rect key={p.date} className={styles.chartBar} x={PAD.left + i * slot + (slot - bar) / 2} y={base - h} width={bar} height={h} rx={Math.min(4, bar / 2)}>
              <title>{`${p.date}: ${label(p.value)}`}</title>
            </rect>
          );
        })}
        <text className={styles.chartAxis} x={PAD.left} y={H - 8}>
          {first}
        </text>
        <text className={styles.chartAxis} x={W - PAD.right} y={H - 8} textAnchor="end">
          {last}
        </text>
      </svg>
    </figure>
  );
}

/** Per-product customers and subscriptions, from the same server read the product
 * screens use. A product granted later appears here by itself. */
function ProductActivity() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.products(id), [id]);
  return (
    <section className={styles.panel} aria-labelledby="dash-products">
      <h2 id="dash-products">Product activity</h2>
      <LoadGate load={load} what="product activity">
        {(d) =>
          d.products.length === 0 ? (
            <p className={styles.muted}>No products have been granted to your account yet.</p>
          ) : (
            <Table caption="Customers and subscriptions by product" head={["Product", "For sale", "Customers", "Active subscriptions"]}>
              {d.products.map((p) => (
                <tr key={p.code}>
                  <th scope="row">{p.name}</th>
                  <td>
                    <Status value={p.enabled_for_sale ? "enabled" : "disabled"} />
                  </td>
                  <td className={styles.num}>{p.customers ?? "—"}</td>
                  <td className={styles.num}>{p.active_subscriptions ?? "—"}</td>
                </tr>
              ))}
            </Table>
          )
        }
      </LoadGate>
    </section>
  );
}

/** The newest unread notifications. The full list, and marking read, is the
 * Notification Center; this only shows that something is waiting. */
function NotificationsPreview() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.notifications(id, "unread"), [id]);
  return (
    <section className={styles.panel} aria-labelledby="dash-notifications">
      <h2 id="dash-notifications">Notifications</h2>
      <LoadGate load={load} what="notifications">
        {(n) =>
          n.items.length === 0 ? (
            <p className={styles.muted}>You are all caught up.</p>
          ) : (
            <ul className={styles.list} aria-label="Unread notifications">
              {n.items.slice(0, 4).map((i) => (
                <li key={i.id} className={styles.listItem}>
                  <span>
                    {i.title}
                    {i.detail ? <span className={styles.muted}> — {i.detail}</span> : null}
                  </span>
                  <span className={styles.muted}>{when(i.occurred_at)}</span>
                </li>
              ))}
            </ul>
          )
        }
      </LoadGate>
      <Link href={`${base}/notifications`}>All notifications</Link>
    </section>
  );
}

function QuickLinks({ base, can }: { base: string; can: (...k: string[]) => boolean }) {
  const items = [
    { href: `${base}/users#add-customer`, label: "Add customer", show: can("customer.provision") },
    { href: `${base}/agency#new-sub-partner`, label: "Add agency", show: can("subpartner.manage") },
    { href: `${base}/users`, label: "Sell a product", show: can("subscription.sell") },
    { href: `${base}/partner-products`, label: "Partner products", show: can("product.view") },
    { href: `${base}/payments/commissions`, label: "View commission", show: can("commission.view") },
    { href: `${base}/affiliates/payouts`, label: "Review payouts", show: can("payout.view") },
    { href: `${base}/brand-settings`, label: "Brand settings", show: can("branding.view") },
  ].filter((i) => i.show);
  return (
    <section className={styles.panel} aria-labelledby="quick-links">
      <h2 id="quick-links">Quick links</h2>
      {items.length === 0 ? (
        <p className={styles.muted}>Nothing to start from here for your role.</p>
      ) : (
        <ul className={styles.quick}>
          {items.map((i) => (
            <li key={i.href}>
              <Link href={i.href}>{i.label}</Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Recent({ d, base }: { d: DashboardData; base: string }) {
  const { customers, commissions, payouts } = d.recent;
  const nothing = !customers?.length && !commissions?.length && !payouts?.length;
  return (
    <section className={styles.panel} aria-labelledby="recent">
      <h2 id="recent">Recent activity</h2>
      {nothing ? (
        <EmptyState title="Nothing yet" icon="history">
          <span>Your first customer, sale or payout will show up here.</span>
        </EmptyState>
      ) : (
        <div className={styles.page}>
          {!!customers?.length && (
            <div>
              <h3>Recent customers</h3>
              <ul className={styles.list}>
                {customers.map((c) => (
                  <li key={c.organization_id} className={styles.listItem}>
                    <span>{c.name || "Customer"}</span>
                    <span>
                      <Status value={c.link_status} /> <span className={styles.muted}>{when(c.started_at)}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <Link href={`${base}/users`}>All customers</Link>
            </div>
          )}
          {!!commissions?.length && (
            <div>
              <h3>Recent commissions</h3>
              <ul className={styles.list}>
                {commissions.map((c) => (
                  <li key={c.id} className={styles.listItem}>
                    <span>{c.kind}</span>
                    <span className={styles.num}>{money(c.amount_minor, c.currency)}</span>
                  </li>
                ))}
              </ul>
              <Link href={`${base}/payments/commissions`}>All commissions</Link>
            </div>
          )}
          {!!payouts?.length && (
            <div>
              <h3>Recent payouts</h3>
              <ul className={styles.list}>
                {payouts.map((p) => (
                  <li key={p.id} className={styles.listItem}>
                    <Status value={p.status} />
                    <span className={styles.num}>{money(p.payable_minor, p.currency)}</span>
                  </li>
                ))}
              </ul>
              <Link href={`${base}/affiliates/payouts`}>All payouts</Link>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
