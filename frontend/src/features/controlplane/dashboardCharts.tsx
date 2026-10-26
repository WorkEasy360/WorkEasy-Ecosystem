"use client";

import { useState } from "react";
import styles from "./AdminDashboard.module.css";

/**
 * The Super Admin dashboard's charts (2026-10-26): a line for money per day, a
 * column per day for sign-ups, a donut for orders per product. Dependency-free
 * SVG, like components/charts. Every chart is decorative-plus-equivalent: the
 * SVG is `aria-hidden` and the same numbers are in a visually hidden `<table>`
 * (WCAG 1.1.1); pointer users also get a tooltip per day or per slice. Colours
 * are tokens (globals.css), the categorical order validated for colour-vision
 * deficiency.
 */

export interface DayValue {
  /** ISO date. */
  day: string;
  /** `null` for a day still to come: nothing is drawn for it. */
  value: number | null;
}

const W = 400;
const H = 210;
const PAD = { top: 12, right: 10, bottom: 28, left: 44 };

/** A round top for the axis: 1, 2, 2.5 or 5 times a power of ten. */
function niceMax(max: number): number {
  if (max <= 0) return 4;
  const power = 10 ** Math.floor(Math.log10(max));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * power >= max) return step * power;
  return 10 * power;
}

const dayLabel = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

/** Axis ticks every 5 days and the last day, as in "1 Oct, 5 Oct ... 31 Oct". */
const isTick = (index: number, count: number) => index === 0 || index === count - 1 || ((index + 1) % 5 === 0 && count - index > 2);

function Frame({
  days,
  max,
  format,
  children,
}: {
  days: DayValue[];
  max: number;
  format: (n: number) => string;
  children: React.ReactNode;
}) {
  const innerH = H - PAD.top - PAD.bottom;
  const innerW = W - PAD.left - PAD.right;
  const step = innerW / Math.max(days.length, 1);
  return (
    <>
      {[0, 1, 2, 3, 4].map((i) => {
        const value = (max / 4) * i;
        const y = PAD.top + innerH - (innerH * i) / 4;
        return (
          <g key={i}>
            <line className={styles.grid} x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} />
            <text className={styles.axis} x={PAD.left - 8} y={y + 4} textAnchor="end">
              {format(value)}
            </text>
          </g>
        );
      })}
      {days.map((d, i) => {
        if (!isTick(i, days.length)) return null;
        // The first and last labels align to the plot's edges, so neither is clipped.
        const first = i === 0;
        const last = i === days.length - 1;
        return (
          <text
            key={d.day}
            className={styles.axis}
            x={first ? PAD.left : last ? W - PAD.right : PAD.left + step * i + step / 2}
            y={H - 8}
            textAnchor={first ? "start" : last ? "end" : "middle"}
          >
            {dayLabel(d.day)}
          </text>
        );
      })}
      {children}
    </>
  );
}

function DataTable({ caption, days, format }: { caption: string; days: DayValue[]; format: (n: number) => string }) {
  return (
    <table className="w360-visually-hidden">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Day</th>
          <th scope="col">Value</th>
        </tr>
      </thead>
      <tbody>
        {days
          .filter((d) => d.value !== null)
          .map((d) => (
            <tr key={d.day}>
              <th scope="row">{dayLabel(d.day)}</th>
              <td>{format(d.value ?? 0)}</td>
            </tr>
          ))}
      </tbody>
    </table>
  );
}

/** Where a tooltip sits, as a share of the chart's box (it scales with it).
 * Placed through the CSSOM, never an inline style attribute (D-010). */
function Tip({ x, y, children }: { x: number; y: number; children: React.ReactNode }) {
  const place = (el: HTMLDivElement | null) => {
    if (!el) return;
    el.style.left = `${(x / W) * 100}%`;
    el.style.top = `${(y / H) * 100}%`;
  };
  return (
    <div ref={place} className={styles.tip} aria-hidden="true">
      {children}
    </div>
  );
}

