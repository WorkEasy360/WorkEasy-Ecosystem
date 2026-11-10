"use client";

import { useMemo, useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import { customersApi } from "@/lib/api/adminPanel";
import { adminConsoleApi, INTERVAL_LABELS, type CatalogOverview } from "@/lib/api/adminConsole";
import { tableState } from "./kycParts";
import { FormDialog, FormPopup } from "./Modal";
import { LoadGate, useLoad } from "./ui";
import { SUPPORTED_CURRENCIES } from "./amounts";
import { SelectField, TextAreaField, TextField } from "./widgets";
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  Select,
  StatusBadge,
  Trunc,
  EMPTY,
  formatDate,
  formatMoney,
  productName,
  sentence,
} from "./kit";

/**
 * What an Admin does for a customer beyond reading it (2026-11-09): create one, see its
 * members and its assignment history, assign a plan. The sections load when they are
 * opened, so the customer's page asks for nothing more until someone wants it.
 */

const MIN_REASON = 10;
const MAX_QUANTITY = 100_000;

// --- a new customer --------------------------------------------------------------------------

export function NewCustomerPopup({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [reason, setReason] = useState("");
  const emailLooksRight = /^[^\s@]+@[^\s@]+$/.test(ownerEmail.trim());
  return (
    <FormPopup
      label={
        <>
          <Icon name="plus" size={16} />
          New customer
        </>
      }
      title="New customer"
      intro="An organization with a named owner. An address already registered becomes the owner straight away; a new one is emailed a link to set a password. You never choose a password for anyone."
      size="sm"
      submitLabel="Create customer"
      canSubmit={name.trim() !== "" && ownerName.trim() !== "" && emailLooksRight && reason.trim().length >= MIN_REASON}
      onOpen={() => {
        setName("");
        setOwnerName("");
        setOwnerEmail("");
        setReason("");
      }}
      onSubmit={async () => {
        const created = await customersApi.create({
          name: name.trim(),
          owner_name: ownerName.trim(),
          owner_email: ownerEmail.trim(),
          reason: reason.trim(),
        });
        return created.owner_invited
          ? "Customer created. The owner was emailed a link to set a password."
          : "Customer created. That address already had an account, so it owns the organization now and no invitation was sent.";
      }}
      onDone={onCreated}
    >
      <TextField label="Organization name" required maxLength={150} value={name} onChange={setName} />
      <TextField label="Owner's name" required maxLength={200} value={ownerName} onChange={setOwnerName} />
      <TextField label="Owner's email" type="email" required maxLength={254} value={ownerEmail} onChange={setOwnerEmail} />
      <TextAreaField label="Reason" maxLength={200} rows={2} value={reason} onChange={setReason} hint={`At least ${MIN_REASON} characters. Kept in the audit log.`} />
    </FormPopup>
  );
}

// --- members and history, on demand ------------------------------------------------------------

function OnDemandCard({
  title,
  description,
  showLabel,
  hideLabel,
  children,
}: {
  title: string;
  description: string;
  showLabel: string;
  hideLabel: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Card
      title={title}
      description={description}
      actions={
        <Button size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? hideLabel : showLabel}
        </Button>
      }
    >
      {open ? children : null}
    </Card>
  );
}

export function CustomerMembersCard({ organizationId }: { organizationId: string }) {
  return (
    <OnDemandCard title="Members" description="Who belongs to this customer and in what standing." showLabel="Show members" hideLabel="Hide members">
      <MembersTable organizationId={organizationId} />
    </OnDemandCard>
  );
}

function MembersTable({ organizationId }: { organizationId: string }) {
  const [load] = useLoad(() => customersApi.members(organizationId), [organizationId]);
  return (
    <DataTable
      caption="Members of this customer"
      columns={[
        { key: "name", header: "Name", value: (m) => m.name, render: (m) => <Trunc>{m.name || m.email}</Trunc> },
        { key: "email", header: "Email", value: (m) => m.email, render: (m) => <Trunc>{m.email}</Trunc> },
        { key: "status", header: "Status", value: (m) => m.status, render: (m) => <StatusBadge value={m.status} /> },
        {
          key: "owner",
          header: "Owner",
          value: (m) => (m.is_owner ? "Owner" : ""),
          render: (m) => (m.is_owner ? <Badge tone="brand">Owner</Badge> : EMPTY),
        },
        { key: "joined", header: "Joined", value: (m) => m.joined_at, render: (m) => formatDate(m.joined_at) },
      ]}
      rows={load.status === "ready" ? load.data.results : []}
      rowKey={(m) => m.membership_id}
      pageSize={0}
      maxHeight={false}
      hideEmptyColumns={false}
      {...tableState(load, "this customer's members")}
      empty={<EmptyState icon="users" title="No members" compact />}
    />
  );
}

