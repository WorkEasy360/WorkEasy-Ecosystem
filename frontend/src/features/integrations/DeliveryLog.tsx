"use client";

import { useEffect, useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { listDeliveries, replayDelivery } from "@/lib/api/integrations";
import type { DeliveryStatus, DeliverySummary } from "@/lib/api/types";
import styles from "./Integrations.module.css";

type Load =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error" }
  | { status: "ready"; rows: DeliverySummary[]; cursor: string | null };

const BADGE: Record<DeliveryStatus, string> = {
  pending: "w360-badge w360-badge-neutral",
  in_progress: "w360-badge w360-badge-neutral",
  retry: "w360-badge w360-badge-warning",
  succeeded: "w360-badge w360-badge-success",
  dead: "w360-badge w360-badge-danger",
};

const FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "dead", label: "Failed (dead)" },
  { value: "retry", label: "Retrying" },
  { value: "pending", label: "Pending" },
  { value: "succeeded", label: "Delivered" },
];

/**
 * Settings → Webhook deliveries (Task 81): status, time, result category and
 * attempts - never the request body, a header, the signing secret or more
 * than a short response excerpt. Replay is offered only for dead deliveries.
 */
export function DeliveryLog({ orgSlug }: { orgSlug: string }) {
  const filterId = useId();
  const [filter, setFilter] = useState("");
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  function fetchPage(cursor?: string, append = false) {
    listDeliveries(orgSlug, { status: filter || undefined, cursor })
      .then((page) =>
        setLoad((current) => ({
          status: "ready",
          rows: append && current.status === "ready" ? [...current.rows, ...page.results] : page.results,
          cursor: page.next_cursor,
        })),
      )
      .catch((err: unknown) =>
        setLoad({ status: err instanceof ApiError && err.status === 403 ? "forbidden" : "error" }),
      );
  }

  useEffect(() => fetchPage(), [orgSlug, filter]); // eslint-disable-line react-hooks/exhaustive-deps

  async function replay(row: DeliverySummary) {
    setNotice(null);
    setPending(row.id);
    try {
      await replayDelivery(orgSlug, row.id);
      setNotice("Replay queued as a new delivery.");
      fetchPage();
    } catch (err) {
      const body = err instanceof ApiError ? (err.body as { detail?: string }) : undefined;
      setNotice(body?.detail ?? "The replay could not be queued.");
    } finally {
      setPending(null);
    }
  }

  return (
    <div className={styles.stack}>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={filterId}>
          Show
        </label>
        <select id={filterId} className="w360-input" value={filter} onChange={(e) => setFilter(e.target.value)}>
          {FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>
      {notice && <p role="status">{notice}</p>}
      {load.status === "loading" && <p aria-live="polite">Loading deliveries…</p>}
      {load.status === "forbidden" && <p role="alert">You don&apos;t have permission to view deliveries.</p>}
      {load.status === "error" && <p role="alert">Couldn&apos;t load deliveries right now.</p>}
      {load.status === "ready" && load.rows.length === 0 && <p>No deliveries yet.</p>}
      {load.status === "ready" && load.rows.length > 0 && (
        <div className={`w360-card ${styles.tableWrap}`}>
          <table className={styles.table}>
            <caption className="w360-visually-hidden">Webhook deliveries, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Event</th>
                <th scope="col">Connection</th>
                <th scope="col">Status</th>
                <th scope="col">Attempts</th>
                <th scope="col">Last result</th>
                <th scope="col">
                  <span className="w360-visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {load.rows.map((row) => (
                <tr key={row.id}>
                  <td>{new Date(row.created_at).toLocaleString()}</td>
                  <td>
                    <code>{row.event ? `${row.event.event_type}.v${row.event.event_version}` : "—"}</code>
                  </td>
                  <td>{row.connection_name}</td>
                  <td>
                    <span className={BADGE[row.status]}>{row.status.replace("_", " ")}</span>
                    {row.dead_reason && <div className={styles.muted}>{row.dead_reason.replaceAll("_", " ")}</div>}
                  </td>
                  <td>
                    {row.attempts} / {row.max_attempts}
                  </td>
                  <td>{row.last_http_status ?? (row.last_category || "—")}</td>
                  <td>
                    {row.replayable && (
                      <button
                        type="button"
                        className="w360-btn w360-btn-secondary"
                        disabled={pending === row.id}
                        onClick={() => replay(row)}
                        aria-label={`Replay delivery of ${row.event?.event_type ?? "event"} to ${row.connection_name}`}
                      >
                        Replay
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {load.status === "ready" && load.cursor && (
        <div>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => fetchPage(load.cursor ?? undefined, true)}>
            Load more
          </button>
        </div>
      )}
    </div>
  );
}
