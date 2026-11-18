"use client";

import { useState } from "react";
import Link from "next/link";
import { storeAdminApi, type Amount, type StoreDashboard as Dashboard } from "@/lib/api/store";
import { LoadGate, money, useLoad } from "../ui";
import { Card, EmptyState, Page, Select, StatCard, StatGrid, formatNumber } from "../kit";
import { StoreHeader } from "./StoreTabs";
import styles from "./ProductManager.module.css";

/**
 * Super Admin → Store → Overview (ADR-056): the Store at a glance. Read from
 * the projections (orders, the commission ledger, customer summaries); nothing
 * here is a source of truth for a transaction. Each section shows only when
 * the staff member holds the grant of the screen it summarises.
 */

const PERIODS = [7, 30, 90, 365] as const;

function amounts(list: Amount[] | undefined): string {
  if (!list || list.length === 0) return "—";
  return list.map((a) => money(a.amount_minor, a.currency)).join(" · ");
}

/** A KPI that opens the page it counts. The value is the link, so a keyboard
 * user reaches it and hears what it is. */
function Tile({ label, value, href, icon, tone }: { label: string; value: React.ReactNode; href?: string; icon?: string; tone?: "green" | "blue" | "purple" | "orange" | "red" }) {
  return <StatCard label={label} icon={icon} tone={tone} value={href ? <Link href={href} className={styles.statLink}>{value}</Link> : value} />;
}

export function StoreDashboard() {
  const [days, setDays] = useState<number>(30);
  const [load] = useLoad(() => storeAdminApi.dashboard(days), [days]);
  return (
    <Page>
      <StoreHeader
        current="dashboard"
        actions={
          <Select
            label="Period"
            prefix="Period:"
            value={String(days)}
            onChange={(v) => setDays(Number(v))}
            options={PERIODS.map((d) => ({ value: String(d), label: `Last ${d} days` }))}
          />
        }
      />
      <LoadGate load={load} what="the store overview">
        {(d: Dashboard) => (
          <>
            <Card title="Catalog">
              <StatGrid>
                <Tile label="Published products" icon="store" value={d.catalog.products_published ?? 0} href="/admin/store" />
                <Tile label="Draft products" icon="edit" tone="orange" value={d.catalog.products_draft ?? 0} href="/admin/products" />
                <Tile label="Coming soon" icon="clock" tone="blue" value={d.catalog.products_coming_soon ?? 0} />
                <Tile label="Active plans" icon="tag" tone="purple" value={d.catalog.plans_active ?? 0} href="/admin/plans" />
              </StatGrid>
            </Card>
            {d.sales && (
              <Card title="Sales" description="Paid orders in the period, by the channel that sold them.">
                <StatGrid>
                  <Tile label="Paid orders" icon="receipt" value={formatNumber(d.sales.orders)} href="/admin/orders" />
                  <Tile label="Revenue collected" icon="currency" tone="blue" value={amounts(d.sales.revenue)} href="/admin/payments" />
                  <Tile label={`Direct sales (${d.sales.by_channel.direct.orders})`} icon="cart" tone="purple" value={amounts(d.sales.by_channel.direct.amounts)} />
                </StatGrid>
                <StatGrid>
                  <Tile label={`Reseller sales (${d.sales.by_channel.reseller.orders})`} icon="handshake" tone="orange" value={amounts(d.sales.by_channel.reseller.amounts)} />
                  <Tile label={`White-label sales (${d.sales.by_channel.white_label.orders})`} icon="palette" tone="blue" value={amounts(d.sales.by_channel.white_label.amounts)} />
                  {d.affiliate_sales && (
                    <Tile
                      label={`Affiliate sales (${d.affiliate_sales.reduce((n, r) => n + r.sales, 0)})`}
                      icon="gift"
                      tone="purple"
                      value={amounts(d.affiliate_sales.map((r) => ({ currency: r.currency, amount_minor: r.basis_minor })))}
                    />
                  )}
                </StatGrid>
                <h3 className={styles.subHead}>Top products</h3>
                {d.sales.top_products.length === 0 ? (
                  <EmptyState icon="chart" title="No paid orders in this period" compact />
                ) : (
                  <ol className={styles.topList}>
                    {d.sales.top_products.map((t) => (
                      <li key={t.code}>
                        {t.name} - {t.orders} order{t.orders === 1 ? "" : "s"}
                      </li>
                    ))}
                  </ol>
                )}
              </Card>
            )}
            {(d.subscriptions || d.partner_requests) && (
              <Card title="Customers and partners">
                <StatGrid>
                  {d.subscriptions && <Tile label="Active subscriptions" icon="checkCircle" value={d.subscriptions.active} href="/admin/subscriptions" />}
                  {d.subscriptions && <Tile label="Trials" icon="clock" tone="blue" value={d.subscriptions.trialing} href="/admin/subscriptions" />}
                  {d.partner_requests && (
                    <Tile
                      label="Pending partner product requests"
                      icon="handshake"
                      tone="orange"
                      value={d.partner_requests.promote + d.partner_requests.sell}
                      href="/admin/partners/product-requests"
                    />
                  )}
                </StatGrid>
              </Card>
            )}
            <p className={styles.muted}>Affiliate sales count only commissions earned on verified payments; a click alone never counts.</p>
          </>
        )}
      </LoadGate>
    </Page>
  );
}