/** One value per day as a line over a soft area, a crosshair and tooltip on hover. */
export function LineChart({ caption, days, format }: { caption: string; days: DayValue[]; format: (n: number) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = niceMax(Math.max(0, ...days.map((d) => d.value ?? 0)));
  const innerH = H - PAD.top - PAD.bottom;
  const step = (W - PAD.left - PAD.right) / Math.max(days.length, 1);
  const x = (i: number) => PAD.left + step * i + step / 2;
  const y = (v: number) => PAD.top + innerH - (innerH * v) / max;
  const drawn = days.map((d, i) => ({ ...d, i })).filter((d) => d.value !== null);
  const line = drawn.map((d, k) => `${k === 0 ? "M" : "L"}${x(d.i).toFixed(1)},${y(d.value ?? 0).toFixed(1)}`).join(" ");
  const last = drawn[drawn.length - 1];
  const area = last ? `${line} L${x(last.i).toFixed(1)},${y(0)} L${x(drawn[0].i).toFixed(1)},${y(0)} Z` : "";
  const shown = hover !== null && days[hover]?.value !== null ? hover : null;
  return (
    <figure className={styles.figure}>
      <div className={styles.plot}>
        <svg viewBox={`0 0 ${W} ${H}`} className={styles.svg} aria-hidden="true" focusable="false">
          <Frame days={days} max={max} format={format}>
            {area && <path className={styles.area} d={area} />}
            {line && <path className={styles.line} d={line} />}
            {shown !== null && (
              <line className={styles.crosshair} x1={x(shown)} x2={x(shown)} y1={PAD.top} y2={y(0)} />
            )}
            {drawn.map((d) =>
              d.i === shown || d === last ? (
                <circle key={d.day} className={styles.point} cx={x(d.i)} cy={y(d.value ?? 0)} r={4.5} />
              ) : null,
            )}
            {days.map((d, i) => (
              <rect
                key={d.day}
                x={x(i) - step / 2}
                y={PAD.top}
                width={step}
                height={innerH}
                className={styles.hit}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              />
            ))}
          </Frame>
        </svg>
        {shown !== null && (
          <Tip x={x(shown)} y={y(days[shown].value ?? 0)}>
            <strong>{format(days[shown].value ?? 0)}</strong>
            <span>{dayLabel(days[shown].day)}</span>
          </Tip>
        )}
      </div>
      <DataTable caption={caption} days={days} format={format} />
    </figure>
  );
}

/** One column per day, rounded at the top and anchored to the baseline. */
export function ColumnChart({ caption, days, format }: { caption: string; days: DayValue[]; format: (n: number) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = niceMax(Math.max(0, ...days.map((d) => d.value ?? 0)));
  const innerH = H - PAD.top - PAD.bottom;
  const step = (W - PAD.left - PAD.right) / Math.max(days.length, 1);
  const width = Math.max(step - 3, 2);
  const base = PAD.top + innerH;
  const shown = hover !== null && days[hover]?.value !== null ? hover : null;
  return (
    <figure className={styles.figure}>
      <div className={styles.plot}>
        <svg viewBox={`0 0 ${W} ${H}`} className={styles.svg} aria-hidden="true" focusable="false">
          <Frame days={days} max={max} format={format}>
            {days.map((d, i) => {
              if (!d.value) return null;
              const h = Math.max((innerH * d.value) / max, 2);
              const left = PAD.left + step * i + (step - width) / 2;
              const r = Math.min(4, width / 2, h);
              const top = base - h;
              const path = `M${left},${base} V${top + r} Q${left},${top} ${left + r},${top} H${left + width - r} Q${left + width},${top} ${left + width},${top + r} V${base} Z`;
              return <path key={d.day} d={path} className={i === shown ? styles.columnActive : styles.column} />;
            })}
            {days.map((d, i) => (
              <rect
                key={d.day}
                x={PAD.left + step * i}
                y={PAD.top}
                width={step}
                height={innerH}
                className={styles.hit}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              />
            ))}
          </Frame>
        </svg>
        {shown !== null && (
          <Tip x={PAD.left + step * shown + step / 2} y={base - (innerH * (days[shown].value ?? 0)) / max}>
            <strong>{format(days[shown].value ?? 0)}</strong>
            <span>{dayLabel(days[shown].day)}</span>
          </Tip>
        )}
      </div>
      <DataTable caption={caption} days={days} format={format} />
    </figure>
  );
}

export interface Slice {
  key: string;
  label: string;
  value: number;
}

/** The categorical order, validated (dataviz validate_palette: all checks pass
 * in this order); a sixth and later slice folds into "Others", in grey. */
const SLICE_CLASSES = ["slice1", "slice2", "slice3", "slice4", "slice5"] as const;
const MAX_SLICES = SLICE_CLASSES.length;

export function foldSlices(slices: Slice[]): Slice[] {
  if (slices.length <= MAX_SLICES + 1) return slices;
  const kept = slices.slice(0, MAX_SLICES);
  const rest = slices.slice(MAX_SLICES).reduce((sum, s) => sum + s.value, 0);
  return [...kept, { key: "__others", label: "Others", value: rest }];
}

/** A donut with the total in the middle and a legend (name and share) beside
 * it, so no slice is told apart by colour alone. */
export function Donut({ caption, slices, centre }: { caption: string; slices: Slice[]; centre: string }) {
  const [hover, setHover] = useState<string | null>(null);
  const shown = foldSlices(slices);
  const total = shown.reduce((sum, s) => sum + s.value, 0);
  const size = 200;
  const r = 78;
  const thickness = 30;
  const c = size / 2;
  // Each slice's start and sweep, worked out before drawing.
  const arcs = shown.map((s, i) => {
    const before = shown.slice(0, i).reduce((sum, p) => sum + p.value, 0);
    return {
      start: -Math.PI / 2 + (total > 0 ? (before / total) * Math.PI * 2 : 0),
      sweep: total > 0 ? (s.value / total) * Math.PI * 2 : 0,
    };
  });
  const share = (v: number) => (total > 0 ? Math.round((v / total) * 100) : 0);
  const cls = (i: number, key: string) => (key === "__others" ? styles.sliceOther : styles[SLICE_CLASSES[i] ?? "sliceOther"]);
  const hovered = shown.find((s) => s.key === hover);
  return (
    <figure className={`${styles.figure} ${styles.donutFigure}`}>
      <div className={styles.donutPlot}>
        <svg viewBox={`0 0 ${size} ${size}`} className={styles.donutSvg} aria-hidden="true" focusable="false">
          {shown.map((s, i) => {
            const { start, sweep } = arcs[i];
            if (sweep <= 0) return null;
            if (sweep >= Math.PI * 2 - 1e-6) {
              return (
                <circle
                  key={s.key}
                  cx={c}
                  cy={c}
                  r={r}
                  className={`${cls(i, s.key)} ${styles.ring}`}
                  onMouseEnter={() => setHover(s.key)}
                  onMouseLeave={() => setHover(null)}
                />
              );
            }
            const end = start + sweep;
            const outer = r + thickness / 2;
            const inner = r - thickness / 2;
            const large = sweep > Math.PI ? 1 : 0;
            const p = (radius: number, a: number) => `${(c + radius * Math.cos(a)).toFixed(2)},${(c + radius * Math.sin(a)).toFixed(2)}`;
            const d = `M${p(outer, start)} A${outer},${outer} 0 ${large} 1 ${p(outer, end)} L${p(inner, end)} A${inner},${inner} 0 ${large} 0 ${p(inner, start)} Z`;
            return (
              <path
                key={s.key}
                d={d}
                className={`${cls(i, s.key)} ${styles.slice}`}
                data-active={hover === s.key || undefined}
                onMouseEnter={() => setHover(s.key)}
                onMouseLeave={() => setHover(null)}
              />
            );
          })}
        </svg>
        <div className={styles.donutCentre} aria-hidden="true">
          <strong>{hovered ? hovered.value : total}</strong>
          <span>{hovered ? hovered.label : centre}</span>
        </div>
      </div>
      <ul className={styles.legend}>
        {shown.map((s, i) => (
          <li key={s.key} data-active={hover === s.key || undefined}>
            <span aria-hidden="true" className={`${styles.swatch} ${cls(i, s.key)}`} />
            <span className={styles.legendLabel}>{s.label}</span>
            <span className={styles.legendValue}>
              {share(s.value)}%<span className="w360-visually-hidden"> ({s.value})</span>
            </span>
          </li>
        ))}
      </ul>
      <figcaption className="w360-visually-hidden">{caption}</figcaption>
    </figure>
  );
}
