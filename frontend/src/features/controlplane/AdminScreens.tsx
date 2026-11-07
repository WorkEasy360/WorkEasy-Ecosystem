"use client";

import { useId, useMemo, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { adminApi, type AuditRow, type CustomerDetail, type CustomerRow, type Diagnosis } from "@/lib/api/controlPlane";
import { adminConsoleApi } from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { CustomerBilling } from "./AdminBilling";
import { StaffRolesPanel, TableSection } from "./AdminCommerce";
import { DateText } from "./AdminUsers";
import { FormPopup } from "./Modal";
import { LoadGate, ReasonAction, autoReason, useAction, useLoad, usePaged } from "./ui";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  DescriptionList,
  EmptyState,
  FilterBar,
  IdCell,
  KeyText,
  Page,
  PageHeader,
  PersonCell,
  SearchInput,
  SectionHeader,
  Select,
  Stack,
  StatusBadge,
  Tabs,
  Trunc,
  EMPTY,
  eventLabel,
  formatDateTime,
  productName,
  sentence,
  useConfirm,
  type Column,
} from "./kit";
import { downloadCsv } from "./kit/csv";
import local from "./AdminPeople.module.css";

const plus = (label: string) => (
  <>
    <Icon name="plus" size={16} />
    {label}
  </>
);

// --- customers --------------------------------------------------------------------

const CUSTOMER_STATUS = [
  { value: "", label: "Any" },
  { value: "active", label: "Active" },
  { value: "suspended", label: "Suspended" },
];

export function Customers() {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const paged = usePaged((cursor) => adminApi.customers({ q: query || undefined, status: status || undefined, cursor }), [query, status]);

  const columns: Column<CustomerRow>[] = [
    {
      key: "org",
      header: "Organization",
      value: (c) => c.display_name,
      render: (c) => (
        <Stack
          main={
            <Link href={`/admin/customers/${c.organization_id}`}>
              <Trunc>{c.display_name}</Trunc>
            </Link>
          }
          sub={<Trunc>{c.owner_email || "No owner yet"}</Trunc>}
        />
      ),
    },
    { key: "status", header: "Status", value: (c) => c.status, render: (c) => <StatusBadge value={c.status} /> },
    { key: "subscription", header: "Subscription", value: (c) => c.subscription_status, render: (c) => <StatusBadge value={c.subscription_status} /> },
    { key: "products", header: "Products", value: (c) => c.products, render: (c) => <Trunc>{c.products.map(productName).join(", ")}</Trunc> },
    { key: "seats", header: "Seats", align: "right", value: (c) => c.seat_capacity, render: (c) => c.seat_capacity ?? EMPTY },
    {
      key: "partner",
      header: "Partner",
      value: (c) => c.partner?.partner_name ?? "Direct",
      render: (c) => (c.partner ? <Stack main={<Trunc>{c.partner.partner_name}</Trunc>} sub={sentence(c.partner.link_status)} /> : "Direct"),
    },
    { key: "created", header: "Created", value: (c) => c.created_at, render: (c) => <DateText iso={c.created_at} /> },
  ];

  return (
    <Page>
      <PageHeader
        title="Customers"
        subtitle="Every customer organization on the platform."
        info="Subscription, products and seats reflect the last reconciliation."
      />
      <FilterBar
        onClear={
          query || status
            ? () => {
                setQuery("");
                setStatus("");
              }
            : undefined
        }
      >
        <SearchInput label="Search by name or slug" value={query} onSearch={setQuery} />
        <Select label="Status" prefix="Status:" value={status} onChange={setStatus} options={CUSTOMER_STATUS} />
      </FilterBar>
      <DataTable
        caption="Customer organizations"
        columns={columns}
        rows={paged.rows}
        rowKey={(c) => c.organization_id}
        rowHref={(c) => `/admin/customers/${c.organization_id}`}
        loading={paged.state === "loading"}
        error={
          paged.state === "forbidden"
            ? "You don't have access to the customer directory."
            : paged.state === "error"
              ? "Couldn't load customers."
              : null
        }
        onLoadMore={() => void paged.more()}
        hasMore={paged.hasMore}
        empty={<EmptyState icon="building" title="No customers match." hint={query || status ? "Try another search or clear the filters." : undefined} />}
      />
    </Page>
  );
}

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

