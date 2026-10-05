"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api/client";
import {
  createWebhookConnection,
  getEventCatalog,
  getSubscriptions,
  listConnections,
  pingConnection,
  rotateSecret,
  setConnectionStatus,
  setSubscriptions,
} from "@/lib/api/integrations";
import type { CatalogEvent, Connection, ConnectionStatus } from "@/lib/api/types";
import { useStepUp } from "@/features/security/useStepUp";
import { SecretReveal } from "./SecretReveal";
import styles from "./Integrations.module.css";

type Load =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error" }
  | { status: "ready"; connections: Connection[]; catalog: CatalogEvent[] };

const STATUS_BADGE: Record<ConnectionStatus, string> = {
  draft: "w360-badge w360-badge-neutral",
  active: "w360-badge w360-badge-success",
  suspended: "w360-badge w360-badge-warning",
  disabled: "w360-badge w360-badge-danger",
};

export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { detail?: string } | undefined;
    if (err.status === 403) return "You don't have permission to do that.";
    if (body?.detail) return body.detail;
  }
  return "Something went wrong. Please try again.";
}

/**
 * Settings → Integrations (Task 79): webhook connections, their event
 * subscriptions, test events and secret rotation. Plain forms, no canvas.
 */
export function ConnectionsSettings({ orgSlug }: { orgSlug: string }) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [revealed, setRevealed] = useState<{ label: string; secret: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { run, stepUp } = useStepUp();

  function refresh() {
    Promise.all([listConnections(orgSlug), getEventCatalog(orgSlug)])
      .then(([page, catalog]) =>
        setLoad({ status: "ready", connections: page.results, catalog: catalog.results }),
      )
      .catch((err: unknown) =>
        setLoad({ status: err instanceof ApiError && err.status === 403 ? "forbidden" : "error" }),
      );
  }

  useEffect(refresh, [orgSlug]);

  async function act(action: () => Promise<void>, success: string) {
    setError(null);
    setMessage(null);
    try {
      await run(async () => {
        await action();
        setMessage(success);
        refresh();
      });
    } catch (err) {
      setError(describeError(err));
    }
  }

  if (load.status === "loading") return <p aria-live="polite">Loading integrations…</p>;
  if (load.status === "forbidden") {
    return <p role="alert">You don&apos;t have permission to view this organization&apos;s integrations.</p>;
  }
  if (load.status === "error") return <p role="alert">Couldn&apos;t load integrations right now.</p>;

  return (
    <div className={styles.stack}>
      {stepUp}
      {revealed && (
        <SecretReveal
          label={revealed.label}
          secret={revealed.secret}
          onDone={() => setRevealed(null)}
        />
      )}
      {message && <p role="status">{message}</p>}
      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}

      <CreateConnectionForm
        onCreate={(name, url) =>
          act(async () => {
            const created = await createWebhookConnection(orgSlug, name, url);
            setRevealed({ label: created.connection.display_name, secret: created.secret });
          }, "Connection created as a draft. Send a test event, then activate it.")
        }
      />

      <p>
        <Link href={`/o/${orgSlug}/settings/webhooks`}>View the delivery log</Link>
      </p>

      {load.connections.length === 0 ? (
        <div className={`w360-card ${styles.card}`}>
          <p>No connections yet. Create one to start receiving events.</p>
        </div>
      ) : (
        load.connections.map((connection) => (
          <ConnectionCard
            key={connection.id}
            orgSlug={orgSlug}
            connection={connection}
            catalog={load.catalog}
            onAct={act}
            onRotated={(secret) => setRevealed({ label: connection.display_name, secret })}
          />
        ))
      )}
    </div>
  );
}

function CreateConnectionForm({ onCreate }: { onCreate: (name: string, url: string) => void }) {
  const nameId = useId();
  const urlId = useId();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");

  return (
    <form
      className={`w360-card ${styles.card} ${styles.form}`}
      onSubmit={(e) => {
        e.preventDefault();
        onCreate(name.trim(), url.trim());
      }}
    >
      <h2 className={styles.cardTitle}>Add a webhook</h2>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={nameId}>
          Name
        </label>
        <input id={nameId} className="w360-input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={urlId}>
          Endpoint URL (https)
        </label>
        <input
          id={urlId}
          className="w360-input"
          type="url"
          required
          inputMode="url"
          placeholder="https://hooks.example.com/workeasy360"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </div>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={!name.trim() || !url.trim()}>
          Create connection
        </button>
      </div>
    </form>
  );
}

