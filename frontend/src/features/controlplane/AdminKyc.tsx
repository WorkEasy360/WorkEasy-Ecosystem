"use client";

import { useState } from "react";
import Link from "next/link";
import { adminApi } from "@/lib/api/controlPlane";
import {
  kycApi,
  type KycApplicationDetail,
  type KycDecision,
  type KycDocument,
  type KycQueueRow,
  type KycRequirement,
} from "@/lib/api/adminPanel";
import { can, useAdmin } from "./ControlShell";
import { OffsetPager } from "./AdminListParts";
import { countryName } from "./countries";
import {
  BUSINESS_FIELDS,
  EXPERIENCES,
  KycBadge,
  NEW_REQUIREMENT,
  RequirementFields,
  experienceLabel,
  fileSize,
  kycLabel,
  requirementBody,
  requirementDraft,
  requirementIsValid,
  tableState,
} from "./kycParts";
import { FormDialog } from "./Modal";
import { LoadGate, describeError, useLoad } from "./ui";
import { TextAreaField } from "./widgets";
import {
  Alert,
  Badge,
  BoolBadge,
  Button,
  Card,
  DataTable,
  DescriptionList,
  EmptyState,
  FilterBar,
  KeyText,
  Page,
  PageHeader,
  SearchInput,
  Select,
  Stack,
  StatusBadge,
  StatusChips,
  Trunc,
  EMPTY,
  formatDate,
  formatDateTime,
  sentence,
  statusLabel,
  useConfirm,
  useToast,
  type Column,
} from "./kit";
import styles from "./AdminKyc.module.css";

/**
 * Admin → Partners → Applications and KYC requirements (2026-11-09). **Two status
 * systems, on purpose:** KYC says where an application stands, the partner account says
 * whether the partner may operate, and every screen here names which is which. Who may
 * do what comes from the server: `may_review` and `may_decide` are its answer.
 */

const PAGE_SIZE = 25;
const MIN_REASON = 10;

// --- the queue -------------------------------------------------------------------------------