export function CustomerDetailScreen({ organizationId }: { organizationId: string }) {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminApi.customer(organizationId), [organizationId]);
  const { act, busy, feedback } = useAction(reload);
  const manage = can(permissions, "platform.staff_customer.manage");
  const subs = can(permissions, "platform.staff_subscription.manage");
  const payments = can(permissions, "platform.staff_payment.view");

  return (
    <Page>
      <LoadGate load={load} what="this customer">
        {(d) => {
          const org = d.organization;
          return (
            <>
              <PageHeader
                crumbs={[{ label: "Customers", href: "/admin/customers" }, { label: org.display_name }]}
                documentTitle={`${org.display_name} · Customers`}
                title={org.display_name}
                badges={<StatusBadge value={org.status} />}
                meta={[
                  <code key="slug">{org.slug}</code>,
                  <span key="created">
                    Created <DateText iso={org.created_at} />
                  </span>,
                  ...(org.suspension_reason ? [`Suspended: ${org.suspension_reason}`] : []),
                ]}
                actions={
                  manage ? (
                    <>
                      <ReasonAction
                        label="Reset network allow-list"
                        busy={busy}
                        onConfirm={(reason) =>
                          act(
                            () => adminConsoleApi.resetNetworkPolicy(organizationId, reason),
                            "The IP allow-list is off: members can sign in from any network again.",
                          )
                        }
                      />
                      {org.status === "active" ? (
                        <ReasonAction
                          label="Suspend organization"
                          danger
                          busy={busy}
                          message="Every member loses access to the organization's products until it is reactivated."
                          onConfirm={(reason) => act(() => adminApi.suspendCustomer(organizationId, reason), "Organization suspended.")}
                        />
                      ) : (
                        <ReasonAction
                          label="Reactivate organization"
                          busy={busy}
                          onConfirm={(reason) => act(() => adminApi.reactivateCustomer(organizationId, reason), "Organization reactivated.")}
                        />
                      )}
                    </>
                  ) : undefined
                }
              />
              {feedback}

              <Card title="Subscription">
                <SubscriptionPanel detail={d} canManage={subs} busy={busy} act={act} organizationId={organizationId} />
              </Card>

              <Card title="Entitlements and seats" description="Rebuilt from subscriptions; staff overrides are time-boxed grants.">
                <DataTable
                  caption="Products this customer holds"
                  columns={[
                    { key: "product", header: "Product", value: ([code]) => code, render: ([code]) => productName(code) },
                    { key: "status", header: "Status", value: ([, e]) => e.status, render: ([, e]) => <StatusBadge value={e.status} /> },
                    {
                      key: "seats",
                      header: "Seats",
                      align: "right",
                      value: ([code]) => d.licenses.find((l) => l.product_code === code)?.seats_total,
                      render: ([code]) => d.licenses.find((l) => l.product_code === code)?.seats_total ?? EMPTY,
                    },
                    {
                      key: "assigned",
                      header: "Assigned",
                      align: "right",
                      value: ([code]) => d.licenses.find((l) => l.product_code === code)?.seats_assigned,
                      render: ([code]) => d.licenses.find((l) => l.product_code === code)?.seats_assigned ?? EMPTY,
                    },
                    {
                      key: "over",
                      header: "Over limit",
                      align: "right",
                      render: ([code]) => d.licenses.find((l) => l.product_code === code)?.over_limit ?? 0,
                    },
                  ]}
                  rows={Object.entries(d.entitlements)}
                  rowKey={([code]) => code}
                  pageSize={0}
                  maxHeight={false}
                  empty={<EmptyState icon="box" title="No entitlements" compact />}
                />
                <OverridePanel detail={d} canManage={subs} busy={busy} act={act} organizationId={organizationId} />
              </Card>

              <Card title="Members, billing and partner">
                <DescriptionList
                  items={[
                    {
                      label: "Members",
                      value: Object.entries(d.members).length
                        ? Object.entries(d.members)
                            .map(([k, v]) => `${v} ${k}`)
                            .join(", ")
                        : null,
                    },
                    {
                      label: "Partner",
                      value: d.partner ? (
                        <span className={local.holders}>
                          <Link href={`/admin/partners/${d.partner.partner_id}`}>{d.partner.display_name}</Link>
                          <StatusBadge value={d.partner.link_status} />
                        </span>
                      ) : (
                        "None (direct customer)"
                      ),
                    },
                    {
                      label: "Billing",
                      value: d.billing ? (
                        <Stack
                          main={`${d.billing.currency}, ${sentence(d.billing.collection_mode)}${d.billing.billing_region ? `, ${d.billing.billing_region}` : ""}`}
                          sub={d.billing.note}
                        />
                      ) : (
                        "No billing account, or you don't hold billing access."
                      ),
                    },
                  ]}
                />
              </Card>

              {payments && <CustomerBilling organizationId={organizationId} />}

              <Card title="Why can't someone launch a product?" description="A read-only check of every step; nothing is changed.">
                <DiagnosticForm organizationId={organizationId} />
              </Card>

              <TableSection title="Recent audit trail">
                <DataTable
                  caption="This customer's own audit log"
                  columns={AUDIT_COLUMNS}
                  rows={d.audit}
                  rowKey={(a) => a.id}
                  pageSize={0}
                  maxHeight={false}
                  empty={<EmptyState icon="audit" title="No entries" compact />}
                />
              </TableSection>
            </>
          );
        }}
      </LoadGate>
    </Page>
  );
}

