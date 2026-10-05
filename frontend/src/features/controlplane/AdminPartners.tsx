"use client";

import { useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { adminApi } from "@/lib/api/controlPlane";
import { partnerConsoleApi } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { ExperienceLabels } from "./PartnerConsole";
import {
  ActivityTab,
  BrandingTab,
  CommissionsTab,
  CustomersTab,
  DomainsTab,
  OverviewTab,
  PayoutsTab,
  ProductsTab,
  SubscriptionsTab,
  UsersTab,
} from "./ResellerTabs";
import { LoadGate, MoreButton, ReasonAction, Status, Table, useAction, useLoad, usePaged } from "./ui";
import { Tabs, type TabItem } from "./widgets";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * One partner (Phase 10c). The page that used to be a flat list of sections is
 * now the same sections under tabs - Overview, Customers, Products & Pricing,
 * Subscriptions, Commissions, Payouts, Branding, Domains, Users & Permissions
 * and Activity - with the lifecycle actions kept above them. Each tab keeps the
 * commands it always had; the tabs add reads, not a second way to change
 * anything.
 */

type TabKey =
  | "overview"
  | "customers"
  | "products"
  | "subscriptions"
  | "commissions"
  | "payouts"
  | "branding"
  | "domains"
  | "users"
  | "activity";

const TAB_LABELS: Record<TabKey, string> = {
  overview: "Overview",
  customers: "Customers",
  products: "Products & Pricing",
  subscriptions: "Subscriptions",
  commissions: "Commissions",
  payouts: "Payouts",
  branding: "Branding",
  domains: "Domains",
  users: "Users & Permissions",
  activity: "Activity",
};

const TAB_ORDER = Object.keys(TAB_LABELS) as TabKey[];

/** Which grant a tab's data needs. A convenience: the server re-checks every read. */
function visibleTabs(permissions: string[]): TabItem<TabKey>[] {
  return TAB_ORDER.filter((key) => {
    if (key === "commissions" || key === "payouts") return can(permissions, "platform.staff_commission.view");
    if (key === "activity") return can(permissions, "platform.staff_audit.view");
    return true;
  }).map((key) => ({ key, label: TAB_LABELS[key] }));
}

export function PartnerDetailScreen({ partnerId }: { partnerId: string }) {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const requested = params.get("tab");
  const [tab, setTab] = useState<TabKey>(TAB_ORDER.includes(requested as TabKey) ? (requested as TabKey) : "overview");
  const [rev, setRev] = useState(0);
  const [load, reload] = useLoad(() => adminApi.partner(partnerId), [partnerId]);
  const bump = () => {
    reload();
    setRev((n) => n + 1);
  };
  const { act, busy, feedback } = useAction(bump);
  const manage = can(permissions, "platform.staff_partner.manage");
  const items = visibleTabs(permissions);
  const selected = items.some((i) => i.key === tab) ? tab : "overview";

  return (
    <div className={styles.stack}>
      <LoadGate load={load} what="this partner">
        {(p) => (
          <>
            <h1>
              {p.display_name} <Status value={p.status} />
            </h1>
            <p className={styles.muted}>
              {p.code} · pays out in {p.payout_currency} · {p.live_customers} live customers
              {p.status_reason && ` · ${p.status_reason}`}
            </p>
            {feedback}
            {manage && (
              <div className={styles.row}>
                {p.status === "draft" && (
                  <ReasonAction label="Activate" busy={busy} onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "active", r, p.version), "Partner activated.")} />
                )}
                {p.status === "active" && (
                  <ReasonAction label="Suspend" busy={busy} onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "suspended", r, p.version), "Partner suspended.")} />
                )}
                {p.status === "suspended" && (
                  <ReasonAction label="Reactivate" busy={busy} onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "active", r, p.version), "Partner reactivated.")} />
                )}
                {p.status !== "terminated" && (
                  <ReasonAction label="Terminate" busy={busy} onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "terminated", r, p.version), "Partner terminated. Its customers were not touched.")} />
                )}
              </div>
            )}
            <Tabs label="Partner sections" items={items} selected={selected} onSelect={setTab}>
              {selected === "overview" && (
                <OverviewTab partnerId={p.id} partner={p} rev={rev} bump={bump} onOpen={(next) => setTab(next)} />
              )}
              {selected === "customers" && <CustomersTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
              {selected === "products" && <ProductsTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
              {selected === "subscriptions" && <SubscriptionsTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
              {selected === "commissions" && <CommissionsTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
              {selected === "payouts" && <PayoutsTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
              {selected === "branding" && <BrandingTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
              {selected === "domains" && <DomainsTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
              {selected === "users" && <UsersTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
              {selected === "activity" && <ActivityTab partnerId={p.id} partner={p} rev={rev} bump={bump} />}
            </Tabs>
          </>
        )}
      </LoadGate>
    </div>
  );
}

/** Partners → Products & Pricing: which partner may sell what. Prices are set per
 * partner, so each row opens that partner's own Products & Pricing tab. */
export function PartnerProductsPricing() {
  const [product, setProduct] = useState("");
  const [products] = useLoad(() => partnerConsoleApi.products().catch(() => ({ results: [] })), []);
  const paged = usePaged(
    (cursor) => partnerConsoleApi.resellers({ scope: "all", product: product || undefined, cursor }),
    [product],
  );
  return (
    <div className={styles.stack}>
      <h1>Products &amp; pricing</h1>
      <p className={styles.lead}>
        Which products each partner may sell. A partner price is set per plan on the partner&apos;s own page; a plan without one is
        charged the list price.
      </p>
      <div className={local.filters}>
        <div>
          <label className="w360-label" htmlFor="pp-product">
            Product
          </label>
          <select id="pp-product" className="w360-input" value={product} onChange={(e) => setProduct(e.target.value)}>
            <option value="">Any</option>
            {products.status === "ready" &&
              products.data.results.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.name}
                </option>
              ))}
          </select>
        </div>
      </div>
      {paged.state === "loading" && <p aria-live="polite">Loading partners…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to partners.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load partners.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Products by partner" head={["Partner", "Status", "Products allowed", ""]} isEmpty={paged.rows.length === 0} empty="No partners match.">
            {paged.rows.map((r) => (
              <tr key={r.id}>
                <th scope="row">
                  <Link href={`/admin/partners/${r.id}?tab=products`}>{r.display_name}</Link>
                  <ExperienceLabels labels={r.experience} />
                </th>
                <td>
                  <Status value={r.status} />
                </td>
                <td>
                  {r.products.length === 0 ? (
                    <span className={styles.muted}>None</span>
                  ) : (
                    r.products.map((p) => (p.plan_code ? `${p.product_code} (${p.plan_code})` : p.product_code)).join(", ")
                  )}
                </td>
                <td>
                  <Link href={`/admin/partners/${r.id}?tab=products`}>Products &amp; pricing</Link>
                </td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}