function ConnectionCard({
  orgSlug,
  connection,
  catalog,
  onAct,
  onRotated,
}: {
  orgSlug: string;
  connection: Connection;
  catalog: CatalogEvent[];
  onAct: (action: () => Promise<void>, success: string) => void;
  onRotated: (secret: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  // A second rotation ends the first one's overlap at once (the backend keeps
  // one previous secret), so a double click would revoke the secret the
  // receiver still uses. The ref guards the click that lands before React
  // re-renders the button as disabled.
  const [rotating, setRotating] = useState(false);
  const rotatingRef = useRef(false);
  const id = connection.id;
  const disabled = connection.status === "disabled";

  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby={`conn-${id}`}>
      <div className={styles.cardHeader}>
        <h2 id={`conn-${id}`} className={styles.cardTitle}>
          {connection.display_name}
        </h2>
        <span className={STATUS_BADGE[connection.status]}>{connection.status}</span>
      </div>
      <p className={styles.muted}>
        {connection.destination || "—"}
        {connection.status_reason && <> · {connection.status_reason}</>}
        {connection.health.consecutive_failures > 0 && (
          <> · {connection.health.consecutive_failures} consecutive failed attempts</>
        )}
      </p>
      {!disabled && (
        <div className={styles.row}>
          {(connection.status === "draft" || connection.status === "suspended") && (
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              onClick={() => onAct(() => setConnectionStatus(orgSlug, id, "active").then(), "Connection activated.")}
            >
              Activate
            </button>
          )}
          {connection.status === "active" && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              onClick={() => onAct(() => setConnectionStatus(orgSlug, id, "suspended").then(), "Connection suspended.")}
            >
              Suspend
            </button>
          )}
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => onAct(() => pingConnection(orgSlug, id).then(), "Test event queued. Check the delivery log.")}
          >
            Send test event
          </button>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
            Events
          </button>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            disabled={rotating}
            aria-busy={rotating}
            onClick={() =>
              onAct(async () => {
                if (rotatingRef.current) return;
                rotatingRef.current = true;
                setRotating(true);
                try {
                  const rotated = await rotateSecret(orgSlug, id);
                  onRotated(rotated.secret);
                } finally {
                  rotatingRef.current = false;
                  setRotating(false);
                }
              }, "New secret issued. The previous one keeps working for 24 hours.")
            }
          >
            Rotate secret
          </button>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => onAct(() => setConnectionStatus(orgSlug, id, "disabled").then(), "Connection disabled.")}
          >
            Disable
          </button>
        </div>
      )}
      {editing && !disabled && (
        <SubscriptionEditor
          orgSlug={orgSlug}
          connectionId={id}
          catalog={catalog}
          onSave={(filters) =>
            onAct(() => setSubscriptions(orgSlug, id, filters).then(), "Subscriptions saved.")
          }
        />
      )}
    </section>
  );
}

function SubscriptionEditor({
  orgSlug,
  connectionId,
  catalog,
  onSave,
}: {
  orgSlug: string;
  connectionId: string;
  catalog: CatalogEvent[];
  onSave: (filters: string[]) => void;
}) {
  const [selected, setSelected] = useState<Set<string> | null>(null);

  useEffect(() => {
    getSubscriptions(orgSlug, connectionId)
      .then(({ event_filters }) => setSelected(new Set(event_filters)))
      .catch(() => setSelected(new Set()));
  }, [orgSlug, connectionId]);

  if (selected === null) return <p aria-live="polite">Loading subscriptions…</p>;
  if (catalog.length === 0) {
    return (
      <p className={styles.muted}>
        None of your organization&apos;s products publish events yet.
      </p>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave([...selected]);
      }}
    >
      <fieldset className={styles.fieldset}>
        <legend>Send these events to this connection</legend>
        {catalog.map((event) => (
          <label key={event.ref} className={styles.checkbox}>
            <input
              type="checkbox"
              checked={selected.has(event.ref)}
              onChange={(e) => {
                const next = new Set(selected);
                if (e.target.checked) next.add(event.ref);
                else next.delete(event.ref);
                setSelected(next);
              }}
            />
            <span>
              <code>{event.ref}</code>
              {event.description && <span className={styles.muted}> — {event.description}</span>}
            </span>
          </label>
        ))}
      </fieldset>
      <div className={styles.row}>
        <button type="submit" className="w360-btn w360-btn-primary">
          Save events
        </button>
      </div>
    </form>
  );
}