const OVERRIDE_DAYS = [7, 30, 90];

function OverridePanel({
  detail,
  canManage,
  busy,
  act,
  organizationId,
}: {
  detail: CustomerDetail;
  canManage: boolean;
  busy: boolean;
  act: Act;
  organizationId: string;
}) {
  const codeId = useId();
  const [kind, setKind] = useState<"product_code" | "feature_key">("product_code");
  const [code, setCode] = useState("");
  const [days, setDays] = useState("30");

  const grant = canManage ? (
    <FormPopup
      label={plus("Grant override")}
      title="Grant an override"
      intro="A time-boxed grant on top of what the subscriptions give; access is recomputed when it starts and when it ends."
      size="sm"
      submitLabel="Grant override"
      canSubmit={Boolean(code.trim())}
      disabled={busy}
      onOpen={() => {
        setKind("product_code");
        setCode("");
        setDays("30");
      }}
      onSubmit={async () => {
        const expires = new Date(Date.now() + Number(days) * 24 * 60 * 60 * 1000).toISOString();
        await adminApi.grantOverride(organizationId, { [kind]: code.trim(), reason: autoReason("Grant override"), expires_at: expires });
      }}
      // The customer's page announces it and reloads, with the override listed.
      onDone={() => void act(async () => {}, "Override granted; access was recomputed.")}
    >
      <Select
        label="Grant access to"
        showLabel
        fullWidth
        value={kind}
        onChange={(v) => setKind(v as typeof kind)}
        options={[
          { value: "product_code", label: "A product" },
          { value: "feature_key", label: "A feature" },
        ]}
      />
      <div className={local.field}>
        <label className="w360-label" htmlFor={codeId}>
          {kind === "product_code" ? "Product code" : "Feature key"}
        </label>
        <input id={codeId} className="w360-input" required value={code} onChange={(e) => setCode(e.target.value)} />
      </div>
      <Select
        label="Expires after"
        showLabel
        fullWidth
        value={days}
        onChange={setDays}
        options={OVERRIDE_DAYS.map((d) => ({ value: String(d), label: `${d} days` }))}
      />
    </FormPopup>
  ) : null;

  return (
    <div className={local.stack}>
      <SectionHeader level={3} title="Staff overrides" actions={grant} />
      <DataTable
        caption="Active staff overrides"
        columns={[
          {
            key: "grants",
            header: "Grants",
            value: (o) => o.product_code || o.feature_key,
            render: (o) => (o.product_code ? productName(o.product_code) : <Stack main={o.feature_key} sub="Feature" />),
          },
          { key: "reason", header: "Reason", value: (o) => o.reason, render: (o) => <Trunc max="lg">{o.reason}</Trunc> },
          { key: "expires", header: "Expires", value: (o) => o.expires_at, render: (o) => (o.expires_at ? formatDateTime(o.expires_at) : "No expiry") },
          {
            key: "action",
            header: "Action",
            align: "right",
            render: (o) =>
              canManage ? (
                <ReasonAction
                  label="Revoke"
                  size="sm"
                  danger
                  busy={busy}
                  message="The customer loses this access now, unless a subscription also gives it."
                  onConfirm={(r) => act(() => adminApi.revokeOverride(organizationId, o.id, r), "Override revoked; access was recomputed.")}
                />
              ) : null,
          },
        ]}
        rows={detail.entitlement_overrides}
        rowKey={(o) => o.id}
        pageSize={0}
        maxHeight={false}
        empty={<EmptyState icon="key" title="No overrides" compact />}
      />
    </div>
  );
}

