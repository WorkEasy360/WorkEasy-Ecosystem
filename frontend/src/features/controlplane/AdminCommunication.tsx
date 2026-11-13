"use client";

import { useEffect, useId, useRef, useState } from "react";
import { adminConsoleApi, type SenderSettings, type TemplateRow } from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { DialogHead, FormPopup, Modal, dialog } from "./Modal";
import { LoadGate, describeError, useAction, useLoad, when } from "./ui";
import { TextField } from "./widgets";
import {
  Badge,
  Card,
  DataTable,
  DescriptionList,
  EmptyState,
  KeyText,
  Page,
  PageHeader,
  SectionHeader,
  Stack,
  eventLabel,
  formatDate,
  type Column,
  type MenuItem,
} from "./kit";
import { popupButton } from "./kit/PopupLabel";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";
import comm from "./AdminCommunication.module.css";

/**
 * Super Admin → Communication → Email templates (Phase 8 extension): the
 * wording of every platform email and the sender it goes out as. Changing
 * either is dangerous (``platform.staff_communication.manage``) - an altered
 * invitation is a phishing kit - so saves go through `useAction`, and each
 * save names the version it was based on so two editors can't silently
 * overwrite each other.
 *
 * Wording is plain text with `{placeholder}` fields; the server validates
 * which placeholders are allowed and which are required, and renders previews
 * with sample values. Everything is displayed as text.
 *
 * 2026-11-07 design system: templates are grouped by what they are about and
 * named in words; the system key stays beside the name in small mono text.
 */

/** Readable names for the registered keys; any other key is named from its words. */
const NAMES: Record<string, string> = {
  "platform.member.invited": "Member invitation",
  "platform.organization.suspended": "Organization suspended",
  "platform.organization.reactivated": "Organization reactivated",
  "platform.account.verify_email": "Verify email address",
  "platform.account.signup_existing": "Sign-up with an existing account",
  "platform.account.password_reset": "Password reset",
  "platform.account.password_changed": "Password changed",
  "platform.partner.member_invited": "Partner team invitation",
  "platform.partner.support_request": "Partner support request",
  "platform.customer.owner_invited": "Customer owner invitation",
  "commercial.invoice.issued": "Invoice issued",
  "commercial.payment.failed": "Payment failed",
  "commercial.payment.received": "Payment received",
  "integration.automation.triggered": "Automation email",
};

/** What a template is for, when the server gives no description. */
const DESCRIPTIONS: Record<string, string> = {
  "integration.automation.triggered": "Sent when an automation rule runs its “send email” action.",
};

export const templateName = (key: string) => NAMES[key] ?? eventLabel(key);

const CATEGORIES = ["Billing", "Account & security", "Organizations", "Partners", "Integrations", "Other"] as const;
type Category = (typeof CATEGORIES)[number];

export function templateCategory(key: string): Category {
  if (key.startsWith("commercial.")) return "Billing";
  if (key.startsWith("platform.account.") || key.includes("password")) return "Account & security";
  if (key.startsWith("platform.partner.")) return "Partners";
  if (key.startsWith("integration.") || key.startsWith("connector.")) return "Integrations";
  if (key.startsWith("platform.member.") || key.startsWith("platform.organization.") || key.startsWith("platform.customer.")) return "Organizations";
  return "Other";
}

const describe = (t: TemplateRow) => t.description || DESCRIPTIONS[t.key] || "No description.";

