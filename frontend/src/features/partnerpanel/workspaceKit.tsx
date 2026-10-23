"use client";

import { useId, useState } from "react";
import type { RangeKey } from "@/lib/api/partnerPanel";
import styles from "./PartnerPanel.module.css";

/**
 * Small pieces the workspace screens share (ADR-047): the date-range control the
 * dashboard has always had, and a copy-to-clipboard button that says what
 * happened. Nothing here knows about money or permissions.
 */

const RANGES: { key: RangeKey; label: string }[] = [
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "90d", label: "90 days" },
  { key: "custom", label: "Custom" },
];

/** The date-range control: 7, 30 or 90 days, or two dates. `range`, `from` and `to`
 * are what to ask the server for; the server validates them and refuses a bad one. */
export function useRangeControls(initial: RangeKey = "30d") {
  const [range, setRange] = useState<RangeKey>(initial);
  // The last range that can be asked for on its own: choosing "Custom" shows the
  // two date fields at once, but there is nothing to request until both are applied.
  const [last, setLast] = useState<RangeKey>(initial);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [applied, setApplied] = useState<{ from?: string; to?: string }>({});
  const fromId = useId();
  const toId = useId();

  const node = (
    <div className={styles.range}>
      <fieldset>
        <legend>Date range</legend>
        {RANGES.map((r) => (
          <button
            key={r.key}
            type="button"
            className={styles.rangeButton}
            aria-pressed={range === r.key}
            onClick={() => {
              setRange(r.key);
              if (r.key !== "custom") {
                setLast(r.key);
                setApplied({});
              }
            }}
          >
            {r.label}
          </button>
        ))}
      </fieldset>
      {range === "custom" && (
        <form
          className={styles.dates}
          onSubmit={(e) => {
            e.preventDefault();
            setApplied({ from, to });
          }}
        >
          <div>
            <label className="w360-label" htmlFor={fromId}>
              From
            </label>
            <input id={fromId} className="w360-input" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label className="w360-label" htmlFor={toId}>
              To
            </label>
            <input id={toId} className="w360-input" type="date" required value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <button type="submit" className="w360-btn w360-btn-secondary" disabled={!from || !to}>
            Apply
          </button>
        </form>
      )}
    </div>
  );

  const ready = range !== "custom" || Boolean(applied.from && applied.to);
  return {
    range: ready ? range : last,
    from: range === "custom" && ready ? applied.from : undefined,
    to: range === "custom" && ready ? applied.to : undefined,
    node,
  };
}

/** Copies `text` and says whether it worked, in a live region that is on the page
 * before anything changes (so it is announced). The text itself is never hidden:
 * if the clipboard is refused the person can still select it. */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  }
  return (
    <span className={styles.copy}>
      <button type="button" className="w360-btn w360-btn-secondary" onClick={() => void copy()}>
        Copy {label}
      </button>
      <span role="status" className={styles.muted}>
        {state === "copied" ? "Copied." : state === "failed" ? "Could not copy - select the text and copy it." : ""}
      </span>
    </span>
  );
}

/** Basis points as a percentage, to two decimals ("50.00%"); an em dash when
 * there is no figure (no clicks is no rate, not 0%). */
export function percent(bp: number | null | undefined): string {
  return bp === null || bp === undefined ? "—" : `${(bp / 100).toFixed(2)}%`;
}
