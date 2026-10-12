"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { adminApi } from "@/lib/api/controlPlane";
import { partnerConsoleApi, type PartnerSummaryView } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { ExperienceLabels } from "./PartnerConsole";
import { BusinessDetailsTab, ContactsTab } from "./PartnerProfileTabs";
import {
  ActivityTab,
  BrandingTab,
  CommissionsTab,
  CustomersTab,
  DomainsTab,
  OverviewTab,
  PayoutsTab,
  ProductsTab,
  ReferralsTab,
  SubscriptionsTab,
  UsersTab,
} from "./ResellerTabs";
import { TAB_LABELS, TAB_ORDER, type TabKey } from "./partnerTabs";
import { LoadGate, MoreButton, ReasonAction, Status, Table, useAction, useLoad, usePaged, type Load } from "./ui";
import { Tabs, type TabItem } from "./widgets";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";
import ps from "./PartnerProfileTabs.module.css";

/**
 * One partner (Phase 10c, complete setup). The partner's page is a set of tabs -
 * Overview, Business Details, Contacts, Customers, Products & Pricing,
 * Subscriptions, Commissions, Payouts, Branding, Domains, Users & Permissions,
 * Referrals and Activity - with the lifecycle actions kept above them. Each tab
 * keeps the commands it always had; the tabs add reads, not a second way to
 * change anything, and the lifecycle is draft / active / suspended / terminated:
 * there is no way to delete a partner, because its history is kept.
 */

/** The tabs this staff member can read, and that are relevant to what the
 * partner can do. Relevance is read from the labels the *server* derived from
 * the partner's grants: Branding and Domains belong to a partner wearing
 * White-label, Referrals to one wearing Affiliate. Hiding a tab is a
 * convenience - the server re-checks every read, and the Overview tab still
 * shows the white-label state and links to enabling it. */
export function visibleTabs(permissions: string[], labels: string[]): TabItem<TabKey>[] {
  return TAB_ORDER.filter((key) => {
    if (key === "commissions" || key === "payouts") return can(permissions, "platform.staff_commission.view");
    if (key === "activity") return can(permissions, "platform.staff_audit.view");
    if (key === "branding" || key === "domains") return labels.includes("white_label");
    if (key === "referrals") return labels.includes("affiliate");
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
  // One overview read for the whole page: the Activate guidance, the Overview and
  // the product options all show it, and a change refreshes it in place (the
  // current answer stays on screen until the new one arrives).
  const [summary, reloadSummary] = useLoad(() => partnerConsoleApi.summary(partnerId), [partnerId]);
  const bump = () => {
    reload();
    reloadSummary();
    setRev((n) => n + 1);
  };
  const { act, busy, feedback } = useAction(bump);
  const manage = can(permissions, "platform.staff_partner.manage");

  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        <Link href="/admin/partners/all">All partners</Link>
      </p>
      <LoadGate load={load} what="this partner">
        {(p) => {
          const labels = (p.experience ?? []).map((e) => e.key);
          const items = visibleTabs(permissions, labels);
          const selected = items.some((i) => i.key === tab) ? tab : "overview";
          const available = items.map((i) => i.key);
          const tabProps = { partnerId: p.id, partner: p, summary, rev, bump };
          return (
          <>
            <div className={local.titleRow}>
              <h1>{p.display_name}</h1>
              <Status value={p.status} />
            </div>
            <ExperienceLabels labels={p.experience ?? []} />
            <p className={styles.muted}>
              {p.code} · pays out in {p.payout_currency} · {p.live_customers} live customers
              {p.status_reason && ` · ${p.status_reason}`}
            </p>
            {feedback}
            {manage && (
              <div className={styles.row}>
                {p.status === "draft" && (
                  <ActivateAction
                    summary={summary}
                    busy={busy}
                    onOpenOverview={() => setTab("overview")}
                    onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "active", r, p.version), "Partner activated.")}
                  />
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
              {selected === "overview" && <OverviewTab {...tabProps} onOpen={(next) => setTab(next)} available={available} />}
              {selected === "business" && <BusinessDetailsTab {...tabProps} />}
              {selected === "contacts" && <ContactsTab {...tabProps} onOpen={(next) => setTab(next)} />}
              {selected === "customers" && <CustomersTab {...tabProps} />}
              {selected === "products" && <ProductsTab {...tabProps} />}
              {selected === "subscriptions" && <SubscriptionsTab {...tabProps} />}
              {selected === "commissions" && <CommissionsTab {...tabProps} />}
              {selected === "payouts" && <PayoutsTab {...tabProps} />}
              {selected === "branding" && <BrandingTab {...tabProps} />}
              {selected === "domains" && <DomainsTab {...tabProps} />}
              {selected === "users" && <UsersTab {...tabProps} />}
              {selected === "referrals" && <ReferralsTab {...tabProps} />}
              {selected === "activity" && <ActivityTab {...tabProps} />}
            </Tabs>
          </>
          );
        }}
      </LoadGate>
    </div>
  );
}

/**
 * Activate, for a draft partner. While the server's readiness answer lists what
 * is missing the button is disabled and the reasons are written next to it - a
 * disabled button cannot be focused, so the words are on the page, not only on
 * the button. This is guidance: the server makes the final check when the action
 * is confirmed. If the readiness answer cannot be read, the action is offered
 * and the server decides.
 */
function ActivateAction({
  summary,
  busy,
  onConfirm,
  onOpenOverview,
}: {
  summary: Load<PartnerSummaryView>;
  busy: boolean;
  onConfirm: (reason: string) => void;
  onOpenOverview: () => void;
}) {
  const why = useId();
  if (summary.status === "loading") {
    return (
      <div className={ps.activateReason}>
        <button type="button" className="w360-btn w360-btn-secondary" disabled aria-describedby={why}>
          Activate
        </button>
        <p id={why} className={styles.muted}>
          Checking whether this partner is ready to activate…
        </p>
      </div>
    );
  }
  const missing = summary.status === "ready" ? summary.data.activation.missing : [];
  if (missing.length === 0) return <ReasonAction label="Activate" busy={busy} onConfirm={onConfirm} />;
  return (
    <div className={ps.activateReason}>
      <button type="button" className="w360-btn w360-btn-secondary" disabled aria-describedby={why}>
        Activate
      </button>
      <p id={why} className={styles.muted}>
        Setup incomplete, so this partner cannot be activated yet. Still missing: {missing.map((m) => m.label).join(", ")}.{" "}
        <button type="button" className={`w360-btn w360-btn-secondary ${ps.linkButton}`} onClick={onOpenOverview}>
          See the setup progress
        </button>
      </p>
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