export function CommunicationScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_communication.manage");
  const [load, reload] = useLoad(() => adminConsoleApi.templates(), []);
  const [selected, setSelected] = useState<{ key: string; preview: boolean } | null>(null);
  const { act, feedback } = useAction();

  const columns: Column<TemplateRow>[] = [
    {
      key: "template",
      header: "Template",
      value: (t) => templateName(t.key),
      render: (t) => <Stack main={templateName(t.key)} sub={<KeyText>{t.key}</KeyText>} />,
    },
    { key: "description", header: "Description", wrap: true, render: (t) => <span className={comm.description}>{describe(t)}</span> },
    {
      key: "status",
      header: "Status",
      value: (t) => (t.override ? "customized" : "default"),
      render: (t) =>
        t.override ? (
          <Stack
            main={
              <Badge tone="info" dot>
                Customized
              </Badge>
            }
            sub={
              <span title={when(t.override.updated_at)}>
                v{t.override.version} · {formatDate(t.override.updated_at)}
              </span>
            }
          />
        ) : (
          <Badge tone="neutral" dot>
            Default
          </Badge>
        ),
    },
  ];

  const menu = (t: TemplateRow): MenuItem[] => [
    { label: manage ? "Edit" : "View", icon: manage ? "edit" : "eye", onSelect: () => setSelected({ key: t.key, preview: false }) },
    { label: "Preview", icon: "eye", onSelect: () => setSelected({ key: t.key, preview: true }) },
    {
      label: "Send me a test",
      icon: "send",
      onSelect: () =>
        void act(async () => {
          await adminConsoleApi.sendTestTemplate(t.key);
        }, `Test of “${templateName(t.key)}” queued to your own address.`),
    },
  ];

  return (
    <Page>
      <PageHeader
        title="Email templates"
        subtitle="What the platform's emails say, and who they come from."
        info={
          <>
            Placeholders such as <code>{"{link}"}</code> are filled in for each recipient when the email is sent.
          </>
        }
      />
      {feedback}
      <LoadGate load={load} what="email templates">
        {({ results }) => {
          const template = results.find((t) => t.key === selected?.key);
          const groups = CATEGORIES.map((c) => ({ category: c, rows: results.filter((t) => templateCategory(t.key) === c) })).filter(
            (g) => g.rows.length > 0,
          );
          return (
            <>
              {results.length === 0 ? (
                <Card as="div">
                  <EmptyState icon="mail" title="No templates" compact />
                </Card>
              ) : (
                groups.map((g) => (
                  <section key={g.category} className={comm.group} aria-labelledby={`tpl-${g.category}`}>
                    <SectionHeader id={`tpl-${g.category}`} title={g.category} description={`${g.rows.length} template${g.rows.length === 1 ? "" : "s"}`} />
                    <DataTable
                      caption={`Email templates: ${g.category}`}
                      columns={columns}
                      rows={g.rows}
                      rowKey={(t) => t.key}
                      rowLabel={(t) => templateName(t.key)}
                      rowMenu={menu}
                      pageSize={0}
                      maxHeight={false}
                      hideEmptyColumns={false}
                    />
                  </section>
                ))
              )}
              {template && (
                <TemplateEditor
                  key={template.key}
                  template={template}
                  manage={manage}
                  previewFirst={selected?.preview ?? false}
                  onChanged={reload}
                  onClose={() => setSelected(null)}
                />
              )}
            </>
          );
        }}
      </LoadGate>
      <Sender manage={manage} />
    </Page>
  );
}

type Field = "subject" | "body";

function TemplateEditor({
  template,
  manage,
  previewFirst = false,
  onChanged,
  onClose,
}: {
  template: TemplateRow;
  manage: boolean;
  /** Opened from "Preview": show the preview at once. */
  previewFirst?: boolean;
  onChanged: () => void;
  onClose: () => void;
}) {
  const subjectId = useId();
  const bodyId = useId();
  const chipsLabel = useId();
  const headingId = useId();
  const [subject, setSubject] = useState(template.override?.subject ?? template.default_subject);
  const [body, setBody] = useState(template.override?.body_text ?? template.default_body_text);
  const [preview, setPreview] = useState<{ subject: string; body_text: string } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [testTo, setTestTo] = useState<string | null>(null);
  const { act, busy, feedback } = useAction(onChanged);
  const subjectRef = useRef<HTMLInputElement | null>(null);
  const bodyRef = useRef<HTMLTextAreaElement | null>(null);
  // The field a placeholder chip inserts into: the one the cursor was last in.
  const lastField = useRef<Field>("body");
  const caret = useRef<{ field: Field; at: number } | null>(null);

  useEffect(() => {
    const pending = caret.current;
    if (!pending) return;
    caret.current = null;
    const el = pending.field === "subject" ? subjectRef.current : bodyRef.current;
    el?.focus();
    el?.setSelectionRange(pending.at, pending.at);
  }, [subject, body]);

  function insert(name: string) {
    const token = `{${name}}`;
    const field = lastField.current;
    const el = field === "subject" ? subjectRef.current : bodyRef.current;
    const text = field === "subject" ? subject : body;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? text.length;
    caret.current = { field, at: start + token.length };
    const next = text.slice(0, start) + token + text.slice(end);
    if (field === "subject") setSubject(next);
    else setBody(next);
  }

  async function runPreview() {
    setPreviewError(null);
    try {
      setPreview(await adminConsoleApi.previewTemplate(template.key, { subject, body_text: body }));
    } catch (err) {
      setPreview(null);
      setPreviewError(describeError(err));
    }
  }

  // "Preview" from the row: the preview of the wording in force, at once.
  useEffect(() => {
    if (!previewFirst) return;
    let live = true;
    adminConsoleApi.previewTemplate(template.key, { subject, body_text: body }).then(
      (result) => live && setPreview(result),
      (err: unknown) => live && setPreviewError(describeError(err)),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const required = new Set(template.required);
  const customised = template.override !== null;

  return (
    <Modal labelledBy={headingId} onClose={onClose} size="lg">
      <form
        className={dialog.form}
        onSubmit={(e) => {
          e.preventDefault();
          if (!manage) return;
          void act(
            () =>
              adminConsoleApi.saveTemplate(template.key, {
                subject,
                body_text: body,
                version: template.override?.version ?? null,
              }),
            "Template saved. New emails use this wording.",
          );
        }}
      >
        <DialogHead titleId={headingId} title={`${manage ? "Edit" : "View"}: ${templateName(template.key)}`} onClose={onClose} />
        <div className={dialog.body}>
          <p className={dialog.intro}>
            {describe(template)} <KeyText>{template.key}</KeyText>
          </p>
          {feedback}
          <div className={dialog.grid}>
            <div className={`${cp.field} ${dialog.span}`}>
              <label className="w360-label" htmlFor={subjectId}>
                Subject
              </label>
              <input
                id={subjectId}
                ref={subjectRef}
                className="w360-input"
                value={subject}
                maxLength={200}
                onFocus={() => {
                  lastField.current = "subject";
                }}
                onChange={(e) => setSubject(e.target.value)}
              />
            </div>
            <div className={`${cp.field} ${dialog.span}`}>
              <label className="w360-label" htmlFor={bodyId}>
                Body (plain text)
              </label>
              <textarea
                id={bodyId}
                ref={bodyRef}
                className={`w360-input ${styles.textarea}`}
                rows={10}
                maxLength={10000}
                value={body}
                onFocus={() => {
                  lastField.current = "body";
                }}
                onChange={(e) => setBody(e.target.value)}
              />
            </div>
            <div className={`${cp.field} ${dialog.span}`}>
              <p id={chipsLabel} className="w360-label">
                Insert a placeholder at the cursor
              </p>
              <ul className={styles.chips} aria-labelledby={chipsLabel}>
                {template.variables.map((name) => (
                  <li key={name}>
                    <button type="button" className={styles.chip} onClick={() => insert(name)}>
                      {`{${name}}`}
                      {required.has(name) ? " (required)" : ""}
                    </button>
                  </li>
                ))}
              </ul>
              <p className="w360-hint">
                Placeholders marked “required” must appear in the body. Write {"{{"} or {"}}"} for a literal brace.
              </p>
            </div>
          </div>
          {!manage && <p className={cp.muted}>You can preview changes; saving needs the communication management permission.</p>}
          {testTo && (
            <p className={cp.muted}>
              The test went to {testTo} (your own address), with the saved wording and sample values.
            </p>
          )}
          {previewError && (
            <p role="alert" className="w360-error-text">
              {previewError}
            </p>
          )}
          {preview && (
            <div className={cp.field} aria-live="polite">
              <p className="w360-label">Preview with sample values (not saved)</p>
              <div className={styles.preview}>
                <p>
                  <strong>Subject:</strong> {preview.subject}
                </p>
                <pre className={styles.pre}>{preview.body_text}</pre>
              </div>
            </div>
          )}
        </div>
        <div className={dialog.foot}>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
            Close
          </button>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            disabled={busy}
            onClick={() =>
              act(async () => {
                const sent = await adminConsoleApi.sendTestTemplate(template.key);
                setTestTo(sent.to);
              }, "Test email queued.")
            }
          >
            Send me a test
          </button>
          {manage && customised && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  await adminConsoleApi.resetTemplate(template.key, template.override?.version ?? 0);
                  setSubject(template.default_subject);
                  setBody(template.default_body_text);
                }, "Template reset to the default wording.")
              }
            >
              Reset to default
            </button>
          )}
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => void runPreview()}>
            Preview
          </button>
          {manage && (
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
              Save template
            </button>
          )}
        </div>
      </form>
    </Modal>
  );
}

