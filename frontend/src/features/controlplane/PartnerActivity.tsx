"use client";

import { useId } from "react";
import Link from "next/link";
import {
  partnerConsoleApi,
  type PartnersRecent,
  type RecentDomain,
  type RecentLedgerEntry,
  type RecentPartner,
  type RecentPayout,
  type RecentProvisioning,
} from "@/lib/api/partnerConsole";
import { can } from "./ControlShell";
import { ExperienceLabels } from "./labels";
import { LoadGate, Status, money, useLoad, when } from "./ui";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * The Partner Control Center dashboard's quick actions and recent activity.
 *
 * **Quick actions are links into the one partner architecture.** "Add
 * reseller", "Create affiliate" and "Add partner" all open the same Add Partner
 * flow with a different capability preset ticked; "Enable white-label" opens
 * the existing white-label setup for an existing partner. None of them creates
 * a kind of partner - there is none to create.
 *
 * **Recent activity is not a new activity system.** It renders one server read
 * (the partner console's "recent" endpoint) assembled from data that already exists. A money
 * section the caller may not read comes back `null` and is not shown at all;
 * an empty list means "nothing yet" and says so.
 */

export function QuickActions({ permissions }: { permissions: string[] }) {
  const manage = can(permissions, "platform.staff_partner.manage");
  const seesMoney = can(permissions, "platform.staff_commission.view");
  if (!manage && !seesMoney) return null;
  return (
    <nav aria-label="Quick actions" className={local.quickActions}>
      {manage && (
        <>
          <Link className="w360-btn w360-btn-primary" href="/admin/partners/new">
            Add partner
          </Link>
          <Link className="w360-btn w360-btn-secondary" href="/admin/partners/resellers/new">
            Add reseller
          </Link>
          <Link className="w360-btn w360-btn-secondary" href="/admin/partners/white-label#wl-enable">
            Enable white-label
          </Link>
          <Link className="w360-btn w360-btn-secondary" href="/admin/partners/new?purpose=affiliate">
            Create affiliate
          </Link>
        </>
      )}
      {seesMoney && (
        <Link className="w360-btn w360-btn-secondary" href="/admin/partners/payouts?status=open">
          Review payouts
        </Link>
      )}
    </nav>
  );
}

function Panel<T>({
  title,
  rows,
  empty,
  more,
  render,
}: {
  title: string;
  rows: T[];
  empty: string;
  more?: { href: string; label: string };
  render: (row: T) => React.ReactNode;
}) {
  const id = useId();
  return (
    <section className={`w360-card ${local.panel}`} aria-labelledby={id}>
      <h3 id={id} className={styles.cardTitle}>
        {title}
      </h3>
      {rows.length === 0 ? (
        <p className={styles.muted}>{empty}</p>
      ) : (
        <ul className={local.activity}>{rows.map(render)}</ul>
      )}
      {more && (
        <Link href={more.href} className={local.panelMore}>
          {more.label}
        </Link>
      )}
    </section>
  );
}

const partnerTab = (partnerId: string, tab: string) => `/admin/partners/${partnerId}?tab=${tab}`;

const KIND_TEXT: Record<string, string> = {
  accrual: "Earned",
  reversal: "Reversed",
  adjustment: "Adjusted",
};

function PartnerItem({ row }: { row: RecentPartner }) {
  return (
    <li className={local.activityItem}>
      <Link href={`/admin/partners/${row.id}`}>{row.display_name}</Link>
      <span className={local.activityMeta}>
        <Status value={row.status} /> <span className={styles.muted}>added {when(row.created_at)}</span>
      </span>
      <ExperienceLabels labels={row.experience} />
    </li>
  );
}

function ProvisioningItem({ row }: { row: RecentProvisioning }) {
  return (
    <li className={local.activityItem}>
      <Link href={partnerTab(row.partner_id, "customers")}>{row.name}</Link>
      <span className={local.activityMeta}>
        <span className={styles.muted}>via {row.partner_name}</span> <Status value={row.link_status} />
        <span className={styles.muted}>{when(row.started_at)}</span>
      </span>
    </li>
  );
}

function SaleItem({ row }: { row: RecentLedgerEntry }) {
  return (
    <li className={local.activityItem}>
      <Link href={partnerTab(row.partner_id, "commissions")}>{row.partner_name || "Partner"}</Link>
      <span className={local.activityMeta}>
        <span>{row.organization_name ? `Sale to ${row.organization_name}` : "Sale"}</span>
        <span className={styles.muted}>
          earned {money(row.amount_minor, row.currency)} · {when(row.event_at)}
        </span>
      </span>
    </li>
  );
}

function LedgerItem({ row }: { row: RecentLedgerEntry }) {
  return (
    <li className={local.activityItem}>
      <Link href={partnerTab(row.partner_id, "commissions")}>{row.partner_name || "Partner"}</Link>
      <span className={local.activityMeta}>
        <span>
          {KIND_TEXT[row.kind] ?? row.kind} {money(row.amount_minor, row.currency)}
        </span>
        <span className={styles.muted}>{when(row.event_at)}</span>
      </span>
    </li>
  );
}

function PayoutItem({ row }: { row: RecentPayout }) {
  return (
    <li className={local.activityItem}>
      <Link href={partnerTab(row.partner_id, "payouts")}>{row.partner_name || "Partner"}</Link>
      <span className={local.activityMeta}>
        <Status value={row.status} />
        <span>{money(row.payable_minor, row.currency)}</span>
        <span className={styles.muted}>period to {row.period_end}</span>
      </span>
    </li>
  );
}

function DomainItem({ row }: { row: RecentDomain }) {
  return (
    <li className={local.activityItem}>
      <Link href={partnerTab(row.partner_id, "domains")} className={styles.mono}>
        {row.hostname}
      </Link>
      <span className={local.activityMeta}>
        <span className={styles.muted}>{row.partner_name}</span> <Status value={row.status} />
        <span className={styles.muted}>requested {when(row.requested_at)}</span>
      </span>
    </li>
  );
}

function ActivityPanels({ d }: { d: PartnersRecent }) {
  return (
    <div className={local.panels}>
      <Panel
        title="Recent partners"
        rows={d.partners}
        empty="No partner accounts yet."
        more={{ href: "/admin/partners/all", label: "All partners" }}
        render={(r) => <PartnerItem key={r.id} row={r} />}
      />
      <Panel
        title="Recent customer provisioning"
        rows={d.provisioning}
        empty="No partner has a customer yet."
        more={{ href: "/admin/partners/customers", label: "All partner customers" }}
        render={(r) => <ProvisioningItem key={`${r.partner_id}-${r.organization_id}-${r.started_at}`} row={r} />}
      />
      {d.sales !== null && (
        <Panel
          title="Recent partner sales"
          rows={d.sales}
          empty="No sale has earned commission yet."
          more={{ href: "/admin/partners/commissions", label: "Commission ledger" }}
          render={(r) => <SaleItem key={r.id} row={r} />}
        />
      )}
      {d.payouts !== null && (
        <Panel
          title="Pending payouts"
          rows={d.payouts}
          empty="No payout is waiting for a decision or a payment."
          more={{ href: "/admin/partners/payouts?status=open", label: "Review payouts" }}
          render={(r) => <PayoutItem key={r.id} row={r} />}
        />
      )}
      <Panel
        title="Domain verification required"
        rows={d.domains}
        empty="No domain is waiting for DNS proof."
        more={{ href: "/admin/partners/white-label?domain_status=awaiting", label: "White-label domains" }}
        render={(r) => <DomainItem key={r.id} row={r} />}
      />
      {d.commission !== null && (
        <Panel
          title="Recent commission activity"
          rows={d.commission}
          empty="The commission ledger is empty."
          more={{ href: "/admin/partners/commissions", label: "Commission ledger" }}
          render={(r) => <LedgerItem key={r.id} row={r} />}
        />
      )}
    </div>
  );
}

export function RecentActivity() {
  const [load] = useLoad(() => partnerConsoleApi.recent(), []);
  return (
    <section aria-labelledby="partner-recent" className={styles.stack}>
      <h2 id="partner-recent">Recent activity</h2>
      <LoadGate load={load} what="recent partner activity">
        {(d) => <ActivityPanels d={d} />}
      </LoadGate>
    </section>
  );
}
