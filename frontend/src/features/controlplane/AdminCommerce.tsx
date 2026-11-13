"use client";

import { useId, useMemo, useState } from "react";
import Link from "next/link";
import { adminApi, type CatalogProduct, type CustomerRow, type ProductSource } from "@/lib/api/controlPlane";
import { adminConsoleApi, type PlatformSettings, type StaffOrderRow, type StaffRoleRow } from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, autoReason, describeError, useLoad, usePaged } from "./ui";
import { DialogHead, Modal, dialog, FormPopup } from "./Modal";
import { TextField } from "./widgets";
import {
  Avatar,
  Badge,
  Button,
  Card,
  DataTable,
  DescriptionList,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  SearchInput,
  SectionHeader,
  Select,
  Stack,
  StatusBadge,
  StatusChips,
  Trunc,
  kit,
  EMPTY,
  formatDate,
  formatMoney,
  productName,
  sentence,
  useConfirm,
  useToast,
  type Column,
} from "./kit";
import { downloadCsv } from "./kit/csv";
import { DateText } from "./AdminUsers";
import { formatBasisPoints, toBasisPoints, toMajor } from "./amounts";
import local from "./AdminPeople.module.css";

/**
 * Super Admin → Commerce and Access (Phase 9; 2026-11-07 design system): every
 * order across customers, customers by subscription state, who holds each
 * product, the platform settings and named staff roles. Reads only what the
 * grants allow; every change is re-authorized (and, where dangerous, audited)
 * by the server.
 */

/** A titled section around a table (the table is its own card). */
export function TableSection({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <section className={local.stack} aria-labelledby={id}>
      <SectionHeader id={id} title={title} description={description} actions={actions} />
      {children}
    </section>
  );
}

/** The table props for a server-paged list. */
function pagedProps<T>(paged: ReturnType<typeof usePaged<T>>, what: string) {
  return {
    loading: paged.state === "loading",
    error:
      paged.state === "forbidden" ? `You don't have access to ${what}.` : paged.state === "error" ? `Couldn't load ${what}.` : null,
    onLoadMore: () => void paged.more(),
    hasMore: paged.hasMore,
  };
}