/** `refresh` changes when a plan is assigned, so an open history shows the new line. */
export function AssignmentHistoryCard({ organizationId, refresh }: { organizationId: string; refresh: number }) {
  return (
    <OnDemandCard title="Assignment history" description="Every plan this customer has held, newest first, and when each ended." showLabel="Show history" hideLabel="Hide history">
      <HistoryTable organizationId={organizationId} refresh={refresh} />
    </OnDemandCard>
  );
}

function HistoryTable({ organizationId, refresh }: { organizationId: string; refresh: number }) {
  const [load] = useLoad(() => customersApi.history(organizationId), [organizationId, refresh]);
  return (
    <DataTable
      caption="Assignment history"
      columns={[
        { key: "product", header: "Product", value: (a) => a.product_code, render: (a) => sentence(a.product_code) },
        { key: "plan", header: "Plan", value: (a) => a.plan_code || a.bundle_code, render: (a) => sentence(a.plan_code || a.bundle_code) },
        { key: "quantity", header: "Quantity", align: "right", value: (a) => a.quantity, render: (a) => a.quantity },
        {
          key: "amount",
          header: "Amount",
          align: "right",
          value: (a) => a.total_amount_minor,
          render: (a) => formatMoney(a.total_amount_minor, a.currency),
        },
        { key: "interval", header: "Interval", value: (a) => a.interval, render: (a) => (a.interval ? (INTERVAL_LABELS[a.interval] ?? sentence(a.interval)) : EMPTY) },
        { key: "status", header: "Subscription", value: (a) => a.subscription_status, render: (a) => <StatusBadge value={a.subscription_status} /> },
        { key: "added", header: "Added", value: (a) => a.added_at, render: (a) => formatDate(a.added_at) },
        { key: "removed", header: "Ended", value: (a) => a.removed_at, render: (a) => (a.removed_at ? formatDate(a.removed_at) : EMPTY) },
      ]}
      rows={load.status === "ready" ? load.data.results : []}
      rowKey={(a) => a.item_id}
      pageSize={0}
      maxHeight={false}
      hideEmptyColumns={false}
      {...tableState(load, "this customer's assignment history")}
      empty={<EmptyState icon="card" title="No plan has been assigned" compact />}
    />
  );
}

// --- assigning a plan --------------------------------------------------------------------------

/** The plans a customer could buy now, each at its version in force. The server checks
 * again and has the last word. */
function assignablePlans(overview: CatalogOverview, now: number) {
  return overview.plans
    .filter((plan) => plan.status === "available" && plan.is_public)
    .flatMap((plan) => {
      const version = plan.versions
        .filter((v) => v.published_at && Date.parse(v.effective_from) <= now && (!v.effective_to || Date.parse(v.effective_to) > now))
        .sort((a, b) => b.version - a.version)[0];
      if (!version) return [];
      const priced = new Set(version.prices.filter((p) => !p.partner_id).map((p) => p.interval));
      const intervals = overview.intervals.filter((interval) => priced.size === 0 || priced.has(interval));
      return [
        {
          value: version.id,
          label: `${productName(plan.product.name)}: ${plan.name}${plan.kind === "add_on" ? " (add-on)" : ""}`,
          intervals: intervals as string[],
        },
      ];
    });
}

export function AssignPlanPopup({ organizationId, onAssigned }: { organizationId: string; onAssigned: (message: string | undefined) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button icon="plus" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        Assign plan
      </Button>
      {open && <AssignPlanDialog organizationId={organizationId} onDone={onAssigned} onClose={() => setOpen(false)} />}
    </>
  );
}