function SubscriptionPanel({
  detail,
  canManage,
  busy,
  act,
  organizationId,
}: {
  detail: CustomerDetail;
  canManage: boolean;
  busy: boolean;
  act: Act;
  organizationId: string;
}) {
  if (detail.subscriptions.length === 0) return <EmptyState icon="card" title="No subscription" compact />;
  return (
    <>
      {detail.subscriptions.map((s) => (
        <div key={s.id} className={local.stack}>
          <div className={local.orgItem}>
            <DescriptionList
              columns={2}
              items={[
                { label: "Status", value: <StatusBadge value={s.status} /> },
                { label: "Billing", value: `${s.currency} · ${sentence(s.billing_interval)}` },
                { label: "Period ends", value: formatDateTime(s.current_period_end) },
                { label: "Cancels", value: s.cancel_at ? formatDateTime(s.cancel_at) : null, when: Boolean(s.cancel_at) },
              ]}
            />
            {canManage && (
              <div className={local.actions}>
                {s.status === "suspended" ? (
                  <ReasonAction
                    label="Resume subscription"
                    busy={busy}
                    onConfirm={(r) => act(() => adminApi.subscriptionAction(organizationId, s.id, "resume", r, s.version), "Subscription resumed.")}
                  />
                ) : (
                  <ReasonAction
                    label="Suspend subscription"
                    danger
                    busy={busy}
                    message="The customer loses access to the subscribed products until it is resumed."
                    onConfirm={(r) => act(() => adminApi.subscriptionAction(organizationId, s.id, "suspend", r, s.version), "Subscription suspended.")}
                  />
                )}
                <ReasonAction
                  label="Cancel at period end"
                  danger
                  busy={busy}
                  message="The subscription ends when the current period ends."
                  onConfirm={(r) => act(() => adminApi.subscriptionAction(organizationId, s.id, "cancel", r, s.version), "Cancellation scheduled.")}
                />
              </div>
            )}
          </div>
          <DataTable
            caption="Subscription lines"
            columns={[
              { key: "product", header: "Product", value: (i) => i.product_code || i.bundle_code, render: (i) => productName(i.product_code || i.bundle_code) },
              { key: "plan", header: "Plan", value: (i) => i.plan_code, render: (i) => (i.plan_code ? sentence(i.plan_code) : "Bundle") },
              { key: "qty", header: "Quantity", align: "right", value: (i) => i.quantity, render: (i) => i.quantity },
              { key: "closed", header: "Closed", value: (i) => i.removed_at, render: (i) => (i.removed_at ? formatDateTime(i.removed_at) : EMPTY) },
            ]}
            rows={s.items}
            rowKey={(i) => i.id}
            pageSize={0}
            maxHeight={false}
            empty={<EmptyState icon="box" title="No lines" compact />}
          />
        </div>
      ))}
    </>
  );
}

