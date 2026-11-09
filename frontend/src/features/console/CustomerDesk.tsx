"use client";

import { useState } from "react";
import { LoadGate, Status, Table, useLoad, when } from "@/features/controlplane/ui";
import { FormPopup, dialog } from "@/features/controlplane/Modal";
import cp from "@/features/controlplane/ControlPlane.module.css";
import { apiFetch } from "@/lib/api/client";
import local from "./ConsoleScreens.module.css";
import { Panel } from "./consoleUi";

/**
 * A customer's own side of the Admin console's Support and Setup (2026-11-09): tickets to
 * Workeasy360, the setup a product asks for, and the published help library. The
 * organization comes from the address and is verified by the server; there is nothing
 * here that could name another.
 */

interface DirectTicket {
  id: string;
  reference: string;
  subject: string;
  status: string;
  updated_at: string;
  resolution?: string;
  messages?: { id: string; side: string; author: string; body: string; created_at: string }[];
}

interface SetupRecord {
  id: string;
  product_code: string;
  status: "pending" | "submitted" | "correction_required" | "approved";
  answers: Record<string, string>;
  decision_reason: string;
  requirements: { key: string; label: string; help_text: string; is_required: boolean }[];
  editable: boolean;
}

interface HelpArticle {
  slug: string;
  kind: "faq" | "setup_guide";
  title: string;
  body: string;
}

const org = (slug: string) => `/api/v1/orgs/${encodeURIComponent(slug)}`;
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

const STATUS_WORDS: Record<SetupRecord["status"], string> = {
  pending: "Waiting for your details",
  submitted: "Sent, being reviewed",
  correction_required: "Please correct this",
  approved: "Approved",
};

function Thread({ slug, id, onClose }: { slug: string; id: string; onClose: () => void }) {
  const [load, reload] = useLoad(() => apiFetch<DirectTicket>(`${org(slug)}/support-tickets/${id}`), [slug, id]);
  const [message, setMessage] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <LoadGate load={load} what="this ticket">
      {(ticket) => (
        <div className={local.actions}>
          <h3>
            {ticket.reference}: {ticket.subject}
          </h3>
          <p>
            <Status value={ticket.status} />
          </p>
          {ticket.resolution && <p>Resolution: {ticket.resolution}</p>}
          <ul className={local.includes}>
            {(ticket.messages ?? []).map((m) => (
              <li key={m.id}>
                <strong>{m.author}</strong> ({when(m.created_at)}): <span className={local.preWrap}>{m.body}</span>
              </li>
            ))}
          </ul>
          <label className="w360-label" htmlFor="direct-reply">
            Reply
          </label>
          <textarea
            id="direct-reply"
            className="w360-input"
            rows={3}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          {problem && (
            <p role="alert" className="w360-error-text">
              {problem}
            </p>
          )}
          <p className={local.actions}>
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              disabled={busy || !message.trim()}
              onClick={() => {
                setBusy(true);
                setProblem(null);
                apiFetch(`${org(slug)}/support-tickets/${id}`, post({ message: message.trim() })).then(
                  () => {
                    setMessage("");
                    setBusy(false);
                    reload();
                  },
                  () => {
                    setProblem("That didn't send. Please try again.");
                    setBusy(false);
                  },
                );
              }}
            >
              Send
            </button>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
              Back to tickets
            </button>
          </p>
        </div>
      )}
    </LoadGate>
  );
}

