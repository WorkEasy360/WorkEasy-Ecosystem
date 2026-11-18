"use client";

import { useState } from "react";
import Link from "next/link";
import { adminApi } from "@/lib/api/controlPlane";
import { customersApi, type SetupRecordRow, type SetupRequirementRow } from "@/lib/api/adminPanel";
import { TableSection } from "./AdminCommerce";
import { OffsetPager } from "./AdminListParts";
import { can, useAdmin } from "./ControlShell";
import { NEW_REQUIREMENT, RequirementFields, requirementBody, requirementDraft, requirementIsValid, tableState } from "./kycParts";
import { FormDialog } from "./Modal";
import { useLoad } from "./ui";
import { TextAreaField } from "./widgets";
import {
  Alert,
  Badge,
  BoolBadge,
  Button,
  DataTable,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  Select,
  Stack,
  StatusChips,
  Trunc,
  EMPTY,
  droppedMessage,
  formatDateTime,
  productName,
  sentence,
  useToast,
  useUrlFilters,
  type ActiveFilter,
  type Column,
  type Tone,
} from "./kit";
import styles from "./AdminKyc.module.css";

/**
 * Admin → Customers → Setup (2026-11-09): what a customer must tell us before a product
 * is fully set up. **A state of its own:** setup never changes a subscription's status
 * and a subscription never waits on it. What each product asks for is data an Admin
 * enters; with none, the product needs no setup and no record appears.
 */

const PAGE_SIZE = 25;
const MIN_REASON = 10;

const SETUP_STATUS: Record<SetupRecordRow["status"], { label: string; tone: Tone }> = {
  pending: { label: "Pending setup", tone: "warning" },
  submitted: { label: "Submitted", tone: "info" },
  correction_required: { label: "Correction required", tone: "warning" },
  approved: { label: "Approved", tone: "success" },
};

const STATUS_KEYS = Object.keys(SETUP_STATUS) as SetupRecordRow["status"][];

/** The queue's filters, kept in its address (2026-11-18, admin-ux phase 3). The
 * product is checked by the server: the catalog may not be readable here. */
const SETUP_FILTERS = {
  status: { options: ["", ...STATUS_KEYS] },
  product: { maxLength: 64 },
};

type Decision = { record: SetupRecordRow; decision: "approve" | "request_correction" };

