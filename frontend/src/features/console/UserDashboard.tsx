"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ProductIcon } from "@/components/ProductIcon";
import { Icon } from "@/components/sidebar/icons";
import { apiFetch } from "@/lib/api/client";
import { accountApi, consoleApi, type Order, type OrgSubscription } from "@/lib/api/console";
import { getAppDirectory } from "@/lib/api/launcher";
import { orgAccountApi, type OrgNotification, type OrgWallets } from "@/lib/api/orgAccount";
import type { AppDirectoryEntry } from "@/lib/api/types";
import { formatDate, formatMoney, formatNumber, formatRelative, usePageTitle } from "@/features/controlplane/kit";
import { useLoad, type Load } from "@/features/controlplane/ui";
import { useOrgAccess } from "./OrgAccess";
import styles from "./UserDashboard.module.css";

/**
 * The User Admin Panel's dashboard (2026-11-11, the owner's specification and
 * reference image): the organization at a glance - its products, team,
 * subscriptions, spend and renewals; the three wallets, each on its own line and
 * never added together; what needs attention; and the way to the Store.
 *
 * Every figure is the server's, read through the member's own permissions. A
 * section whose data this member may not see is left out rather than shown as
 * zero, and nothing here is an example number.
 */

const HELD = new Set<AppDirectoryEntry["state"]>(["active", "no_seat", "integration_suspended"]);
const DAY = 86_400_000;
const RENEWAL_WINDOW_DAYS = 30;

type Period = "this_month" | "last_month" | "last_90_days";
const PERIODS: { value: Period; label: string; previous: string }[] = [
  { value: "this_month", label: "This Month", previous: "vs last month" },
  { value: "last_month", label: "Last Month", previous: "vs the month before" },
  { value: "last_90_days", label: "Last 90 Days", previous: "vs the 90 days before" },
];

/** The period's start and end, and the equal period before it. */
export function periodBounds(period: Period, now: Date = new Date()): { start: number; end: number; prevStart: number } {
  if (period === "last_90_days") {
    const end = now.getTime();
    const start = end - 90 * DAY;
    return { start, end, prevStart: start - 90 * DAY };
  }
  const monthStart = (offset: number) => new Date(now.getFullYear(), now.getMonth() + offset, 1).getTime();
  if (period === "last_month") return { start: monthStart(-1), end: monthStart(0), prevStart: monthStart(-2) };
  return { start: monthStart(0), end: now.getTime() + 1, prevStart: monthStart(-1) };
}

const PAID = new Set<Order["status"]>(["paid", "fulfilled"]);

/** What was paid in a window, in one currency (orders in another are counted apart,
 * never converted). */
export function spendIn(orders: Order[], from: number, to: number, currency: string): number {
  return orders
    .filter((o) => PAID.has(o.status) && o.currency === currency)
    .filter((o) => {
      const at = Date.parse(o.paid_at ?? o.created_at);
      return at >= from && at < to;
    })
    .reduce((sum, o) => sum + o.amount_due_minor, 0);
}

/** Change against the period before, in percent; null when there is nothing to compare with. */
export function percentChange(now: number, before: number): number | null {
  if (before <= 0) return null;
  return Math.round(((now - before) / before) * 1000) / 10;
}

/** The organization's orders back to `since` (they come newest first), a few pages at most. */
async function ordersSince(slug: string, since: number): Promise<Order[]> {
  const rows: Order[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < 8; page += 1) {
    const result = await consoleApi.orders(slug, cursor);
    rows.push(...result.results);
    const oldest = result.results[result.results.length - 1];
    if (!result.next_cursor || !oldest || Date.parse(oldest.created_at) < since) break;
    cursor = result.next_cursor;
  }
  return rows;
}

interface SetupRow {
  id: string;
  product_code: string;
  status: string;
}

const daysUntil = (iso: string, now: number) => Math.max(0, Math.ceil((Date.parse(iso) - now) / DAY));

/** One subscription line per product the organization holds through it. */
function subscriptionLines(subs: OrgSubscription[]) {
  return subs
    .filter((s) => s.status !== "expired" && s.status !== "cancelled")
    .flatMap((s) =>
      s.items
        .filter((i) => !i.removed_at && i.product_code)
        .map((i) => ({ sub: s, item: i, ends: s.cancel_at ?? s.trial_ends_at ?? s.current_period_end })),
    );
}

