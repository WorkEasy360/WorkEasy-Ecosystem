"use client";

import { useId, useMemo, useState } from "react";
import { partnerKycApi, type KycDocument, type KycRequirement, type KycStatus, type PartnerKycApplication } from "@/lib/api/adminPanel";
import { countryName, countryOptions } from "@/features/controlplane/countries";
import { BUSINESS_FIELDS, KycBadge, experienceLabel, fileSize } from "@/features/controlplane/kycParts";
import { Alert, Badge, Button, Card, DescriptionList, formatDate, formatDateTime, useConfirm, type Tone } from "@/features/controlplane/kit";
import { LoadGate, describeError, useLoad } from "@/features/controlplane/ui";
import { SelectField, TextField } from "@/features/controlplane/widgets";
import { PageHeader } from "./PanelKit";
import styles from "./KycApplication.module.css";

/**
 * A partner's own KYC application (2026-11-09). It works for a **draft** partner, which
 * holds no permission at all and has an empty menu: the status notice in the shell is the
 * way in. Editing is open while the application is a draft or was sent back; once sent it
 * reads only. `may_edit` is the server's answer: only the partner's administrators may
 * change it, and they alone may open its documents. Every request is authorized again.
 */

const DOCUMENT_TYPES = new Set(["application/pdf", "image/png", "image/jpeg"]);
const DOCUMENT_ACCEPT = "application/pdf,image/png,image/jpeg";
const MAX_MEGABYTES = 10;
const MAX_BYTES = MAX_MEGABYTES * 1024 * 1024;

const BANNER: Record<KycStatus, { tone: Tone; title: string; text?: string }> = {
  draft: {
    tone: "info",
    title: "Your application has not been sent yet",
    text: "Fill in your business details, add the documents asked for, then submit it. Your partner account stays inactive until it is approved.",
  },
  submitted: {
    tone: "info",
    title: "Your application has been sent",
    text: "It is waiting for a reviewer. You can't change it while it waits. We'll email you when there is a decision.",
  },
  under_review: {
    tone: "info",
    title: "Your application is being reviewed",
    text: "You can't change it during the review. We'll email you when there is a decision.",
  },
  correction_required: { tone: "warning", title: "Corrections are needed" },
  approved: { tone: "success", title: "Your application is approved" },
  rejected: { tone: "danger", title: "Your application was not approved" },
};

type Notice = { tone: "success" | "danger"; text: string } | null;

