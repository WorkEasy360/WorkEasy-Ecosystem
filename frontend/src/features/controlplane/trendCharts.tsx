"use client";

import { useState } from "react";
import styles from "./AdminDashboard.module.css";

/**
 * The dashboard's month-by-month charts (2026-11-09): several lines, and pairs of
 * columns. Dependency-free SVG like `dashboardCharts.tsx`, with the same rule: the SVG
 * is `aria-hidden` and the same numbers are in a visually hidden `<table>`. A series
 * is never told apart by colour alone: the legend names each, and the tooltip too.
 */

export interface Series {
  key: string;
  label: string;
  /** One of the series colours in AdminDashboard.module.css. */
  tone: "green" | "blue" | "orange" | "deep" | "light";
  values: number[];
}

const W = 460;
const H = 230;
const PAD = { top: 12, right: 12, bottom: 28, left: 52 };
const innerH = H - PAD.top - PAD.bottom;
const innerW = W - PAD.left - PAD.right;

function niceMax(max: number): number {
  if (max <= 0) return 4;
  const power = 10 ** Math.floor(Math.log10(max));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * power >= max) return step * power;
  return 10 * power;
}

const MONTH = new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" });
const MONTH_YEAR = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });

const clean = (text: string) => text.replace("Sept", "Sep");

/** "2026-11" to "Nov". */
export const monthLabel = (key: string) => clean(MONTH.format(new Date(`${key}-01T00:00:00Z`)));
const monthLong = (key: string) => clean(MONTH_YEAR.format(new Date(`${key}-01T00:00:00Z`)));

function Grid({ months, max, format }: { months: string[]; max: number; format: (n: number) => string }) {
  const step = innerW / Math.max(months.length, 1);
  return (
    <>
      {[0, 1, 2, 3, 4].map((i) => {
        const y = PAD.top + innerH - (innerH * i) / 4;
        return (
          <g key={i}>
            <line className={styles.grid} x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} />
            <text className={styles.axis} x={PAD.left - 8} y={y + 4} textAnchor="end">
              {format((max / 4) * i)}
            </text>
          </g>
        );
      })}
      {months.map((m, i) => (
        <text key={m} className={styles.axis} x={PAD.left + step * i + step / 2} y={H - 8} textAnchor="middle">
          {monthLabel(m)}
        </text>
      ))}
    </>
  );
}

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

function HiddenTable({ caption, months, series, format }: { caption: string; months: string[]; series: Series[]; format: (n: number) => string }) {
  return (
    <table className="w360-visually-hidden">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Month</th>
          {series.map((s) => (
            <th key={s.key} scope="col">
              {s.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {months.map((m, i) => (
          <tr key={m}>
            <th scope="row">{monthLong(m)}</th>
            {series.map((s) => (
              <td key={s.key}>{format(s.values[i] ?? 0)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TipRows({ month, series, index, format }: { month: string; series: Series[]; index: number; format: (n: number) => string }) {
  return (
    <>
      <strong>{monthLong(month)}</strong>
      {series.map((s) => (
        <span key={s.key} className={styles.tipRow}>
          <span aria-hidden="true" className={styles.swatch} data-tone={s.tone} />
          {s.label}: {format(s.values[index] ?? 0)}
        </span>
      ))}
    </>
  );
}

/** Several values per month as lines, the first over a soft area. */
export function MultiLineChart({
  caption,
  months,
  series,
  format,
}: {
  caption: string;
  months: string[];
  series: Series[];
  format: (n: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values)));
  const step = innerW / Math.max(months.length, 1);
  const x = (i: number) => PAD.left + step * i + step / 2;
  const y = (v: number) => PAD.top + innerH - (innerH * v) / max;
  const path = (values: number[]) =>
    values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <figure className={styles.figure}>
      <div className={styles.chartScroll}>
        <div className={styles.plot}>
          <svg viewBox={`0 0 ${W} ${H}`} className={styles.svg} aria-hidden="true" focusable="false">
            <Grid months={months} max={max} format={format} />
            {series.map((s) => {
              const line = path(s.values);
              const area = line && months.length > 0 ? `${line} L${x(months.length - 1).toFixed(1)},${y(0)} L${x(0).toFixed(1)},${y(0)} Z` : "";
              return (
                <g key={s.key} data-tone={s.tone} className={styles.seriesGroup}>
                  {area && <path className={styles.seriesArea} d={area} />}
                  {line && <path className={styles.seriesLine} d={line} />}
                  {s.values.map((v, i) => (
                    <circle key={months[i]} className={styles.seriesPoint} cx={x(i)} cy={y(v)} r={hover === i ? 5 : 3.5} />
                  ))}
                </g>
              );
            })}
            {hover !== null && <line className={styles.crosshair} x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={y(0)} />}
            {months.map((m, i) => (
              <rect
                key={m}
                x={x(i) - step / 2}
                y={PAD.top}
                width={step}
                height={innerH}
                className={styles.hit}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              />
            ))}
          </svg>
          {hover !== null && months[hover] && (
            <Tip x={x(hover)} y={y(Math.max(0, ...series.map((s) => s.values[hover] ?? 0)))}>
              <TipRows month={months[hover]} series={series} index={hover} format={format} />
            </Tip>
          )}
        </div>
      </div>
      <HiddenTable caption={caption} months={months} series={series} format={format} />
    </figure>
  );
}

/** Two or more columns side by side for each month. */
export function GroupedBars({
  caption,
  months,
  series,
  format,
}: {
  caption: string;
  months: string[];
  series: Series[];
  format: (n: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values)));
  const step = innerW / Math.max(months.length, 1);
  const barW = Math.min((step * 0.7) / Math.max(series.length, 1), 22);
  const y = (v: number) => PAD.top + innerH - (innerH * Math.max(v, 0)) / max;
  const groupX = (i: number) => PAD.left + step * i + step / 2 - (barW * series.length) / 2;
  return (
    <figure className={styles.figure}>
      <div className={styles.chartScroll}>
        <div className={styles.plot}>
          <svg viewBox={`0 0 ${W} ${H}`} className={styles.svg} aria-hidden="true" focusable="false">
            <Grid months={months} max={max} format={format} />
            {months.map((m, i) => (
              <g key={m}>
                {series.map((s, k) => {
                  const value = s.values[i] ?? 0;
                  return (
                    <rect
                      key={s.key}
                      className={styles.seriesBar}
                      data-tone={s.tone}
                      x={groupX(i) + barW * k + 1}
                      y={y(value)}
                      width={Math.max(barW - 2, 1)}
                      height={Math.max(PAD.top + innerH - y(value), 0)}
                      rx={2}
                    />
                  );
                })}
                <rect
                  x={PAD.left + step * i}
                  y={PAD.top}
                  width={step}
                  height={innerH}
                  className={styles.hit}
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover(null)}
                />
              </g>
            ))}
          </svg>
          {hover !== null && months[hover] && (
            <Tip x={PAD.left + step * hover + step / 2} y={y(Math.max(0, ...series.map((s) => s.values[hover] ?? 0)))}>
              <TipRows month={months[hover]} series={series} index={hover} format={format} />
            </Tip>
          )}
        </div>
      </div>
      <HiddenTable caption={caption} months={months} series={series} format={format} />
    </figure>
  );
}