export function CustomerSetupScreen() {
  const { permissions } = useAdmin();
  const decides = can(permissions, "platform.staff_subscription.manage");
  const managesRequirements = can(permissions, "platform.staff_catalog.manage");
  const readsRequirements = managesRequirements || can(permissions, "platform.staff_catalog.view");
  const toast = useToast();
  const filters = useUrlFilters(SETUP_FILTERS);
  const { status, product } = filters.values;
  // The page of the list: a new filter (here or from the address) starts again on the first.
  const filterKey = `${status}|${product}`;
  const [paging, setPaging] = useState({ key: filterKey, offset: 0 });
  const offset = paging.key === filterKey ? paging.offset : 0;
  const setOffset = (next: number) => setPaging({ key: filterKey, offset: next });
  // The last answer: the chips and the pager stay put, and keep focus, while the next one loads.
  const [seen, setSeen] = useState<{ counts: Record<string, number>; total: number; limit: number; shown: number } | null>(null);
  const [deciding, setDeciding] = useState<Decision | null>(null);
  const [products] = useLoad(() => adminApi.products(), []);
  const [load, reload] = useLoad(async () => {
    const page = await customersApi.setup({ status: status || undefined, product: product || undefined, limit: PAGE_SIZE, offset });
    setSeen({ counts: page.counts, total: page.total, limit: page.limit, shown: page.results.length });
    return page;
  }, [status, product, offset]);
  const page = load.status === "ready" ? load.data : null;
  const catalog = products.status === "ready" ? products.data.results : [];
  const productLabel = (code: string) => {
    const found = catalog.find((p) => p.code === code);
    return found ? productName(found.name) : sentence(code);
  };

  const columns: Column<SetupRecordRow>[] = [
    {
      key: "organization",
      header: "Organization",
      value: (r) => r.organization_name,
      render: (r) => (
        <Link href={`/admin/customers/${r.organization_id}`}>
          <Trunc>{r.organization_name || r.organization_id}</Trunc>
        </Link>
      ),
    },
    { key: "product", header: "Product", priority: "high", value: (r) => r.product_code, render: (r) => productLabel(r.product_code) },
    {
      key: "status",
      header: "Setup status",
      priority: "essential",
      value: (r) => r.status,
      render: (r) => (
        <Stack
          main={
            <Badge tone={SETUP_STATUS[r.status]?.tone ?? "neutral"} dot>
              {SETUP_STATUS[r.status]?.label ?? sentence(r.status)}
            </Badge>
          }
          sub={r.decided_at ? `${r.decided_by_name || "Decided"}, ${formatDateTime(r.decided_at)}` : undefined}
        />
      ),
    },
    {
      key: "answers",
      header: "Answers",
      wrap: true,
      priority: "medium",
      render: (r) => <Answers record={r} requirements={page?.requirements ?? []} />,
    },
    { key: "reason", header: "Reason", wrap: true, priority: "low", value: (r) => r.decision_reason, render: (r) => r.decision_reason || EMPTY },
  ];

  // The decisions, in the Actions column (admin-ux phase 3): only on a submitted
  // record, and only for staff who may decide.
  const actions = (r: SetupRecordRow) =>
    decides && r.status === "submitted" ? (
      <>
        <Button
          size="sm"
          variant="primary"
          icon="check"
          onClick={() => setDeciding({ record: r, decision: "approve" })}
          aria-label={`Approve ${r.organization_name} ${productLabel(r.product_code)}`}
        >
          Approve
        </Button>
        <Button
          size="sm"
          icon="edit"
          onClick={() => setDeciding({ record: r, decision: "request_correction" })}
          aria-label={`Request correction from ${r.organization_name} for ${productLabel(r.product_code)}`}
        >
          Request correction
        </Button>
      </>
    ) : null;

  const counts = seen?.counts;
  const allCount = counts ? Object.values(counts).reduce((sum, n) => sum + n, 0) : undefined;
  const dropped = droppedMessage(filters.dropped);
  // The applied filters, as tags (the status is the chip row above).
  const active: ActiveFilter[] = product
    ? [{ key: "product", label: "Product", value: productLabel(product), onRemove: () => filters.set({ product: "" }) }]
    : [];
  return (
    <Page>
      <PageHeader title="Setup" subtitle="What customers must tell us before a product is fully set up." />
      <Alert tone="info">
        Setup is separate from subscription status. Approving or correcting setup never changes a subscription, and a subscription never waits on setup.
      </Alert>
      {dropped && <Alert tone="warning" title={dropped} />}
      <StatusChips
        label="Setup status"
        value={status}
        onChange={(next) => filters.set({ status: next })}
        // The counts are of every record: with a product chosen they would not match the rows.
        chips={[
          { value: "", label: "All", count: product ? undefined : allCount },
          ...STATUS_KEYS.map((key) => ({
            value: key,
            label: SETUP_STATUS[key].label,
            count: counts && !product ? (counts[key] ?? 0) : undefined,
          })),
        ]}
      />
      {(catalog.length > 0 || product) && (
        <FilterBar label="Filter setup records" active={active} onClear={product ? () => filters.set({ product: "" }) : undefined}>
          {catalog.length > 0 && (
            <Select
              label="Product"
              prefix="Product:"
              value={product}
              onChange={(next) => filters.set({ product: next })}
              options={[
                { value: "", label: "All products" },
                ...catalog.map((p) => ({ value: p.code, label: productName(p.name) })),
                // A product from the address that the catalog does not list stays shown.
                ...(product && !catalog.some((p) => p.code === product) ? [{ value: product, label: sentence(product) }] : []),
              ]}
            />
          )}
        </FilterBar>
      )}
      <DataTable
        caption="Customer setup"
        columns={columns}
        rows={page?.results ?? []}
        rowKey={(r) => r.id}
        rowLabel={(r) => `${r.organization_name || r.organization_id}, ${productLabel(r.product_code)}`}
        actions={decides ? actions : undefined}
        pageSize={0}
        hideEmptyColumns={false}
        {...tableState(load, "customer setup")}
        onRetry={load.status === "error" ? reload : undefined}
        empty={
          <EmptyState
            icon="check"
            title="No setup records"
            hint={status || product ? "Try another status or product." : "A record appears when a customer holds a product that has setup requirements."}
          />
        }
      />
      {seen && <OffsetPager total={seen.total} limit={seen.limit} offset={offset} shown={seen.shown} onOffset={setOffset} />}
      {readsRequirements && (
        <SetupRequirements
          manage={managesRequirements}
          products={catalog}
          productLabel={productLabel}
          onChanged={(message) => {
            toast.success(message);
            // A new requirement can open records for customers who already hold the product.
            reload();
          }}
        />
      )}
      {deciding && (
        <DecisionDialog
          decision={deciding}
          productLabel={productLabel(deciding.record.product_code)}
          onClose={() => setDeciding(null)}
          onDone={(message) => {
            if (message) toast.success(message);
            reload();
          }}
        />
      )}
    </Page>
  );
}