function Sender({ manage }: { manage: boolean }) {
  const [load, reload] = useLoad(() => adminConsoleApi.sender(), []);
  return (
    <LoadGate load={load} what="sender settings">
      {(settings) => <SenderSettingsView settings={settings} manage={manage} onSaved={reload} />}
    </LoadGate>
  );
}

/** The sender in force, as text, and (for a manager) an "Edit sender" popup
 * prefilled from it each time it opens; a save names the version it read. */
function SenderSettingsView({ settings, manage, onSaved }: { settings: SenderSettings; manage: boolean; onSaved: () => void }) {
  const [name, setName] = useState(settings.sender_name);
  const [replyTo, setReplyTo] = useState(settings.reply_to);
  return (
    <Card
      title="Sender"
      description="The from address is fixed by the platform's mail configuration; the name and reply-to are yours to set."
      actions={
        manage ? (
          <FormPopup
            label="Edit sender"
            buttonClassName={popupButton("secondary")}
            title="Sender"
            intro="The from address is fixed by the platform's mail configuration; the name and reply-to are yours to set."
            submitLabel="Save sender"
            onOpen={() => {
              setName(settings.sender_name);
              setReplyTo(settings.reply_to);
            }}
            onSubmit={async () => {
              await adminConsoleApi.saveSender({ sender_name: name.trim(), reply_to: replyTo.trim(), version: settings.version });
              return "Sender settings saved.";
            }}
            onDone={onSaved}
          >
            <TextField
              label="Sender name"
              value={name}
              onChange={setName}
              maxLength={100}
              hint="One line, without < > or quotes. Empty uses the platform name."
            />
            <TextField label="Reply-to address (optional)" type="email" value={replyTo} onChange={setReplyTo} maxLength={254} />
          </FormPopup>
        ) : undefined
      }
    >
      <DescriptionList
        items={[
          { label: "From address", value: <code className={cp.mono}>{settings.from_address}</code> },
          { label: "Sender name", value: settings.sender_name || "The platform name" },
          { label: "Reply-to address", value: settings.reply_to || <span className={cp.muted}>Not set</span> },
        ]}
      />
    </Card>
  );
}