function AssignPlanDialog({
  organizationId,
  onDone,
  onClose,
}: {
  organizationId: string;
  onDone: (message: string | undefined) => void;
  onClose: () => void;
}) {
  // One order per opening: a double click or a retry after a failure replays this id
  // instead of placing a second order.
  const [commandId] = useState(() => crypto.randomUUID());
  const [now] = useState(() => Date.now());
  const [catalog] = useLoad(() => adminConsoleApi.catalogOverview(), []);
  const [chosen, setChosen] = useState("");
  const [pickedInterval, setPickedInterval] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [currency, setCurrency] = useState("");
  const [reason, setReason] = useState("");
  const plans = useMemo(() => (catalog.status === "ready" ? assignablePlans(catalog.data, now) : []), [catalog, now]);
  const plan = plans.find((p) => p.value === chosen) ?? plans[0];
  const billing = plan?.intervals.includes(pickedInterval) ? pickedInterval : (plan?.intervals[0] ?? "");
  const count = /^\d{1,6}$/.test(quantity.trim()) ? Number(quantity.trim()) : 0;
  const countIsValid = count >= 1 && count <= MAX_QUANTITY;

  return (
    <FormDialog
      title="Assign plan"
      intro="This places the order the customer would have placed. A paid plan gets an invoice that must be paid; nothing is granted before that."
      size="sm"
      submitLabel="Assign plan"
      canSubmit={Boolean(plan) && billing !== "" && countIsValid && reason.trim().length >= MIN_REASON}
      onSubmit={async () => {
        const placed = await customersApi.assignPlan(
          organizationId,
          { plan_version_id: plan!.value, billing_interval: billing, quantity: count, reason: reason.trim(), ...(currency ? { currency } : {}) },
          commandId,
        );
        return placed.requires_payment
          ? `Order ${placed.number} placed. Its invoice now waits for payment.`
          : `Order ${placed.number} placed. Nothing waits for payment.`;
      }}
      onDone={onDone}
      onClose={onClose}
    >
      <LoadGate load={catalog} what="the catalog">
        {() =>
          plans.length === 0 ? (
            <EmptyState icon="box" title="No plan is on sale" hint="Publish a public plan first." compact />
          ) : (
            <>
              <Select label="Plan" showLabel fullWidth value={plan?.value ?? ""} onChange={setChosen} options={plans.map(({ value, label }) => ({ value, label }))} />
              <Select
                label="Interval"
                showLabel
                fullWidth
                value={billing}
                onChange={setPickedInterval}
                options={(plan?.intervals ?? []).map((i) => ({ value: i, label: INTERVAL_LABELS[i] ?? sentence(i) }))}
              />
            </>
          )
        }
      </LoadGate>
      <TextField
        label="Quantity"
        inputMode="numeric"
        required
        value={quantity}
        onChange={setQuantity}
        error={countIsValid ? undefined : `Enter a whole number from 1 to ${MAX_QUANTITY.toLocaleString("en-IN")}.`}
      />
      <SelectField
        label="Billing currency"
        value={currency}
        onChange={setCurrency}
        options={[{ value: "", label: "The platform's currency" }, ...SUPPORTED_CURRENCIES.map((code) => ({ value: code, label: code }))]}
        hint="Used only if this customer has no billing account yet. An existing account keeps its currency."
      />
      <TextAreaField label="Reason" maxLength={200} rows={2} value={reason} onChange={setReason} hint={`At least ${MIN_REASON} characters. Kept in the audit log.`} />
    </FormDialog>
  );
}

// --- the customer's partner (2026-11-10) -------------------------------------------------------

const END_REASONS: Record<string, string> = {
  partner_changed_by_staff: "Changed by an Admin",
  ended_by_staff: "Ended by an Admin",
  partner_terminated: "Partner terminated",
};

/** Assign a partner, change it, and every relationship this customer has had. A change
 * ends the current relationship and starts the new one; past commissions stay with the
 * partner of the relationship live at the time of each sale. */