function Answers({ record, requirements }: { record: SetupRecordRow; requirements: SetupRequirementRow[] }) {
  const entries = Object.entries(record.answers).filter(([, value]) => value);
  if (entries.length === 0) return <span className={styles.note}>Nothing sent yet</span>;
  const label = (key: string) => requirements.find((r) => r.product_code === record.product_code && r.key === key)?.label ?? sentence(key);
  return (
    <dl className={styles.answers}>
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{label(key)}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function DecisionDialog({
  decision: { record, decision },
  productLabel,
  onDone,
  onClose,
}: {
  decision: Decision;
  productLabel: string;
  onDone: (message: string | undefined) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const approving = decision === "approve";
  return (
    <FormDialog
      title={approving ? "Approve setup" : "Request a correction"}
      intro={`${record.organization_name}, ${productLabel}. This changes setup only: the subscription stays as it is.`}
      size="sm"
      submitLabel={approving ? "Approve" : "Request correction"}
      canSubmit={approving || reason.trim().length >= MIN_REASON}
      onSubmit={async () => {
        await customersApi.decideSetup(record.id, { decision, reason: reason.trim() || undefined, expected_version: record.version });
        return approving ? "Setup approved." : "Correction requested. The customer can answer again.";
      }}
      onDone={onDone}
      onClose={onClose}
    >
      <TextAreaField
        label={approving ? "Reason (optional)" : "What to correct"}
        maxLength={500}
        value={reason}
        onChange={setReason}
        hint={approving ? undefined : `At least ${MIN_REASON} characters. The customer reads this.`}
      />
    </FormDialog>
  );
}

// --- what a product asks for -----------------------------------------------------------------------

function SetupRequirements({
  manage,
  products,
  productLabel,
  onChanged,
}: {
  manage: boolean;
  products: { code: string; name: string }[];
  productLabel: (code: string) => string;
  onChanged: (message: string) => void;
}) {
  const [load, reload] = useLoad(() => customersApi.setupRequirements(), []);
  const [editing, setEditing] = useState<SetupRequirementRow | "new" | null>(null);

  const columns: Column<SetupRequirementRow>[] = [
    { key: "product", header: "Product", value: (r) => r.product_code, render: (r) => productLabel(r.product_code) },
    { key: "label", header: "Label", value: (r) => r.label, render: (r) => <Trunc>{r.label}</Trunc> },
    { key: "help", header: "Help text", wrap: true, value: (r) => r.help_text, render: (r) => r.help_text || EMPTY },
    {
      key: "required",
      header: "Required",
      value: (r) => (r.is_required ? "Required" : "Optional"),
      render: (r) => <BoolBadge value={r.is_required} yes="Required" no="Optional" noTone="neutral" />,
    },
    {
      key: "active",
      header: "Active",
      value: (r) => (r.is_active ? "Active" : "Inactive"),
      render: (r) => <BoolBadge value={r.is_active} yes="Active" no="Inactive" noTone="neutral" />,
    },
    { key: "order", header: "Order", align: "right", value: (r) => r.display_order, render: (r) => r.display_order },
  ];

  return (
    <TableSection
      title="Setup requirements"
      description="What each product asks a customer for. With none, a product needs no setup."
      actions={
        manage ? (
          <Button icon="plus" onClick={() => setEditing("new")}>
            Add requirement
          </Button>
        ) : undefined
      }
    >
      <DataTable
        caption="Setup requirements"
        columns={columns}
        rows={load.status === "ready" ? load.data.results : []}
        rowKey={(r) => r.id}
        rowLabel={(r) => r.label}
        // Edit, in the Actions column (admin-ux phase 3), for those who may change requirements.
        actions={
          manage
            ? (r) => (
                <Button size="sm" icon="edit" onClick={() => setEditing(r)} aria-label={`Edit ${r.label}`}>
                  Edit
                </Button>
              )
            : undefined
        }
        pageSize={0}
        maxHeight={false}
        hideEmptyColumns={false}
        {...tableState(load, "setup requirements")}
        onRetry={load.status === "error" ? reload : undefined}
        empty={<EmptyState icon="file" title="No setup requirements" hint="With none entered, no product needs setup and no record appears." compact />}
      />
      {editing && (
        <SetupRequirementDialog
          requirement={editing === "new" ? null : editing}
          products={products}
          onClose={() => setEditing(null)}
          onDone={(message) => {
            reload();
            onChanged(message ?? "Requirement saved.");
          }}
        />
      )}
    </TableSection>
  );
}

function SetupRequirementDialog({
  requirement,
  products,
  onDone,
  onClose,
}: {
  requirement: SetupRequirementRow | null;
  products: { code: string; name: string }[];
  onDone: (message: string | undefined) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(() => (requirement ? requirementDraft(requirement) : NEW_REQUIREMENT));
  const [productCode, setProductCode] = useState(requirement?.product_code ?? "");
  const options = products.map((p) => ({ value: p.code, label: productName(p.name) }));
  return (
    <FormDialog
      title={requirement ? `Edit ${requirement.label}` : "Add a setup requirement"}
      size="sm"
      submitLabel={requirement ? "Save" : "Add requirement"}
      canSubmit={requirementIsValid(draft) && productCode !== ""}
      onSubmit={async () => {
        const body = { product_code: productCode, ...requirementBody(draft) };
        if (requirement) await customersApi.updateSetupRequirement(requirement.id, body);
        else await customersApi.createSetupRequirement(body);
        return requirement ? "Requirement saved." : "Requirement added.";
      }}
      onDone={onDone}
      onClose={onClose}
    >
      <Select
        label="Product"
        showLabel
        fullWidth
        value={productCode}
        onChange={setProductCode}
        // A requirement's own product stays choosable even if the list did not load.
        options={[
          { value: "", label: "Choose a product" },
          ...options,
          ...(requirement && !options.some((o) => o.value === requirement.product_code) ? [{ value: requirement.product_code, label: sentence(requirement.product_code) }] : []),
        ]}
      />
      <RequirementFields draft={draft} onChange={setDraft} />
    </FormDialog>
  );
}
