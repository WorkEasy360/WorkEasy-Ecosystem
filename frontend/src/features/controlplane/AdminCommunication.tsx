"use client";

import { useEffect, useId, useRef, useState } from "react";
import { adminConsoleApi, type SenderSettings, type TemplateRow } from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, Table, describeError, useAction, useLoad, when } from "./ui";
import { TextField } from "./widgets";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";

/**
 * Super Admin → Communication (Phase 8 extension): the wording of every
 * platform email and the sender it goes out as. Changing either is dangerous
 * (``platform.staff_communication.manage``) - an altered invitation is
 * a phishing kit - so saves go through `useAction`, and each save names the
 * version it was based on so two editors can't silently overwrite each other.
 *
 * Wording is plain text with `{placeholder}` fields; the server validates
 * which placeholders are allowed and which are required, and renders previews
 * with sample values. Everything is displayed as text.
 */
type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

export function CommunicationScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_communication.manage");
  const [load, reload] = useLoad(() => adminConsoleApi.templates(), []);
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className={cp.stack}>
      <div>
        <h1>Communication</h1>
        <p className={cp.lead}>
          What the platform&apos;s emails say and who they come from. Placeholders such as <code>{"{link}"}</code> are
          filled in for each recipient when the email is sent.
        </p>
      </div>
      <section className={styles.section} aria-labelledby="templates-h">
        <h2 id="templates-h" className={styles.sectionTitle}>
          Email templates
        </h2>
        <LoadGate load={load} what="email templates">
          {({ results }) => {
            const template = results.find((t) => t.key === selected);
            return (
              <>
                <Table
                  caption="Email templates"
                  head={["Template", "Description", "Wording", "Actions"]}
                  isEmpty={results.length === 0}
                  empty="No templates."
                >
                  {results.map((t) => (
                    <tr key={t.key}>
                      <th scope="row" className={cp.mono}>
                        {t.key}
                      </th>
                      <td>{t.description}</td>
                      <td>
                        {t.override ? (
                          <>
                            Customised <span className={cp.muted}>(v{t.override.version}, {when(t.override.updated_at)})</span>
                          </>
                        ) : (
                          "Default"
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="w360-btn w360-btn-secondary"
                          aria-label={`${manage ? "Edit" : "View"} ${t.key}`}
                          aria-pressed={selected === t.key}
                          onClick={() => setSelected(t.key)}
                        >
                          {manage ? "Edit" : "View"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </Table>
                {template && (
                  <TemplateEditor
                    key={template.key}
                    template={template}
                    manage={manage}
                    onChanged={reload}
                    onClose={() => setSelected(null)}
                  />
                )}
              </>
            );
          }}
        </LoadGate>
      </section>
      <Sender manage={manage} />
    </div>
  );
}

type Field = "subject" | "body";

function TemplateEditor({
  template,
  manage,
  onChanged,
  onClose,
}: {
  template: TemplateRow;
  manage: boolean;
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
  const heading = useRef<HTMLHeadingElement | null>(null);
  const subjectRef = useRef<HTMLInputElement | null>(null);
  const bodyRef = useRef<HTMLTextAreaElement | null>(null);
  // The field a placeholder chip inserts into: the one the cursor was last in.
  const lastField = useRef<Field>("body");
  const caret = useRef<{ field: Field; at: number } | null>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);

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

  const required = new Set(template.required);
  const customised = template.override !== null;

  return (
    <section className={`w360-card ${cp.card}`} aria-labelledby={headingId}>
      <form
        className={styles.builder}
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
        <h3 id={headingId} ref={heading} tabIndex={-1} className={styles.panelTitle}>
          {template.key}
        </h3>
        <p className={cp.muted}>{template.description}</p>
        <div className={cp.field}>
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
        <div className={cp.field}>
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
        <div className={cp.field}>
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
        <div className={styles.actions}>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => void runPreview()}>
            Preview
          </button>
          {manage && (
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
              Save template
            </button>
          )}
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
          <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        {!manage && <p className={cp.muted}>You can preview changes; saving needs platform.staff_communication.manage.</p>}
      </form>
      {feedback}
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
    </section>
  );
}

function Sender({ manage }: { manage: boolean }) {
  const [load, reload] = useLoad(() => adminConsoleApi.sender(), []);
  // Owned here, not by the form: the form re-mounts on each new version and
  // the outcome must survive that.
  const { act, busy, feedback } = useAction(reload);
  return (
    <section className={`w360-card ${cp.card}`} aria-labelledby="sender-h">
      <h2 id="sender-h" className={cp.cardTitle}>
        Sender
      </h2>
      {feedback}
      <LoadGate load={load} what="sender settings">
        {(settings) => <SenderForm key={settings.version} settings={settings} manage={manage} act={act} busy={busy} />}
      </LoadGate>
    </section>
  );
}

function SenderForm({
  settings,
  manage,
  act,
  busy,
}: {
  settings: SenderSettings;
  manage: boolean;
  act: Act;
  busy: boolean;
}) {
  const [name, setName] = useState(settings.sender_name);
  const [replyTo, setReplyTo] = useState(settings.reply_to);
  return (
    <form
      className={cp.form}
      onSubmit={(e) => {
        e.preventDefault();
        if (!manage) return;
        void act(
          () => adminConsoleApi.saveSender({ sender_name: name.trim(), reply_to: replyTo.trim(), version: settings.version }),
          "Sender settings saved.",
        );
      }}
    >
      <p>
        Emails are sent from <code className={cp.mono}>{settings.from_address}</code>. That address is fixed by the
        platform&apos;s mail configuration; the name and reply-to below are yours to set.
      </p>
      <TextField
        label="Sender name"
        value={name}
        onChange={setName}
        maxLength={100}
        readOnly={!manage}
        hint="One line, without < > or quotes. Empty uses the platform name."
      />
      <TextField
        label="Reply-to address (optional)"
        type="email"
        value={replyTo}
        onChange={setReplyTo}
        maxLength={254}
        readOnly={!manage}
      />
      {manage && (
        <div>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
            Save sender
          </button>
        </div>
      )}
    </form>
  );
}
