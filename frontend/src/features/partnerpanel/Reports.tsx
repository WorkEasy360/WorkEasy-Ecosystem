"use client";

import { LoadGate, Table, money, useLoad } from "@/features/controlplane/ui";
import { panelApi, type PartnerReports } from "@/lib/api/partnerPanel";
import { Amounts, EmptyState, Kpi, PageHeader, usePanel } from "./PanelKit";
import { percent, useRangeControls } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/** One CSV cell: quoted, and a leading formula character neutralised so a
 * spreadsheet never runs it (the same rule as the server's customer export). */
const cell = (value: string | number): string => {
  const text = String(value);
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replace(/"/g, '""')}"`;
};

/** The report's rows as CSV - the server's figures, formatted, nothing added. */
export function reportCsv(d: PartnerReports): string {
  const rows: (string | number)[][] = [["Section", "Item", "Value"]];
  rows.push(["Period", "From", d.range.from], ["Period", "To", d.range.to]);
  if (d.customers) {
    for (const [k, v] of Object.entries(d.customers)) rows.push(["Customers", k.replace(/_/g, " "), v]);
  }
  for (const p of d.products ?? []) {
    rows.push(["Products", `${p.product_name} - customers holding`, p.customers_holding ?? ""]);
    rows.push(["Products", `${p.product_name} - active subscriptions`, p.active_subscriptions ?? ""]);
  }
  if (d.sales) {
    rows.push(["Sales", "transactions", d.sales.transactions]);
    for (const a of d.sales.gross) rows.push(["Sales", `gross ${a.currency}`, money(a.amount_minor, a.currency)]);
    for (const a of d.sales.commission_earned) rows.push(["Sales", `commission ${a.currency}`, money(a.amount_minor, a.currency)]);
    for (const p of d.sales.by_product ?? []) {
      rows.push(["Revenue by product", `${p.product_name} (${p.currency}) sales`, p.sales]);
      rows.push(["Revenue by product", `${p.product_name} (${p.currency}) gross`, money(p.gross_minor, p.currency)]);
      rows.push(["Revenue by product", `${p.product_name} (${p.currency}) commission`, money(p.commission_minor, p.currency)]);
    }
  }
  if (d.referrals) {
    rows.push(["Referrals", "clicks", d.referrals.clicks], ["Referrals", "conversions", d.referrals.conversions]);
    for (const c of d.referrals.by_campaign) {
      rows.push(["Referrals by campaign", `${c.campaign} clicks`, c.clicks]);
      rows.push(["Referrals by campaign", `${c.campaign} conversions`, c.conversions]);
    }
  }
  return rows.map((r) => r.map(cell).join(",")).join("\r\n");
}

/** CSV download and the browser's print dialog (Save as PDF). */
function ExportBar({ d }: { d: PartnerReports }) {
  return (
    <p className={`${styles.actionsRow} ${styles.noPrint}`}>
      <button
        type="button"
        className="w360-btn w360-btn-secondary"
        onClick={() => {
          const blob = new Blob([reportCsv(d)], { type: "text/csv;charset=utf-8" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `workeasy360-report-${d.range.from.slice(0, 10)}-${d.range.to.slice(0, 10)}.csv`;
          a.click();
          URL.revokeObjectURL(url);
        }}
      >
        Download CSV
      </button>
      <button type="button" className="w360-btn w360-btn-secondary" onClick={() => window.print()}>
        Print / Save as PDF
      </button>
    </p>
  );
}

/**
 * Reports & Analytics (ADR-047): the same page in every workspace, showing only
 * the sections the signed-in person's permissions allow.
 *
 * **Factual only.** Every number is a count of rows or a sum of the commission
 * ledger, computed by the server for the period chosen; there is no score, no
 * forecast and no figure this screen derives. A section the person may not see is
 * simply not drawn (the server sends `null`, never zero).
 */
export function Reports() {
  const { id } = usePanel();
  const controls = useRangeControls();
  const [load] = useLoad(
    () => panelApi.reports(id, controls.range, controls.from, controls.to),
    [id, controls.range, controls.from ?? "", controls.to ?? ""],
  );
  return (
    <div className={styles.page}>
      <PageHeader
        title="Reports & Analytics"
        lead="Counts and ledger totals for the period you choose. Nothing here is estimated or forecast."
        actions={controls.node}
      />
      <LoadGate load={load} what="your reports">
        {(d) => {
          const nothing = !d.customers && !d.products && !d.sales && !d.referrals;
          return (
            <>
              {!nothing && <ExportBar d={d} />}
              {d.customers && (
                <section className={styles.panel} aria-labelledby="rep-customers">
                  <h2 id="rep-customers">Customers</h2>
                  <ul className={styles.kpis} aria-label="Customer figures">
                    <Kpi icon="users" label="Total customers" value={d.customers.total} />
                    <Kpi icon="building" label="Active" value={d.customers.active} />
                    <Kpi icon="history" label="Suspended" value={d.customers.suspended} />
                    <Kpi icon="lock" label="Ended" value={d.customers.ended} />
                    <Kpi icon="card" label="Active subscriptions" value={d.customers.active_subscriptions} />
                  </ul>
                </section>
              )}
              {d.products && (
                <section className={styles.panel} aria-labelledby="rep-products">
                  <h2 id="rep-products">Product performance</h2>
                  <Table
                    caption="Customers and subscriptions by product"
                    head={["Product", "Customers holding it", "Active subscriptions"]}
                    isEmpty={d.products.length === 0}
                    empty="No products have been approved for your account yet."
                  >
                    {d.products.map((p) => (
                      <tr key={p.product_code}>
                        <th scope="row">{p.product_name}</th>
                        <td className={styles.num}>{p.customers_holding}</td>
                        <td className={styles.num}>{p.active_subscriptions}</td>
                      </tr>
                    ))}
                  </Table>
                </section>
              )}
              {d.sales && (
                <section className={styles.panel} aria-labelledby="rep-sales">
                  <h2 id="rep-sales">Sales and commission</h2>
                  <ul className={styles.kpis} aria-label="Sales figures">
                    <Kpi icon="receipt" label="Transactions" value={d.sales.transactions} note="Sales on which you earn" />
                    <Kpi icon="chart" label="Gross sales" value={<Amounts amounts={d.sales.gross} />} />
                    <Kpi icon="currency" label="Commission earned" value={<Amounts amounts={d.sales.commission_earned} />} />
                  </ul>
                  <p className={styles.muted}>From the commission ledger. A sale on which nothing is earned is not in it.</p>
                  {d.sales.by_product && (
                    <Table
                      caption="Revenue by product"
                      head={["Product", "Currency", "Sales", "Gross", "Commission"]}
                      isEmpty={d.sales.by_product.length === 0}
                      empty="No product sales in this period."
                    >
                      {d.sales.by_product.map((p) => (
                        <tr key={`${p.product_code}-${p.currency}`}>
                          <th scope="row">{p.product_name}</th>
                          <td>{p.currency}</td>
                          <td className={styles.num}>{p.sales}</td>
                          <td className={styles.num}>{money(p.gross_minor, p.currency)}</td>
                          <td className={styles.num}>{money(p.commission_minor, p.currency)}</td>
                        </tr>
                      ))}
                    </Table>
                  )}
                </section>
              )}
              {d.referrals && (
                <section className={styles.panel} aria-labelledby="rep-referrals">
                  <h2 id="rep-referrals">Referrals</h2>
                  <ul className={styles.kpis} aria-label="Referral figures">
                    <Kpi icon="network" label="Clicks" value={d.referrals.clicks} />
                    <Kpi icon="users" label="Unique visitors" value={d.referrals.unique_visitors} />
                    <Kpi icon="users" label="Registrations" value={d.referrals.registrations} note="Created a Workeasy360 account" />
                    <Kpi icon="bolt" label="Conversions" value={d.referrals.conversions} />
                    <Kpi icon="chart" label="Conversion rate" value={percent(d.referrals.conversion_rate_bp)} />
                  </ul>
                  <Table
                    caption="Referrals by campaign"
                    head={["Campaign", "Clicks", "Conversions", "Conversion rate"]}
                    isEmpty={d.referrals.by_campaign.length === 0}
                    empty="No links yet."
                  >
                    {d.referrals.by_campaign.map((c) => (
                      <tr key={c.campaign}>
                        <th scope="row">{c.campaign}</th>
                        <td className={styles.num}>{c.clicks}</td>
                        <td className={styles.num}>{c.conversions}</td>
                        <td className={styles.num}>{percent(c.conversion_rate_bp)}</td>
                      </tr>
                    ))}
                  </Table>
                </section>
              )}
              {nothing && <EmptyState title="There is nothing to report for your role." icon="chart" />}
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}
