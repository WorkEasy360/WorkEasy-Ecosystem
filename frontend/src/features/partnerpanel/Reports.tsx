"use client";

import { LoadGate, Table, useLoad } from "@/features/controlplane/ui";
import { panelApi } from "@/lib/api/partnerPanel";
import { Amounts, EmptyState, Kpi, PageHeader, usePanel } from "./PanelKit";
import { percent, useRangeControls } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

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
                </section>
              )}
              {d.referrals && (
                <section className={styles.panel} aria-labelledby="rep-referrals">
                  <h2 id="rep-referrals">Referrals</h2>
                  <ul className={styles.kpis} aria-label="Referral figures">
                    <Kpi icon="network" label="Clicks" value={d.referrals.clicks} />
                    <Kpi icon="users" label="Signups" unavailable={d.not_implemented.affiliate_signups} />
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