const STATUS_WORDS: Record<string, { label: string; tone: string }> = {
  active: { label: "Active", tone: "green" },
  trialing: { label: "Trial", tone: "blue" },
  past_due: { label: "Payment due", tone: "orange" },
  paused: { label: "Paused", tone: "neutral" },
  suspended: { label: "Suspended", tone: "red" },
  pending_activation: { label: "Pending", tone: "orange" },
};

function StatusPill({ status }: { status: string }) {
  const words = STATUS_WORDS[status] ?? { label: status.replace(/_/g, " "), tone: "neutral" };
  return (
    <span className={styles.pill} data-tone={words.tone}>
      {words.label}
    </span>
  );
}

// --- pieces ---------------------------------------------------------------------------------

function Stat({
  icon,
  tone,
  label,
  value,
  note,
  href,
}: {
  icon: string;
  tone: string;
  label: string;
  value: React.ReactNode;
  note?: React.ReactNode;
  href?: string;
}) {
  // A long figure (a sum with paise, a large balance) is set smaller rather than
  // broken across lines.
  const long = typeof value === "string" && value.length > 9;
  const body = (
    <span className={styles.statInner}>
      <span className={styles.statIcon} aria-hidden="true">
        <Icon name={icon} size={22} />
      </span>
      <span className={styles.statBody}>
        <span className={styles.statLabel}>{label}</span>
        <span className={styles.statValue} data-long={long || undefined}>
          {value}
        </span>
        {note && <span className={styles.statNote}>{note}</span>}
      </span>
    </span>
  );
  return href ? (
    <Link href={href} className={styles.stat} data-tone={tone}>
      {body}
    </Link>
  ) : (
    <div className={styles.stat} data-tone={tone}>
      {body}
    </div>
  );
}

function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className={styles.skeleton} aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} />
      ))}
    </div>
  );
}

/** A panel's body for a load: a skeleton while it loads, a sentence when it fails
 * or is refused, the content when it is ready. */
function Loaded<T>({ load, what, children }: { load: Load<T>; what: string; children: (data: T) => React.ReactNode }) {
  if (load.status === "loading")
    return (
      <>
        <span className="w360-visually-hidden" role="status">
          Loading {what}…
        </span>
        <Skeleton />
      </>
    );
  if (load.status === "forbidden") return <p className={styles.muted}>Contact your organization admin to see {what}.</p>;
  if (load.status === "error") return <p className={styles.muted} role="alert">{`Couldn't load ${what}. Try again shortly.`}</p>;
  return <>{children(load.data)}</>;
}