export function PartnerKycScreen({ partnerId, embedded = false }: { partnerId: string; embedded?: boolean }) {
  const [load, reload] = useLoad(() => partnerKycApi.get(partnerId), [partnerId]);
  const [notice, setNotice] = useState<Notice>(null);
  return (
    <div className={styles.stack}>
      {embedded ? (
        <h2>KYC application</h2>
      ) : (
        <PageHeader title="Your partner application" lead="What we need to know about your business before your partner account can start." />
      )}
      <LoadGate load={load} what="your application">
        {(app) => (
          <>
            <StatusBanner app={app} />
            <KycHistory app={app} />
            {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
            {/* A new version is a new starting point for the form. Uploading a document
                keeps the version, so an upload never loses what was being typed. */}
            <Application key={app.version} partnerId={partnerId} app={app} reload={reload} setNotice={setNotice} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

/** The decisions so far, with their reasons and dates (ADR-063: KYC history). */
function KycHistory({ app }: { app: PartnerKycApplication }) {
  const history = app.history ?? [];
  if (history.length === 0) return null;
  return (
    <details>
      <summary>KYC history ({history.length})</summary>
      <ul>
        {history.map((h) => (
          <li key={`${h.decided_at}-${h.to_status}`}>
            {formatDateTime(h.decided_at)}: <KycBadge status={h.to_status as PartnerKycApplication["status"]} />
            {h.reason ? ` - ${h.reason}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** The documents the reviewer flagged for correction, by their own names. */
function flaggedLabels(app: PartnerKycApplication): string[] {
  const flagged = app.correction?.flagged_requirements ?? [];
  return app.requirements.filter((r) => flagged.includes(r.key)).map((r) => r.label);
}

function StatusBanner({ app }: { app: PartnerKycApplication }) {
  const banner = BANNER[app.status];
  const reason = app.correction?.reason || app.decision_reason;
  return (
    <Alert tone={banner.tone} title={banner.title}>
      <p>
        <KycBadge status={app.status} prefix="KYC status: " />
      </p>
      {banner.text && <p>{banner.text}</p>}
      {app.status === "approved" && app.decided_at && <p>Approved on {formatDateTime(app.decided_at)}.</p>}
      {(app.status === "correction_required" || app.status === "rejected") && reason && <p>{reason}</p>}
      {app.status === "correction_required" && flaggedLabels(app).length > 0 && (
        <p>
          <strong>To correct:</strong> {flaggedLabels(app).join(", ")}.
        </p>
      )}
      {app.status === "rejected" && <p>Next step: raise a ticket under Help → Support Tickets to ask Workeasy360 what you can do.</p>}
    </Alert>
  );
}

function detailsFrom(app: PartnerKycApplication): Record<string, string> {
  return Object.fromEntries(BUSINESS_FIELDS.map((f) => [f.key, app.details[f.key] ?? ""]));
}

function trimmed(details: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(details).map(([key, value]) => [key, value.trim()]));
}

function Application({
  partnerId,
  app,
  reload,
  setNotice,
}: {
  partnerId: string;
  app: PartnerKycApplication;
  reload: () => void;
  setNotice: (notice: Notice) => void;
}) {
  const [details, setDetails] = useState(() => detailsFrom(app));
  const [busy, setBusy] = useState(false);
  const countries = useMemo(() => countryOptions(), []);
  const canEdit = app.editable && app.may_edit;
  const clean = trimmed(details);
  const dirty = JSON.stringify(clean) !== JSON.stringify(detailsFrom(app));

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      await partnerKycApi.save(partnerId, clean, app.version);
      setNotice({ tone: "success", text: "Your business details are saved." });
      reload();
    } catch (err) {
      setNotice({ tone: "danger", text: describeError(err) });
    } finally {
      setBusy(false);
    }
  }

  // What is typed goes with the application: unsaved details are saved first, and after
  // a save the page reads again, so a refused submit never leaves a stale version behind.
  async function submit() {
    setBusy(true);
    setNotice(null);
    try {
      if (dirty) await partnerKycApi.save(partnerId, clean, app.version);
      await partnerKycApi.submit(partnerId);
      setNotice({ tone: "success", text: "Your application has been sent." });
      reload();
    } catch (err) {
      setNotice({ tone: "danger", text: describeError(err) });
      if (dirty) reload();
    } finally {
      setBusy(false);
    }
  }

  const missing = [
    ...(clean.legal_name ? [] : ["Legal name"]),
    ...(clean.country ? [] : ["Country"]),
    ...app.requirements.filter((r) => r.is_required && !app.documents.some((d) => d.requirement_key === r.key)).map((r) => r.label),
  ];

  return (
    <>
      {app.requested_experience && <p className={styles.note}>You asked to partner as: {experienceLabel(app.requested_experience)}.</p>}
      {app.editable && !app.may_edit && (
        <Alert tone="info">Only this partner&apos;s administrators can change this application or open its documents.</Alert>
      )}

      <Card title="Business details" description={canEdit ? "The legal name and country are needed before you can submit." : undefined}>
        {canEdit ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (dirty && !busy) void save();
            }}
            noValidate
          >
            <div className={styles.grid}>
              {BUSINESS_FIELDS.map((f) =>
                f.key === "country" ? (
                  <SelectField
                    key={f.key}
                    label={f.label}
                    required
                    showRequired
                    value={details.country}
                    onChange={(value) => setDetails({ ...details, country: value })}
                    options={countries}
                  />
                ) : (
                  <TextField
                    key={f.key}
                    label={f.label}
                    required={f.key === "legal_name"}
                    showRequired={f.key === "legal_name"}
                    maxLength={f.maxLength}
                    autoComplete={f.autoComplete}
                    value={details[f.key]}
                    onChange={(value) => setDetails({ ...details, [f.key]: value })}
                  />
                ),
              )}
            </div>
            <div className={styles.formActions}>
              <Button type="submit" loading={busy} disabled={!dirty}>
                Save details
              </Button>
              {!dirty && <span className={styles.note}>No unsaved changes.</span>}
            </div>
          </form>
        ) : (
          <DescriptionList
            columns={2}
            items={BUSINESS_FIELDS.map((f) => ({
              label: f.label,
              value: f.key === "country" && app.details.country ? countryName(app.details.country) : app.details[f.key],
            }))}
          />
        )}
      </Card>

      <Card title="Documents" description={app.requirements.length ? `PDF, PNG or JPEG, at most ${MAX_MEGABYTES} MB each.` : undefined}>
        {app.requirements.length === 0 ? (
          <p className={styles.note}>No documents are asked for at the moment: your business details are the whole application.</p>
        ) : (
          <ul className={styles.docs} aria-label="Documents to provide">
            {app.requirements.map((requirement) => (
              <RequirementRow
                key={requirement.key}
                partnerId={partnerId}
                requirement={requirement}
                document={app.documents.find((d) => d.requirement_key === requirement.key)}
                editable={canEdit}
                mayOpen={app.may_edit}
                flagged={app.status === "correction_required" && (app.correction?.flagged_requirements ?? []).includes(requirement.key)}
                onChanged={reload}
              />
            ))}
          </ul>
        )}
      </Card>

      {canEdit && (
        <Card title="Submit your application" description="Once it is sent you can't change it, unless a reviewer asks for corrections.">
          {missing.length > 0 && (
            <div>
              <p>Still needed before you can submit:</p>
              <ul className={styles.missing} aria-label="Still needed">
                {missing.map((label) => (
                  <li key={label}>{label}</li>
                ))}
              </ul>
            </div>
          )}
          <div className={styles.formActions}>
            <Button variant="primary" loading={busy} disabled={missing.length > 0} onClick={() => void submit()}>
              Submit application
            </Button>
            {dirty && <span className={styles.note}>Your unsaved details are saved first.</span>}
          </div>
        </Card>
      )}
    </>
  );
}

function RequirementRow({
  partnerId,
  requirement,
  document,
  editable,
  mayOpen,
  flagged,
  onChanged,
}: {
  partnerId: string;
  requirement: KycRequirement;
  document: KycDocument | undefined;
  editable: boolean;
  mayOpen: boolean;
  flagged: boolean;
  onChanged: () => void;
}) {
  const inputId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { ask, dialog: confirm } = useConfirm();

  async function upload(file: File, input: HTMLInputElement) {
    setError(null);
    if (!DOCUMENT_TYPES.has(file.type)) {
      setError("A document must be a PDF, a PNG or a JPEG.");
      input.value = "";
      return;
    }
    if (file.size > MAX_BYTES) {
      setError(`A document must be at most ${MAX_MEGABYTES} MB.`);
      input.value = "";
      return;
    }
    setBusy(true);
    try {
      await partnerKycApi.upload(partnerId, requirement.key, file);
      onChanged();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
      input.value = "";
    }
  }

  return (
    <li className={flagged ? `${styles.doc} ${styles.flagged}` : styles.doc}>
      <div className={styles.docHead}>
        {requirement.label}
        <Badge tone="neutral">{requirement.is_required ? "Required" : "Optional"}</Badge>
        {flagged && <Badge tone="warning">Needs correction</Badge>}
      </div>
      {requirement.help_text && <p className={styles.note}>{requirement.help_text}</p>}
      {document ? (
        <div className={styles.file}>
          <span className={styles.fileName}>{document.filename}</span>
          <span className={styles.note}>
            {fileSize(document.size_bytes)}, uploaded {formatDate(document.uploaded_at)}
          </span>
          {document.scan === "pending" && <Badge tone="warning">Waiting for the malware scan</Badge>}
          {document.scan === "infected" && <Badge tone="danger">A threat was found and the file deleted: upload it again</Badge>}
          {mayOpen && document.scan !== "pending" && document.scan !== "infected" && (
            <a href={partnerKycApi.documentUrl(partnerId, document.id)} target="_blank" rel="noopener" aria-label={`Open ${document.filename}`}>
              Open
            </a>
          )}
          {editable && (
            <Button
              size="sm"
              variant="danger"
              disabled={busy}
              aria-label={`Remove ${document.filename}`}
              onClick={() =>
                ask({
                  title: `Remove ${document.filename}?`,
                  message: `It no longer counts towards ${requirement.label}. You can upload another.`,
                  confirmLabel: "Remove document",
                  danger: true,
                  onConfirm: async () => {
                    await partnerKycApi.remove(partnerId, document.id);
                    onChanged();
                  },
                })
              }
            >
              Remove
            </Button>
          )}
        </div>
      ) : (
        <p className={styles.note}>Nothing uploaded yet.</p>
      )}
      {editable && (
        <div className={styles.upload}>
          <label className="w360-label" htmlFor={inputId}>
            {document ? `Replace ${requirement.label}` : `Upload ${requirement.label}`}
          </label>
          <input
            id={inputId}
            type="file"
            className="w360-input"
            accept={DOCUMENT_ACCEPT}
            disabled={busy}
            aria-busy={busy || undefined}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void upload(file, e.target);
            }}
          />
        </div>
      )}
      {busy && (
        <p role="status" className={styles.note}>
          Uploading…
        </p>
      )}
      {error && (
        <p role="alert" className="w360-error-text">
          {error}
        </p>
      )}
      {confirm}
    </li>
  );
}