function DiagnosticForm({ organizationId }: { organizationId: string }) {
  const productId = useId();
  const emailId = useId();
  const [product, setProduct] = useState("");
  const [email, setEmail] = useState("");
  const [result, setResult] = useState<Diagnosis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await adminApi.diagnose(organizationId, product.trim(), email.trim() || undefined));
    } catch {
      setResult(null);
      setError("The diagnostic could not be run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={local.stack}>
      <form className={local.diagForm} onSubmit={submit}>
        <div className={local.field}>
          <label className="w360-label" htmlFor={productId}>
            Product code
          </label>
          <input id={productId} className="w360-input" required value={product} onChange={(e) => setProduct(e.target.value)} maxLength={32} placeholder="easy_crm" />
        </div>
        <div className={local.field}>
          <label className="w360-label" htmlFor={emailId}>
            Member&apos;s email (optional)
          </label>
          <input id={emailId} className="w360-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} />
        </div>
        <Button type="submit" variant="primary" icon="play" loading={busy} disabled={!product.trim()}>
          Run diagnostics
        </Button>
      </form>
      {error && <Alert tone="danger">{error}</Alert>}
      {result && <DiagnosticResult result={result} />}
    </div>
  );
}

/** The result of the read-only launch check: the verdict, then each step with
 * its status. Shared with the partner portal's customer page. */
export function DiagnosticResult({ result }: { result: Diagnosis }) {
  return (
    <div aria-live="polite" className={local.stack}>
      <Alert
        tone={result.can_launch ? "success" : "danger"}
        title={result.can_launch ? "Nothing blocks a launch." : `Blocked at: ${result.blocking_step}.`}
      >
        Read-only — nothing was changed.
      </Alert>
      <ol className={local.chain} aria-label="Steps checked">
        {result.chain.map((s) => (
          <li key={s.step} className={local.chainItem}>
            {s.ok ? (
              <Badge tone="success" icon="check">
                ok
              </Badge>
            ) : (
              <Badge tone="danger" icon="x">
                blocked
              </Badge>
            )}
            <span>{sentence(s.step)}</span>
            <code>{s.code}</code>
          </li>
        ))}
      </ol>
    </div>
  );
}

// --- diagnostics ---------------------------------------------------------------------------

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Settings → Diagnostics: find the organization by name, slug or ID, then
 * run the same read-only launch check as on the customer's page. */