export function PartnerAssignmentCard({ organizationId, onChanged }: { organizationId: string; onChanged?: () => void }) {
  const [refresh, setRefresh] = useState(0);
  const [load] = useLoad(() => customersApi.partner(organizationId), [organizationId, refresh]);
  const data = load.status === "ready" ? load.data : null;
  const action =
    data?.may_manage ? (
      <PartnerPicker
        organizationId={organizationId}
        current={data.current}
        onDone={() => {
          setRefresh((n) => n + 1);
          onChanged?.();
        }}
      />
    ) : undefined;
  return (
    <Card
      title="Partner"
      description="The partner that serves this customer. Changing it never moves commissions already earned."
      actions={action}
    >
      <p>
        {data?.current ? (
          <>
            <strong>{data.current.partner_name}</strong> <Trunc>{data.current.partner_code}</Trunc> <StatusBadge value={data.current.status} />{" "}
            since {formatDate(data.current.started_at)}
          </>
        ) : data ? (
          "No partner is assigned."
        ) : null}
      </p>
      <DataTable
        caption="Partner assignment history"
        columns={[
          { key: "partner", header: "Partner", value: (r) => r.partner_name, render: (r) => <Trunc>{r.partner_name}</Trunc> },
          { key: "status", header: "Status", value: (r) => r.status, render: (r) => <StatusBadge value={r.status} /> },
          { key: "started", header: "Started", value: (r) => r.started_at, render: (r) => formatDate(r.started_at) },
          { key: "by", header: "Assigned by", value: (r) => r.started_by_name, render: (r) => r.started_by_name || EMPTY },
          { key: "ended", header: "Ended", value: (r) => r.ended_at, render: (r) => (r.ended_at ? formatDate(r.ended_at) : EMPTY) },
          {
            key: "why",
            header: "Why it ended",
            value: (r) => r.end_reason,
            render: (r) => (r.end_reason ? (END_REASONS[r.end_reason] ?? sentence(r.end_reason)) : EMPTY),
          },
        ]}
        rows={data?.history ?? []}
        rowKey={(r) => r.link_id}
        pageSize={0}
        maxHeight={false}
        hideEmptyColumns={false}
        {...tableState(load, "this customer's partner history")}
        empty={<EmptyState icon="handshake" title="This customer has never had a partner" compact />}
      />
    </Card>
  );
}

function PartnerPicker({
  organizationId,
  current,
  onDone,
}: {
  organizationId: string;
  current: { partner_id: string; partner_name: string } | null;
  onDone: () => void;
}) {
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState("");
  const [reason, setReason] = useState("");
  const [options] = useLoad(() => customersApi.assignablePartners(query), [query]);
  const list = options.status === "ready" ? options.data.results.filter((p) => p.id !== current?.partner_id) : [];
  const verb = current ? "Change partner" : "Assign partner";
  return (
    <FormPopup
      label={verb}
      buttonClassName="w360-btn w360-btn-secondary"
      title={verb}
      intro={
        current
          ? `${current.partner_name} stops serving this customer and the partner you choose starts. Commissions already earned stay where they are.`
          : "The partner you choose starts serving this customer. Only active partners that can serve customers are listed."
      }
      submitLabel={verb}
      onOpen={() => {
        setChosen("");
        setReason("");
        setQuery("");
      }}
      // A partner and a reason of at least MIN_REASON characters; the server checks again.
      canSubmit={Boolean(chosen) && reason.trim().length >= MIN_REASON}
      onSubmit={async () => {
        await customersApi.assignPartner(organizationId, { partner_id: chosen, reason: reason.trim() });
        return current ? "Partner changed." : "Partner assigned.";
      }}
      onDone={onDone}
    >
      <TextField label="Find a partner" value={query} onChange={setQuery} placeholder="Name or code" maxLength={80} />
      <SelectField
        label="Partner"
        value={chosen}
        onChange={setChosen}
        required
        showRequired
        options={[{ value: "", label: options.status === "loading" ? "Loading…" : list.length ? "Select…" : "No matching partner" }, ...list.map((p) => ({ value: p.id, label: `${p.display_name} (${p.code})` }))]}
      />
      <TextAreaField
        label="Reason"
        value={reason}
        onChange={setReason}
        maxLength={500}
        hint={`At least ${MIN_REASON} characters. Recorded in the audit log.`}
      />
    </FormPopup>
  );
}

