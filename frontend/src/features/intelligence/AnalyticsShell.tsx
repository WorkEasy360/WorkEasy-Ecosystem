"use client";

import { useEffect, useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import {
  listMetrics,
  queryMetric,
  type MetricDefinition,
  type MetricResult,
  type MetricRow,
} from "@/lib/api/intelligence";
import styles from "./Intelligence.module.css";

type Granularity = "hour" | "day" | "month";

/** Default windows per granularity, inside the server's bounds. */
const WINDOW_DAYS: Record<Granularity, number> = { hour: 2, day: 30, month: 365 };

function utcMidnight(daysAgo: number): Date {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - daysAgo));
}

/** The query window for a granularity, aligned the way the API requires. */
export function windowFor(granularity: Granularity): { start: string; end: string } {
  const end = utcMidnight(-1);
  if (granularity === "month") {
    const now = new Date();
    const start = new Date(Date.UTC(now.getUTCFullYear() - 1, now.getUTCMonth(), 1));
    return { start: start.toISOString(), end: end.toISOString() };
  }
  return { start: utcMidnight(WINDOW_DAYS[granularity] - 1).toISOString(), end: end.toISOString() };
}

function formatValue(row: MetricRow): string {
  if (row.amount_minor !== undefined && row.currency) {
    const exponent = row.currency === "JPY" ? 0 : 2;
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: row.currency }).format(
        row.amount_minor / 10 ** exponent,
      );
    } catch {
      return `${row.amount_minor} ${row.currency} (minor units)`;
    }
  }
  return String(row.value ?? row.count);
}

/**
 * Analytics (Task 90): a generic shell over metrics the organization's
 * products define - not a product dashboard. It lists what exists, runs one
 * bounded query, and says plainly how fresh the answer is.
 */
export function AnalyticsShell({ orgSlug }: { orgSlug: string }) {
  const metricId = useId();
  const granularityId = useId();
  const groupId = useId();
  const [metrics, setMetrics] = useState<MetricDefinition[] | "forbidden" | "error" | null>(null);
  const [selected, setSelected] = useState("");
  const [granularity, setGranularity] = useState<Granularity>("day");
  const [groupBy, setGroupBy] = useState("");
  const [result, setResult] = useState<MetricResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listMetrics(orgSlug)
      .then((body) => {
        setMetrics(body.results);
        if (body.results[0]) setSelected(`${body.results[0].key}@${body.results[0].version}`);
      })
      .catch((err: unknown) => setMetrics(err instanceof ApiError && err.status === 403 ? "forbidden" : "error"));
  }, [orgSlug]);

  if (metrics === null) return <p aria-live="polite">Loading metrics…</p>;
  if (metrics === "forbidden") return <p role="alert">You don&apos;t have permission to view analytics.</p>;
  if (metrics === "error") return <p role="alert">Couldn&apos;t load analytics right now.</p>;
  if (metrics.length === 0) {
    return <p className={styles.muted}>None of this organization&apos;s products publish metrics yet.</p>;
  }

  const [key, version] = selected.split("@");
  const definition = metrics.find((m) => m.key === key && String(m.version) === version);

  async function run() {
    if (!definition) return;
    setBusy(true);
    setError(null);
    try {
      setResult(
        await queryMetric(orgSlug, {
          metric: definition.key,
          version: definition.version,
          granularity,
          group_by: groupBy || undefined,
          ...windowFor(granularity),
        }),
      );
    } catch (err) {
      const body = err instanceof ApiError ? (err.body as { detail?: string } | undefined) : undefined;
      setError(body?.detail ?? "Couldn't run that query right now.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.stack}>
      <form
        className={`w360-card ${styles.card} ${styles.form}`}
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <div className={styles.field}>
          <label className="w360-label" htmlFor={metricId}>
            Metric
          </label>
          <select
            id={metricId}
            className="w360-input"
            value={selected}
            onChange={(e) => {
              setSelected(e.target.value);
              setGroupBy("");
              setResult(null);
            }}
          >
            {metrics.map((m) => (
              <option key={`${m.key}@${m.version}`} value={`${m.key}@${m.version}`}>
                {m.description || m.key} (v{m.version}, {m.owner})
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={granularityId}>
            Granularity
          </label>
          <select
            id={granularityId}
            className="w360-input"
            value={granularity}
            onChange={(e) => setGranularity(e.target.value as Granularity)}
          >
            <option value="hour">Hourly (last 2 days)</option>
            <option value="day">Daily (last 30 days)</option>
            <option value="month">Monthly (last 12 months)</option>
          </select>
        </div>
        {definition && definition.dimensions.length > 0 && (
          <div className={styles.field}>
            <label className="w360-label" htmlFor={groupId}>
              Group by
            </label>
            <select id={groupId} className="w360-input" value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
              <option value="">Nothing</option>
              {definition.dimensions.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !definition}>
            Run query
          </button>
        </div>
      </form>

      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}

      {result && (
        <section aria-labelledby="analytics-result">
          <h2 id="analytics-result" className={styles.sectionTitle}>
            {definition?.description || result.metric.key}
          </h2>
          <p role="status" className={styles.muted}>
            {result.freshness.up_to_date
              ? `Up to date${result.freshness.last_projected_at ? ` as of ${new Date(result.freshness.last_projected_at).toLocaleString()}` : ""}.`
              : `${result.freshness.pending_facts} recent event(s) not yet included - figures are catching up.`}
          </p>
          {result.results.length === 0 ? (
            <p className={styles.muted}>No data in this window.</p>
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className={styles.visuallyHidden}>Metric values by {result.granularity}</caption>
                <thead>
                  <tr>
                    <th scope="col">{result.granularity === "hour" ? "Hour (UTC)" : result.granularity === "day" ? "Day (UTC)" : "Month (UTC)"}</th>
                    {result.group_by.map((g) => (
                      <th key={g} scope="col">
                        {g}
                      </th>
                    ))}
                    <th scope="col">Value</th>
                    <th scope="col">Events</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((row) => (
                    <tr key={`${row.bucket}|${JSON.stringify(row.dimensions)}|${row.currency ?? ""}`}>
                      <td>{row.bucket.slice(0, result.granularity === "hour" ? 16 : 10).replace("T", " ")}</td>
                      {result.group_by.map((g) => (
                        <td key={g}>{row.dimensions[g]}</td>
                      ))}
                      <td>{formatValue(row)}</td>
                      <td>{row.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
