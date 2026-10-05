"use client";

import { useId } from "react";
import styles from "./Charts.module.css";

/**
 * Small, dependency-free charts for the consoles (Phase 8 extension; no chart
 * library is added - see docs/phase-4/01-frontend-architecture.md).
 *
 * Every chart is decorative-plus-equivalent: the SVG is `aria-hidden` and the
 * same numbers are in a real `<table>` (visually hidden unless the reader
 * asks to see it), so a screen-reader user gets the data, not a picture of it
 * (WCAG 1.1.1). Bars are drawn in `currentColor`, which follows the theme and
 * forced-colours mode.
 */

export interface BarDatum {
  label: string;
  value: number;
}

export function BarChart({
  title,
  data,
  valueLabel,
  empty = "No activity in this period.",
}: {
  title: string;
  data: BarDatum[];
  valueLabel: string;
  empty?: string;
}) {
  const tableId = useId();
  const max = Math.max(1, ...data.map((d) => d.value));
  const total = data.reduce((sum, d) => sum + d.value, 0);
  const width = 640;
  const height = 180;
  const gap = 6;
  const barWidth = data.length ? Math.max(4, (width - gap * (data.length - 1)) / data.length) : 0;

  return (
    <figure className={styles.figure}>
      <figcaption className={styles.caption}>{title}</figcaption>
      {total === 0 ? (
        <p className={styles.empty}>{empty}</p>
      ) : (
        <svg
          className={styles.svg}
          viewBox={`0 0 ${width} ${height + 20}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          focusable="false"
        >
          <line x1="0" x2={width} y1={height} y2={height} className={styles.axis} />
          {data.map((d, i) => {
            const h = Math.round((d.value / max) * (height - 8));
            return (
              <rect
                key={d.label}
                x={i * (barWidth + gap)}
                y={height - h}
                width={barWidth}
                height={h}
                rx="2"
                className={styles.bar}
              />
            );
          })}
        </svg>
      )}
      <details className={styles.details}>
        <summary>Show as a table</summary>
        <table id={tableId} className={styles.table}>
          <caption className="w360-visually-hidden">{title}</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">{valueLabel}</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.label}>
                <td>{d.label}</td>
                <td className={styles.num}>{d.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** One headline number with its label and an optional note. */
export function StatTile({
  label,
  value,
  note,
}: {
  label: string;
  value: React.ReactNode;
  note?: React.ReactNode;
}) {
  return (
    <div className={`w360-card ${styles.tile}`}>
      <p className={styles.tileLabel}>{label}</p>
      <p className={styles.tileValue}>{value}</p>
      {note && <p className={styles.tileNote}>{note}</p>}
    </div>
  );
}
