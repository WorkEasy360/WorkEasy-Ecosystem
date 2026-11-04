"use client";

import { useState } from "react";
import Link from "next/link";
import { storeAdminApi, type Amount, type StoreDashboard as Dashboard } from "@/lib/api/store";
import { LoadGate, money, useLoad } from "../ui";
import { StoreTabs } from "./StoreTabs";
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

function Tile({ label, value, href }: { label: string; value: React.ReactNode; href?: string }) {
  return (
    <div className={styles.card}>
      <p className={styles.muted}>{label}</p>
      <p className={styles.cardTitle}>{href ? <Link href={href}>{value}</Link> : value}</p>
    </div>
  );
}

export function StoreDashboard() {
  const [days, setDays] = useState<number>(30);
  const [load] = useLoad(() => storeAdminApi.dashboard(days), [days]);
  return (
    <div className={styles.page}>
      <h1>Store overview</h1>
      <StoreTabs current="dashboard" />
      <label className="w360-label">
        Period{" "}
        <select className="w360-input" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {PERIODS.map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </select>
      </label>
      <LoadGate load={load} what="the store overview">
        {(d: Dashboard) => (
          <>
            <section aria-labelledby="dash-catalog">
              <h2 id="dash-catalog">Catalog</h2>
              <div className={styles.formGrid}>
                <Tile label="Published products" value={d.catalog.products_published ?? 0} href="/admin/store" />
                <Tile label="Draft products" value={d.catalog.products_draft ?? 0} href="/admin/products" />
                <Tile label="Coming soon" value={d.catalog.products_coming_soon ?? 0} />
                <Tile label="Active plans" value={d.catalog.plans_active ?? 0} href="/admin/plans" />
              </div>
            </section>
            {d.sales && (
              <section aria-labelledby="dash-sales">
                <h2 id="dash-sales">Sales</h2>
                <div className={styles.formGrid}>
                  <Tile label="Paid orders" value={d.sales.orders} href="/admin/orders" />
                  <Tile label="Revenue collected" value={amounts(d.sales.revenue)} href="/admin/payments" />
                  <Tile label={`Direct sales (${d.sales.by_channel.direct.orders})`} value={amounts(d.sales.by_channel.direct.amounts)} />
                  <Tile label={`Reseller sales (${d.sales.by_channel.reseller.orders})`} value={amounts(d.sales.by_channel.reseller.amounts)} />
                  <Tile label={`White-label sales (${d.sales.by_channel.white_label.orders})`} value={amounts(d.sales.by_channel.white_label.amounts)} />
                  {d.affiliate_sales && (
                    <Tile
                      label={`Affiliate sales (${d.affiliate_sales.reduce((n, r) => n + r.sales, 0)})`}
                      value={amounts(d.affiliate_sales.map((r) => ({ currency: r.currency, amount_minor: r.basis_minor })))}
                    />
                  )}
                </div>
                <h3>Top products</h3>
                {d.sales.top_products.length === 0 ? (
                  <p className={styles.muted}>No paid orders in this period.</p>
                ) : (
                  <ol>
                    {d.sales.top_products.map((t) => (
                      <li key={t.code}>
                        {t.name} - {t.orders} order{t.orders === 1 ? "" : "s"}
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            )}
            {(d.subscriptions || d.partner_requests) && (
              <section aria-labelledby="dash-subs">
                <h2 id="dash-subs">Customers and partners</h2>
                <div className={styles.formGrid}>
                  {d.subscriptions && (
                    <>
                      <Tile label="Active subscriptions" value={d.subscriptions.active} href="/admin/subscriptions" />
                      <Tile label="Trials" value={d.subscriptions.trialing} href="/admin/subscriptions" />
                    </>
                  )}
                  {d.partner_requests && (
                    <Tile
                      label="Pending partner product requests"
                      value={d.partner_requests.promote + d.partner_requests.sell}
                      href="/admin/partners/product-requests"
                    />
                  )}
                </div>
              </section>
            )}
            <p className={styles.muted}>Affiliate sales count only commissions earned on verified payments; a click alone never counts.</p>
          </>
        )}
      </LoadGate>
    </div>
  );
}
