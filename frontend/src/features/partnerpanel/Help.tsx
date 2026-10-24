"use client";

import { useId, useState } from "react";
import { LoadGate, useAction, useLoad, when } from "@/features/controlplane/ui";
import { panelApi, type NotificationView, type SupportResponse } from "@/lib/api/partnerPanel";
import { EmptyState, NotAvailable, PageHeader, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

// --- support -------------------------------------------------------------------------------

/** An address a browser may be sent to: https only, and never `javascript:`. */
function safeHttpsUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

/** Support Manager: where to get help. There is no ticket system on the platform,
 * so there is no list of requests - the page points at the contact address and
 * Easy Help Desk, and says so. */
export function Support() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.support(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Support Manager" lead="Get help with your partner account. Requests are handled by Workeasy360 support." />
      <LoadGate load={load} what="support">
        {(d) => <SupportBody data={d} />}
      </LoadGate>
    </div>
  );
}

function SupportBody({ data }: { data: SupportResponse }) {
  const subjectId = useId();
  const priorityId = useId();
  const detailsId = useId();
  const [subject, setSubject] = useState("");
  const [priority, setPriority] = useState("normal");
  const [details, setDetails] = useState("");
  const desk = data.help_desk.available ? safeHttpsUrl(data.help_desk.url) : null;
  const body = `Partner account: ${data.partner.name}\nPriority: ${priority}\n\n${details}`;
  const mailto = data.contact.available
    ? `mailto:${data.contact.email}?subject=${encodeURIComponent(subject.trim() || "Partner support request")}&body=${encodeURIComponent(body)}`
    : null;
  return (
    <>
      <div className={styles.twoCol}>
        <section className={styles.panel} aria-labelledby="support-contact">
          <h2 id="support-contact">Support contact</h2>
          {data.contact.available ? (
            <p>
              Email <a href={`mailto:${data.contact.email}`}>{data.contact.email}</a>
            </p>
          ) : (
            <p className={styles.muted}>No support email address is configured for partner accounts yet.</p>
          )}
        </section>
        <section className={styles.panel} aria-labelledby="support-desk">
          <h2 id="support-desk">{data.help_desk.name}</h2>
          {desk ? (
            <p>
              <a href={desk} target="_blank" rel="noopener noreferrer">
                Open {data.help_desk.name}
              </a>{" "}
              <span className={styles.muted}>(opens in a new tab). Track your requests there.</span>
            </p>
          ) : (
            <p className={styles.muted}>{data.help_desk.suspended ? `${data.help_desk.name} is temporarily unavailable.` : `${data.help_desk.name} is not connected yet.`}</p>
          )}
        </section>
      </div>
      <section className={styles.panel} aria-labelledby="support-request">
        <h2 id="support-request">Send a support request</h2>
        {mailto ? (
          <form
            className={styles.page}
            onSubmit={(e) => {
              e.preventDefault();
              window.location.href = mailto;
            }}
          >
            <p className={styles.muted}>This prepares an email to support in your own email app. Nothing is sent or stored from here.</p>
            <div className={styles.formGrid}>
              <div>
                <label className="w360-label" htmlFor={subjectId}>
                  Subject
                </label>
                <input id={subjectId} className="w360-input" required maxLength={120} value={subject} onChange={(e) => setSubject(e.target.value)} />
              </div>
              <div>
                <label className="w360-label" htmlFor={priorityId}>
                  Priority
                </label>
                <select id={priorityId} className="w360-input" value={priority} onChange={(e) => setPriority(e.target.value)}>
                  {data.priorities.map((p) => (
                    <option key={p} value={p}>
                      {p[0]?.toUpperCase()}
                      {p.slice(1)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="w360-label" htmlFor={detailsId}>
                What do you need help with?
              </label>
              <textarea id={detailsId} className="w360-input" rows={5} required maxLength={2000} value={details} onChange={(e) => setDetails(e.target.value)} />
            </div>
            <p className={styles.muted}>Never include a password, an API key or a card number.</p>
            <div>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={!subject.trim() || !details.trim()}>
                Prepare email
              </button>
            </div>
          </form>
        ) : (
          <p className={styles.muted}>
            {desk ? "Raise your request in Easy Help Desk above." : "Support requests will be possible once a support address or Easy Help Desk is connected."}
          </p>
        )}
      </section>
      <NotAvailable title="Open and resolved requests" reasons={[data.not_implemented.partner_tickets ?? ""]} />
    </>
  );
}

// --- notifications -----------------------------------------------------------------------------

const TABS: { key: NotificationView; label: string }[] = [
  { key: "unread", label: "Unread" },
  { key: "all", label: "All" },
  { key: "important", label: "Important" },
];

/** Notification Center: what happened on the partner account, from the platform's
 * one audit log (nothing is stored twice). Reading is personal to each member. */
export function Notifications() {
  const { id } = usePanel();
  const [view, setView] = useState<NotificationView>("unread");
  const [load, reload] = useLoad(() => panelApi.notifications(id, view), [id, view]);
  const { act, busy, feedback } = useAction(reload);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Notification Center"
        lead="Customer, commission, payout, domain, product and security events for your partner account. Marking one read only changes what you see."
      />
      {feedback}
      <LoadGate load={load} what="notifications">
        {(d) => (
          <>
            <div className={styles.actions}>
              <div className={styles.filters} role="group" aria-label="Show">
                {TABS.map((t) => (
                  <button
                    key={t.key}
                    type="button"
                    className="w360-btn w360-btn-secondary"
                    aria-pressed={view === t.key}
                    onClick={() => setView(t.key)}
                  >
                    {t.label}
                    {t.key === "unread" ? ` (${d.counts.unread})` : t.key === "important" ? ` (${d.counts.important})` : ` (${d.counts.all})`}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="w360-btn w360-btn-secondary"
                disabled={busy || d.counts.unread === 0}
                onClick={() => void act(() => panelApi.markNotificationsRead(id, { all: true }), "Everything is marked read.")}
              >
                Mark all read
              </button>
            </div>
            {d.items.length === 0 ? (
              <EmptyState title={view === "unread" ? "You are all caught up" : "Nothing here yet"} icon="bell">
                <span>New events on your account will appear here.</span>
              </EmptyState>
            ) : (
              <ul className={styles.notificationList} aria-label="Notifications">
                {d.items.map((n) => (
                  <li key={n.id} className={styles.notification} data-unread={!n.read} data-important={n.important}>
                    <div className={styles.actions}>
                      <strong>{n.title}</strong>
                      <span className="w360-badge w360-badge-neutral">{n.category}</span>
                      {n.important && <span className="w360-badge w360-badge-warning">important</span>}
                      {!n.read && <span className="w360-badge w360-badge-success">unread</span>}
                    </div>
                    {n.detail && <span>{n.detail}</span>}
                    <span className={styles.muted}>{when(n.occurred_at)}</span>
                    {!n.read && (
                      <div>
                        <button
                          type="button"
                          className="w360-btn w360-btn-secondary"
                          disabled={busy}
                          aria-label={`Mark read: ${n.title}`}
                          onClick={() => void act(() => panelApi.markNotificationsRead(id, { ids: [n.id] }), "Marked read.")}
                        >
                          Mark read
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {d.truncated && <p className={styles.muted}>Showing the latest {d.window} events.</p>}
            <NotAvailable title="Not recorded as notifications" reasons={Object.values(d.not_implemented)} />
          </>
        )}
      </LoadGate>
    </div>
  );
}