function Panel({
  title,
  icon,
  count,
  action,
  children,
  className,
}: {
  title: string;
  icon: string;
  count?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  const id = `dash-${title.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <section className={`${styles.panel} ${className ?? ""}`} aria-labelledby={id}>
      <div className={styles.panelHead}>
        <span className={styles.panelIcon} aria-hidden="true">
          <Icon name={icon} size={20} />
        </span>
        <h2 id={id} className={styles.panelTitle}>
          {title}
          {count !== undefined && <span className={styles.panelCount}> {count}</span>}
        </h2>
        {action && <span className={styles.panelAction}>{action}</span>}
      </div>
      {children}
    </section>
  );
}

function ViewAll({ href, label }: { href: string; label: string }) {
  return (
    <Link href={href} className={styles.viewAll} aria-label={label}>
      View All <Icon name="chevronRight" size={16} />
    </Link>
  );
}

// --- the screen -----------------------------------------------------------------------------

export function UserDashboard() {
  const { slug, access, can, memberName } = useOrgAccess();
  usePageTitle("Dashboard");
  const base = `/o/${slug}`;
  const [period, setPeriod] = useState<Period>("this_month");
  // "Now" is when the page opened, so a re-render never moves a renewal's day count.
  const [now] = useState(() => Date.now());
  const canPlans = can("commercial.subscription.view");
  const canBilling = can("platform.billing.view");
  const canSpend = can("platform.billing.manage");
  const canMembers = can("platform.member.view");
  const canAudit = can("platform.audit.view");

  const [directory] = useLoad(() => getAppDirectory(slug).then((r) => r.apps), [slug]);
  const [setup] = useLoad(
    () => apiFetch<{ results: SetupRow[] }>(`/api/v1/orgs/${encodeURIComponent(slug)}/setup`).then((r) => r.results),
    [slug],
  );
  const [explore] = useLoad(() => consoleApi.explore(slug), [slug]);
  const [subscriptions] = useLoad(
    () => (canPlans ? consoleApi.subscriptions(slug).then((r) => r.results) : Promise.resolve(null)),
    [slug, canPlans],
  );
  const [summary] = useLoad(() => (canMembers ? consoleApi.dashboard(slug, 30) : Promise.resolve(null)), [slug, canMembers]);
  const [wallets] = useLoad<OrgWallets | null>(
    () => (canBilling ? orgAccountApi.wallets(slug) : Promise.resolve(null)),
    [slug, canBilling],
  );
  const bounds = useMemo(() => periodBounds(period), [period]);
  const [orders] = useLoad(
    () => (canBilling ? ordersSince(slug, bounds.prevStart) : Promise.resolve(null)),
    [slug, canBilling, bounds.prevStart],
  );
  const [activity] = useLoad(
    () =>
      canAudit
        ? consoleApi.audit(slug).then((p) =>
            p.results.slice(0, 5).map((e) => ({ key: e.id, text: auditText(e.action), at: e.occurred_at })),
          )
        : accountApi
            .activity()
            .then((r) => r.events.slice(0, 5).map((e) => ({ key: `${e.kind}-${e.occurred_at}`, text: accountText(e.kind), at: e.occurred_at }))),
    [slug, canAudit],
  );
  const [alerts] = useLoad(() => orgAccountApi.notifications(slug).then((r) => r.items), [slug]);

  const name = memberName?.split(" ")[0];
  const held = directory.status === "ready" ? directory.data.filter((a) => HELD.has(a.state)) : [];
  const names = new Map((directory.status === "ready" ? directory.data : []).map((a) => [a.product_code, a]));
  const trials = held.filter((a) => a.entitlement_status === "trial" || a.trial).length;
  const active = held.length - trials;
  const setupOpen = setup.status === "ready" ? setup.data.filter((r) => r.status !== "approved").length : 0;
  const expired =
    explore.status === "ready"
      ? explore.data.products.filter((p) => p.trial_state === "used" && p.trial_outcome === "expired" && !held.some((h) => h.product_code === p.code)).length
      : 0;
  const lines = subscriptions.status === "ready" && subscriptions.data ? subscriptionLines(subscriptions.data) : [];
  const renewals = lines
    .filter((l) => !l.sub.cancel_at && Date.parse(l.ends) - now <= RENEWAL_WINDOW_DAYS * DAY && Date.parse(l.ends) >= now)
    .sort((a, b) => Date.parse(a.ends) - Date.parse(b.ends));
  // The organization's one billing currency: the wallets' answer, else its subscriptions' or orders'.
  const currency =
    (wallets.status === "ready" ? wallets.data?.currency : null) ||
    (subscriptions.status === "ready" ? subscriptions.data?.[0]?.currency : null) ||
    (orders.status === "ready" ? orders.data?.[0]?.currency : null) ||
    null;
  const spendNow = orders.status === "ready" && orders.data && currency ? spendIn(orders.data, bounds.start, bounds.end, currency) : null;
  const spendBefore = orders.status === "ready" && orders.data && currency ? spendIn(orders.data, bounds.prevStart, bounds.start, currency) : null;
  const delta = spendNow !== null && spendBefore !== null ? percentChange(spendNow, spendBefore) : null;
  const periodWords = PERIODS.find((p) => p.value === period)!;
  const productName = (code: string) => names.get(code)?.display_name ?? code;
  const pending = alerts.status === "ready" ? alerts.data.filter((a) => a.tone !== "info").slice(0, 4) : [];

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <span className={styles.headIcon} aria-hidden="true">
          <Icon name="building" size={28} />
        </span>
        <div className={styles.headText}>
          <h1 className={styles.title}>
            {name ? `Welcome, ${name}` : "Welcome"} <span aria-hidden="true">👋</span>
          </h1>
          <p className={styles.lead}>
            Here&apos;s your organization overview and WorkEasy360 account summary
            {access?.organization.display_name ? ` for ${access.organization.display_name}` : ""}.
          </p>
        </div>
        {canBilling && (
          <label className={styles.period}>
            <Icon name="calendar" size={18} />
            <span className="w360-visually-hidden">Period for total spend</span>
            <select value={period} onChange={(e) => setPeriod(e.target.value as Period)}>
              {PERIODS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>

      {pending.length > 0 && <PendingActions base={base} items={pending} />}

      <div className={styles.stats}>
        <Stat
          icon="box"
          tone="green"
          label="Active Products"
          href={`${base}/apps`}
          value={directory.status === "ready" ? formatNumber(active) : "—"}
          note={
            directory.status === "ready"
              ? [trials ? `${trials} in trial` : null, setupOpen ? `${setupOpen} awaiting setup` : null, expired ? `${expired} expired` : null]
                  .filter(Boolean)
                  .join(" · ") || `${held.length} subscribed`
              : "Loading…"
          }
        />
        {canMembers && (
          <Stat
            icon="users"
            tone="blue"
            label="Team Members"
            href={can("platform.member.view") ? `${base}/settings/members` : undefined}
            value={summary.status === "ready" && summary.data?.members ? formatNumber(summary.data.members.active_members) : "—"}
            note={
              summary.status === "ready" && summary.data?.members
                ? summary.data.members.pending_invitations
                  ? `${summary.data.members.pending_invitations} pending invitation${summary.data.members.pending_invitations === 1 ? "" : "s"}`
                  : `${summary.data.members.members_added} added in 30 days`
                : undefined
            }
          />
        )}
        {canPlans && (
          <Stat
            icon="layers"
            tone="purple"
            label="Total Subscriptions"
            href={`${base}/settings/billing`}
            value={subscriptions.status === "ready" ? formatNumber(lines.length) : "—"}
            note={
              subscriptions.status === "ready"
                ? `${lines.filter((l) => l.sub.status === "trialing").length} in trial`
                : undefined
            }
          />
        )}
        {canBilling && (
          <Stat
            icon="currency"
            tone="orange"
            label="Total Spend"
            href={`${base}/orders`}
            value={spendNow !== null && currency ? kpiMoney(spendNow, currency) : orders.status === "loading" ? "…" : "—"}
            note={
              delta !== null ? (
                <span className={styles.delta} data-up={delta >= 0 || undefined}>
                  <Icon name={delta >= 0 ? "arrowUp" : "arrowDown"} size={14} />
                  {`${Math.abs(delta)}% ${periodWords.previous}`}
                </span>
              ) : spendNow !== null ? (
                `${periodWords.label} · paid orders`
              ) : undefined
            }
          />
        )}
        {canPlans && (
          <Stat
            icon="rocket"
            tone="red"
            label="Renewals Due"
            href={`${base}/settings/billing`}
            value={subscriptions.status === "ready" ? formatNumber(renewals.length) : "—"}
            note={
              renewals[0]
                ? `Next in ${daysUntil(renewals[0].ends, now)} day${daysUntil(renewals[0].ends, now) === 1 ? "" : "s"}`
                : subscriptions.status === "ready"
                  ? `None in the next ${RENEWAL_WINDOW_DAYS} days`
                  : undefined
            }
          />
        )}
      </div>

      <div className={styles.rowWide}>
        <Panel
          title="My Products"
          icon="layers"
          count={directory.status === "ready" ? `(${held.length} ${held.length === 1 ? "product" : "products"})` : undefined}
          action={<ViewAll href={`${base}/apps`} label="View all products" />}
        >
          <Loaded load={directory} what="your products">
            {() =>
              held.length === 0 ? (
                <div className={styles.empty}>
                  <p>Your organization doesn&apos;t have any products yet.</p>
                  <Link href={`${base}/store`} className={styles.primaryLink}>
                    Explore products
                  </Link>
                </div>
              ) : (
                <ul className={styles.products}>
                  {held.slice(0, 4).map((app) => (
                    <li key={app.product_code}>
                      <Link href={`${base}/apps?product=${encodeURIComponent(app.product_code)}`} className={styles.product}>
                        <span className={styles.productTop}>
                          <span className={styles.productIcon} aria-hidden="true">
                            <ProductIcon icon={app.icon} size={24} />
                          </span>
                          <StatusPill status={app.trial || app.entitlement_status === "trial" ? "trialing" : app.entitlement_status ?? "active"} />
                        </span>
                        <span className={styles.productName}>{app.display_name}</span>
                        <span className={styles.productLine}>{app.tagline || app.plan?.name || app.category}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )
            }
          </Loaded>
        </Panel>
        {canBilling ? (
          <WalletsPanel base={base} load={wallets} canSpend={canSpend} />
        ) : (
          <Panel title="Wallets & Credits" icon="wallet">
            <p className={styles.muted}>
              Wallet balances are shown to your organization&apos;s owner and Billing Managers. Contact your
              organization admin.
            </p>
          </Panel>
        )}
      </div>

      <div className={styles.rowThree} data-full={canPlans || undefined}>
        {canPlans && (
          <Panel
            title="Subscriptions"
            icon="calendar"
            count={subscriptions.status === "ready" ? `(${lines.length})` : undefined}
            action={<ViewAll href={`${base}/settings/billing`} label="View all subscriptions" />}
          >
            <Loaded load={subscriptions} what="your subscriptions">
              {() =>
                lines.length === 0 ? (
                  <p className={styles.muted}>No active subscriptions.</p>
                ) : (
                  // Scrolls sideways on a narrow screen, so it takes focus (keyboard scrolling).
                  <div className={styles.tableWrap} role="region" aria-label="Subscriptions" tabIndex={0}>
                    <table className={styles.table}>
                      <caption className="w360-visually-hidden">Subscriptions</caption>
                      <thead>
                        <tr>
                          <th scope="col">Product</th>
                          <th scope="col">Plan</th>
                          <th scope="col">Valid till</th>
                          <th scope="col">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {lines.slice(0, 5).map((l) => (
                          <tr key={l.item.id}>
                            <th scope="row">
                              <span className={styles.cellProduct}>
                                <span className={styles.miniIcon} aria-hidden="true">
                                  <ProductIcon icon={names.get(l.item.product_code)?.icon} size={14} />
                                </span>
                                {productName(l.item.product_code)}
                              </span>
                            </th>
                            <td>
                              {l.item.plan_name || l.item.plan_code}
                              {l.item.quantity > 1 && <span className={styles.cellSub}>{l.item.quantity} users</span>}
                            </td>
                            <td>{formatDate(l.ends)}</td>
                            <td>
                              <StatusPill status={l.sub.status} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              }
            </Loaded>
          </Panel>
        )}
        {canPlans && (
          <Panel title="Upcoming Renewals" icon="bell" action={<ViewAll href={`${base}/settings/billing`} label="View all renewals" />}>
            <Loaded load={subscriptions} what="your renewals">
              {() =>
                renewals.length === 0 ? (
                  <p className={styles.muted}>Nothing renews in the next {RENEWAL_WINDOW_DAYS} days.</p>
                ) : (
                  <ul className={styles.renewals}>
                    {renewals.slice(0, 3).map((l) => {
                      const days = daysUntil(l.ends, now);
                      const auto = l.sub.renewal_policy === "auto_renew" && l.sub.status !== "trialing";
                      return (
                        <li key={l.item.id} className={styles.renewal}>
                          <span className={styles.miniIcon} aria-hidden="true">
                            <ProductIcon icon={names.get(l.item.product_code)?.icon} size={16} />
                          </span>
                          <span className={styles.renewalText}>
                            <strong>
                              {productName(l.item.product_code)}
                              {l.item.plan_name ? ` – ${l.item.plan_name}` : ""}
                            </strong>
                            <span>
                              {l.sub.status === "trialing" ? "Trial ends" : "Renews"} on {formatDate(l.ends)}{" "}
                              <span className={styles.urgent}>
                                ({days} day{days === 1 ? "" : "s"} left)
                              </span>
                            </span>
                          </span>
                          {auto ? (
                            <span className={styles.pill} data-tone="green">
                              Auto-renews
                            </span>
                          ) : (
                            <Link
                              href={`${base}/settings/billing`}
                              className={styles.renewButton}
                              aria-label={`Renew ${productName(l.item.product_code)}`}
                            >
                              Renew
                            </Link>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )
              }
            </Loaded>
          </Panel>
        )}
        <QuickActions base={base} can={can} />
      </div>

      <div className={styles.rowWide}>
        <Panel title="Recent Activity" icon="history" action={canAudit ? <ViewAll href={`${base}/settings/audit`} label="View all activity" /> : undefined}>
          <Loaded load={activity} what="recent activity">
            {(events) =>
              events.length === 0 ? (
                <p className={styles.muted}>Nothing yet.</p>
              ) : (
                <ul className={styles.activity}>
                  {events.map((e) => (
                    <li key={e.key}>
                      <span className={styles.activityIcon} aria-hidden="true">
                        <Icon name="checkCircle" size={16} />
                      </span>
                      <span className={styles.activityText}>{e.text}</span>
                      <time className={styles.activityTime} dateTime={e.at}>
                        {formatRelative(e.at)}
                      </time>
                    </li>
                  ))}
                </ul>
              )
            }
          </Loaded>
        </Panel>
        <section className={styles.storeBanner} aria-labelledby="dash-store">
          <div className={styles.storeText}>
            <h2 id="dash-store" className={styles.storeTitle}>
              Explore More Products on <span>WorkEasy360 Store</span>
            </h2>
            <p>Discover more WorkEasy360 business apps for your organization - start a free trial or buy in a few clicks.</p>
            <Link href={`${base}/store`} className={styles.storeButton}>
              Visit Store <Icon name="chevronRight" size={16} />
            </Link>
          </div>
          <span className={styles.storeArt} aria-hidden="true">
            <Icon name="store" size={72} />
          </span>
        </section>
      </div>
    </div>
  );
}

function PendingActions({ base, items }: { base: string; items: OrgNotification[] }) {
  return (
    <section className={styles.alerts} aria-labelledby="dash-pending">
      <h2 id="dash-pending" className="w360-visually-hidden">
        Pending actions
      </h2>
      <ul>
        {items.map((n) => (
          <li key={n.id} data-tone={n.tone}>
            <Icon name={n.tone === "danger" ? "alert" : "info"} size={18} />
            <span className={styles.alertText}>
              <strong>{n.title}</strong>
              {n.detail && <span> {n.detail}</span>}
            </span>
            <Link href={`${base}/${n.href.replace(/^\/+/, "")}`} className={styles.alertLink}>
              Review
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function WalletsPanel({ base, load, canSpend }: { base: string; load: Load<OrgWallets | null>; canSpend: boolean }) {
  return (
    <Panel
      title="Wallets & Credits"
      icon="wallet"
      action={
        canSpend ? (
          <Link href={`${base}/wallets/platform?action=top-up`} className={styles.topUp}>
            <Icon name="plus" size={16} /> Top Up
          </Link>
        ) : undefined
      }
    >
      <Loaded load={load} what="your wallet balances">
        {(w) =>
          !w ? null : (
            <>
              <ul className={styles.wallets}>
                <li>
                  <Link href={`${base}/wallets/platform`} className={styles.wallet} data-tone="green">
                    <span className={styles.walletIcon} aria-hidden="true">
                      <Icon name="wallet" size={20} />
                    </span>
                    <span className={styles.walletName}>
                      <strong>Platform Wallet</strong>
                      <span>(Plan Activation)</span>
                    </span>
                    <span className={styles.walletValue}>
                      {formatMoney(w.platform.balance_minor, w.platform.currency || w.currency)}
                      {w.platform.is_low && <span className={styles.low}>Low balance</span>}
                    </span>
                    <Icon name="chevronRight" size={18} />
                  </Link>
                </li>
                <li>
                  <Link href={`${base}/wallets/ai-credits`} className={styles.wallet} data-tone="purple">
                    <span className={styles.walletIcon} aria-hidden="true">
                      <Icon name="sparkles" size={20} />
                    </span>
                    <span className={styles.walletName}>
                      <strong>AI Credits</strong>
                      <span>(AI Tasks &amp; Usage)</span>
                    </span>
                    <span className={styles.walletValue}>
                      {formatNumber(w.ai_credits.balance)} Credits
                      {w.ai_credits.is_low && <span className={styles.low}>Low balance</span>}
                    </span>
                    <Icon name="chevronRight" size={18} />
                  </Link>
                </li>
                <li>
                  <Link href={`${base}/wallets/consumption`} className={styles.wallet} data-tone="blue">
                    <span className={styles.walletIcon} aria-hidden="true">
                      <Icon name="bolt" size={20} />
                    </span>
                    <span className={styles.walletName}>
                      <strong>Consumption Wallet</strong>
                      <span>(WhatsApp, SMS, Email, API)</span>
                    </span>
                    <span className={styles.walletValue}>
                      {formatMoney(w.consumption.balance_minor, w.consumption.currency || w.currency)}
                      {w.consumption.is_low && <span className={styles.low}>Low balance</span>}
                    </span>
                    <Icon name="chevronRight" size={18} />
                  </Link>
                </li>
              </ul>
              {!canSpend && <p className={styles.contact}>To add money or buy credits, contact your organization admin.</p>}
            </>
          )
        }
      </Loaded>
    </Panel>
  );
}

function QuickActions({ base, can }: { base: string; can: (permission: string) => boolean }) {
  const actions = [
    { icon: "cart", tone: "green", label: "Buy / Upgrade Product", href: `${base}/store`, when: true },
    { icon: "wallet", tone: "blue", label: "Add Money to Wallet", href: `${base}/wallets/platform?action=top-up`, when: can("platform.billing.manage") },
    { icon: "userPlus", tone: "purple", label: "Invite Team Member", href: `${base}/settings/invitations`, when: can("platform.member.invite") },
    { icon: "headset", tone: "orange", label: "Raise Support Ticket", href: `${base}/help?new=1`, when: true },
  ].filter((a) => a.when);
  return (
    <Panel title="Quick Actions" icon="bolt">
      <ul className={styles.quick}>
        {actions.map((a) => (
          <li key={a.label}>
            <Link href={a.href} className={styles.quickAction} data-tone={a.tone}>
              <Icon name={a.icon} size={24} />
              <span>{a.label}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

// --- words ------------------------------------------------------------------------------------

/** A KPI amount: whole units without the ".00" ("₹64,800"), anything else exact. */
export function kpiMoney(minor: number, currency: string): string {
  if (minor % 100 !== 0 || currency === "JPY") return formatMoney(minor, currency);
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency, minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(minor / 100);
  } catch {
    return formatMoney(minor, currency);
  }
}

const AUDIT_WORDS: Record<string, string> = {
  "subscription.activated": "Subscription activated",
  "subscription.created": "Subscription created",
  "subscription.cancelled": "Subscription cancelled",
  "subscription.renewed": "Subscription renewed",
  "order.placed": "Order placed",
  "order.paid": "Order paid",
  "wallet.order_placed": "Wallet top-up ordered",
  "membership.created": "Team member added",
  "member.added": "Team member added",
  "invitation.created": "Invitation sent",
  "invitation.accepted": "Invitation accepted",
  "member.role_changed": "Member role changed",
  "seat.assigned": "Product seat assigned",
  "billing.invoice_issued": "Invoice issued",
  "billing.invoice_marked_paid": "Invoice marked paid",
  "billing.payment_succeeded": "Payment received",
  "billing.payment_failed": "Payment failed",
  "commercial.billing_account.opened": "Billing account opened",
  "commercial.order.fulfilled": "Order fulfilled",
  "commercial.subscription.plan_purchased": "Plan purchased",
  "wallet.offline_top_up": "Wallet topped up by bank transfer",
  "wallet.threshold_set": "Low-balance alert set",
  "organization.created": "Organization created",
};

/** An audit action as a sentence ("subscription.activated" -> "Subscription activated"). */
export function auditText(action: string): string {
  if (AUDIT_WORDS[action]) return AUDIT_WORDS[action];
  // The module a record comes from is not news to the customer.
  const words = action
    .replace(/^(commercial|platform)\./, "")
    .replace(/[._]+/g, " ")
    .trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Activity";
}

function accountText(kind: string): string {
  const words = kind.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Account activity";
}