const CREATED = [
  { value: "", label: "Any time" },
  { value: "1", label: "Today" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
];

function within(iso: string, days: string, now: number): boolean {
  if (!days) return true;
  const t = Date.parse(iso);
  return Number.isFinite(t) && now - t <= Number(days) * 24 * 60 * 60 * 1000;
}

// --- orders ---------------------------------------------------------------------------------

const ORDER_TABS = [
  { key: "", label: "All" },
  { key: "pending_payment", label: "Awaiting payment" },
  { key: "fulfilled", label: "Fulfilled" },
  { key: "failed", label: "Failed" },
  { key: "expired", label: "Expired" },
  { key: "cancelled", label: "Cancelled" },
] as const;

/** Purchase sources (ADR-053): the Store, a partner placing it for its customer
 * (a partner activation), or a white-label partner's branded Store. */
const SOURCES = [
  { value: "", label: "All" },
  { value: "direct", label: "Store (direct)" },
  { value: "partner", label: "Partner activation" },
  { value: "storefront", label: "Partner storefront" },
];
const SOURCE_TEXT: Record<string, string> = { direct: "Store", partner: "Partner activation", storefront: "Partner storefront" };

/** `initialQuery`: an order number from the address (the command palette). */
export function StaffOrdersScreen({ initialQuery = "", initialSource = "" }: { initialQuery?: string; initialSource?: string } = {}) {
  const [status, setStatus] = useState("");
  const [source, setSource] = useState(SOURCES.some((s) => s.value === initialSource) ? initialSource : "");
  const [query, setQuery] = useState(initialQuery);
  const [created, setCreated] = useState("");
  const [now] = useState(() => Date.now());
  const paged = usePaged(
    (cursor) => adminConsoleApi.orders({ status: status || undefined, channel: source || undefined, cursor }),
    [status, source],
  );
  // The orders endpoint filters by status and purchase source: the search and the
  // date range narrow the orders loaded so far.
  const rows = useMemo(() => {
    const needle = query.toLowerCase();
    return paged.rows.filter(
      (o) =>
        (!needle || `${o.number} ${o.organization_name} ${o.description}`.toLowerCase().includes(needle)) &&
        within(o.created_at, created, now),
    );
  }, [paged.rows, query, created, now]);

  const columns: Column<StaffOrderRow>[] = [
    { key: "number", header: "Order #", value: (o) => o.number, render: (o) => o.number },
    {
      key: "customer",
      header: "Customer",
      value: (o) => o.organization_name,
      render: (o) => (
        <Link href={`/admin/customers/${o.organization_id}`}>
          <Trunc>{o.organization_name || o.organization_id}</Trunc>
        </Link>
      ),
    },
    { key: "item", header: "Item", value: (o) => o.description, render: (o) => <Trunc max="lg">{o.description}</Trunc> },
    {
      key: "source",
      header: "Purchase source",
      value: (o) => o.channel ?? "direct",
      render: (o) => (
        <Stack
          main={SOURCE_TEXT[o.channel ?? "direct"] ?? sentence(o.channel ?? "")}
          sub={
            o.partner_id ? (
              <Link href={`/admin/partners/${o.partner_id}`}>
                <Trunc>{o.partner_name || o.partner_id}</Trunc>
              </Link>
            ) : undefined
          }
        />
      ),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      value: (o) => o.amount_due_minor,
      render: (o) => (o.amount_due_minor ? formatMoney(o.amount_due_minor, o.currency) : EMPTY),
    },
    { key: "status", header: "Status", value: (o) => o.status, render: (o) => <StatusBadge value={o.status} /> },
    {
      key: "payment",
      header: "Payment",
      // Never empty, so the column is not hidden when no order on the page is paid
      // (the table hides columns whose every value is empty).
      value: (o) => o.paid_at ?? (o.status === "pending_payment" ? "verify" : "not_paid"),
      render: (o) =>
        o.paid_at ? (
          <span>
            Paid <DateText iso={o.paid_at} />
          </span>
        ) : o.status === "pending_payment" ? (
          // Payment verification (an offline payment marked as received) happens on
          // the customer's invoice, where the reference and reason are recorded.
          <Link href={`/admin/customers/${o.organization_id}#billing-h`}>Verify payment</Link>
        ) : (
          "Not paid"
        ),
    },
    { key: "created", header: "Created", value: (o) => o.created_at, render: (o) => <DateText iso={o.created_at} /> },
  ];

  const filtered = Boolean(query || created || source);
  return (
    <Page>
      <PageHeader
        title="Orders"
        subtitle="Every order across customers, newest first."
        info="Payment confirms an order; nothing is granted before it is paid."
      />
      <StatusChips label="Order status" value={status} onChange={setStatus} chips={ORDER_TABS.map((t) => ({ value: t.key, label: t.label }))} />
      <FilterBar
        onClear={
          filtered
            ? () => {
                setQuery("");
                setCreated("");
                setSource("");
              }
            : undefined
        }
        end={
          <Button
            icon="download"
            disabled={rows.length === 0}
            onClick={() =>
              downloadCsv(
                "orders.csv",
                ["Order", "Customer", "Item", "Purchase source", "Source reference", "Amount", "Currency", "Status", "Paid", "Created"],
                rows.map((o) => [
                  o.number,
                  o.organization_name,
                  o.description,
                  SOURCE_TEXT[o.channel ?? "direct"] ?? (o.channel ?? ""),
                  o.partner_name ?? "",
                  // In the currency's own minor unit (a yen has none): never a fixed /100.
                  toMajor(o.amount_due_minor, o.currency),
                  o.currency,
                  o.status,
                  o.paid_at ?? "",
                  o.created_at,
                ]),
              )
            }
          >
            Export CSV
          </Button>
        }
      >
        <SearchInput label="Search by order number or customer" value={query} onSearch={setQuery} />
        <Select label="Created" prefix="Created:" value={created} onChange={setCreated} options={CREATED} />
        <Select label="Purchase source" prefix="Source:" value={source} onChange={setSource} options={SOURCES} />
      </FilterBar>
      <DataTable
        caption="Orders"
        columns={columns}
        rows={rows}
        rowKey={(o) => o.order_id}
        rowHref={(o) => `/admin/customers/${o.organization_id}`}
        {...pagedProps(paged, "orders")}
        empty={<EmptyState icon="receipt" title="No orders" hint={filtered || status ? "Try another status or clear the filters." : "Orders appear here when customers check out."} />}
      />
    </Page>
  );
}

// --- subscriptions ------------------------------------------------------------------------------

const SUBSCRIPTION_TABS = [
  { key: "", label: "All" },
  { key: "active", label: "Active" },
  { key: "trialing", label: "Trial" },
  { key: "past_due", label: "Past due" },
  { key: "grace", label: "Grace" },
  { key: "suspended", label: "Suspended" },
  { key: "cancelled", label: "Cancelled" },
  { key: "none", label: "No subscription" },
] as const;

/** Customers by subscription state - the customer directory, filtered. */
export function SubscriptionsScreen() {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const paged = usePaged(
    (cursor) => adminApi.customers({ subscription: status || undefined, q: query || undefined, cursor }),
    [status, query],
  );
  const columns: Column<CustomerRow>[] = [
    {
      key: "customer",
      header: "Customer",
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
    { key: "products", header: "Products", value: (c) => c.products, render: (c) => <Trunc>{c.products.map(productName).join(", ")}</Trunc> },
    {
      key: "status",
      header: "Status",
      value: (c) => c.subscription_status,
      render: (c) => (c.subscription_status ? <StatusBadge value={c.subscription_status} /> : "No subscription"),
    },
    { key: "seats", header: "Seats", align: "right", value: (c) => c.seat_capacity, render: (c) => c.seat_capacity ?? EMPTY },
    { key: "since", header: "Customer since", value: (c) => c.created_at, render: (c) => <DateText iso={c.created_at} /> },
  ];
  return (
    <Page>
      <PageHeader title="Subscriptions" subtitle="Customers by the state of their subscription. Open a customer to act on it." />
      <StatusChips
        label="Subscription status"
        value={status}
        onChange={setStatus}
        chips={SUBSCRIPTION_TABS.map((t) => ({ value: t.key, label: t.label }))}
      />
      <FilterBar onClear={query ? () => setQuery("") : undefined}>
        <SearchInput label="Search customers" value={query} onSearch={setQuery} />
      </FilterBar>
      <DataTable
        caption="Customers"
        columns={columns}
        rows={paged.rows}
        rowKey={(c) => c.organization_id}
        rowHref={(c) => `/admin/customers/${c.organization_id}`}
        {...pagedProps(paged, "subscriptions")}
        empty={<EmptyState icon="card" title="No customers" hint={status || query ? "Try another status or search." : undefined} />}
      />
    </Page>
  );
}

// --- entitlements ------------------------------------------------------------------------------

/** Who holds each product now. Changing one customer's access - an override -
 * is on the customer's page, audited. */
export function EntitlementsScreen() {
  const [products] = useLoad(() => adminApi.products(), []);
  const [chosen, setChosen] = useState("");
  return (
    <Page>
      <PageHeader
        title="Entitlements"
        subtitle="Which customers hold a product now."
        info="Access comes from four sources, kept apart: a subscription, a bundle, a staff override (granted on the customer's page) or a product trial, which is not a subscription. The Source column names which one."
      />
      <LoadGate load={products} what="products">
        {({ results }: { results: CatalogProduct[] }) => {
          const code = chosen || results[0]?.code || "";
          if (!code) return <EmptyState icon="box" title="No products yet" hint="Products appear here once they are added to the catalog." />;
          return (
            <>
              <FilterBar>
                <Select
                  label="Product"
                  prefix="Product:"
                  value={code}
                  onChange={setChosen}
                  options={results.map((p) => ({ value: p.code, label: productName(p.name) }))}
                />
              </FilterBar>
              <Holders code={code} name={productName(results.find((p) => p.code === code)?.name ?? code)} />
            </>
          );
        }}
      </LoadGate>
    </Page>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  subscription: "Subscription",
  bundle: "Bundle",
  grant: "Staff override",
  trial: "Product trial",
};

export const sourceLabel = (kind: string | null | undefined): string => (kind ? (SOURCE_LABEL[kind] ?? sentence(kind)) : "Unknown");

/** Where one product's access comes from, with "ended" when it is no longer in
 * effect (a trial past its end shows here until the next recalculation). */
function SourceBadge({ source }: { source: ProductSource | null | undefined }) {
  if (!source) return <span>{EMPTY}</span>;
  const tone = source.kind === "subscription" || source.kind === "bundle" ? "success" : source.kind === "trial" ? "warning" : "info";
  const ended = !source.effective_now;
  return (
    <Badge tone={ended ? "neutral" : tone}>
      {sourceLabel(source.kind)}
      {ended ? ", ended" : source.kind === "trial" && source.effective_to ? `, ends ${formatDate(source.effective_to)}` : ""}
    </Badge>
  );
}

function Holders({ code, name }: { code: string; name: string }) {
  const paged = usePaged((cursor) => adminApi.customers({ product: code, cursor }), [code]);
  const columns: Column<CustomerRow>[] = [
    {
      key: "customer",
      header: "Customer",
      value: (c) => c.display_name,
      render: (c) => (
        <Link href={`/admin/customers/${c.organization_id}`}>
          <Trunc>{c.display_name}</Trunc>
        </Link>
      ),
    },
    {
      key: "source",
      header: "Source",
      value: (c) => sourceLabel(c.product_source?.kind),
      render: (c) => <SourceBadge source={c.product_source} />,
    },
    { key: "access", header: "Access", value: (c) => c.access_class, render: (c) => (c.access_class ? sentence(c.access_class) : EMPTY) },
    {
      key: "subscription",
      header: "Subscription",
      value: (c) => c.subscription_status,
      render: (c) => (c.subscription_status ? <StatusBadge value={c.subscription_status} /> : "No subscription"),
    },
    { key: "seats", header: "Seats", align: "right", value: (c) => c.seat_capacity, render: (c) => c.seat_capacity ?? EMPTY },
  ];
  return (
    <DataTable
      caption={`Customers holding ${name}`}
      columns={columns}
      rows={paged.rows}
      rowKey={(c) => c.organization_id}
      rowHref={(c) => `/admin/customers/${c.organization_id}`}
      {...pagedProps(paged, "holders")}
      empty={
        <EmptyState icon="users" title="Nobody holds this product" hint="Customers appear here when a subscription, bundle, override or product trial gives them access." />
      }
    />
  );
}

// --- platform settings ---------------------------------------------------------------------------

type SettingsDraft = Pick<
  PlatformSettings,
  | "signup_enabled"
  | "partner_applications_enabled"
  | "support_email"
  | "sales_email"
  | "public_catalog_currency"
  | "company_name"
  | "company_legal_name"
  | "company_address"
  | "company_tax_id"
  | "notifications_email"
  | "partner_legacy_kyc_policy"
  | "partner_suspended_commission_policy"
> & { tax_rate: string };
type SectionKey = "company" | "signup" | "applications" | "partners" | "contact" | "billing" | "notifications";

export function PlatformSettingsScreen() {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminConsoleApi.settings(), []);
  const manage = can(permissions, "platform.staff_settings.manage");
  return (
    <Page>
      <PageHeader title="Platform settings" subtitle="Company details, billing, and the public site's switches and addresses. Every change is recorded in the audit log." />
      <LoadGate load={load} what="platform settings">
        {(s) => <SettingsSections settings={s} manage={manage} onSaved={reload} />}
      </LoadGate>
    </Page>
  );
}

/** A stored rate as the text its field starts with: 1250 is "12.5", none is empty. */
const taxRateText = (bp: number | null) => (bp === null ? "" : formatBasisPoints(bp).replace("%", ""));

const draftOf = (s: PlatformSettings): SettingsDraft => ({
  signup_enabled: s.signup_enabled,
  partner_applications_enabled: s.partner_applications_enabled,
  support_email: s.support_email,
  sales_email: s.sales_email,
  public_catalog_currency: s.public_catalog_currency,
  company_name: s.company_name,
  company_legal_name: s.company_legal_name,
  company_address: s.company_address,
  company_tax_id: s.company_tax_id,
  notifications_email: s.notifications_email,
  partner_legacy_kyc_policy: s.partner_legacy_kyc_policy ?? "allow",
  partner_suspended_commission_policy: s.partner_suspended_commission_policy ?? "accrue",
  tax_rate: taxRateText(s.tax_rate_bp),
});

/** The settings in force, one card per section, each edited in place (Save /
 * Cancel). Saving sends every setting with the version read: the server replaces
 * the whole row, so a field left out would be emptied, and two editors can't
 * silently overwrite each other. */
function SettingsSections({ settings, manage, onSaved }: { settings: PlatformSettings; manage: boolean; onSaved: () => void }) {
  const [editing, setEditing] = useState<SectionKey | null>(null);
  const [draft, setDraft] = useState<SettingsDraft>(() => draftOf(settings));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const ids = { signup: useId(), applications: useId() };
  const defaultCurrency = `Deployment default (${settings.default_currency})`;
  const defaultTax = `Deployment default (${formatBasisPoints(settings.default_tax_rate_bp)})`;

  const typedTax = draft.tax_rate.trim();
  const taxRateBp = typedTax ? toBasisPoints(typedTax) : null;
  const taxInvalid = Boolean(typedTax) && taxRateBp === null;

  const open = (key: SectionKey) => {
    setDraft(draftOf(settings));
    setError(null);
    setEditing(key);
  };

  const save = async () => {
    if (taxInvalid) return;
    setBusy(true);
    setError(null);
    try {
      await adminConsoleApi.saveSettings({
        signup_enabled: draft.signup_enabled,
        partner_applications_enabled: draft.partner_applications_enabled,
        partner_legacy_kyc_policy: draft.partner_legacy_kyc_policy,
        partner_suspended_commission_policy: draft.partner_suspended_commission_policy,
        support_email: draft.support_email.trim(),
        sales_email: draft.sales_email.trim(),
        public_catalog_currency: draft.public_catalog_currency,
        company_name: draft.company_name.trim(),
        company_legal_name: draft.company_legal_name.trim(),
        company_address: draft.company_address.trim(),
        company_tax_id: draft.company_tax_id.trim(),
        tax_rate_bp: taxRateBp,
        notifications_email: draft.notifications_email.trim(),
        version: settings.version,
      });
      setEditing(null);
      toast.success("Platform settings saved.");
      onSaved();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  const editButton = (key: SectionKey, label: string) =>
    manage && editing !== key ? (
      <Button size="sm" icon="edit" onClick={() => open(key)} disabled={editing !== null}>
        {label}
      </Button>
    ) : undefined;

  const notSet = (key: SectionKey, what: string) =>
    manage ? (
      <span className={local.holders}>
        <span className={local.note}>Not set</span>
        <Button size="sm" variant="link" onClick={() => open(key)} disabled={editing !== null} aria-label={`Add ${what}`}>
          Add
        </Button>
      </span>
    ) : (
      "Not set"
    );

  const footer = (
    <>
      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}
      <div className={local.actions}>
        <Button onClick={() => setEditing(null)} disabled={busy}>
          Cancel
        </Button>
        <Button variant="primary" loading={busy} disabled={taxInvalid} onClick={() => void save()}>
          Save
        </Button>
      </div>
    </>
  );

  return (
    <div className={local.stack}>
      <Card title="Company information" description="WorkEasy360's own business details." actions={editButton("company", "Edit company information")}>
        {editing === "company" ? (
          <>
            <div className={local.formGrid}>
              <TextField label="Company name" maxLength={200} value={draft.company_name} onChange={(v) => setDraft({ ...draft, company_name: v })} />
              <TextField label="Legal name" maxLength={200} value={draft.company_legal_name} onChange={(v) => setDraft({ ...draft, company_legal_name: v })} />
              <TextField label="Address" maxLength={500} hint="Saved as one line." value={draft.company_address} onChange={(v) => setDraft({ ...draft, company_address: v })} />
              <TextField label="Tax ID" maxLength={64} value={draft.company_tax_id} onChange={(v) => setDraft({ ...draft, company_tax_id: v })} />
            </div>
            {footer}
          </>
        ) : (
          <DescriptionList
            columns={2}
            items={[
              { label: "Company name", value: settings.company_name || notSet("company", "a company name") },
              { label: "Legal name", value: settings.company_legal_name || notSet("company", "a legal name") },
              { label: "Address", value: settings.company_address || notSet("company", "an address") },
              { label: "Tax ID", value: settings.company_tax_id || notSet("company", "a tax ID") },
            ]}
          />
        )}
      </Card>

      <Card title="Signup" description="Whether new people can create an account on the public site." actions={editButton("signup", "Edit signup")}>
        {editing === "signup" ? (
          <>
            <label className={local.check} htmlFor={ids.signup}>
              <input
                id={ids.signup}
                type="checkbox"
                checked={draft.signup_enabled}
                onChange={(e) => setDraft({ ...draft, signup_enabled: e.target.checked })}
              />
              Self-service signup is open
            </label>
            <p className={local.note}>Close it to stop new accounts during abuse; existing accounts are unaffected.</p>
            {footer}
          </>
        ) : (
          <DescriptionList items={[{ label: "Self-service signup", value: settings.signup_enabled ? "Open" : "Closed" }]} />
        )}
      </Card>

      <Card title="Applications" description="Whether people can apply to become a partner." actions={editButton("applications", "Edit applications")}>
        {editing === "applications" ? (
          <>
            <label className={local.check} htmlFor={ids.applications}>
              <input
                id={ids.applications}
                type="checkbox"
                checked={draft.partner_applications_enabled}
                onChange={(e) => setDraft({ ...draft, partner_applications_enabled: e.target.checked })}
              />
              Partner applications are open
            </label>
            <p className={local.note}>Close it to stop new applications; partners already approved are unaffected.</p>
            {footer}
          </>
        ) : (
          <DescriptionList items={[{ label: "Partner applications", value: settings.partner_applications_enabled ? "Open" : "Closed" }]} />
        )}
      </Card>

      <Card
        title="Partner policies"
        description="Two partner rules only an Admin decides (ADR-063). Both apply from the next request."
        actions={editButton("partners", "Edit partner policies")}
      >
        {editing === "partners" ? (
          <>
            <fieldset className={local.formGrid}>
              <legend>Partners active before KYC was required</legend>
              <label className={local.check}>
                <input
                  type="radio"
                  name="legacy-kyc"
                  checked={draft.partner_legacy_kyc_policy === "allow"}
                  onChange={() => setDraft({ ...draft, partner_legacy_kyc_policy: "allow" })}
                />
                Keep trading, labelled &quot;Active before KYC&quot;
              </label>
              <label className={local.check}>
                <input
                  type="radio"
                  name="legacy-kyc"
                  checked={draft.partner_legacy_kyc_policy === "restrict"}
                  onChange={() => setDraft({ ...draft, partner_legacy_kyc_policy: "restrict" })}
                />
                Restrict to onboarding until their KYC is approved
              </label>
            </fieldset>
            <p className={local.note}>
              Restricting pauses their referral links and attribution, customers, plan activation, the Platform Wallet and payout requests until
              their KYC is approved. No KYC approval is ever recorded for them automatically. Approving KYC needs a malware scanner in production.
            </p>
            <fieldset className={local.formGrid}>
              <legend>Commission while a partner is suspended</legend>
              <label className={local.check}>
                <input
                  type="radio"
                  name="suspended-commission"
                  checked={draft.partner_suspended_commission_policy === "accrue"}
                  onChange={() => setDraft({ ...draft, partner_suspended_commission_policy: "accrue" })}
                />
                Keep accruing; payouts stay on hold
              </label>
              <label className={local.check}>
                <input
                  type="radio"
                  name="suspended-commission"
                  checked={draft.partner_suspended_commission_policy === "stop"}
                  onChange={() => setDraft({ ...draft, partner_suspended_commission_policy: "stop" })}
                />
                Stop accruing new commission
              </label>
            </fieldset>
            {footer}
          </>
        ) : (
          <DescriptionList
            items={[
              {
                label: "Active before KYC",
                value: settings.partner_legacy_kyc_policy === "restrict" ? "Restricted until KYC is approved" : "Keep trading",
              },
              {
                label: "Suspended partners",
                value: settings.partner_suspended_commission_policy === "stop" ? "No new commission" : "Commission accrues; payouts on hold",
              },
            ]}
          />
        )}
      </Card>

      <Card title="Contact emails" description="Where the public site sends people who need help or want to buy." actions={editButton("contact", "Edit contact emails")}>
        {editing === "contact" ? (
          <>
            <div className={local.formGrid}>
              <TextField label="Support email" type="email" maxLength={254} value={draft.support_email} onChange={(v) => setDraft({ ...draft, support_email: v })} />
              <TextField label="Sales email" type="email" maxLength={254} value={draft.sales_email} onChange={(v) => setDraft({ ...draft, sales_email: v })} />
            </div>
            {footer}
          </>
        ) : (
          <DescriptionList
            columns={2}
            items={[
              { label: "Support email", value: settings.support_email || notSet("contact", "a support email") },
              { label: "Sales email", value: settings.sales_email || notSet("contact", "a sales email") },
            ]}
          />
        )}
      </Card>

      <Card title="Billing" description="The currency the public catalog shows prices in, and the tax rate on invoices." actions={editButton("billing", "Edit billing")}>
        {editing === "billing" ? (
          <>
            <div className={local.formGrid}>
              <Select
                label="Public catalog currency"
                showLabel
                fullWidth
                value={draft.public_catalog_currency}
                onChange={(v) => setDraft({ ...draft, public_catalog_currency: v })}
                options={[{ value: "", label: defaultCurrency }, ...settings.currencies.map((c) => ({ value: c, label: c }))]}
              />
              <TextField
                label="Tax rate (%)"
                inputMode="decimal"
                maxLength={7}
                value={draft.tax_rate}
                onChange={(v) => setDraft({ ...draft, tax_rate: v })}
                hint={`Left empty, ${formatBasisPoints(settings.default_tax_rate_bp)} applies, the deployment's default.`}
                error={taxInvalid ? "Enter a percentage from 0 to 100, with at most two decimals." : undefined}
              />
            </div>
            {footer}
          </>
        ) : (
          <DescriptionList
            columns={2}
            items={[
              { label: "Public catalog currency", value: settings.public_catalog_currency || defaultCurrency },
              { label: "Deployment default", value: settings.default_currency },
              { label: "Tax rate", value: settings.tax_rate_bp === null ? defaultTax : formatBasisPoints(settings.tax_rate_bp) },
            ]}
          />
        )}
      </Card>

      <Card title="Notifications" description="The address kept for platform notices to staff." actions={editButton("notifications", "Edit notifications")}>
        {editing === "notifications" ? (
          <>
            <div className={local.formGrid}>
              <TextField label="Notifications email" type="email" maxLength={254} value={draft.notifications_email} onChange={(v) => setDraft({ ...draft, notifications_email: v })} />
            </div>
            {footer}
          </>
        ) : (
          <DescriptionList items={[{ label: "Notifications email", value: settings.notifications_email || notSet("notifications", "a notifications email") }]} />
        )}
      </Card>
    </div>
  );
}

// --- staff roles -----------------------------------------------------------------------------------

export const ROLES = [
  { value: "SUPER_ADMIN", label: "Admin" },
  { value: "USER_ADMIN", label: "User Admin" },
  { value: "PRODUCT_ADMIN", label: "Product Admin" },
  { value: "BILLING_ADMIN", label: "Billing Admin" },
  { value: "SUPPORT_ADMIN", label: "Support Admin" },
  { value: "READ_ONLY_ADMIN", label: "Read-only Admin" },
];

/** Named roles over the one grant model: granting a role grants each of its
 * permissions; who holds a role is derived from the grants in force. */
export function StaffRolesPanel() {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminConsoleApi.roles(), []);
  const { ask, dialog: confirm } = useConfirm();
  const manage = can(permissions, "platform.staff_security.manage");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("SUPPORT_ADMIN");
  const [managing, setManaging] = useState<string | null>(null);

  const grant = manage ? (
    <FormPopup
      label="Grant a role"
      title="Grant a role"
      intro="Granting a role grants each of its permissions."
      size="sm"
      submitLabel="Grant role"
      canSubmit={Boolean(email.trim())}
      onOpen={() => {
        setEmail("");
        setRole(managing ?? "SUPPORT_ADMIN");
      }}
      onSubmit={async () => {
        await adminConsoleApi.grantRole({ email: email.trim(), role, reason: autoReason("Grant role") });
        return "Role granted.";
      }}
      onDone={reload}
    >
      <TextField label="Staff member's email" type="email" required value={email} onChange={setEmail} />
      <div className={local.field}>
        <Select label="Role" showLabel fullWidth value={role} onChange={setRole} options={ROLES} />
      </div>
    </FormPopup>
  ) : null;

  return (
    <TableSection title="Roles" description="Give each person the narrowest role that covers their work." actions={grant}>
      <LoadGate load={load} what="staff roles">
        {({ results }: { results: StaffRoleRow[] }) => (
          <>
            <DataTable
              caption="Roles and who holds them"
              columns={[
                { key: "role", header: "Role", value: (r) => r.name, render: (r) => <Stack main={r.name} sub={`${r.permissions.length} permissions`} /> },
                { key: "covers", header: "What it covers", wrap: true, render: (r) => r.description },
                {
                  key: "holders",
                  header: "Holders",
                  value: (r) => r.holders.length,
                  render: (r) =>
                    r.holders.length === 0 ? (
                      EMPTY
                    ) : (
                      <span className={local.holders}>
                        <span className={kit.avatarStack}>
                          {r.holders.slice(0, 4).map((h) => (
                            <Avatar key={h.user_id} name={h.email} />
                          ))}
                        </span>
                        <span className={local.note}>
                          {r.holders.length === 1 ? r.holders[0].email : `${r.holders.length} people`}
                        </span>
                        <span className="w360-visually-hidden">{r.holders.map((h) => h.email).join(", ")}</span>
                      </span>
                    ),
                },
                {
                  key: "manage",
                  header: "Manage",
                  align: "right",
                  render: (r) => (
                    <Button size="sm" onClick={() => setManaging(r.key)} aria-label={`Manage ${r.name}`}>
                      Manage
                    </Button>
                  ),
                },
              ]}
              rows={results}
              rowKey={(r) => r.key}
              pageSize={0}
              maxHeight={false}
              stickyFirst={false}
              empty={<EmptyState icon="shield" title="No roles" compact />}
            />
            {managing && (
              <RoleHolders
                role={results.find((r) => r.key === managing)}
                manage={manage}
                onClose={() => setManaging(null)}
                onRemove={(role, h) => {
                  setManaging(null);
                  ask({
                    title: `Remove ${role.name} from ${h.email}?`,
                    message: `${h.email} loses every permission this role grants, at once.`,
                    confirmLabel: `Remove ${role.name}`,
                    danger: true,
                    reason: true,
                    typeToConfirm: role.key === "SUPER_ADMIN" ? "REMOVE" : undefined,
                    onConfirm: async (reason) => {
                      await adminConsoleApi.revokeRole({ user_id: h.user_id, role: role.key, reason });
                      reload();
                      return `${role.name} removed.`;
                    },
                  });
                }}
              />
            )}
          </>
        )}
      </LoadGate>
      {confirm}
    </TableSection>
  );
}

/** One role's holders, each removable (confirmed; Super Admin asks to type REMOVE). */
function RoleHolders({
  role,
  manage,
  onClose,
  onRemove,
}: {
  role: StaffRoleRow | undefined;
  manage: boolean;
  onClose: () => void;
  onRemove: (role: StaffRoleRow, holder: { user_id: string; email: string }) => void;
}) {
  const titleId = useId();
  if (!role) return null;
  return (
    <Modal labelledBy={titleId} onClose={onClose} size="md">
      <DialogHead titleId={titleId} title={role.name} onClose={onClose} />
      <div className={dialog.body}>
        <p className={dialog.intro}>{role.description}</p>
        {role.holders.length === 0 ? (
          <EmptyState icon="users" title="Nobody holds this role" compact />
        ) : (
          <ul className={local.orgList} aria-label={`Holders of ${role.name}`}>
            {role.holders.map((h) => (
              <li key={h.user_id} className={local.orgItem}>
                <span className={local.holders}>
                  <Avatar name={h.email} />
                  {h.email}
                </span>
                {manage && (
                  <Button size="sm" variant="danger" onClick={() => onRemove(role, h)} aria-label={`Remove ${role.name} from ${h.email}`}>
                    Remove
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className={dialog.foot}>
        <Button onClick={onClose}>Done</Button>
      </div>
    </Modal>
  );
}
