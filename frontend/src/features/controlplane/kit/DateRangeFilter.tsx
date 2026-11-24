"use client";

import { useEffect, useId, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Icon } from "@/components/sidebar/icons";
import { Button } from "./Button";
import { usePopover } from "./Menu";
import k from "./Kit.module.css";

const noSubscription = () => () => {};

/** A whole day as the server reads it: `YYYY-MM-DD`, in UTC. */
export type Day = string;

export interface DayRange {
  from: Day;
  to: Day;
}

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Whether `value` is a real calendar day written `YYYY-MM-DD`. */
export function isDay(value: string): boolean {
  if (!DAY_RE.test(value)) return false;
  const at = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(at.getTime()) && at.toISOString().slice(0, 10) === value;
}

/** Today, or `days` before it, as a UTC day (the server's whole-day bounds are UTC). */
export function utcDay(daysBefore = 0, now: Date = new Date()): Day {
  return new Date(now.getTime() - daysBefore * 86_400_000).toISOString().slice(0, 10);
}

/** The console's one way to write a date ("18 Nov 2026", as `formatDate`), read in
 * UTC so the time zone never shifts a day - whatever the browser's language. */
const DAY_FORMAT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** "18 Nov 2026" for a `YYYY-MM-DD` day. */
export function dayLabel(day: Day): string {
  if (!isDay(day)) return day;
  return DAY_FORMAT.format(new Date(`${day}T00:00:00Z`)).replace("Sept", "Sep");
}

/** "1 Nov 2026 – 18 Nov 2026", "From 1 Nov 2026", "Until 18 Nov 2026", or "Any time". */
export function rangeLabel({ from, to }: DayRange): string {
  if (from && to) return from === to ? dayLabel(from) : `${dayLabel(from)} – ${dayLabel(to)}`;
  if (from) return `From ${dayLabel(from)}`;
  if (to) return `Until ${dayLabel(to)}`;
  return "Any time";
}

/** What is wrong with a range, or null. Either end may be empty. */
export function rangeProblem({ from, to }: DayRange, today: Day = utcDay()): string | null {
  if (from && !isDay(from)) return "Enter the start date in full.";
  if (to && !isDay(to)) return "Enter the end date in full.";
  if (from && to && from > to) return "The start date is after the end date.";
  if (from && from > today) return "The start date is in the future.";
  return null;
}

function presets(now: Date = new Date()): { label: string; range: DayRange }[] {
  const today = utcDay(0, now);
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth();
  const first = (year: number, month: number) => new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10);
  return [
    { label: "Today", range: { from: today, to: today } },
    { label: "Last 7 days", range: { from: utcDay(6, now), to: today } },
    { label: "Last 30 days", range: { from: utcDay(29, now), to: today } },
    { label: "This month", range: { from: first(y, m), to: today } },
    {
      label: "Last month",
      range: { from: first(y, m - 1), to: new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10) },
    },
  ];
}

/**
 * A date-range filter (2026-11-18, admin-ux phase 3): a button that says the
 * range ("Created: Last 30 days" reads as "Created: 20 Oct 2026 – 18 Nov 2026")
 * and a small panel with quick ranges, From and To, and **Apply** / **Reset** -
 * the range is sent to the server only on Apply, so half-typed dates never
 * reload the list. Whole UTC days, both ends included, either end optional.
 * Keyboard: the panel takes focus, Tab stays inside it, Escape closes it and
 * returns to the button.
 */
export function DateRangeFilter({
  label,
  value,
  onApply,
}: {
  /** What the dates are ("Created", "Issued", "Signed up"). */
  label: string;
  value: DayRange;
  onApply: (range: DayRange) => void;
}) {
  const { open, setOpen, triggerRef: trigger, panelRef: panel } = usePopover<HTMLButtonElement, HTMLDivElement>("start");
  const inBrowser = useSyncExternalStore(noSubscription, () => true, () => false);
  const titleId = useId();
  const fromId = useId();
  const toId = useId();
  const problemId = useId();
  const [draft, setDraft] = useState<DayRange>(value);
  const today = utcDay();
  const problem = rangeProblem(draft, today);
  const unchanged = draft.from === value.from && draft.to === value.to;
  const set = Boolean(value.from || value.to);

  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector<HTMLElement>("button, input")?.focus();
  }, [open, panel]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab" || !panel.current) return;
    // Keep Tab inside the panel while it is open.
    const items = Array.from(panel.current.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled])"));
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const apply = (range: DayRange) => {
    onApply(range);
    close();
  };

  return (
    <span className={k.select}>
      <button
        ref={trigger}
        type="button"
        className={k.selectButton}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-set={set || undefined}
        onClick={() => {
          if (!open) setDraft(value);
          setOpen(!open);
        }}
      >
        <Icon name="calendar" size={16} />
        <span className={k.selectValue}>
          <span className={k.selectPrefix}>{label}:</span> {rangeLabel(value)}
        </span>
        <Icon name="chevronDown" size={16} />
      </button>
      {open &&
        inBrowser &&
        createPortal(
          <div
            ref={panel}
            role="dialog"
            aria-labelledby={titleId}
            className={`${k.menu} ${k.datePanel}`}
            onKeyDown={onKeyDown}
          >
            <p id={titleId} className={k.datePanelTitle}>
              {label} between
            </p>
            <div className={k.datePresets} role="group" aria-label="Quick ranges">
              {presets().map((p) => (
                <button
                  key={p.label}
                  type="button"
                  className={k.chip}
                  aria-pressed={draft.from === p.range.from && draft.to === p.range.to}
                  onClick={() => setDraft(p.range)}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className={k.dateFields}>
              <label htmlFor={fromId} className={k.dateField}>
                <span>From</span>
                <input
                  id={fromId}
                  type="date"
                  className={k.field}
                  value={draft.from}
                  max={draft.to || today}
                  aria-describedby={problem ? problemId : undefined}
                  aria-invalid={problem && problem.includes("start") ? true : undefined}
                  onChange={(e) => setDraft({ ...draft, from: e.target.value })}
                />
              </label>
              <label htmlFor={toId} className={k.dateField}>
                <span>To</span>
                <input
                  id={toId}
                  type="date"
                  className={k.field}
                  value={draft.to}
                  min={draft.from || undefined}
                  max={today}
                  aria-describedby={problem ? problemId : undefined}
                  aria-invalid={problem && problem.includes("end") ? true : undefined}
                  onChange={(e) => setDraft({ ...draft, to: e.target.value })}
                />
              </label>
            </div>
            {problem && (
              <p id={problemId} role="alert" className={k.dateProblem}>
                {problem}
              </p>
            )}
            <div className={k.datePanelFoot}>
              <Button variant="link" onClick={() => apply({ from: "", to: "" })} disabled={!set && !draft.from && !draft.to}>
                Reset
              </Button>
              <Button variant="primary" size="sm" disabled={Boolean(problem) || unchanged} onClick={() => apply(draft)}>
                Apply
              </Button>
            </div>
          </div>,
          document.body,
        )}
    </span>
  );
}