const QUEUE_CHIPS = [
  { value: "", label: "Waiting for review" },
  { value: "all", label: "All" },
  { value: "draft", label: "Draft" },
  { value: "submitted", label: "Submitted" },
  { value: "under_review", label: "Under review" },
  { value: "correction_required", label: "Correction required" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
];

function chipCount(value: string, counts: Record<string, number>): number {
  if (value === "") return (counts.submitted ?? 0) + (counts.under_review ?? 0);
  if (value === "all") return Object.values(counts).reduce((sum, n) => sum + n, 0);
  return counts[value] ?? 0;
}

const applicationHref = (partnerId: string) => `/admin/partners/applications/${partnerId}`;

export function KycApplicationsScreen() {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  // The last answer: the chips and the pager stay put, and keep focus, while the next one loads.
  const [seen, setSeen] = useState<{
    counts: Record<string, number>;
    total: number;
    limit: number;
    shown: number;
    awaiting: number;
    legacy: number;
  } | null>(null);
  const [load] = useLoad(async () => {
    const page = await kycApi.queue({ status: status || undefined, q: query || undefined, limit: PAGE_SIZE, offset });
    setSeen({
      counts: page.counts,
      total: page.total,
      limit: page.limit,
      shown: page.results.length,
      awaiting: page.awaiting_activation ?? 0,
      legacy: page.legacy_active ?? 0,
    });
    return page;
  }, [status, query, offset]);

  const columns: Column<KycQueueRow>[] = [
    {
      key: "partner",
      header: "Partner",
      value: (r) => r.partner_name,
      render: (r) => (
        <Stack
          main={
            <Link href={applicationHref(r.partner_id)}>
              <Trunc>{r.partner_name}</Trunc>
            </Link>
          }
          sub={r.partner_code}
        />
      ),
    },
    { key: "email", header: "Contact email", value: (r) => r.contact_email, render: (r) => <Trunc>{r.contact_email}</Trunc> },
    { key: "status", header: "KYC status", value: (r) => r.status, render: (r) => <KycBadge status={r.status} /> },
    { key: "account", header: "Partner account status", value: (r) => r.partner_status, render: (r) => <StatusBadge value={r.partner_status} /> },
    {
      key: "experience",
      header: "Requested experience",
      value: (r) => r.requested_experience,
      render: (r) => experienceLabel(r.requested_experience),
    },
    { key: "submitted", header: "Submitted", value: (r) => r.submitted_at, render: (r) => (r.submitted_at ? formatDate(r.submitted_at) : "Not sent") },
    { key: "reviewer", header: "Reviewer", value: (r) => r.reviewer_name, render: (r) => r.reviewer_name || EMPTY },
  ];

  const choose = (next: string) => {
    setStatus(next);
    setOffset(0);
  };

  return (
    <Page>
      <PageHeader
        title="Applications"
        subtitle="Partner applications and where each one stands."
        info="KYC status says where an application stands. Partner account status says whether the partner may operate. An active partner needs both: an approved application, then an Admin's activation."
      />
      {seen && seen.awaiting > 0 && (
        <Alert tone="info" title={`${seen.awaiting} approved ${seen.awaiting === 1 ? "partner is" : "partners are"} waiting to be activated`}>
          Approving an application does not activate the partner. Open an approved application and choose Activate partner, or{" "}
          <button type="button" className={styles.linkButton} onClick={() => choose("approved")}>
            show approved applications
          </button>
          .
        </Alert>
      )}
      {seen && seen.legacy > 0 && (
        <Alert tone="neutral" title={`${seen.legacy} ${seen.legacy === 1 ? "partner was" : "partners were"} active before KYC was required`}>
          {seen.legacy === 1 ? "It keeps its" : "They keep their"} status and {seen.legacy === 1 ? "has" : "have"} no approved application on file. Marking{" "}
          {seen.legacy === 1 ? "it" : "them"} approved would record a review nobody made, so they are listed on{" "}
          <Link href="/admin/partners/all?kyc=legacy">Approved Partners</Link> with the label &ldquo;Active before KYC&rdquo;. Any partner leaving draft now
          needs an approved application.
        </Alert>
      )}
      <StatusChips
        label="KYC status"
        value={status}
        onChange={choose}
        // The counts are of every application: during a search they would not match the rows.
        chips={QUEUE_CHIPS.map((c) => ({ ...c, count: seen && !query ? chipCount(c.value, seen.counts) : undefined }))}
      />
      <FilterBar
        onClear={
          query
            ? () => {
                setQuery("");
                setOffset(0);
              }
            : undefined
        }
      >
        <SearchInput
          label="Search by partner, code or email"
          value={query}
          onSearch={(next) => {
            setQuery(next);
            setOffset(0);
          }}
        />
      </FilterBar>
      <DataTable
        caption="Partner applications"
        columns={columns}
        rows={load.status === "ready" ? load.data.results : []}
        rowKey={(r) => r.partner_id}
        rowHref={(r) => applicationHref(r.partner_id)}
        pageSize={0}
        hideEmptyColumns={false}
        {...tableState(load, "partner applications")}
        empty={
          status === "" && !query ? (
            <EmptyState icon="handshake" title="Nothing is waiting for review" hint="Applications appear here once a partner submits them." />
          ) : (
            <EmptyState icon="handshake" title="No applications" hint="Try another status or search." />
          )
        }
      />
      {seen && <OffsetPager total={seen.total} limit={seen.limit} offset={offset} shown={seen.shown} onOffset={setOffset} />}
    </Page>
  );
}

// --- one application ----------------------------------------------------------------------------

export function KycApplicationScreen({ partnerId }: { partnerId: string }) {
  const [load, reload] = useLoad(() => kycApi.detail(partnerId), [partnerId]);
  return (
    <Page>
      <LoadGate load={load} what="this application">
        {(app) => <Application app={app} reload={reload} />}
      </LoadGate>
    </Page>
  );
}

type OpenDialog = "approve" | "correct" | "reject" | null;

function Application({ app, reload }: { app: KycApplicationDetail; reload: () => void }) {
  const { permissions } = useAdmin();
  const toast = useToast();
  const { ask, dialog: confirm } = useConfirm();
  const [open, setOpen] = useState<OpenDialog>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const decided = (message: string | undefined) => {
    if (message) toast.success(message);
    reload();
  };
  const readyToActivate = app.status === "approved" && app.partner_status === "draft";
  const mayActivate = readyToActivate && can(permissions, "platform.staff_partner.manage") && app.partner_version !== undefined;

  function activate() {
    ask({
      title: `Activate ${app.partner_name}?`,
      message:
        "Its KYC application is approved. Activating gives the partner the capabilities approved for it, and its administrators can start working in the partner portal.",
      confirmLabel: "Activate partner",
      onConfirm: async () => {
        await adminApi.setPartnerStatus(app.partner_id, "active", "Activated after KYC approval.", app.partner_version as number);
        reload();
        return `${app.partner_name} is now active.`;
      },
    });
  }

  async function startReview() {
    setBusy(true);
    setError(null);
    try {
      await kycApi.startReview(app.partner_id);
      decided("Review started. You are now its reviewer.");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Applications", href: "/admin/partners/applications" }, { label: app.partner_name }]}
        documentTitle={`${app.partner_name} · Applications`}
        title={app.partner_name}
        badges={
          <>
            <KycBadge status={app.status} prefix="KYC: " />
            <StatusBadge value={app.partner_status} label={`Partner account: ${statusLabel(app.partner_status)}`} />
          </>
        }
        meta={[
          app.partner_code,
          app.contact_email,
          `Requested: ${experienceLabel(app.requested_experience)}`,
          `Submitted: ${app.submitted_at ? formatDateTime(app.submitted_at) : "not yet"}`,
          `Reviewer: ${app.reviewer_name || "nobody yet"}`,
        ]}
        actions={
          mayActivate ? (
            <Button variant="primary" onClick={activate}>
              Activate partner
            </Button>
          ) : app.may_decide ? (
            <>
              {app.status === "submitted" && (
                <Button loading={busy} onClick={() => void startReview()}>
                  Start review
                </Button>
              )}
              <Button onClick={() => setOpen("correct")}>Request corrections</Button>
              <Button variant="danger" onClick={() => setOpen("reject")}>
                Reject
              </Button>
              <Button variant="primary" onClick={() => setOpen("approve")}>
                Approve
              </Button>
            </>
          ) : undefined
        }
      />
      {error && <Alert tone="danger">{error}</Alert>}
      {readyToActivate && (
        <Alert tone="info" title="Approved, not yet active">
          {mayActivate
            ? "Approval does not activate a partner. Choose Activate partner when it should start operating."
            : "Approval does not activate a partner. Someone who may manage partners has to activate it."}
        </Alert>
      )}
      {app.decision_reason && (app.status === "correction_required" || app.status === "rejected") && (
        <Alert tone="warning" title={app.status === "rejected" ? "Why it was rejected" : "What the partner was asked to correct"}>
          {app.decision_reason}
        </Alert>
      )}

      <Card title="Business details" description="What the partner told us. Plain text, not checked by anyone until you check it.">
        <BusinessDetails details={app.details} />
      </Card>

      <Card
        title="Documents"
        description="Documents come from the applicant. Each is opened only after the malware scan says it is clean, and an application cannot be approved while one is still waiting. Every open is written to the access log below."
      >
        <Documents app={app} />
      </Card>

      <Card title="Decision history" description="Every step, with who took it and why.">
        <DecisionHistory app={app} />
      </Card>

      <Card
        title="Document access log"
        description="Who opened which document. The latest 50 opens."
        actions={
          <Button size="sm" onClick={reload}>
            Refresh log
          </Button>
        }
      >
        <AccessLog app={app} />
      </Card>

      {can(permissions, "platform.staff_partner.view") && (
        <p className={styles.note}>
          <Link href={`/admin/partners/${app.partner_id}`}>Open this partner&apos;s page</Link>
        </p>
      )}

      {open === "approve" && <ApproveDialog app={app} onDone={decided} onClose={() => setOpen(null)} />}
      {open === "correct" && <CorrectionsDialog app={app} onDone={decided} onClose={() => setOpen(null)} />}
      {open === "reject" && (
        <RejectDialog
          onClose={() => setOpen(null)}
          onContinue={(reason) =>
            ask({
              title: `Reject ${app.partner_name}?`,
              message: "The partner is told, with your reason, and this application can't be sent again.",
              confirmLabel: "Reject application",
              danger: true,
              onConfirm: async () => {
                await kycApi.decide(app.partner_id, { decision: "reject", reason, expected_version: app.version });
                reload();
                return "Application rejected. The partner has been told.";
              },
            })
          }
        />
      )}
      {confirm}
    </>
  );
}

function BusinessDetails({ details }: { details: Record<string, string> }) {
  if (!BUSINESS_FIELDS.some((f) => details[f.key])) {
    return <EmptyState icon="building" title="No business details yet" hint="The partner has not entered any." compact />;
  }
  return (
    <DescriptionList
      columns={2}
      items={BUSINESS_FIELDS.map((f) => ({
        label: f.label,
        value: f.key === "country" && details.country ? countryName(details.country) : details[f.key],
      }))}
    />
  );
}

function Documents({ app }: { app: KycApplicationDetail }) {
  const known = new Set(app.requirements.map((r) => r.key));
  const others = app.documents.filter((d) => !known.has(d.requirement_key));
  return (
    <>
      {app.missing.length > 0 && (
        <Alert tone="warning" title="Still missing">
          <ul className={styles.missing}>
            {app.missing.map((label) => (
              <li key={label}>{label}</li>
            ))}
          </ul>
        </Alert>
      )}
      {app.requirements.length === 0 && others.length === 0 ? (
        <EmptyState icon="file" title="No documents asked for" hint="With no KYC requirements entered, an application is business details alone." compact />
      ) : (
        <ul className={styles.docs} aria-label="Documents by requirement">
          {app.requirements.map((r) => (
            <RequirementDocuments key={r.key} requirement={r} app={app} />
          ))}
          {others.length > 0 && (
            <li className={styles.doc}>
              <div className={styles.docHead}>Documents for requirements no longer asked for</div>
              <DocumentList documents={others} app={app} showRequirement />
            </li>
          )}
        </ul>
      )}
    </>
  );
}

function RequirementDocuments({ requirement, app }: { requirement: KycRequirement; app: KycApplicationDetail }) {
  const documents = app.documents.filter((d) => d.requirement_key === requirement.key);
  return (
    <li className={styles.doc}>
      <div className={styles.docHead}>
        {requirement.label}
        <Badge tone="neutral">{requirement.is_required ? "Required" : "Optional"}</Badge>
      </div>
      {requirement.help_text && <p className={styles.note}>{requirement.help_text}</p>}
      {documents.length === 0 ? <p className={styles.note}>Nothing uploaded.</p> : <DocumentList documents={documents} app={app} />}
    </li>
  );
}

/** The malware scan of one document, in words: never only a colour. */
function ScanBadge({ scan }: { scan: KycDocument["scan"] }) {
  if (scan === "clean") return <Badge tone="success">Scanned: clean</Badge>;
  if (scan === "infected") return <Badge tone="danger">Threat found: content deleted</Badge>;
  if (scan === "pending") return <Badge tone="warning">Waiting for malware scan: cannot be opened yet</Badge>;
  if (scan === "not_required") return <Badge tone="neutral">Not scanned (scanning is off on this server)</Badge>;
  return null;
}

function DocumentList({ documents, app, showRequirement = false }: { documents: KycDocument[]; app: KycApplicationDetail; showRequirement?: boolean }) {
  return (
    <ul className={styles.files}>
      {documents.map((d) => (
        <li key={d.id} className={d.removed_at ? `${styles.file} ${styles.replaced}` : styles.file}>
          <span className={styles.fileName}>{d.filename}</span>
          <span className={styles.note}>
            {showRequirement ? `${sentence(d.requirement_key)}, ` : ""}
            {fileSize(d.size_bytes)}, uploaded {formatDate(d.uploaded_at)}
          </span>
          {d.removed_at && <Badge tone="neutral">Replaced {formatDate(d.removed_at)}</Badge>}
          <ScanBadge scan={d.scan} />
          {app.may_review && (d.scan === "clean" || d.scan === "not_required" || d.scan === undefined) && (
            <a href={kycApi.documentUrl(app.partner_id, d.id)} target="_blank" rel="noopener" aria-label={`Open ${d.filename}`}>
              Open
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}

function DecisionHistory({ app }: { app: KycApplicationDetail }) {
  const label = (key: string) => app.requirements.find((r) => r.key === key)?.label ?? sentence(key);
  const columns: Column<KycDecision>[] = [
    { key: "when", header: "When", value: (d) => d.decided_at, render: (d) => formatDateTime(d.decided_at) },
    { key: "who", header: "Who", value: (d) => d.decided_by_name, render: (d) => d.decided_by_name || EMPTY },
    { key: "from", header: "From", value: (d) => d.from_status, render: (d) => kycLabel(d.from_status) },
    { key: "to", header: "To", value: (d) => d.to_status, render: (d) => <KycBadge status={d.to_status} /> },
    { key: "reason", header: "Reason", wrap: true, value: (d) => d.reason, render: (d) => d.reason || EMPTY },
    {
      key: "flagged",
      header: "Flagged",
      wrap: true,
      value: (d) => d.flagged_requirements,
      render: (d) => (d.flagged_requirements.length ? d.flagged_requirements.map(label).join(", ") : EMPTY),
    },
  ];
  return (
    <DataTable
      caption="Decision history"
      columns={columns}
      rows={app.decisions}
      rowKey={(d) => d.id}
      pageSize={0}
      maxHeight={false}
      hideEmptyColumns={false}
      empty={<EmptyState icon="audit" title="No decisions yet" compact />}
    />
  );
}

function AccessLog({ app }: { app: KycApplicationDetail }) {
  const filename = (id: string) => app.documents.find((d) => d.id === id)?.filename ?? EMPTY;
  return (
    <DataTable
      caption="Document access log"
      columns={[
        { key: "when", header: "When", value: (a) => a.accessed_at, render: (a) => formatDateTime(a.accessed_at) },
        { key: "who", header: "Who", value: (a) => a.accessed_by_name, render: (a) => a.accessed_by_name || EMPTY },
        { key: "document", header: "Document", value: (a) => filename(a.document_id), render: (a) => <Trunc max="lg">{filename(a.document_id)}</Trunc> },
      ]}
      rows={app.document_access}
      rowKey={(a) => `${a.document_id}-${a.accessed_at}`}
      pageSize={0}
      maxHeight={false}
      hideEmptyColumns={false}
      empty={<EmptyState icon="audit" title="Nobody has opened a document" compact />}
    />
  );
}

// --- the decisions ----------------------------------------------------------------------------

function ApproveDialog({ app, onDone, onClose }: { app: KycApplicationDetail; onDone: (message: string | undefined) => void; onClose: () => void }) {
  const requested = app.requested_experience;
  const [experience, setExperience] = useState<string>(requested);
  // ADR-063: White Label grants branding and a domain only; selling or
  // referring is granted here explicitly, never by default.
  const [also, setAlso] = useState<string[]>([]);
  // Sent empty, the server falls back on what was requested: "no new capabilities" is a
  // true choice only when nothing was requested.
  const options = requested ? [...EXPERIENCES] : [{ value: "", label: "No new capabilities" }, ...EXPERIENCES];
  return (
    <FormDialog
      title={`Approve ${app.partner_name}`}
      intro="Approving records your decision and the experience chosen here. It does not activate the partner: that is a separate step for someone who may manage partners. A partner already active or suspended keeps its status."
      size="sm"
      submitLabel="Approve"
      onSubmit={async () => {
        await kycApi.decide(app.partner_id, {
          decision: "approve",
          experience: experience || undefined,
          expected_version: app.version,
          ...(experience && also.length ? { also: also.filter((x) => x !== experience) } : {}),
        });
        return "Application approved.";
      }}
      onDone={onDone}
      onClose={onClose}
    >
      <Select label="Experience to grant" showLabel fullWidth value={experience} onChange={setExperience} options={options} />
      {experience && (
        <fieldset>
          <legend>Also allow</legend>
          <p>
            {experience === "white_label"
              ? "White Label alone is branding and a custom domain. Tick what else this partner is approved for."
              : "Other permissions this partner is approved for, if any."}
          </p>
          {EXPERIENCES.filter((e) => e.value && e.value !== experience).map((e) => (
            <label key={e.value}>
              <input
                type="checkbox"
                checked={also.includes(e.value)}
                onChange={(ev) => setAlso(ev.target.checked ? [...also, e.value] : also.filter((x) => x !== e.value))}
              />{" "}
              {e.label}
            </label>
          ))}
        </fieldset>
      )}
    </FormDialog>
  );
}

function CorrectionsDialog({ app, onDone, onClose }: { app: KycApplicationDetail; onDone: (message: string | undefined) => void; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [flagged, setFlagged] = useState<string[]>([]);
  return (
    <FormDialog
      title="Request corrections"
      intro="The partner reads this and can edit the application again."
      size="sm"
      submitLabel="Request corrections"
      canSubmit={reason.trim().length >= MIN_REASON}
      onSubmit={async () => {
        await kycApi.decide(app.partner_id, {
          decision: "request_correction",
          reason: reason.trim(),
          flagged_requirements: flagged,
          expected_version: app.version,
        });
        return "Corrections requested. The partner has been told.";
      }}
      onDone={onDone}
      onClose={onClose}
    >
      <TextAreaField label="What needs correcting" maxLength={500} value={reason} onChange={setReason} hint={`At least ${MIN_REASON} characters.`} />
      {app.requirements.length > 0 && (
        <fieldset className={styles.checks}>
          <legend className="w360-label">Documents to flag</legend>
          {app.requirements.map((r) => (
            <label key={r.key} className={styles.check}>
              <input
                type="checkbox"
                checked={flagged.includes(r.key)}
                onChange={(e) => setFlagged(e.target.checked ? [...flagged, r.key] : flagged.filter((key) => key !== r.key))}
              />
              {r.label}
            </label>
          ))}
        </fieldset>
      )}
    </FormDialog>
  );
}

function RejectDialog({ onContinue, onClose }: { onContinue: (reason: string) => void; onClose: () => void }) {
  const [reason, setReason] = useState("");
  return (
    <FormDialog
      title="Reject this application"
      intro="The partner reads your reason. You confirm on the next step."
      size="sm"
      danger
      submitLabel="Continue"
      canSubmit={reason.trim().length >= MIN_REASON}
      onSubmit={() => onContinue(reason.trim())}
      onClose={onClose}
    >
      <TextAreaField label="Why it is rejected" maxLength={500} value={reason} onChange={setReason} hint={`At least ${MIN_REASON} characters.`} />
    </FormDialog>
  );
}

// --- what an application asks for -----------------------------------------------------------------

export function KycRequirementsScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_kyc.review");
  const toast = useToast();
  const [load, reload] = useLoad(() => kycApi.requirements(), []);
  const [editing, setEditing] = useState<KycRequirement | "new" | null>(null);

  const columns: Column<KycRequirement>[] = [
    {
      key: "label",
      header: "Label",
      value: (r) => r.label,
      render: (r) => <Stack main={<Trunc>{r.label}</Trunc>} sub={<KeyText>{r.key}</KeyText>} />,
    },
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
    ...(manage
      ? [
          {
            key: "edit",
            header: "Edit",
            align: "right",
            render: (r: KycRequirement) => (
              <Button size="sm" icon="edit" onClick={() => setEditing(r)} aria-label={`Edit ${r.label}`}>
                Edit
              </Button>
            ),
          } satisfies Column<KycRequirement>,
        ]
      : []),
  ];

  return (
    <Page>
      <PageHeader
        title="KYC requirements"
        subtitle="The documents a partner application must include."
        info="Inactive requirements are no longer asked for. Documents already uploaded for them stay on the application."
        primary={
          manage ? (
            <Button variant="primary" icon="plus" onClick={() => setEditing("new")}>
              Add requirement
            </Button>
          ) : undefined
        }
      />
      <DataTable
        caption="KYC requirements"
        columns={columns}
        rows={load.status === "ready" ? load.data.results : []}
        rowKey={(r) => r.id}
        pageSize={0}
        hideEmptyColumns={false}
        {...tableState(load, "KYC requirements")}
        empty={
          <EmptyState
            icon="file"
            title="No KYC requirements"
            hint="With none entered, an application is business details alone: a partner can submit without uploading anything."
          />
        }
      />
      {editing && (
        <RequirementDialog
          requirement={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onDone={(message) => {
            if (message) toast.success(message);
            reload();
          }}
        />
      )}
    </Page>
  );
}

function RequirementDialog({
  requirement,
  onDone,
  onClose,
}: {
  requirement: KycRequirement | null;
  onDone: (message: string | undefined) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(() => (requirement ? requirementDraft(requirement) : NEW_REQUIREMENT));
  return (
    <FormDialog
      title={requirement ? `Edit ${requirement.label}` : "Add a requirement"}
      size="sm"
      submitLabel={requirement ? "Save" : "Add requirement"}
      canSubmit={requirementIsValid(draft)}
      onSubmit={async () => {
        const body = requirementBody(draft);
        if (requirement) await kycApi.updateRequirement(requirement.id, body);
        else await kycApi.createRequirement(body);
        return requirement ? "Requirement saved." : "Requirement added.";
      }}
      onDone={onDone}
      onClose={onClose}
    >
      <RequirementFields draft={draft} onChange={setDraft} />
    </FormDialog>
  );
}

// --- KYC document access, across every application (2026-11-10) ------------------------------

/** Settings → KYC document access: the restricted KYC store's read log. Who may open
 * KYC documents at all is the KYC review permission, granted in Staff access; every
 * opening is written here before the bytes leave, and the log cannot be edited. */
export function KycAccessLogScreen() {
  const [offset, setOffset] = useState(0);
  const [load] = useLoad(() => kycApi.accessLog({ limit: PAGE_SIZE, offset }), [offset]);
  const columns: Column<{ accessed_at: string; accessed_by_name: string; partner_id: string; partner_name: string; requirement_key: string; ip_prefix: string }>[] = [
    { key: "when", header: "When", value: (r) => r.accessed_at, render: (r) => formatDateTime(r.accessed_at) },
    { key: "who", header: "Opened by", value: (r) => r.accessed_by_name, render: (r) => r.accessed_by_name || EMPTY },
    {
      key: "partner",
      header: "Partner",
      value: (r) => r.partner_name,
      render: (r) => (
        <Link href={applicationHref(r.partner_id)}>
          <Trunc>{r.partner_name}</Trunc>
        </Link>
      ),
    },
    { key: "document", header: "Document", value: (r) => r.requirement_key, render: (r) => sentence(r.requirement_key) },
    { key: "network", header: "Network", value: (r) => r.ip_prefix, render: (r) => <KeyText>{r.ip_prefix || EMPTY}</KeyText> },
  ];
  return (
    <Page>
      <PageHeader
        title="KYC document access"
        subtitle="Every time staff opened a partner's KYC document, newest first."
        info="KYC documents are restricted files: only staff with the KYC review permission can open them, each only after a clean malware scan, and every opening is recorded here and in the audit log. Grant or withdraw the permission in Staff access."
      />
      <DataTable
        caption="KYC document access"
        columns={columns}
        rows={load.status === "ready" ? load.data.results : []}
        rowKey={(r) => `${r.accessed_at}-${r.partner_id}-${r.requirement_key}`}
        pageSize={0}
        hideEmptyColumns={false}
        {...tableState(load, "the KYC access log")}
        empty={<EmptyState icon="shield" title="No KYC document has been opened" />}
      />
      {load.status === "ready" && <OffsetPager total={load.data.total} limit={load.data.limit} offset={offset} shown={load.data.results.length} onOffset={setOffset} />}
    </Page>
  );
}