/** Tickets to Workeasy360, for an organization with no partner looking after it. */
export function PlatformSupport({ slug }: { slug: string }) {
  const [load, reload] = useLoad(
    () => apiFetch<{ items: DirectTicket[] }>(`${org(slug)}/support-tickets`),
    [slug],
  );
  const [open, setOpen] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  if (load.status !== "ready") return null;
  if (open) {
    return (
      <Panel title="Your ticket">
        <Thread
          slug={slug}
          id={open}
          onClose={() => {
            setOpen(null);
            reload();
          }}
        />
      </Panel>
    );
  }
  return (
    <Panel title="Ask Workeasy360">
      <p>Raise a ticket and follow the answer here.</p>
      <div className={local.actions}>
        <FormPopup
          label="New ticket"
          title="New ticket"
          submitLabel="Send ticket"
          canSubmit={Boolean(subject.trim() && message.trim())}
          onSubmit={async () => {
            const made = await apiFetch<DirectTicket>(
              `${org(slug)}/support-tickets`,
              post({ subject: subject.trim(), message: message.trim() }),
            );
            setSubject("");
            setMessage("");
            reload();
            setOpen(made.id);
            return "Your ticket was sent.";
          }}
        >
          <div className={dialog.span}>
            <label className="w360-label" htmlFor="direct-subject">
              Subject
            </label>
            <input
              id="direct-subject"
              className="w360-input"
              maxLength={200}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          </div>
          <div className={dialog.span}>
            <label className="w360-label" htmlFor="direct-message">
              How can we help?
            </label>
            <textarea
              id="direct-message"
              className="w360-input"
              rows={5}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          </div>
        </FormPopup>
      </div>
      <Table
        caption="Your tickets"
        head={["Ticket", "Subject", "Status", "Updated"]}
        isEmpty={load.data.items.length === 0}
        empty="No tickets yet."
      >
        {load.data.items.map((t) => (
          <tr key={t.id}>
            <th scope="row">
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(t.id)}>
                {t.reference}
              </button>
            </th>
            <td>{t.subject}</td>
            <td>
              <Status value={t.status} />
            </td>
            <td>{when(t.updated_at)}</td>
          </tr>
        ))}
      </Table>
    </Panel>
  );
}

function SetupForm({ slug, record, onDone }: { slug: string; record: SetupRecord; onDone: () => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>(record.answers ?? {});
  const missing = record.requirements.some((r) => r.is_required && !(answers[r.key] ?? "").trim());
  return (
    <FormPopup
      label={record.status === "correction_required" ? "Correct and send" : "Send details"}
      title="Setup details"
      submitLabel="Send"
      canSubmit={!missing}
      onSubmit={async () => {
        await apiFetch(`${org(slug)}/setup/${record.id}/submit`, post({ answers }));
        onDone();
        return "Thanks. We'll review these.";
      }}
    >
      {record.requirements.map((r) => (
        <div key={r.key} className={dialog.span}>
          <label className="w360-label" htmlFor={`setup-${r.key}`}>
            {r.label}
            {r.is_required ? " (required)" : ""}
          </label>
          <input
            id={`setup-${r.key}`}
            className="w360-input"
            maxLength={2000}
            value={answers[r.key] ?? ""}
            onChange={(e) => setAnswers((a) => ({ ...a, [r.key]: e.target.value }))}
          />
          {r.help_text && <p className="w360-hint">{r.help_text}</p>}
        </div>
      ))}
    </FormPopup>
  );
}

/** What a product still needs from this organization. Nothing shows when nothing does. */
export function CustomerSetupPanel({ slug }: { slug: string }) {
  const [load, reload] = useLoad(() => apiFetch<{ results: SetupRecord[] }>(`${org(slug)}/setup`), [slug]);
  if (load.status !== "ready" || load.data.results.length === 0) return null;
  return (
    <Panel title="Product setup">
      <ul className={local.includes}>
        {load.data.results.map((record) => (
          <li key={record.id}>
            <strong>{record.product_code}</strong>: {STATUS_WORDS[record.status]}
            {record.decision_reason && <p role="status">{record.decision_reason}</p>}
            {record.editable && (
              <div className={local.actions}>
                <SetupForm slug={slug} record={record} onDone={reload} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/** The published library, for customers. Plain text, never markup. */
export function HelpLibrary() {
  const [load] = useLoad(
    () => apiFetch<{ results: HelpArticle[] }>("/api/v1/public/help", { query: { audience: "customer" } }),
    [],
  );
  if (load.status !== "ready" || load.data.results.length === 0) return null;
  return (
    <Panel title="Guides and answers">
      {load.data.results.map((article) => (
        <details key={article.slug} className={cp.muted}>
          <summary>{article.title}</summary>
          <p className={local.preWrap}>{article.body}</p>
        </details>
      ))}
    </Panel>
  );
}
