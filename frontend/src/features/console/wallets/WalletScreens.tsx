"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Icon } from "@/components/sidebar/icons";
import { consoleApi, type Order } from "@/lib/api/console";
import {
  orgAccountApi,
  type MeterMonth,
  type OrgWallets,
  type TransactionPage,
  type UsagePage,
  type WalletEntry,
  type WalletKind,
} from "@/lib/api/orgAccount";
import {
  Alert,
  Badge,
  Button,
  ButtonLink,
  Card,
  DataTable,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  Select,
  Skeleton,
  StatCard,
  StatGrid,
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  type Column,
} from "@/features/controlplane/kit";
import { downloadCsv } from "@/features/controlplane/kit/csv";
import { useLoad, type Load } from "@/features/controlplane/ui";
import { ContactOrgAdmin } from "@/features/console/consoleUi";
import { useOrgAccess } from "@/features/console/OrgAccess";
import { SpendDialog, useWalletSpend, type SpendKind } from "./WalletSpend";
import styles from "./Wallets.module.css";

/**
 * Wallets & Credits (User Admin Panel, 2026-11-11): the organization's three
 * balances, each on its own - the Platform Wallet and the Consumption Wallet are
 * money in the billing currency, AI Credits are whole credits. **Never added
 * together.** Everything shown is the server's (`GET /orgs/<slug>/wallets`), for
 * the member's own organization; who may read and spend is decided there too:
 * `platform.billing.view` to see balances, `platform.billing.manage` to add money.
 */

const VIEW = "platform.billing.view";
const PAGE = 25;

const KIND_LABEL: Record<WalletKind, string> = {
  platform: "Platform Wallet",
  ai_credits: "AI Credits",
  consumption: "Consumption Wallet",
};

const KIND_PAGE: Record<WalletKind, string> = {
  platform: "wallets/platform",
  ai_credits: "wallets/ai-credits",
  consumption: "wallets/consumption",
};

/** The value of one movement, as its wallet counts: money or credits. */
export function entryAmount(entry: Pick<WalletEntry, "amount">, kind: WalletKind, currency: string | null | undefined): string {
  const sign = entry.amount > 0 ? "+" : "−";
  const size = Math.abs(entry.amount);
  return kind === "ai_credits" ? `${sign}${formatNumber(size)} credits` : `${sign}${formatMoney(size, currency ?? "")}`;
}

function balanceOf(kind: WalletKind, w: OrgWallets): string {
  if (kind === "ai_credits") return `${formatNumber(w.ai_credits.balance)} credits`;
  const wallet = kind === "platform" ? w.platform : w.consumption;
  return formatMoney(wallet.balance_minor, wallet.currency || w.currency || "");
}

function isLow(kind: WalletKind, w: OrgWallets): boolean {
  return kind === "ai_credits" ? w.ai_credits.is_low : kind === "platform" ? w.platform.is_low : w.consumption.is_low;
}

/** Members who may not see billing are told who can, instead of a failing page. */
export function NoWalletAccess() {
  return (
    <Alert tone="info" title="Wallet balances are visible to your organization's billing team">
      <ContactOrgAdmin what="wallets, credits and top-ups" />
    </Alert>
  );
}

function useWallets(): [Load<OrgWallets>, () => void] {
  const { slug, can } = useOrgAccess();
  const allowed = can(VIEW);
  return useLoad(
    () => (allowed ? orgAccountApi.wallets(slug) : Promise.reject(new Error("forbidden"))),
    [slug, allowed],
  );
}

function OverviewSkeleton() {
  return (
    <div className={styles.walletGrid} aria-busy="true" aria-live="polite">
      <span className="w360-visually-hidden">Loading your wallets…</span>
      {[0, 1, 2].map((i) => (
        <div key={i} className={styles.walletCard}>
          <Skeleton width="40%" />
          <Skeleton width="60%" height={28} />
          <Skeleton width="80%" />
          <Skeleton width="70%" />
        </div>
      ))}
    </div>
  );
}

