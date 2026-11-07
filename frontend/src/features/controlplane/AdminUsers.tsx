"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { USER_SEGMENTS, adminConsoleApi, type UserAction, type UserDetail, type UserRow } from "@/lib/api/adminConsole";
import { useSession } from "@/lib/auth/useSession";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, ReasonAction, useAction, useLoad, usePaged } from "./ui";
import {
  Avatar,
  Badge,
  BoolBadge,
  Button,
  Card,
  DataTable,
  DescriptionList,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  PersonCell,
  SearchInput,
  Select,
  StatusBadge,
  StatusChips,
  Tabs,
  Tooltip,
  Trunc,
  EMPTY,
  exactTime,
  formatDate,
  formatDateTime,
  formatMoney,
  formatRelative,
  productName,
  sentence,
  useConfirm,
  type Column,
  type MenuItem,
} from "./kit";
import { downloadCsv } from "./kit/csv";
import local from "./AdminPeople.module.css";

const LIFECYCLE_LABELS: Record<string, string> = {
  explorer: "Explorer",
  trial: "Trial",
  customer: "Customer",
  unverified: "Unverified",
};

const lifecycle = (key: string) => LIFECYCLE_LABELS[key] ?? sentence(key);

/** A date with the exact moment in a tooltip. */
export function DateText({ iso, relative = false, never = EMPTY }: { iso: string | null | undefined; relative?: boolean; never?: string }) {
  if (!iso) return <>{never}</>;
  return (
    <Tooltip content={exactTime(iso)}>
      <time dateTime={iso} tabIndex={0}>
        {relative ? formatRelative(iso) : formatDate(iso)}
      </time>
    </Tooltip>
  );
}

