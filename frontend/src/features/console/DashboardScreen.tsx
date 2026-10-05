"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { consoleApi, type DashboardSummary } from "@/lib/api/console";
import { BarChart, StatTile } from "@/components/charts/Charts";
import { LoadGate, Status, Table, useLoad, when } from "@/features/controlplane/ui";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { Panel, day, intervalLabel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

const WINDOWS = [7, 30, 90] as const;
type WindowDays = (typeof WINDOWS)[number];

/**
 * The administrator's view of the organization, shown below everyone's home
 * (Phase 9 - it was a page of its own): who is here, who is using it, what
 * the organization holds. Every section is permission-gated by the server - a
 * section missing from the response is simply not shown, and the page never
 * guesses at numbers it was not given.
 */
export function AdminInsights() {
  const { slug, can } = useOrgAccess();
  const [windowDays, setWindowDays] = useState<WindowDays>(7);
  const [load] = useLoad(() => consoleApi.dashboard(slug, windowDays), [slug, windowDays]);
  const base = `/o/${slug}`;

  return (
    <div className={styles.page}>
      <div className={local.actions}>
        {can("platform.member.invite") && (
          <Link className="w360-btn w360-btn-secondary" href={`${base}/settings/invitations`}>
            Invite members
          </Link>
        )}
        {can("platform.seat.assign") && (
          <Link className="w360-btn w360-btn-secondary" href={`${base}/settings/products`}>
            Manage product access
          </Link>
        )}
      </div>
      <div>
        <div className={styles.segmented} role="group" aria-label="Period shown">
          {WINDOWS.map((w) => (
            <button key={w} type="button" aria-pressed={windowDays === w} onClick={() => setWindowDays(w)}>
              Last {w} days
            </button>
          ))}
        </div>
      </div>
      <LoadGate load={load} what="the dashboard">
        {(d) => <DashboardBody data={d} slug={slug} />}
      </LoadGate>
    </div>
  );
}

function DashboardBody({ data, slug }: { data: DashboardSummary; slug: string }) {
  const tilesId = useId();
  const m = data.members;
  const activity = data.activity;
  const days = data.window_days;
  const nothing = !m && !activity && !data.apps && data.subscription === undefined;

  if (nothing) {
    return <p className={cp.muted}>There is nothing to show here for your role yet.</p>;
  }

  return (
    <>
      {(m || data.apps) && (
        <section aria-labelledby={tilesId}>
          <h2 id={tilesId} className="w360-visually-hidden">
            At a glance
          </h2>
          <div className={styles.tiles}>
            {m && (
              <>
                <StatTile label="Users" value={m.active_members} note="Active members" />
                <StatTile label={`Added in last ${days} days`} value={m.members_added} />
                <StatTile label="Invited – not joined yet" value={m.pending_invitations + m.invited_members} />
                <StatTile label="Expired invitations" value={m.expired_invitations} />
                <StatTile label="Suspended" value={m.suspended_members} />
              </>
            )}
            {data.apps && <StatTile label="Apps" value={data.apps.entitled} note="Products your organization holds" />}
          </div>
        </section>
      )}

      {activity && (
        <div className={styles.split}>
          <Panel title="Sign-in activity">
            <BarChart
              title="Members who signed in, per day"
              data={activity.series.map((s) => ({
                label: day(s.date, { month: "short", day: "numeric" }),
                value: s.members,
              }))}
              valueLabel="Members"
              empty={`Nobody signed in during the last ${days} days.`}
            />
          </Panel>
          <Panel title="Most signed-in members">
            <Table
              caption={`Last ${days} days`}
              head={["Member", "Sign-ins", "Last sign-in"]}
              isEmpty={activity.most_active.length === 0}
              empty="No records found"
            >
              {activity.most_active.map((row) => (
                <tr key={row.membership_id}>
                  <th scope="row">
                    {row.name || row.email}
                    {row.name && <div className={cp.muted}>{row.email}</div>}
                  </th>
                  <td className={cp.num}>{row.sign_ins}</td>
                  <td>{when(row.last_sign_in_at)}</td>
                </tr>
              ))}
            </Table>
          </Panel>
        </div>
      )}

      {data.subscription !== undefined && (
        <Panel title="Subscription">
          {data.subscription ? (
            <>
              <dl className={local.details}>
                <div>
                  <dt>Status</dt>
                  <dd>
                    <Status value={data.subscription.status} />
                  </dd>
                </div>
                <div>
                  <dt>Billing</dt>
                  <dd>{intervalLabel(data.subscription.billing_interval)}</dd>
                </div>
                {data.subscription.status === "trialing" && data.subscription.trial_ends_at ? (
                  <div>
                    <dt>Trial ends</dt>
                    <dd>{day(data.subscription.trial_ends_at)}</dd>
                  </div>
                ) : (
                  <div>
                    <dt>{data.subscription.status === "active" ? "Renews" : "Current period ends"}</dt>
                    <dd>{day(data.subscription.current_period_end)}</dd>
                  </div>
                )}
                <div>
                  <dt>Products</dt>
                  <dd>{data.subscription.products.join(", ") || "—"}</dd>
                </div>
              </dl>
              <p className={local.textBlock}>
                <Link href={`/o/${slug}/settings/billing`}>Subscription &amp; billing</Link>
              </p>
            </>
          ) : (
            <p className={local.textBlock}>
              No active subscription. <Link href={`/o/${slug}/explore`}>Explore products</Link>
            </p>
          )}
        </Panel>
      )}
    </>
  );
}