function LoadProblem({ load }: { load: Load<OrgWallets> }) {
  if (load.status === "forbidden") return <NoWalletAccess />;
  if (load.status === "error") {
    return <Alert tone="danger" title="Your wallets could not be loaded">{load.message} Try again shortly.</Alert>;
  }
  return null;
}

/** "Set up billing first": a wallet is topped up in the billing currency. */
function NoBillingAccount({ slug }: { slug: string }) {
  return (
    <Alert
      tone="info"
      title="Set up your billing profile to add money"
      actions={
        <ButtonLink href={`/o/${slug}/settings/billing/profile`} size="sm" variant="primary">
          Billing Profile
        </ButtonLink>
      }
    >
      Wallets are topped up in your organization&apos;s billing currency, chosen when the billing profile is opened.
    </Alert>
  );
}

/** Opens the spend dialog when the page is reached with `?action=` (the dashboard's quick actions). */
function useActionOnArrival(kind: SpendKind, wanted: string, ready: boolean, open: (k: SpendKind) => void) {
  const params = useSearchParams();
  const action = params.get("action");
  // Adjusted while rendering (React's pattern for state that follows a prop): once.
  const [done, setDone] = useState(false);
  if (!done && ready && action === wanted) {
    setDone(true);
    open(kind);
  }
}

function SpendButton({
  kind,
  wallets,
  onSpend,
  busy,
  size = "md",
}: {
  kind: SpendKind;
  wallets: OrgWallets;
  onSpend: (kind: SpendKind) => void;
  busy: boolean;
  size?: "sm" | "md";
}) {
  if (!wallets.can_spend) return null;
  const label = kind === "platform" ? "Add Money" : kind === "consumption" ? "Recharge" : "Buy Credits";
  const disabled = !wallets.currency || busy || (kind === "ai_credits" && wallets.credit_packs.length === 0);
  return (
    <Button variant="primary" size={size} icon="plus" disabled={disabled} onClick={() => onSpend(kind)} aria-haspopup="dialog">
      {label}
    </Button>
  );
}

// --- overview ---------------------------------------------------------------------------