const SIGNED_UP = [
  { value: "", label: "Any time" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
  { value: "365", label: "Last 12 months" },
];

/** The rows already loaded that fall inside `days` (client-side: the user
 * directory filters only by search and segment on the server). */
function withinDays(iso: string, days: string, now: number): boolean {
  if (!days) return true;
  const t = Date.parse(iso);
  return Number.isFinite(t) && now - t <= Number(days) * 24 * 60 * 60 * 1000;
}

/**
 * Super Admin → Users (Phase 8 extension, Phase 9; 2026-11-07 design system):
 * every account on the platform, segmented by what its organizations hold -
 * Explorers (nothing yet), Trial, Customers - and by account state. One
 * identity each. Never a credential. Managing an account
 * (`platform.staff_user.manage`) reaches every organization the person belongs
 * to, so each action is confirmed and audited. Nobody is offered "Deactivate"
 * on their own account (the server refuses it too).
 *
 * `initialQuery`: a search arriving in the address (`/admin/users?q=...`).
 */
export function Users({ initialQuery = "" }: { initialQuery?: string } = {}) {
  const { permissions } = useAdmin();
  const session = useSession();
  const me = session.status === "ready" ? session.session.user_id : undefined;
  const manage = can(permissions, "platform.staff_user.manage");
  const [query, setQuery] = useState(initialQuery);
  const [segment, setSegment] = useState("");
  const [product, setProduct] = useState("");
  const [signedUp, setSignedUp] = useState("");
  const [now] = useState(() => Date.now());
  const paged = usePaged(
    (cursor) => adminConsoleApi.users({ q: query || undefined, segment: segment || undefined, cursor }),
    [query, segment],
  );
  const { ask, dialog } = useConfirm();

  const products = useMemo(() => [...new Set(paged.rows.flatMap((u) => u.products))].sort(), [paged.rows]);
  const rows = useMemo(
    () => paged.rows.filter((u) => (!product || u.products.includes(product)) && withinDays(u.created_at, signedUp, now)),
    [paged.rows, product, signedUp, now],
  );

  const act = (u: UserRow, action: UserAction, title: string, message: string, confirmLabel: string, done: string, danger = false) =>
    ask({
      title,
      message,
      confirmLabel,
      danger,
      reason: true,
      actionName: confirmLabel,
      onConfirm: async (reason) => {
        await adminConsoleApi.userAction(u.user_id, action, reason);
        paged.reload();
        return done;
      },
    });

  const menu = (u: UserRow): MenuItem[] => {
    const who = u.name || u.email;
    return [
      { label: "View", icon: "eye", href: `/admin/users/${u.user_id}` },
      {
        label: "Sign out everywhere",
        icon: "logout",
        when: manage,
        onSelect: () =>
          act(u, "sign-out", `Sign ${who} out everywhere?`, "Every session of this account ends now.", "Sign out everywhere", "Every session of this account has ended."),
      },
      {
        label: "Reactivate",
        icon: "refresh",
        when: manage && u.status === "disabled",
        onSelect: () => act(u, "enable", `Reactivate ${who}?`, "The account can sign in again.", "Reactivate", "The account is active again."),
      },
      {
        label: "Deactivate",
        icon: "ban",
        danger: true,
        separator: true,
        when: manage && u.status !== "disabled" && session.status === "ready" && me !== u.user_id,
        onSelect: () =>
          act(
            u,
            "disable",
            `Deactivate ${who}?`,
            "They are signed out everywhere and cannot sign in until reactivated. Their organizations are not changed.",
            "Deactivate",
            "The account is deactivated and signed out everywhere.",
            true,
          ),
      },
    ];
  };

  const columns: Column<UserRow>[] = [
    {
      key: "user",
      header: "User",
      value: (u) => u.name || u.email,
      render: (u) => (
        <PersonCell
          name={u.name}
          email={u.email}
          href={`/admin/users/${u.user_id}`}
          badge={u.is_staff ? <Badge tone="brand">Staff</Badge> : undefined}
        />
      ),
    },
    {
      key: "status",
      header: "Status",
      value: (u) => (u.locked ? "locked" : u.status),
      render: (u) => (u.locked ? <StatusBadge value="locked" /> : <StatusBadge value={u.status} />),
    },
    {
      key: "verified",
      header: "Verified",
      value: (u) => (u.email_verified ? 1 : 0),
      render: (u) => <BoolBadge value={u.email_verified} yes="Verified" no="Unverified" />,
    },
    { key: "lifecycle", header: "Lifecycle", value: (u) => lifecycle(u.lifecycle), render: (u) => lifecycle(u.lifecycle) },
    {
      key: "orgs",
      header: "Organizations",
      value: (u) => u.organization_names,
      render: (u) => <Trunc max="sm">{u.organization_names.join(", ")}</Trunc>,
    },
    {
      key: "products",
      header: "Products",
      value: (u) => u.products,
      render: (u) => <Trunc max="sm">{u.products.map(productName).join(", ")}</Trunc>,
    },
    {
      key: "subscription",
      header: "Subscription",
      value: (u) => u.subscription_status,
      render: (u) => (u.subscription_status ? <StatusBadge value={u.subscription_status} /> : EMPTY),
    },
    { key: "created", header: "Signed up", value: (u) => u.created_at, render: (u) => <DateText iso={u.created_at} /> },
    {
      key: "active",
      header: "Last active",
      value: (u) => u.last_sign_in_at,
      render: (u) => <DateText iso={u.last_sign_in_at} relative never="Never" />,
    },
  ];

  const exportCsv = () =>
    downloadCsv(
      "users.csv",
      ["Name", "Email", "Status", "Verified", "Lifecycle", "Organizations", "Products", "Subscription", "Signed up", "Last active"],
      rows.map((u) => [
        u.name,
        u.email,
        u.status,
        u.email_verified ? "yes" : "no",
        lifecycle(u.lifecycle),
        u.organization_names.join("; "),
        u.products.join("; "),
        u.subscription_status,
        u.created_at,
        u.last_sign_in_at ?? "",
      ]),
    );

  const filtered = Boolean(query || product || signedUp);
  return (
    <Page>
      <PageHeader
        title="Users"
        subtitle="Every account on the platform, with its organizations and subscriptions."
        info="Subscribed people pay for a product. The free tier is everyone else: on a trial (Trial) or with no product yet (Explorers)."
      />
      <StatusChips
        label="Segment"
        value={segment}
        onChange={setSegment}
        chips={USER_SEGMENTS.map((s) => ({ value: s.key, label: s.label }))}
      />
      <FilterBar
        onClear={
          filtered
            ? () => {
                setQuery("");
                setProduct("");
                setSignedUp("");
              }
            : undefined
        }
        end={
          <Button icon="download" onClick={exportCsv} disabled={rows.length === 0}>
            Export CSV
          </Button>
        }
      >
        <SearchInput label="Search by name or email" value={query} onSearch={setQuery} />
        <Select
          label="Product"
          prefix="Product:"
          value={product}
          onChange={setProduct}
          options={[{ value: "", label: "Any" }, ...products.map((p) => ({ value: p, label: productName(p) }))]}
        />
        <Select label="Signed up" prefix="Signed up:" value={signedUp} onChange={setSignedUp} options={SIGNED_UP} />
      </FilterBar>
      <DataTable
        caption="User accounts"
        columns={columns}
        rows={rows}
        rowKey={(u) => u.user_id}
        rowHref={(u) => `/admin/users/${u.user_id}`}
        rowLabel={(u) => u.name || u.email}
        rowMenu={menu}
        loading={paged.state === "loading"}
        error={
          paged.state === "forbidden"
            ? "You don't have access to the user directory."
            : paged.state === "error"
              ? "Couldn't load users."
              : null
        }
        onLoadMore={() => void paged.more()}
        hasMore={paged.hasMore}
        empty={
          <EmptyState
            icon="users"
            title={paged.state === "forbidden" ? "You don't have access to the user directory." : paged.state === "error" ? "Couldn't load users." : "No users match."}
            hint={filtered ? "Try another search or clear the filters." : undefined}
          />
        }
      />
      {dialog}
    </Page>
  );
}

type DetailTab = "overview" | "organizations" | "subscriptions" | "sessions" | "activity";

export function UserDetailScreen({ userId }: { userId: string }) {
  const { permissions } = useAdmin();
  const session = useSession();
  const [load, reload] = useLoad(() => adminConsoleApi.user(userId), [userId]);
  const { act, busy, feedback } = useAction(reload);
  const manage = can(permissions, "platform.staff_user.manage");
  // Until the session is known, "is this me?" is unknown - so Deactivate waits.
  const me = session.status === "ready" ? session.session.user_id : undefined;
  const isSelf = me === userId;
  const mayDisable = session.status === "ready" && !isSelf;
  const [tab, setTab] = useState<DetailTab>("overview");

  const run = (action: UserAction, reason: string, success: string) =>
    act(() => adminConsoleApi.userAction(userId, action, reason), success);

  return (
    <Page>
      <LoadGate load={load} what="this user">
        {(u) => {
          const name = u.name || u.email;
          return (
            <>
              <PageHeader
                crumbs={[{ label: "Users", href: "/admin/users" }, { label: name }]}
                documentTitle={`${name} · Users`}
                avatar={<Avatar name={name} size="lg" />}
                title={name}
                badges={
                  <>
                    <StatusBadge value={u.status} />
                    {u.is_staff && <Badge tone="brand">Staff member</Badge>}
                    {u.locked && <Badge tone="warning" icon="lock">Locked (too many failed sign-ins)</Badge>}
                  </>
                }
                subtitle={u.name ? u.email : undefined}
                meta={[
                  lifecycle(u.lifecycle),
                  <BoolBadge key="v" value={u.email_verified} yes="Email verified" no="Email not verified" />,
                  <span key="c">
                    Signed up <DateText iso={u.created_at} />
                  </span>,
                  <span key="l">
                    Last active <DateText iso={u.last_sign_in_at} relative never="never" />
                  </span>,
                  ...(u.acquisition_source ? [`Came from: ${sentence(u.acquisition_source)}`] : []),
                  ...(manage && isSelf ? ["This is your own account, so it can't be deactivated here."] : []),
                ]}
                actions={
                  manage ? (
                    <>
                      <ReasonAction
                        label="Sign out everywhere"
                        busy={busy}
                        onConfirm={(reason) => run("sign-out", reason, "Every session of this account has ended.")}
                      />
                      {u.locked && <ReasonAction label="Unlock" busy={busy} onConfirm={(reason) => run("unlock", reason, "The account is unlocked.")} />}
                      {u.status === "disabled" ? (
                        <ReasonAction label="Reactivate" busy={busy} onConfirm={(reason) => run("enable", reason, "The account is active again.")} />
                      ) : (
                        mayDisable && (
                          <ReasonAction
                            label="Deactivate"
                            danger
                            busy={busy}
                            message="They are signed out everywhere and cannot sign in until reactivated. Their organizations are not changed."
                            onConfirm={(reason) => run("disable", reason, "The account is deactivated and signed out everywhere.")}
                          />
                        )
                      )}
                    </>
                  ) : undefined
                }
                tabs={
                  <Tabs<DetailTab>
                    label="User sections"
                    selected={tab}
                    onSelect={setTab}
                    tabs={[
                      { key: "overview", label: "Overview" },
                      { key: "organizations", label: "Organizations", count: u.memberships.length },
                      { key: "subscriptions", label: "Subscriptions" },
                      { key: "sessions", label: "Sessions", count: u.sessions.length },
                      { key: "activity", label: "Activity" },
                    ]}
                  />
                }
              />
              {feedback}
              <UserTab tab={tab} user={u} />
            </>
          );
        }}
      </LoadGate>
    </Page>
  );
}

function UserTab({ tab, user: u }: { tab: DetailTab; user: UserDetail }) {
  if (tab === "overview") {
    return (
      <div className={local.cardGrid}>
        <Card title="Account">
          <DescriptionList
            items={[
              { label: "Name", value: u.name },
              { label: "Email", value: u.email },
              { label: "Status", value: <StatusBadge value={u.status} /> },
              { label: "Email verified", value: <BoolBadge value={u.email_verified} yes="Verified" no="Unverified" /> },
              { label: "Lifecycle", value: lifecycle(u.lifecycle) },
              { label: "Signed up", value: formatDateTime(u.created_at) },
              { label: "Last sign-in", value: u.last_sign_in_at ? formatDateTime(u.last_sign_in_at) : "Never" },
              { label: "Came from", value: u.acquisition_source ? sentence(u.acquisition_source) : null },
            ]}
          />
        </Card>
        <Card title="Organizations" description="Where this person is a member.">
          <OrgList user={u} />
        </Card>
      </div>
    );
  }
  if (tab === "organizations") {
    return (
      <Card title="Organizations" description="Where this person is a member.">
        <OrgList user={u} />
      </Card>
    );
  }
  if (tab === "subscriptions") {
    const subs = u.memberships;
    return (
      <div className={local.stack}>
        <DataTable
          caption="Subscriptions of this person's organizations"
          columns={[
            {
              key: "org",
              header: "Organization",
              value: (m) => m.name,
              render: (m) => (
<Link href={`/admin/customers/${m.organization_id}`}>{m.name}</Link>
              ),
            },
            {
              key: "status",
              header: "Subscription",
              value: (m) => m.subscription_status,
              render: (m) => (m.subscription_status ? <StatusBadge value={m.subscription_status} /> : "No subscription"),
            },
            { key: "products", header: "Products", value: (m) => m.products, render: (m) => <Trunc>{m.products.map(productName).join(", ")}</Trunc> },
          ]}
          rows={subs}
          rowKey={(m) => m.organization_id}
          rowHref={(m) => `/admin/customers/${m.organization_id}`}
          pageSize={0}
          maxHeight={false}
          empty={<EmptyState icon="card" title="No subscriptions" hint="None of this person's organizations subscribes to a product." compact />}
        />
        <DataTable
          caption="Recent orders of this person's organizations"
          columns={[
            { key: "number", header: "Order", value: (o) => o.number, render: (o) => o.number },
            { key: "item", header: "Item", value: (o) => o.description, render: (o) => <Trunc max="lg">{o.description}</Trunc> },
            {
              key: "amount",
              header: "Amount",
              align: "right",
              value: (o) => o.amount_due_minor,
              render: (o) => (o.amount_due_minor ? formatMoney(o.amount_due_minor, o.currency) : EMPTY),
            },
            { key: "status", header: "Status", value: (o) => o.status, render: (o) => <StatusBadge value={o.status} /> },
            { key: "placed", header: "Placed", value: (o) => o.created_at, render: (o) => <DateText iso={o.created_at} /> },
          ]}
          rows={u.orders}
          rowKey={(o) => o.order_id}
          pageSize={0}
          maxHeight={false}
          empty={<EmptyState icon="receipt" title="No orders" compact />}
        />
      </div>
    );
  }
  if (tab === "sessions") {
    return (
      <DataTable
        caption="Signed-in sessions"
        columns={[
          { key: "in", header: "Signed in", value: (s) => s.signed_in_at, render: (s) => formatDateTime(s.signed_in_at) },
          { key: "last", header: "Last active", value: (s) => s.last_active_at, render: (s) => <DateText iso={s.last_active_at} relative /> },
          { key: "expires", header: "Expires", value: (s) => s.expires_at, render: (s) => formatDateTime(s.expires_at) },
          { key: "ip", header: "Network", value: (s) => s.ip_prefix, render: (s) => <code>{s.ip_prefix || EMPTY}</code> },
          { key: "device", header: "Device", value: (s) => s.device_label, render: (s) => s.device_label || EMPTY },
        ]}
        rows={u.sessions}
        rowKey={(s) => s.session_id}
        pageSize={0}
        maxHeight={false}
        empty={<EmptyState icon="device" title="No active sessions" compact />}
      />
    );
  }
  return (
    <DataTable
      caption="Recent account events"
      columns={[
        { key: "when", header: "When", value: (a) => a.occurred_at, render: (a) => <DateText iso={a.occurred_at} relative /> },
        { key: "event", header: "Event", value: (a) => a.kind, render: (a) => sentence(a.kind) },
        { key: "detail", header: "Detail", value: (a) => a.reason, render: (a) => (a.reason ? sentence(a.reason) : EMPTY) },
        { key: "ip", header: "Network", value: (a) => a.ip_prefix, render: (a) => <code>{a.ip_prefix || EMPTY}</code> },
        { key: "device", header: "Device", value: (a) => a.device_label, render: (a) => a.device_label || EMPTY },
      ]}
      rows={u.activity.map((a, i) => ({ ...a, key: `${a.occurred_at}-${i}` }))}
      rowKey={(a) => a.key}
      pageSize={0}
      maxHeight={false}
      empty={<EmptyState icon="history" title="No recent account events" compact />}
    />
  );
}

function OrgList({ user: u }: { user: UserDetail }) {
  if (u.memberships.length === 0) return <EmptyState icon="building" title="Not a member of any organization" compact />;
  return (
    <ul className={local.orgList}>
      {u.memberships.map((m) => (
        <li key={m.organization_id} className={local.orgItem}>
          <span>
            <Link href={`/admin/customers/${m.organization_id}`}>{m.name}</Link>{" "}
            {m.is_owner && <Badge tone="brand">Owner</Badge>}
            <span className={local.note}>
              {" "}
              {m.products.length ? m.products.map(productName).join(", ") : "No products"}
            </span>
          </span>
          {m.subscription_status ? <StatusBadge value={m.subscription_status} /> : <Badge>No subscription</Badge>}
        </li>
      ))}
    </ul>
  );
}
