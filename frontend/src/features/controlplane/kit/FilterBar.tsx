"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import { Button } from "./Button";
import { formatNumber } from "./format";
import k from "./Kit.module.css";

/** A value that settles `delay` ms after its last change (search as you type). */
export function useDebounced<T>(value: T, delay = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(t);
  }, [value, delay]);
  return settled;
}

/**
 * The search box: a magnifier, the text, a clear button. It searches as the
 * person types (300 ms after the last key) - there is no separate Search
 * button. Enter applies at once.
 */
export function SearchInput({
  label,
  value,
  onSearch,
  placeholder,
  delay = 300,
  maxLength = 100,
}: {
  /** The accessible name ("Search users"). */
  label: string;
  /** The applied search (from the caller's state or the address). */
  value: string;
  /** Called with the trimmed text once it settles, on Enter and on clear. */
  onSearch: (value: string) => void;
  placeholder?: string;
  delay?: number;
  maxLength?: number;
}) {
  const id = useId();
  const [draft, setDraft] = useState(value);
  const [synced, setSynced] = useState(value);
  // A new applied value from outside (a chip, the address) replaces the draft.
  if (value !== synced) {
    setSynced(value);
    setDraft(value);
  }
  const settled = useDebounced(draft, delay);
  const latest = useRef(onSearch);
  useEffect(() => {
    latest.current = onSearch;
  }, [onSearch]);
  useEffect(() => {
    if (settled.trim() !== value) latest.current(settled.trim());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled]);

  return (
    <div className={k.search} role="search">
      <label htmlFor={id} className="w360-visually-hidden">
        {label}
      </label>
      <Icon name="search" size={16} />
      <input
        id={id}
        type="search"
        className={k.searchInput}
        value={draft}
        placeholder={placeholder ?? `${label}…`}
        maxLength={maxLength}
        autoComplete="off"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onSearch(draft.trim());
          } else if (e.key === "Escape" && draft) {
            e.preventDefault();
            setDraft("");
            onSearch("");
          }
        }}
      />
      {draft && (
        <button
          type="button"
          className={k.searchClear}
          aria-label="Clear search"
          title="Clear search"
          onClick={() => {
            setDraft("");
            onSearch("");
          }}
        >
          <Icon name="x" size={14} />
        </button>
      )}
    </div>
  );
}

/**
 * One row above a table: search first, then filters, "Clear filters" when any
 * is set, and the right side for Export or the column picker. Wraps cleanly
 * on a narrow screen.
 */
export function FilterBar({
  children,
  end,
  onClear,
  label = "Filters",
}: {
  children: React.ReactNode;
  /** Right-aligned controls (Export CSV, Columns). */
  end?: React.ReactNode;
  /** Shown as "Clear filters" when given (pass it only while a filter is set). */
  onClear?: () => void;
  label?: string;
}) {
  return (
    <div className={k.filterBar} role="group" aria-label={label}>
      <div className={k.filterGrow}>
        {children}
        {onClear && (
          <Button variant="link" onClick={onClear}>
            Clear filters
          </Button>
        )}
      </div>
      {end && <div className={k.filterEnd}>{end}</div>}
    </div>
  );
}

export interface Chip<V extends string = string> {
  value: V;
  label: string;
  count?: number | null;
}

/** Filter chips with counts ("All 9 · Subscribed 2 · Trial 0"): toggle
 * buttons, one pressed. Scrolls sideways when the row is narrow. */
export function StatusChips<V extends string = string>({
  label,
  chips,
  value,
  onChange,
}: {
  label: string;
  chips: Chip<V>[];
  value: V;
  onChange: (value: V) => void;
}) {
  return (
    <div className={k.chips} role="group" aria-label={label}>
      {chips.map((chip) => (
        <button
          key={chip.value || "all"}
          type="button"
          className={k.chip}
          aria-pressed={chip.value === value}
          onClick={() => onChange(chip.value)}
        >
          <span>{chip.label}</span>{" "}
          {chip.count !== undefined && chip.count !== null && <span className={k.chipCount}>{formatNumber(chip.count)}</span>}
        </button>
      ))}
    </div>
  );
}
