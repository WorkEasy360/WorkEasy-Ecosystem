"use client";

import { adminApi, type AttentionItem } from "@/lib/api/controlPlane";
import { DataTable, EmptyState, Page, PageHeader, type Column } from "./kit";
import { ButtonLink } from "./kit/Button";
import { useLoad } from "./ui";

const WHAT: Record<AttentionItem["key"], { title: string; href: string; action: string }> = {
  partner_upgrades: { title: "Partner upgrade requests waiting for a decision", href: "/admin/partners/all", action: "Review" },
  draft_partners: { title: "Partners still in draft", href: "/admin/partners/all?status=draft", action: "Open drafts" },
  payouts: { title: "Payouts waiting for action", href: "/admin/partners/withdrawals", action: "Open withdrawals" },
  kyc_reviews: { title: "Partner applications waiting for KYC review", href: "/admin/partners/applications", action: "Review" },
  failed_payments: { title: "Failed payments and activations", href: "/admin/payments/failed", action: "Open" },
  low_balance: { title: "Wallets low on balance", href: "/admin/wallets?low=1", action: "Open wallets" },
  support_tickets: { title: "Support tickets still open", href: "/admin/tickets", action: "Open tickets" },
};

const COLUMNS: Column<AttentionItem>[] = [
  { key: "what", header: "What is waiting", render: (i) => WHAT[i.key].title, value: (i) => WHAT[i.key].title },
  { key: "count", header: "Count", align: "right", render: (i) => i.count, value: (i) => i.count },
  {
    key: "open",
    header: "Action",
    align: "right",
    render: (i) => (
      <ButtonLink href={WHAT[i.key].href} size="sm">
        {WHAT[i.key].action}
      </ButtonLink>
    ),
  },
];

/** Everything waiting for staff, in one list (the bell's "View all"). Only what
 * this staff member's grants cover is counted by the server. */
export function AdminNotifications() {
  const [load] = useLoad(() => adminApi.attention(), []);
  const rows = load.status === "ready" ? load.data.items.filter((i) => WHAT[i.key] && i.count > 0) : [];
  return (
    <Page>
      <PageHeader title="Notifications" subtitle="What is waiting for you across the console." />
      <DataTable
          caption="Notifications"
          columns={COLUMNS}
          rows={rows}
          rowKey={(i) => i.key}
          rowHref={(i) => WHAT[i.key].href}
          loading={load.status === "loading"}
          error={load.status === "error" ? load.message : load.status === "forbidden" ? "You don't have access to notifications." : null}
          pageSize={0}
          empty={<EmptyState icon="bell" title="You're all caught up" hint="Nothing is waiting for a decision right now." />}
        />
    </Page>
  );
}
