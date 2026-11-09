"use client";

import { IconButton, formatNumber } from "./kit";
import styles from "./AdminListParts.module.css";

/**
 * Two pieces the Admin console's offset-paged lists share (2026-11-09): the page
 * controls under a table, and a from/to pair of dates in a filter bar. The table's own
 * footer pages rows it already holds; these lists hold one server page at a time.
 */

/** "Showing 26–50 of 132", previous and next. Asks for a page; never computes one the
 * server did not offer. */
export function OffsetPager({
  total,
  limit,
  offset,
  shown,
  onOffset,
}: {
  total: number;
  limit: number;
  offset: number;
  /** How many rows this page really holds. */
  shown: number;
  onOffset: (next: number) => void;
}) {
  if (total === 0 && offset === 0) return null;
  const words = shown === 0 ? "Nothing on this page" : `Showing ${formatNumber(offset + 1)}–${formatNumber(offset + shown)} of ${formatNumber(total)}`;
  return (
    <nav className={styles.pager} aria-label="Pages">
      <span aria-live="polite">{words}</span>
      <span className={styles.pagerButtons}>
        <IconButton icon="chevronLeft" label="Previous page" size="sm" variant="secondary" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - limit))} />
        <IconButton icon="chevronRight" label="Next page" size="sm" variant="secondary" disabled={offset + limit >= total} onClick={() => onOffset(offset + limit)} />
      </span>
    </nav>
  );
}

/** From and to, as whole days, for a filter bar. Either may be left empty. */
export function DateRange({
  from,
  to,
  onFrom,
  onTo,
}: {
  from: string;
  to: string;
  onFrom: (value: string) => void;
  onTo: (value: string) => void;
}) {
  return (
    <span className={styles.dates}>
      <label className={styles.dateField}>
        <span>From</span>
        <input type="date" className="w360-input" value={from} max={to || undefined} onChange={(e) => onFrom(e.target.value)} />
      </label>
      <label className={styles.dateField}>
        <span>To</span>
        <input type="date" className="w360-input" value={to} min={from || undefined} onChange={(e) => onTo(e.target.value)} />
      </label>
    </span>
  );
}

/** A day as the server reads it (UTC), `days` before today. */
export function dayBefore(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}
