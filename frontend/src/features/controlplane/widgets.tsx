"use client";

import { Fragment, useEffect, useId, useRef, useState } from "react";
import { describeError } from "./ui";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";

/**
 * Small building blocks for the Super Admin console screens (Phase 8
 * extension): accessible tabs, a segmented single choice built on native
 * radios, the description editor with its text-only preview, and a resumable
 * multi-step runner with a visible progress list.
 */

// --- tabs --------------------------------------------------------------------------

export interface TabItem<K extends string> {
  key: K;
  label: React.ReactNode;
}

/** WAI-ARIA tabs with automatic activation: arrow keys (and Home/End) move
 * between tabs, only the selected tab is in the tab order. Renders the tab
 * list and one panel as siblings so a caller can lay them out in a grid. */
export function Tabs<K extends string>({
  label,
  items,
  selected,
  onSelect,
  vertical = false,
  listClassName,
  tabClassName,
  panelClassName,
  children,
}: {
  label: string;
  items: TabItem<K>[];
  selected: K;
  onSelect: (key: K) => void;
  vertical?: boolean;
  listClassName?: string;
  tabClassName?: string;
  panelClassName?: string;
  children: React.ReactNode;
}) {
  const base = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const tabId = (key: string) => `${base}-tab-${key}`;
  const panelId = `${base}-panel`;

  function onKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const step: Record<string, number> = vertical ? { ArrowDown: 1, ArrowUp: -1 } : { ArrowRight: 1, ArrowLeft: -1 };
    let target: number | null = null;
    if (e.key in step) target = (index + step[e.key] + items.length) % items.length;
    else if (e.key === "Home") target = 0;
    else if (e.key === "End") target = items.length - 1;
    if (target === null) return;
    e.preventDefault();
    const key = items[target].key;
    onSelect(key);
    refs.current[key]?.focus();
  }

  return (
    <>
      <div
        role="tablist"
        aria-label={label}
        aria-orientation={vertical ? "vertical" : "horizontal"}
        className={listClassName ?? styles.tabs}
      >
        {items.map((item, index) => (
          <button
            key={item.key}
            ref={(el) => {
              refs.current[item.key] = el;
            }}
            type="button"
            role="tab"
            id={tabId(item.key)}
            aria-selected={item.key === selected}
            aria-controls={panelId}
            tabIndex={item.key === selected ? 0 : -1}
            className={tabClassName ?? styles.tab}
            onClick={() => onSelect(item.key)}
            onKeyDown={(e) => onKeyDown(e, index)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={panelId} aria-labelledby={tabId(selected)} className={panelClassName ?? styles.tabPanel}>
        {children}
      </div>
    </>
  );
}

// --- labelled fields -------------------------------------------------------------------

/** The sighted "required" cue: a mark that is hidden from assistive technology,
 * which already hears the input's own `required`. */
export function RequiredMark() {
  return (
    <span className={cp.required} aria-hidden="true">
      {" *"}
    </span>
  );
}

/** A labelled multi-line plain-text field with a character count. */
export function TextAreaField({
  label,
  value,
  onChange,
  hint,
  error,
  maxLength,
  rows = 4,
}: {
  label: React.ReactNode;
  value: string;
  onChange: (value: string) => void;
  hint?: React.ReactNode;
  error?: string;
  maxLength: number;
  rows?: number;
}) {
  const id = useId();
  const hintId = useId();
  const errorId = useId();
  return (
    <div className={cp.field}>
      <label className="w360-label" htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        className="w360-input"
        rows={rows}
        value={value}
        maxLength={maxLength}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(hint ? hintId : false, error ? errorId : false)}
      />
      <p id={hintId} className="w360-hint">
        {hint ? <>{hint} </> : null}
        {value.length} of {maxLength} characters.
      </p>
      {error && (
        <p id={errorId} className="w360-error-text">
          {error}
        </p>
      )}
    </div>
  );
}

function describedBy(...ids: (string | false | undefined)[]): string | undefined {
  const joined = ids.filter(Boolean).join(" ");
  return joined || undefined;
}

/** A labelled input with an optional hint and an inline error, wired with
 * aria-describedby / aria-invalid. */
export function TextField({
  label,
  value,
  onChange,
  hint,
  error,
  showRequired,
  ...input
}: {
  label: React.ReactNode;
  value: string;
  onChange: (value: string) => void;
  hint?: React.ReactNode;
  error?: string;
  /** Draw the visible "required" mark after the label. The input's own
   * `required` is what assistive technology announces; this is the sighted cue. */
  showRequired?: boolean;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "id">) {
  const id = useId();
  const hintId = useId();
  const errorId = useId();
  return (
    <div className={cp.field}>
      <label className="w360-label" htmlFor={id}>
        {label}
        {showRequired && <RequiredMark />}
      </label>
      <input
        {...input}
        id={id}
        className="w360-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(hint ? hintId : false, error ? errorId : false)}
      />
      {hint && (
        <p id={hintId} className="w360-hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="w360-error-text">
          {error}
        </p>
      )}
    </div>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  hint,
  error,
  disabled,
  required,
  showRequired,
}: {
  label: React.ReactNode;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  hint?: React.ReactNode;
  error?: string;
  disabled?: boolean;
  required?: boolean;
  showRequired?: boolean;
}) {
  const id = useId();
  const hintId = useId();
  const errorId = useId();
  return (
    <div className={cp.field}>
      <label className="w360-label" htmlFor={id}>
        {label}
        {showRequired && <RequiredMark />}
      </label>
      <select
        id={id}
        className="w360-input"
        value={value}
        disabled={disabled}
        required={required}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(hint ? hintId : false, error ? errorId : false)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && (
        <p id={hintId} className="w360-hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="w360-error-text">
          {error}
        </p>
      )}
    </div>
  );
}

// --- segmented single choice --------------------------------------------------------

export interface Choice<V extends string> {
  value: V;
  label: string;
  disabled?: boolean;
}

/** One-of-several as native radio buttons (arrow keys, form semantics and a
 * screen reader's "1 of 4" for free), drawn as a segmented control. */
export function Segmented<V extends string>({
  legend,
  options,
  value,
  onChange,
  hint,
  error,
}: {
  legend: string;
  options: Choice<V>[];
  value: V;
  onChange: (value: V) => void;
  hint?: React.ReactNode;
  error?: string;
}) {
  const name = useId();
  const hintId = useId();
  const errorId = useId();
  return (
    <fieldset className={styles.fieldset} aria-describedby={describedBy(hint ? hintId : false, error ? errorId : false)}>
      <legend className="w360-label">{legend}</legend>
      <div className={styles.segmented}>
        {options.map((option) => (
          <label key={option.value} className={styles.segment}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
      {hint && (
        <p id={hintId} className="w360-hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="w360-error-text">
          {error}
        </p>
      )}
    </fieldset>
  );
}

// --- description editor ------------------------------------------------------------------

/**
 * A textarea with a small formatting toolbar that inserts Markdown at the
 * cursor, and a live preview. The preview is built from React text and a
 * handful of elements (strong, em, ul/li, a with an https address) - never
 * from HTML, so nothing typed here can inject markup or script.
 */
export function DescriptionField({
  label = "Description",
  value,
  onChange,
  maxLength = 5000,
  error,
}: {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  maxLength?: number;
  error?: string;
}) {
  const id = useId();
  const hintId = useId();
  const errorId = useId();
  const previewLabel = useId();
  const area = useRef<HTMLTextAreaElement | null>(null);
  // Where the caret goes once the inserted text is rendered.
  const caret = useRef<[number, number] | null>(null);

  useEffect(() => {
    const at = caret.current;
    if (!at || !area.current) return;
    caret.current = null;
    area.current.focus();
    area.current.setSelectionRange(at[0], at[1]);
  }, [value]);

  function selection(): [number, number] {
    const el = area.current;
    return el ? [el.selectionStart ?? value.length, el.selectionEnd ?? value.length] : [value.length, value.length];
  }

  function wrap(before: string, after: string, placeholder: string) {
    const [start, end] = selection();
    const chosen = value.slice(start, end) || placeholder;
    caret.current = [start + before.length, start + before.length + chosen.length];
    onChange(value.slice(0, start) + before + chosen + after + value.slice(end));
  }

  function bullet() {
    const [start] = selection();
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    caret.current = [start + 2, start + 2];
    onChange(`${value.slice(0, lineStart)}- ${value.slice(lineStart)}`);
  }

  const describedBy = [hintId, error ? errorId : ""].filter(Boolean).join(" ");
  return (
    <div className={cp.field}>
      <label className="w360-label" htmlFor={id}>
        {label}
      </label>
      <div className={styles.toolbar} role="group" aria-label={`Formatting for ${label}`}>
        <button type="button" className={styles.toolButton} onClick={() => wrap("**", "**", "bold text")}>
          Bold
        </button>
        <button type="button" className={styles.toolButton} onClick={() => wrap("*", "*", "italic text")}>
          Italic
        </button>
        <button type="button" className={styles.toolButton} onClick={bullet}>
          List item
        </button>
        <button type="button" className={styles.toolButton} onClick={() => wrap("[", "](https://)", "link text")}>
          Link
        </button>
      </div>
      <textarea
        id={id}
        ref={area}
        className={`w360-input ${styles.textarea}`}
        value={value}
        maxLength={maxLength}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      <p id={hintId} className="w360-hint">
        Supports **bold**, *italic*, “- ” list items and [links](https://example.com). {value.length}/{maxLength}
      </p>
      {error && (
        <p id={errorId} className="w360-error-text">
          {error}
        </p>
      )}
      <span className="w360-label" id={previewLabel}>
        Preview
      </span>
      <div className={styles.preview} role="group" aria-labelledby={previewLabel}>
        <MarkdownPreview text={value} />
      </div>
    </div>
  );
}

const INLINE = /\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|\[([^\]\n]+)\]\(([^)\s]+)\)/g;
/** Only a real https address becomes a link; the toolbar's "https://"
 * placeholder, javascript:, data: and the rest render as plain text. */
const SAFE_URL = /^https:\/\/[^\s/?#]+\.[^\s]+$/i;

function renderInline(text: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    if (match[1] !== undefined) out.push(<strong key={n++}>{match[1]}</strong>);
    else if (match[2] !== undefined) out.push(<em key={n++}>{match[2]}</em>);
    else if (SAFE_URL.test(match[4])) {
      out.push(
        <a key={n++} href={match[4]} target="_blank" rel="noopener noreferrer nofollow">
          {match[3]}
          <span className="w360-visually-hidden"> (opens in a new tab)</span>
        </a>,
      );
    } else out.push(<span key={n++}>{match[3]}</span>);
    last = at + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** The Markdown subset the builder offers, rendered as React elements. */
export function MarkdownPreview({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let paragraph: string[] = [];
  let items: string[] = [];
  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    const lines = paragraph;
    blocks.push(
      <p key={blocks.length}>
        {lines.map((line, i) => (
          <Fragment key={i}>
            {i > 0 && <br />}
            {renderInline(line)}
          </Fragment>
        ))}
      </p>,
    );
    paragraph = [];
  };
  const flushList = () => {
    if (items.length === 0) return;
    const entries = items;
    blocks.push(
      <ul key={blocks.length}>
        {entries.map((entry, i) => (
          <li key={i}>{renderInline(entry)}</li>
        ))}
      </ul>,
    );
    items = [];
  };
  for (const line of text.split(/\r?\n/)) {
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph();
      items.push(bullet[1]);
    } else if (!line.trim()) {
      flushParagraph();
      flushList();
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  if (blocks.length === 0) return <p className={cp.muted}>Nothing to preview yet.</p>;
  return <>{blocks}</>;
}

// --- resumable steps ------------------------------------------------------------------------

export type StepState = "pending" | "running" | "done" | "failed";

export interface StepView {
  key: string;
  label: string;
  state: StepState;
  detail?: string;
}

export interface StepDef {
  key: string;
  label: string;
  run: () => Promise<void>;
}

const STATE_TEXT: Record<StepState, string> = {
  pending: "Not started",
  running: "In progress…",
  done: "Done",
  failed: "Failed",
};

const STATE_BADGE: Record<StepState, string> = {
  pending: "w360-badge w360-badge-neutral",
  running: "w360-badge w360-badge-neutral",
  done: "w360-badge w360-badge-success",
  failed: "w360-badge w360-badge-danger",
};

/**
 * Runs an ordered list of calls and remembers which ones finished, so running
 * the same list again - after fixing what made a step fail - skips everything
 * already done instead of creating it twice.
 */
export function useStepRunner() {
  const done = useRef<Set<string>>(new Set());
  const [steps, setSteps] = useState<StepView[]>([]);

  const mark = (key: string, state: StepState, detail?: string) =>
    setSteps((list) => list.map((s) => (s.key === key ? { ...s, state, detail } : s)));

  /** Show the list about to run (already-finished steps stay "Done"). */
  function prepare(defs: { key: string; label: string }[]) {
    setSteps(defs.map((d) => ({ key: d.key, label: d.label, state: done.current.has(d.key) ? "done" : "pending" })));
  }

  async function runAll(defs: StepDef[]) {
    for (const def of defs) {
      if (done.current.has(def.key)) continue;
      mark(def.key, "running");
      try {
        await def.run();
      } catch (err) {
        mark(def.key, "failed", describeError(err));
        throw err;
      }
      done.current.add(def.key);
      mark(def.key, "done");
    }
  }

  const started = steps.some((s) => s.state === "done");
  // True while a step is in flight.
  const running = steps.some((s) => s.state === "running");
  return { steps, prepare, runAll, started, running };
}

export function ProgressList({ steps, title = "Progress" }: { steps: StepView[]; title?: string }) {
  if (steps.length === 0) return null;
  return (
    <div role="status" className={cp.field}>
      <p className={cp.muted}>{title}</p>
      <ol className={styles.progress}>
        {steps.map((s) => (
          <li key={s.key}>
            <span className={STATE_BADGE[s.state]}>{STATE_TEXT[s.state]}</span>
            <span>{s.label}</span>
            {s.detail && <span className="w360-error-text">{s.detail}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A list of what must be fixed before anything is sent. */
export function ErrorSummary({ errors, focusRef }: { errors: string[]; focusRef?: React.RefObject<HTMLDivElement | null> }) {
  if (errors.length === 0) return null;
  return (
    <div role="alert" tabIndex={-1} ref={focusRef} className="w360-error-text">
      <p>Fix {errors.length === 1 ? "this" : `these ${errors.length} things`} before saving:</p>
      <ul className={styles.errorList}>
        {errors.map((e) => (
          <li key={e}>{e}</li>
        ))}
      </ul>
    </div>
  );
}