export function SupportScreen() {
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<{ id: string; name: string } | null>(null);
  const byId = UUID.test(query.trim());
  const paged = usePaged(
    (cursor) => (query && !byId ? adminApi.customers({ q: query, cursor }) : Promise.resolve({ results: [], next_cursor: null, has_more: false })),
    [query, byId],
  );
  return (
    <Page>
      <PageHeader
        title="Diagnostics"
        subtitle="Why can't a customer launch a product? A read-only check that changes nothing."
        info="It runs the same chain of checks as the customer's page and reports state - never a secret."
      />
      <Card title="1. Find the organization" description="Search by name or slug, or paste an organization ID.">
        <SearchInput
          label="Find an organization"
          value={query}
          onSearch={(v) => {
            setQuery(v);
            if (UUID.test(v.trim())) setChosen({ id: v.trim(), name: v.trim() });
          }}
          placeholder="Organization name, slug or ID…"
        />
        {query && !byId && paged.state === "loading" && <p className={local.note}>Searching…</p>}
        {query && !byId && paged.state === "ready" && paged.rows.length === 0 && (
          <EmptyState icon="search" title="No organization matches" hint="Try another name, or paste the organization's ID." compact />
        )}
        {paged.state === "forbidden" && <Alert tone="warning">You don&apos;t have access to the customer directory; paste an organization ID instead.</Alert>}
        {paged.rows.length > 0 && (
          <ul className={local.lookupList} aria-label="Matching organizations">
            {paged.rows.slice(0, 8).map((c) => (
              <li key={c.organization_id}>
                <button
                  type="button"
                  className={local.lookupOption}
                  aria-pressed={chosen?.id === c.organization_id}
                  onClick={() => setChosen({ id: c.organization_id, name: c.display_name })}
                >
                  <PersonCell name={c.display_name} email={c.owner_email || c.slug} />
                  <StatusBadge value={c.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card
        title="2. Run the check"
        description={chosen ? <>For {chosen.name}</> : "Choose an organization first."}
      >
        {chosen ? (
          <DiagnosticForm key={chosen.id} organizationId={chosen.id} />
        ) : (
          <div className={local.diagForm}>
            <Button variant="primary" icon="play" disabled>
              Run diagnostics
            </Button>
          </div>
        )}
      </Card>
    </Page>
  );
}

// --- audit ------------------------------------------------------------------------------

const AUDIT_COLUMNS: Column<AuditRow>[] = [
  { key: "when", header: "When", value: (a) => a.occurred_at, render: (a) => <DateText iso={a.occurred_at} relative /> },
  {
    key: "action",
    header: "Action",
    value: (a) => a.action,
    render: (a) => <Stack main={eventLabel(a.action)} sub={<KeyText>{a.action}</KeyText>} />,
  },
  { key: "actor", header: "By", value: (a) => a.actor_kind, render: (a) => sentence(a.actor_kind) },
  {
    key: "outcome",
    header: "Outcome",
    value: (a) => a.outcome,
    render: (a) => (a.outcome ? <StatusBadge value={a.outcome === "success" ? "succeeded" : a.outcome} label={sentence(a.outcome)} /> : EMPTY),
  },
];

const AUDIT_DATES = [
  { value: "", label: "Any time" },
  { value: "1", label: "Last 24 hours" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
];

const AUDIT_AREAS = [
  { value: "", label: "All actions" },
  { value: "staff.", label: "Staff" },
  { value: "partner.", label: "Partners" },
  { value: "payout.", label: "Payouts" },
  { value: "customer.", label: "Customers" },
  { value: "catalog.", label: "Catalog" },
  { value: "commercial.", label: "Billing" },
  { value: "platform.", label: "Platform" },
];

export function AuditScreen() {
  const [prefix, setPrefix] = useState("");
  const [area, setArea] = useState("");
  const [actor, setActor] = useState("");
  const [resource, setResource] = useState("");
  const [days, setDays] = useState("");
  const [now] = useState(() => Date.now());
  const action = prefix || area;
  const paged = usePaged((cursor) => adminApi.audit(cursor, action || undefined), [action]);

  // The audit endpoint filters by action prefix; who, what and when narrow
  // the entries loaded so far.
  const actors = useMemo(() => [...new Set(paged.rows.map((a) => a.actor_kind).filter(Boolean))].sort(), [paged.rows]);
  const resources = useMemo(() => [...new Set(paged.rows.map((a) => a.resource_type).filter(Boolean))].sort(), [paged.rows]);
  const rows = useMemo(
    () =>
      paged.rows.filter(
        (a) =>
          (!actor || a.actor_kind === actor) &&
          (!resource || a.resource_type === resource) &&
          (!days || now - Date.parse(a.occurred_at) <= Number(days) * 24 * 60 * 60 * 1000),
      ),
    [paged.rows, actor, resource, days, now],
  );

  const columns: Column<AuditRow>[] = [
    ...AUDIT_COLUMNS.slice(0, 3),
    {
      key: "resource",
      header: "Resource",
      value: (a) => a.resource_type,
      render: (a) => (a.resource_type ? <Stack main={eventLabel(a.resource_type)} sub={<IdCell value={a.resource_id} label="Copy resource ID" />} /> : EMPTY),
    },
    { key: "reason", header: "Reason", value: (a) => a.reason, render: (a) => <Trunc max="lg">{a.reason}</Trunc> },
    AUDIT_COLUMNS[3],
  ];
  const filtered = Boolean(prefix || area || actor || resource || days);
  return (
    <Page>
      <PageHeader title="Audit log" subtitle="Every staff action in the admin console, newest first." />
      <FilterBar
        onClear={
          filtered
            ? () => {
                setPrefix("");
                setArea("");
                setActor("");
                setResource("");
                setDays("");
              }
            : undefined
        }
        end={
          <Button
            icon="download"
            disabled={rows.length === 0}
            onClick={() =>
              downloadCsv(
                "audit-log.csv",
                ["When", "Action", "By", "Resource", "Resource ID", "Outcome", "Reason"],
                rows.map((a) => [a.occurred_at, a.action, a.actor_kind, a.resource_type, a.resource_id, a.outcome, a.reason]),
              )
            }
          >
            Export CSV
          </Button>
        }
      >
        <SearchInput label="Filter by action (e.g. payout., partner.)" value={prefix} onSearch={setPrefix} placeholder="Action, e.g. partner." />
        <Select label="Area" prefix="Area:" value={area} onChange={setArea} options={AUDIT_AREAS} />
        <Select label="By" prefix="By:" value={actor} onChange={setActor} options={[{ value: "", label: "Anyone" }, ...actors.map((a) => ({ value: a, label: sentence(a) }))]} />
        <Select
          label="Resource"
          prefix="Resource:"
          value={resource}
          onChange={setResource}
          options={[{ value: "", label: "Any" }, ...resources.map((r) => ({ value: r, label: eventLabel(r) }))]}
        />
        <Select label="Date" prefix="Date:" value={days} onChange={setDays} options={AUDIT_DATES} />
      </FilterBar>
      <DataTable
        caption="Audit entries"
        columns={columns}
        rows={rows}
        rowKey={(a) => a.id}
        loading={paged.state === "loading"}
        error={paged.state === "forbidden" ? "You don't have access to the audit trail." : paged.state === "error" ? "Couldn't load the audit trail." : null}
        onLoadMore={() => void paged.more()}
        hasMore={paged.hasMore}
        empty={<EmptyState icon="audit" title="No audit entries" hint={filtered ? "Try other filters." : undefined} />}
      />
    </Page>
  );
}

// --- staff access ---------------------------------------------------------------------------

type Grant = { id: string; email: string; permission: string; granted_at: string; expires_at: string | null; reason: string };

/** "platform.staff_customer.view" -> { area: "Customer", verb: "View" }. */
function permissionParts(key: string): { area: string; verb: string } {
  const m = /^platform\.staff_([a-z_]+)\.([a-z_]+)$/.exec(key);
  return m ? { area: sentence(m[1]), verb: sentence(m[2]) } : { area: key, verb: "" };
}

export function SecurityScreen() {
  const [tab, setTab] = useState<"roles" | "permissions">("roles");
  return (
    <Page>
      <PageHeader
        title="Staff access"
        subtitle="Who can do what in the admin console."
        info="Grants take effect and end immediately, a time-boxed grant ends on its own when it expires, and the last permanent security administrator cannot be removed."
        tabs={
          <Tabs
            label="Staff access"
            selected={tab}
            onSelect={setTab}
            tabs={[
              { key: "roles", label: "Roles" },
              { key: "permissions", label: "Permissions" },
            ]}
          />
        }
      />
      {tab === "roles" ? <StaffRolesPanel /> : <PermissionsPanel />}
    </Page>
  );
}

function PermissionsPanel() {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminApi.securityGrants(), []);
  const manage = can(permissions, "platform.staff_security.manage");
  const { ask, dialog: confirm } = useConfirm();
  const emailId = useId();
  const permId = useId();
  const expiresId = useId();
  const [email, setEmail] = useState("");
  const [permission, setPermission] = useState("platform.staff_customer.view");
  // Optional: the last moment of the chosen (local) day. Empty means no expiry.
  const [expiresOn, setExpiresOn] = useState("");
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [query, setQuery] = useState("");
  const [area, setArea] = useState("");
  const [expiry, setExpiry] = useState("");

  const grant = manage ? (
    <FormPopup
      label={plus("Grant a permission")}
      title="Grant a permission"
      intro="It takes effect immediately; a time-boxed grant ends on its own when it expires."
      size="sm"
      submitLabel="Grant"
      canSubmit={Boolean(email.trim() && permission.trim())}
      onOpen={() => {
        setEmail("");
        setPermission("platform.staff_customer.view");
        setExpiresOn("");
      }}
      onSubmit={async () => {
        const expiresAt = expiresOn ? new Date(`${expiresOn}T23:59:59`).toISOString() : undefined;
        await adminApi.grantPermission(email.trim(), permission.trim(), autoReason("Grant permission"), expiresAt);
        return "Permission granted.";
      }}
      onDone={reload}
    >
      <div className={local.field}>
        <label className="w360-label" htmlFor={emailId}>
          Staff member&apos;s email
        </label>
        <input id={emailId} className="w360-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div className={local.field}>
        <label className="w360-label" htmlFor={permId}>
          Permission
        </label>
        <input id={permId} className="w360-input" required value={permission} onChange={(e) => setPermission(e.target.value)} />
      </div>
      <div className={local.field}>
        <label className="w360-label" htmlFor={expiresId}>
          Expires on (optional)
        </label>
        <input id={expiresId} className="w360-input" type="date" min={today} value={expiresOn} onChange={(e) => setExpiresOn(e.target.value)} />
      </div>
    </FormPopup>
  ) : null;

  return (
    <TableSection title="Individual permissions" description="Each permission held, by whom and until when." actions={grant}>
      <LoadGate load={load} what="permissions">
        {(d) => {
          const areas = [...new Set(d.results.map((g) => permissionParts(g.permission).area))].sort();
          const needle = query.toLowerCase();
          const rows = d.results.filter(
            (g) =>
              (!needle || `${g.email} ${g.permission}`.toLowerCase().includes(needle)) &&
              (!area || permissionParts(g.permission).area === area) &&
              (!expiry || (expiry === "timed" ? Boolean(g.expires_at) : !g.expires_at)),
          );
          const columns: Column<Grant>[] = [
            { key: "holder", header: "Holder", value: (g) => g.email, render: (g) => <PersonCell name={g.email} /> },
            {
              key: "permission",
              header: "Permission",
              value: (g) => g.permission,
              render: (g) => {
                const p = permissionParts(g.permission);
                return <Stack main={p.verb ? `${p.area} · ${p.verb}` : p.area} sub={<KeyText>{g.permission}</KeyText>} />;
              },
            },
            { key: "granted", header: "Granted", value: (g) => g.granted_at, render: (g) => <DateText iso={g.granted_at} /> },
            { key: "expires", header: "Expires", value: (g) => g.expires_at, render: (g) => (g.expires_at ? <DateText iso={g.expires_at} /> : "No expiry") },
            { key: "reason", header: "Reason", value: (g) => g.reason, render: (g) => <Trunc max="md">{g.reason}</Trunc> },
          ];
          return (
            <div className={local.stack}>
              <FilterBar
                  onClear={
                    query || area || expiry
                      ? () => {
                          setQuery("");
                          setArea("");
                          setExpiry("");
                        }
                      : undefined
                  }
                >
                  <SearchInput label="Search by email or permission" value={query} onSearch={setQuery} />
                  <Select label="Area" prefix="Area:" value={area} onChange={setArea} options={[{ value: "", label: "All" }, ...areas.map((a) => ({ value: a, label: a }))]} />
                  <Select
                    label="Expiry"
                    prefix="Expiry:"
                    value={expiry}
                    onChange={setExpiry}
                    options={[
                      { value: "", label: "Any" },
                      { value: "timed", label: "Time-boxed" },
                      { value: "permanent", label: "No expiry" },
                    ]}
                  />
                </FilterBar>
              <DataTable
                caption="Active grants"
                columns={columns}
                rows={rows}
                rowKey={(g) => g.id}
                rowLabel={(g) => `${g.email}, ${g.permission}`}
                rowMenu={
                  manage
                    ? (g) => [
                        {
                          label: "Revoke",
                          icon: "ban",
                          danger: true,
                          onSelect: () =>
                            ask({
                              title: `Revoke ${g.permission} from ${g.email}?`,
                              message: "It ends at once. The last permanent security administrator cannot be removed.",
                              confirmLabel: "Revoke",
                              danger: true,
                              reason: true,
                              onConfirm: async (reason) => {
                                await adminApi.revokePermission(g.id, reason);
                                reload();
                                return "Permission revoked.";
                              },
                            }),
                        },
                      ]
                    : undefined
                }
                empty={<EmptyState icon="key" title={d.results.length ? "No permission matches" : "No permissions granted"} compact />}
              />
            </div>
          );
        }}
      </LoadGate>
      {confirm}
    </TableSection>
  );
}