function RecentList({ entries, kind, currency }: { entries: WalletEntry[]; kind: WalletKind; currency: string }) {
  if (entries.length === 0) return <p className={styles.muted}>No transactions yet.</p>;
  return (
    <ul className={styles.recent}>
      {entries.slice(0, 3).map((e) => (
        <li key={e.id}>
          <span className={styles.recentText}>
            <span>{e.entry_type_label}</span>
            <span className={styles.muted}>{formatDate(e.created_at)}</span>
          </span>
          <span className={styles.amount} data-sign={e.amount > 0 ? "in" : "out"}>
            {entryAmount(e, kind, currency)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function LowBadge({ low }: { low: boolean }) {
  return low ? <Badge tone="danger">Low balance</Badge> : null;
}

function MeterTable({ rows, currency, caption }: { rows: MeterMonth[]; currency: string; caption: string }) {
  return (
    <table className={styles.meters}>
      <caption className="w360-visually-hidden">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Service</th>
          <th scope="col">Used</th>
          <th scope="col">Included</th>
          <th scope="col">Charged</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((m) => (
          <tr key={m.meter}>
            <th scope="row">{m.label}</th>
            <td>{formatNumber(m.quantity)}</td>
            <td>{m.included_units === null ? "Not charged" : formatNumber(m.included_units)}</td>
            <td>{formatMoney(m.amount_minor, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function WalletCard({
  kind,
  title,
  icon,
  wallets,
  slug,
  children,
  links,
  onSpend,
  busy,
}: {
  kind: WalletKind;
  title: string;
  icon: string;
  wallets: OrgWallets;
  slug: string;
  children: React.ReactNode;
  links: React.ReactNode;
  onSpend: (kind: SpendKind) => void;
  busy: boolean;
}) {
  const headingId = useId();
  return (
    <section className={styles.walletCard} aria-labelledby={headingId} data-kind={kind}>
      <div className={styles.walletHead}>
        <span className={styles.walletIcon} data-kind={kind} aria-hidden="true">
          <Icon name={icon} size={20} />
        </span>
        <h2 id={headingId} className={styles.walletTitle}>
          {title}
        </h2>
        <LowBadge low={isLow(kind, wallets)} />
      </div>
      <p className={styles.balance}>
        <span className="w360-visually-hidden">Available balance: </span>
        {balanceOf(kind, wallets)}
      </p>
      {children}
      <div className={styles.walletActions}>
        <SpendButton kind={kind} wallets={wallets} onSpend={onSpend} busy={busy} />
        {links}
      </div>
      {!wallets.can_spend && <ContactOrgAdmin what={kind === "ai_credits" ? "buying credits" : "adding money"} />}
      <Link className={styles.more} href={`/o/${slug}/${KIND_PAGE[kind]}`}>
        View transactions <Icon name="chevronRight" size={14} />
      </Link>
    </section>
  );
}

export function WalletsOverviewScreen() {
  const { slug, can } = useOrgAccess();
  const [load, reload] = useWallets();
  const spend = useWalletSpend(slug, reload);
  if (!can(VIEW)) {
    return (
      <Page>
        <PageHeader title="Wallets & Credits" subtitle="Your organization's Platform Wallet, AI Credits and Consumption Wallet." />
        <NoWalletAccess />
      </Page>
    );
  }
  return (
    <Page>
      <PageHeader
        title="Wallets & Credits"
        subtitle="Three separate balances for your organization. They are never combined."
      />
      {spend.feedback}
      {load.status === "loading" && <OverviewSkeleton />}
      <LoadProblem load={load} />
      {load.status === "ready" && (
        <>
          {!load.data.currency && <NoBillingAccount slug={slug} />}
          <div className={styles.walletGrid}>
            <WalletCard
              kind="platform"
              title="Platform Wallet"
              icon="wallet"
              wallets={load.data}
              slug={slug}
              onSpend={spend.ask}
              busy={spend.busy}
              links={null}
            >
              <p className={styles.purpose}>Pays for plan activations and subscription renewals.</p>
              <RecentList entries={load.data.platform.recent} kind="platform" currency={load.data.platform.currency} />
              <OtherCurrencies rows={load.data.platform.other_currencies} />
            </WalletCard>
            <WalletCard
              kind="ai_credits"
              title="AI Credits"
              icon="sparkles"
              wallets={load.data}
              slug={slug}
              onSpend={spend.ask}
              busy={spend.busy}
              links={
                <ButtonLink href={`/o/${slug}/wallets/usage?meter=ai_tokens`} variant="secondary">
                  View Usage
                </ButtonLink>
              }
            >
              <AiFacts wallets={load.data} />
            </WalletCard>
            <WalletCard
              kind="consumption"
              title="Consumption Wallet"
              icon="bolt"
              wallets={load.data}
              slug={slug}
              onSpend={spend.ask}
              busy={spend.busy}
              links={
                <ButtonLink href={`/o/${slug}/wallets/usage`} variant="secondary">
                  View Usage
                </ButtonLink>
              }
            >
              <p className={styles.purpose}>Pays for WhatsApp, SMS, email, API and webhook usage.</p>
              <MeterTable
                rows={load.data.consumption.this_month}
                currency={load.data.consumption.currency || load.data.currency || ""}
                caption="Consumption this month"
              />
            </WalletCard>
          </div>
          <p className={styles.footnote}>
            An empty Consumption Wallet or AI Credits balance pauses only the services it pays for. It never cancels a
            subscription.
          </p>
          {spend.open && (
            <SpendDialog kind={spend.open} slug={slug} wallets={load.data} onClose={spend.close} placeAndPay={spend.placeAndPay} />
          )}
        </>
      )}
    </Page>
  );
}

function OtherCurrencies({ rows }: { rows: { currency: string; balance_minor: number }[] | undefined }) {
  if (!rows || rows.length === 0) return null;
  return (
    <p className={styles.muted}>
      Also held: {rows.map((r) => formatMoney(r.balance_minor, r.currency)).join(", ")} (kept separately, never converted).
    </p>
  );
}

function AiFacts({ wallets }: { wallets: OrgWallets }) {
  const ai = wallets.ai_credits;
  return (
    <dl className={styles.facts}>
      <div>
        <dt>Included each month</dt>
        <dd>{ai.included_per_month === null ? "None" : `${formatNumber(ai.included_per_month)} AI tokens`}</dd>
      </div>
      <div>
        <dt>Used this month</dt>
        <dd>{formatNumber(ai.used_this_month)} AI tokens</dd>
      </div>
      <div>
        <dt>Purchased</dt>
        <dd>{formatNumber(ai.purchased_total)} credits</dd>
      </div>
      <div>
        <dt>Allowance resets</dt>
        <dd>{ai.resets_at ? formatDate(ai.resets_at) : "No monthly allowance"}</dd>
      </div>
    </dl>
  );
}

// --- one wallet's transactions --------------------------------------------------------------

function DateRange({
  from,
  to,
  onChange,
}: {
  from: string;
  to: string;
  onChange: (next: { from: string; to: string }) => void;
}) {
  const fromId = useId();
  const toId = useId();
  return (
    <span className={styles.dates}>
      <label htmlFor={fromId}>From</label>
      <input id={fromId} type="date" className="w360-input" value={from} max={to || undefined} onChange={(e) => onChange({ from: e.target.value, to })} />
      <label htmlFor={toId}>To</label>
      <input id={toId} type="date" className="w360-input" value={to} min={from || undefined} onChange={(e) => onChange({ from, to: e.target.value })} />
    </span>
  );
}

function Pager({ total, offset, limit, onPage }: { total: number; offset: number; limit: number; onPage: (offset: number) => void }) {
  if (total <= limit) return total > 0 ? <p className={styles.pager}>Showing {formatNumber(total)} of {formatNumber(total)}</p> : null;
  const last = Math.min(offset + limit, total);
  return (
    <div className={styles.pager}>
      <span aria-live="polite">
        Showing {formatNumber(offset + 1)}–{formatNumber(last)} of {formatNumber(total)}
      </span>
      <Button size="sm" icon="chevronLeft" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}>
        Previous
      </Button>
      <Button size="sm" iconEnd="chevronRight" disabled={last >= total} onClick={() => onPage(offset + limit)}>
        Next
      </Button>
    </div>
  );
}

function reference(e: WalletEntry): string {
  if (!e.source_id) return "—";
  const what = e.source_type === "order" ? "Order" : e.source_type === "usage_event" ? "Usage" : e.source_type || "Ref";
  return `${what} ${e.source_id}`;
}

function PendingTopUps({ slug, kind }: { slug: string; kind: WalletKind }) {
  const { can } = useOrgAccess();
  const [load] = useLoad(() => consoleApi.orders(slug), [slug]);
  if (load.status !== "ready") return null;
  // A wallet order's description names what it buys: "<wallet> top-up" or "<pack>: N AI credits".
  const mine = (o: Order) =>
    kind === "ai_credits" ? o.description.endsWith("AI credits") : o.description.startsWith(KIND_LABEL[kind]);
  const relevant = load.data.results.filter(
    (o: Order) => (o.kind as string) === "wallet" && o.status === "pending_payment" && mine(o),
  );
  if (relevant.length === 0) return null;
  return (
    <Alert
      tone="warning"
      title={`${relevant.length === 1 ? "A payment is" : `${relevant.length} payments are`} waiting`}
      actions={
        can("platform.billing.manage") ? (
          <ButtonLink href={`/o/${slug}/orders`} size="sm">
            Orders &amp; Payments
          </ButtonLink>
        ) : undefined
      }
    >
      {relevant.map((o) => `${o.number}: ${o.description} (${formatMoney(o.amount_due_minor, o.currency)})`).join("; ")}. Awaiting
      payment - the balance changes once it is paid.
    </Alert>
  );
}

export function WalletLedgerScreen({ kind }: { kind: WalletKind }) {
  const { slug, can } = useOrgAccess();
  const [wallets, reloadWallets] = useWallets();
  const [type, setType] = useState("");
  const [range, setRange] = useState({ from: "", to: "" });
  const [offset, setOffset] = useState(0);
  const allowed = can(VIEW);
  const [page] = useLoad<TransactionPage>(
    () =>
      allowed
        ? orgAccountApi.transactions(slug, { kind, type, from: range.from, to: range.to, limit: PAGE, offset })
        : Promise.reject(new Error("forbidden")),
    [slug, allowed, kind, type, range.from, range.to, offset],
  );
  const spend = useWalletSpend(slug, reloadWallets);
  const spendKind: SpendKind = kind;
  useActionOnArrival(spendKind, kind === "ai_credits" ? "buy" : "top-up", wallets.status === "ready" && wallets.data.can_spend, spend.ask);

  const title = KIND_LABEL[kind];
  if (!allowed) {
    return (
      <Page>
        <PageHeader title={title} />
        <NoWalletAccess />
      </Page>
    );
  }
  const currency = wallets.status === "ready" ? (kind === "platform" ? wallets.data.platform.currency : wallets.data.consumption.currency) || wallets.data.currency || "" : "";
  const columns: Column<TransactionPage["items"][number]>[] = [
    { key: "date", header: "Date", value: (e) => e.created_at, render: (e) => formatDateTime(e.created_at) },
    { key: "type", header: "Type", value: (e) => e.entry_type_label, render: (e) => <Badge tone={e.amount > 0 ? "success" : "neutral"}>{e.entry_type_label}</Badge> },
    { key: "reason", header: "Description", wrap: true, value: (e) => e.reason, render: (e) => e.reason },
    { key: "ref", header: "Reference", value: (e) => reference(e), render: (e) => <span className={styles.mono}>{reference(e)}</span> },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      value: (e) => e.amount,
      render: (e) => (
        <span className={styles.amount} data-sign={e.amount > 0 ? "in" : "out"}>
          {entryAmount(e, kind, e.currency || currency)}
        </span>
      ),
    },
    {
      key: "after",
      header: "Balance after",
      align: "right",
      value: (e) => e.balance_after,
      render: (e) => (kind === "ai_credits" ? `${formatNumber(e.balance_after)} credits` : formatMoney(e.balance_after, e.currency || currency)),
    },
    { key: "status", header: "Status", value: () => "completed", render: () => <Badge tone="success">Completed</Badge> },
  ];
  const typeOptions = [
    { value: "", label: "All types" },
    ...(page.status === "ready" ? page.data.types : []),
  ];
  const filtered = Boolean(type || range.from || range.to);
  return (
    <Page>
      <PageHeader
        title={title}
        subtitle={
          kind === "platform"
            ? "Money for plan activations and subscription renewals, and every movement."
            : kind === "ai_credits"
              ? "Credits for AI features, what is included each month, and every movement."
              : "Money for WhatsApp, SMS, email, API and webhook usage, and every movement."
        }
        primary={wallets.status === "ready" ? <SpendButton kind={spendKind} wallets={wallets.data} onSpend={spend.ask} busy={spend.busy} /> : undefined}
        actions={
          kind !== "platform" ? (
            <ButtonLink href={`/o/${slug}/wallets/usage${kind === "ai_credits" ? "?meter=ai_tokens" : ""}`}>View Usage</ButtonLink>
          ) : undefined
        }
      />
      {spend.feedback}
      <LoadProblem load={wallets} />
      {wallets.status === "ready" && (
        <>
          {!wallets.data.currency && kind !== "ai_credits" && <NoBillingAccount slug={slug} />}
          {isLow(kind, wallets.data) && (
            <Alert tone="warning" title="This balance is low">
              {kind === "ai_credits"
                ? "AI features that use credits pause when it runs out. Your subscriptions stay active."
                : kind === "consumption"
                  ? "Messaging, API and webhook services pause when it runs out. Your subscriptions stay active."
                  : "Renewals paid from this wallet need enough balance."}
            </Alert>
          )}
          <WalletStats kind={kind} wallets={wallets.data} />
          {kind === "consumption" && (
            <Card title="This month by service" description="What each service used, what was included free, and what it cost.">
              <MeterTable rows={wallets.data.consumption.this_month} currency={currency} caption="Consumption this month by service" />
            </Card>
          )}
          {!wallets.data.can_spend && <ContactOrgAdmin what={kind === "ai_credits" ? "buying credits" : "adding money"} />}
          <PendingTopUps slug={slug} kind={kind} />
        </>
      )}
      <Card title="Transactions" flush>
        <FilterBar
          label="Filter transactions"
          onClear={
            filtered
              ? () => {
                  setType("");
                  setRange({ from: "", to: "" });
                  setOffset(0);
                }
              : undefined
          }
        >
          <Select
            label="Transaction type"
            value={type}
            options={typeOptions}
            onChange={(v) => {
              setType(v);
              setOffset(0);
            }}
          />
          <DateRange
            from={range.from}
            to={range.to}
            onChange={(next) => {
              setRange(next);
              setOffset(0);
            }}
          />
        </FilterBar>
        <DataTable
          caption={`${title} transactions`}
          columns={columns}
          rows={page.status === "ready" ? page.data.items : []}
          rowKey={(e) => e.id}
          loading={page.status === "loading"}
          error={page.status === "error" ? "Transactions could not be loaded. Try again shortly." : page.status === "forbidden" ? "You don't have access to these transactions." : null}
          pageSize={0}
          hideEmptyColumns={false}
          empty={
            <EmptyState
              icon="receipt"
              title={filtered ? "No transactions match these filters." : "No transactions yet."}
              hint={filtered ? "Try other dates or clear the filters." : "Top-ups, purchases, deductions and refunds appear here."}
            />
          }
        />
        {page.status === "ready" && <Pager total={page.data.total} offset={offset} limit={PAGE} onPage={setOffset} />}
      </Card>
      {spend.open && wallets.status === "ready" && (
        <SpendDialog kind={spend.open} slug={slug} wallets={wallets.data} onClose={spend.close} placeAndPay={spend.placeAndPay} />
      )}
    </Page>
  );
}

function WalletStats({ kind, wallets }: { kind: WalletKind; wallets: OrgWallets }) {
  if (kind === "ai_credits") {
    const ai = wallets.ai_credits;
    return (
      <StatGrid label="AI Credits">
        <StatCard label="Available credits" icon="sparkles" tone="purple" value={formatNumber(ai.balance)} />
        <StatCard
          label="Included each month"
          icon="gift"
          tone="green"
          value={ai.included_per_month === null ? "None" : formatNumber(ai.included_per_month)}
          note={ai.included_per_month === null ? undefined : "AI tokens"}
        />
        <StatCard label="Used this month" icon="chart" tone="blue" value={formatNumber(ai.used_this_month)} note="AI tokens" />
        <StatCard label="Purchased credits" icon="cart" tone="orange" value={formatNumber(ai.purchased_total)} note={ai.resets_at ? `Allowance resets ${formatDate(ai.resets_at)}` : "Purchased credits do not expire"} />
      </StatGrid>
    );
  }
  const wallet = kind === "platform" ? wallets.platform : wallets.consumption;
  const currency = wallet.currency || wallets.currency || "";
  const charged = kind === "consumption" ? wallets.consumption.this_month.reduce((sum, m) => sum + m.amount_minor, 0) : null;
  return (
    <StatGrid label={KIND_LABEL[kind]}>
      <StatCard label="Available balance" icon={kind === "platform" ? "wallet" : "bolt"} tone="green" value={formatMoney(wallet.balance_minor, currency)} />
      <StatCard
        label="Low-balance alert below"
        icon="bell"
        tone="orange"
        value={wallet.low_balance_threshold === null ? "Not set" : formatMoney(wallet.low_balance_threshold, currency)}
      />
      {charged !== null && <StatCard label="Charged this month" icon="receipt" tone="blue" value={formatMoney(charged, currency)} />}
    </StatGrid>
  );
}

// --- usage -------------------------------------------------------------------------------

const EXPORT_PAGES = 10;

export function WalletUsageScreen() {
  const { slug, can } = useOrgAccess();
  const params = useSearchParams();
  const [wallets] = useWallets();
  const [meter, setMeter] = useState(params.get("meter") ?? "");
  const [product, setProduct] = useState(params.get("product") ?? "");
  const [range, setRange] = useState({ from: "", to: "" });
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportProblem, setExportProblem] = useState<string | null>(null);
  const allowed = can(VIEW);
  const query = { meter, product, from: range.from, to: range.to };
  const [page] = useLoad<UsagePage>(
    () => (allowed ? orgAccountApi.usage(slug, { ...query, limit: PAGE, offset }) : Promise.reject(new Error("forbidden"))),
    [slug, allowed, meter, product, range.from, range.to, offset],
  );

  if (!allowed) {
    return (
      <Page>
        <PageHeader title="Usage" />
        <NoWalletAccess />
      </Page>
    );
  }

  async function exportCsv() {
    setExporting(true);
    setExportProblem(null);
    try {
      const rows: UsagePage["items"] = [];
      for (let i = 0; i < EXPORT_PAGES; i += 1) {
        const part = await orgAccountApi.usage(slug, { ...query, limit: 100, offset: i * 100 });
        rows.push(...part.items);
        if (rows.length >= part.total || part.items.length === 0) break;
      }
      downloadCsv(
        `usage-${slug}-${new Date().toISOString().slice(0, 10)}.csv`,
        ["Date", "Product", "Service", "Quantity", "Outcome", "Charged units", "Amount", "Currency"],
        rows.map((r) => [
          r.occurred_at,
          r.product_code,
          r.meter_label,
          r.quantity,
          r.outcome_label,
          r.charged_units,
          r.meter === "ai_tokens" ? r.amount : (r.amount / 100).toFixed(2),
          r.meter === "ai_tokens" ? "credits" : r.currency,
        ]),
      );
    } catch {
      setExportProblem("The export could not be prepared. Try again shortly.");
    } finally {
      setExporting(false);
    }
  }

  const columns: Column<UsagePage["items"][number]>[] = [
    { key: "date", header: "Date", value: (r) => r.occurred_at, render: (r) => formatDateTime(r.occurred_at) },
    { key: "product", header: "Product", value: (r) => r.product_code, render: (r) => r.product_code || "—" },
    { key: "meter", header: "Service", value: (r) => r.meter_label, render: (r) => r.meter_label },
    { key: "qty", header: "Quantity", align: "right", value: (r) => r.quantity, render: (r) => formatNumber(r.quantity) },
    {
      key: "outcome",
      header: "Outcome",
      value: (r) => r.outcome,
      render: (r) => (
        <Badge tone={r.outcome === "failed" ? "danger" : r.outcome === "charged" ? "info" : "success"}>{r.outcome_label}</Badge>
      ),
    },
    {
      key: "amount",
      header: "Charged",
      align: "right",
      value: (r) => r.amount,
      render: (r) =>
        r.meter === "ai_tokens"
          ? `${formatNumber(r.amount)} credits`
          : formatMoney(r.amount, r.currency || (wallets.status === "ready" ? (wallets.data.currency ?? "") : "")),
    },
  ];
  const meterOptions = [{ value: "", label: "All services" }, ...(page.status === "ready" ? page.data.meters : [])];
  const productOptions = [
    { value: "", label: "All products" },
    ...(page.status === "ready" ? page.data.products.map((p) => ({ value: p, label: p })) : product ? [{ value: product, label: product }] : []),
  ];
  const filtered = Boolean(meter || product || range.from || range.to);
  return (
    <Page>
      <PageHeader
        title="Usage"
        subtitle="What your products used, what was included free, and what was charged."
        actions={
          <Button icon="download" onClick={() => void exportCsv()} loading={exporting} disabled={page.status !== "ready" || page.data.total === 0}>
            Export CSV
          </Button>
        }
      />
      {exportProblem && <Alert tone="danger" title={exportProblem} />}
      <LoadProblem load={wallets} />
      {wallets.status === "ready" && <AllowanceSummary wallets={wallets.data} />}
      <Card title="Usage events" flush>
        <FilterBar
          label="Filter usage"
          onClear={
            filtered
              ? () => {
                  setMeter("");
                  setProduct("");
                  setRange({ from: "", to: "" });
                  setOffset(0);
                }
              : undefined
          }
        >
          <Select label="Product" value={product} options={productOptions} onChange={(v) => { setProduct(v); setOffset(0); }} />
          <Select label="Service" value={meter} options={meterOptions} onChange={(v) => { setMeter(v); setOffset(0); }} />
          <DateRange from={range.from} to={range.to} onChange={(next) => { setRange(next); setOffset(0); }} />
        </FilterBar>
        <DataTable
          caption="Usage events"
          columns={columns}
          rows={page.status === "ready" ? page.data.items : []}
          rowKey={(r) => r.id}
          loading={page.status === "loading"}
          error={page.status === "error" ? "Usage could not be loaded. Try again shortly." : null}
          pageSize={0}
          hideEmptyColumns={false}
          empty={
            <EmptyState
              icon="chart"
              title={filtered ? "No usage matches these filters." : "No usage yet."}
              hint={filtered ? "Try other dates or clear the filters." : "Messages, API calls, webhooks and AI tokens your products report appear here."}
            />
          }
        />
        {page.status === "ready" && <Pager total={page.data.total} offset={offset} limit={PAGE} onPage={setOffset} />}
      </Card>
    </Page>
  );
}

function AllowanceSummary({ wallets }: { wallets: OrgWallets }) {
  const ai = wallets.ai_credits;
  const rows: MeterMonth[] = [
    {
      meter: "ai_tokens",
      label: "AI tokens",
      quantity: ai.used_this_month,
      included_units: ai.included_per_month,
      charged_units: 0,
      amount_minor: 0,
    },
    ...wallets.consumption.this_month,
  ];
  return (
    <Card title="Included allowance this month" description="Free units each month come off first; only what is beyond them is charged.">
      <table className={styles.meters}>
        <caption className="w360-visually-hidden">Included allowance this month</caption>
        <thead>
          <tr>
            <th scope="col">Service</th>
            <th scope="col">Used</th>
            <th scope="col">Included</th>
            <th scope="col">Left</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.meter}>
              <th scope="row">{m.label}</th>
              <td>{formatNumber(m.quantity)}</td>
              <td>{m.included_units === null ? "Not charged" : formatNumber(m.included_units)}</td>
              <td>{m.included_units === null ? "—" : formatNumber(Math.max(0, m.included_units - m.quantity))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
