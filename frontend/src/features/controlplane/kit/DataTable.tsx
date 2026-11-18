"use client";

import { Fragment, useEffect, useId, useMemo, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/sidebar/icons";
import { Button, IconButton } from "./Button";
import { RowMenu, usePopover, type MenuItem } from "./Menu";
import { EmptyState, Skeleton } from "./Layout";
import { formatNumber } from "./format";
import k from "./Kit.module.css";

const noSubscription = () => () => {};

/**
 * How early a column folds away as its table narrows (2026-11-18, admin-ux
 * phase 3). A folded column's value is still one click away, in the row's
 * details ("Show details"), so nothing is lost on a small screen:
 * - `essential`: never folds (the first column always is);
 * - `high`: folds when the table is narrower than 600 px (a phone);
 * - `medium`: under 900 px (a tablet, a narrow window);
 * - `low`: under 1200 px (a laptop with the sidebar open).
 */
export type ColumnPriority = "essential" | "high" | "medium" | "low";

export interface Column<R> {
  key: string;
  /** Sentence case ("Signed up", never "SIGNED UP"). */
  header: string;
  render: (row: R) => React.ReactNode;
  /** The raw value: used to sort, and to hide a column empty in every row. */
  value?: (row: R) => string | number | boolean | null | undefined | unknown[];
  sortable?: boolean;
  align?: "left" | "right" | "center";
  /** Let the text wrap (a description); cells are one line otherwise. */
  wrap?: boolean;
  /** Can be turned off in the column picker (default: every column but the first). */
  hideable?: boolean;
  /** Off until turned on in the column picker. */
  defaultHidden?: boolean;
  /** When the column folds into the row's details (default `high`; the first column never folds). */
  priority?: ColumnPriority;
}

const FOLDS: Exclude<ColumnPriority, "essential">[] = ["low", "medium", "high"];

const isBlank = (v: unknown) =>
  v === null || v === undefined || v === "" || v === "—" || (Array.isArray(v) && v.length === 0);

/** A stable local-storage key's hidden columns. */
function useHiddenColumns(storageKey: string | undefined, defaults: string[]) {
  const [hidden, setHidden] = useState<string[]>(() => {
    if (!storageKey || typeof window === "undefined") return defaults;
    try {
      const saved = window.localStorage.getItem(`w360.columns.${storageKey}`);
      return saved ? (JSON.parse(saved) as string[]) : defaults;
    } catch {
      return defaults;
    }
  });
  const update = (next: string[]) => {
    setHidden(next);
    if (storageKey) {
      try {
        window.localStorage.setItem(`w360.columns.${storageKey}`, JSON.stringify(next));
      } catch {
        /* private mode: the choice lasts for this page only */
      }
    }
  };
  return [hidden, update] as const;
}

/**
 * The console's one table. Sentence-case headings, a sticky heading row and
 * first column (the table scrolls inside its own card, never the page), one
 * line per cell (long text is cut with an ellipsis and shown whole in a
 * tooltip), numbers right-aligned in tabular figures, a hover state, and
 * pagination with "Showing 1–25 of 132".
 *
 * **Actions (2026-11-18, admin-ux phase 3).** Every table that has row actions
 * has one "Actions" column, last, held at the right edge while the table
 * scrolls sideways: the row's frequent actions as buttons (`actions`: View,
 * Login as Guest, Approve…), then its "⋯" menu (`rowMenu`) for the rest. In a
 * narrow table the buttons keep their icon and their accessible name and drop
 * the visible word.
 *
 * **Small screens.** A column folds away below its `priority` width and its
 * value moves into the row's details, opened by the button at the start of the
 * row - the table never needs a sideways scroll on a phone to be read.
 *
 * **States.** Skeleton rows while loading; the error with "Try again"
 * (`onRetry`); the empty state; "Load more" with its own spinner and its own
 * error. Sorting a server-paged list that has more rows says that it sorted
 * only the rows loaded so far.
 *
 * Columns empty in every row are hidden. A row with `rowHref` opens on a click
 * anywhere in it (its first cell also holds a real link, for the keyboard).
 */
export function DataTable<R>({
  caption,
  columns,
  rows,
  rowKey,
  rowHref,
  rowMenu,
  actions,
  rowLabel,
  loading = false,
  error,
  onRetry,
  empty,
  pageSize = 25,
  onLoadMore,
  hasMore = false,
  loadingMore = false,
  moreError,
  total,
  selectable,
  columnPicker = false,
  storageKey,
  hideEmptyColumns = true,
  stickyFirst = true,
  maxHeight = true,
  initialSort,
  responsive = true,
}: {
  /** What the table lists - read by assistive technology, not drawn. */
  caption: string;
  columns: Column<R>[];
  rows: R[];
  rowKey: (row: R) => string;
  rowHref?: (row: R) => string | null | undefined;
  /** The row's less frequent actions, in its "⋯" menu. */
  rowMenu?: (row: R) => MenuItem[];
  /** The row's frequent actions, as buttons in the Actions column (before the menu). */
  actions?: (row: R) => React.ReactNode;
  /** The row's name, for its menu ("Actions for Asha Rao") and its details. */
  rowLabel?: (row: R) => string;
  loading?: boolean;
  error?: string | null;
  /** "Try again" beside the error. */
  onRetry?: () => void;
  /** The empty state (an EmptyState). */
  empty?: React.ReactNode;
  /** Rows per page when every row is loaded (0: no pages). */
  pageSize?: number;
  /** Server pages: a "Load more" button below the rows. */
  onLoadMore?: () => void;
  hasMore?: boolean;
  /** The next page is on its way. */
  loadingMore?: boolean;
  /** The next page could not be loaded (the rows already shown stay). */
  moreError?: string | null;
  /** How many rows exist in all, when the server says. */
  total?: number | null;
  selectable?: {
    selected: Set<string>;
    onChange: (next: Set<string>) => void;
    /** The bulk actions, shown while rows are selected. */
    actions: React.ReactNode;
  };
  columnPicker?: boolean;
  storageKey?: string;
  hideEmptyColumns?: boolean;
  stickyFirst?: boolean;
  /** false: the table grows with its rows (a short list on a detail page). */
  maxHeight?: boolean;
  initialSort?: { key: string; dir: "asc" | "desc" };
  /** false: no column folds on a small screen (the table scrolls sideways instead). */
  responsive?: boolean;
}) {
  const router = useRouter();
  const captionId = useId();
  const detailsId = useId();
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(initialSort ?? null);
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [hidden, setHidden] = useHiddenColumns(
    storageKey,
    columns.filter((c) => c.defaultHidden).map((c) => c.key),
  );

  // A new list (a filter, a search) starts again on its first page.
  const [rowsSeen, setRowsSeen] = useState(rows);
  if (rowsSeen !== rows) {
    setRowsSeen(rows);
    if (!onLoadMore) setPage(0);
  }

  const visibleColumns = useMemo(
    () =>
      columns.filter((c, i) => {
        if (hidden.includes(c.key) && i !== 0) return false;
        if (hideEmptyColumns && c.value && rows.length > 0 && rows.every((r) => isBlank(c.value!(r)))) return false;
        return true;
      }),
    [columns, hidden, hideEmptyColumns, rows],
  );

  // Each visible column's fold, and the widest fold any column has (it decides
  // from which width the rows' "Show details" buttons appear).
  const folds = visibleColumns.map((c, i): ColumnPriority => (!responsive || i === 0 ? "essential" : (c.priority ?? "high")));
  const widestFold = FOLDS.find((p) => folds.includes(p)) ?? null;
  const foldClass = (p: ColumnPriority) => (p === "essential" ? undefined : k[`fold-${p}`]);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.value) return rows;
    const get = col.value;
    const out = [...rows].sort((a, b) => {
      const va = get(a);
      const vb = get(b);
      if (isBlank(va) && isBlank(vb)) return 0;
      if (isBlank(va)) return 1;
      if (isBlank(vb)) return -1;
      if (typeof va === "number" && typeof vb === "number") return va - vb;
      return String(va).localeCompare(String(vb), undefined, { numeric: true, sensitivity: "base" });
    });
    return sort.dir === "desc" ? out.reverse() : out;
  }, [rows, sort, columns]);

  const paged = !onLoadMore && pageSize > 0;
  const pages = paged ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const current = Math.min(page, pages - 1);
  const shown = paged ? sorted.slice(current * pageSize, current * pageSize + pageSize) : sorted;
  const from = sorted.length === 0 ? 0 : paged ? current * pageSize + 1 : 1;
  const to = paged ? Math.min(sorted.length, (current + 1) * pageSize) : sorted.length;
  const all = total ?? (onLoadMore && hasMore ? null : sorted.length);

  const keys = shown.map(rowKey);
  const allSelected = selectable ? keys.length > 0 && keys.every((key) => selectable.selected.has(key)) : false;
  const someSelected = selectable ? keys.some((key) => selectable.selected.has(key)) : false;

  const hasActions = Boolean(actions || rowMenu);
  const colSpan = visibleColumns.length + (selectable ? 1 : 0) + (hasActions ? 1 : 0);

  const toggleSort = (key: string) => {
    setPage(0);
    setSort((s) => (s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }));
  };

  const toggleDetails = (key: string) =>
    setOpen((now) => {
      const next = new Set(now);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const alignClass = (c: Column<R>) => (c.align === "right" ? k.num : c.align === "center" ? k.center : c.wrap ? k.wrap : undefined);
  const cellClass = (c: Column<R>, i: number) =>
    [alignClass(c), i === 0 && stickyFirst ? k.stickyCol : "", foldClass(folds[i])].filter(Boolean).join(" ") || undefined;

  if (!loading && !error && rows.length === 0) {
    return <div className={k.tableCard}>{empty ?? <EmptyState icon="search" title="Nothing to show" compact />}</div>;
  }

  const retry = onRetry ? (
    <Button size="sm" icon="refresh" onClick={onRetry}>
      Try again
    </Button>
  ) : null;

  return (
    <div className={`${k.tableCard} ${k.tableResponsive}`}>
      {(selectable && selectable.selected.size > 0) || columnPicker ? (
        <div className={k.tableToolbar}>
          {selectable && selectable.selected.size > 0 ? (
            <>
              <strong>{formatNumber(selectable.selected.size)} selected</strong>
              {selectable.actions}
              <Button variant="link" onClick={() => selectable.onChange(new Set())}>
                Clear selection
              </Button>
            </>
          ) : null}
          {columnPicker && (
            <span className={k.filterEnd}>
              <ColumnPicker columns={columns} hidden={hidden} onChange={setHidden} />
            </span>
          )}
        </div>
      ) : null}
      <div
        className={k.tableScroll}
        data-no-max={!maxHeight || undefined}
        role="region"
        aria-labelledby={captionId}
        tabIndex={0}
      >
        <table className={k.dt} aria-busy={loading || undefined}>
          <caption id={captionId}>{caption}</caption>
          <thead>
            <tr>
              {selectable && (
                <th scope="col" className={k.checkCol}>
                  <input
                    type="checkbox"
                    aria-label="Select all rows on this page"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = someSelected && !allSelected;
                    }}
                    onChange={() => {
                      const next = new Set(selectable.selected);
                      if (allSelected) keys.forEach((key) => next.delete(key));
                      else keys.forEach((key) => next.add(key));
                      selectable.onChange(next);
                    }}
                  />
                </th>
              )}
              {visibleColumns.map((c, i) => {
                const sortable = c.sortable ?? Boolean(c.value);
                const sortedHere = sort?.key === c.key;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    className={cellClass(c, i)}
                    aria-sort={sortedHere ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                  >
                    {sortable ? (
                      <button type="button" className={k.sortButton} data-sorted={sortedHere || undefined} onClick={() => toggleSort(c.key)}>
                        <span>{c.header}</span>
                        <Icon name={sortedHere ? (sort!.dir === "asc" ? "arrowUp" : "arrowDown") : "sort"} size={12} />
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
              {hasActions && (
                <th scope="col" className={actions ? k.actionsCol : `${k.actionsCol} ${k.menuOnly}`}>
                  Actions
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {loading && rows.length === 0
              ? Array.from({ length: 5 }, (_, r) => (
                  <tr key={`skeleton-${r}`} aria-hidden="true">
                    {Array.from({ length: colSpan }, (_, c) => (
                      <td key={c}>
                        <Skeleton width={c === 0 ? "60%" : "40%"} />
                      </td>
                    ))}
                  </tr>
                ))
              : null}
            {error && rows.length === 0 && !loading ? (
              <tr>
                <td colSpan={colSpan}>
                  <div role="alert" className={k.tableError}>
                    <Icon name="alert" size={16} />
                    <span>{error}</span>
                    {retry}
                  </div>
                </td>
              </tr>
            ) : null}
            {shown.map((row) => {
              const key = rowKey(row);
              const href = rowHref?.(row) ?? null;
              const selected = selectable?.selected.has(key) ?? false;
              const expanded = widestFold !== null && open.has(key);
              const label = rowLabel?.(row) ?? "this row";
              const detailsRowId = `${detailsId}-${key}`;
              return (
                <Fragment key={key}>
                  <tr
                    data-clickable={href ? true : undefined}
                    aria-selected={selectable ? selected : undefined}
                    onClick={(e) => {
                      if (!href) return;
                      const target = e.target as HTMLElement;
                      if (target.closest("a, button, input, select, textarea, label, [role='menu']")) return;
                      if (window.getSelection()?.toString()) return;
                      if (e.metaKey || e.ctrlKey) window.open(href, "_blank", "noopener");
                      else router.push(href);
                    }}
                  >
                    {selectable && (
                      <td className={k.checkCol}>
                        <input
                          type="checkbox"
                          aria-label={`Select ${label}`}
                          checked={selected}
                          onChange={() => {
                            const next = new Set(selectable.selected);
                            if (selected) next.delete(key);
                            else next.add(key);
                            selectable.onChange(next);
                          }}
                        />
                      </td>
                    )}
                    {visibleColumns.map((c, i) =>
                      i === 0 ? (
                        <th key={c.key} scope="row" className={cellClass(c, i)}>
                          {widestFold ? (
                            <span className={k.firstCell}>
                              <button
                                type="button"
                                className={`${k.foldToggle} ${k[`toggle-${widestFold}`]}`}
                                aria-expanded={expanded}
                                aria-controls={expanded ? detailsRowId : undefined}
                                aria-label={`${expanded ? "Hide" : "Show"} details of ${label}`}
                                title={expanded ? "Hide details" : "Show details"}
                                onClick={() => toggleDetails(key)}
                              >
                                <Icon name={expanded ? "chevronDown" : "chevronRight"} size={14} />
                              </button>
                              <span className={k.firstCellBody}>{c.render(row)}</span>
                            </span>
                          ) : (
                            c.render(row)
                          )}
                        </th>
                      ) : (
                        <td key={c.key} className={cellClass(c, i)}>
                          {c.render(row)}
                        </td>
                      ),
                    )}
                    {hasActions && (
                      <td className={actions ? k.actionsCol : `${k.actionsCol} ${k.menuOnly}`}>
                        <span className={k.rowActions}>
                          {actions?.(row)}
                          {rowMenu && <RowMenu label={`Actions for ${label}`} items={rowMenu(row)} />}
                        </span>
                      </td>
                    )}
                  </tr>
                  {expanded && widestFold && (
                    <tr id={detailsRowId} className={`${k.detailsRow} ${k[`toggle-${widestFold}`]}`}>
                      <td colSpan={colSpan}>
                        <dl className={k.detailsList} aria-label={`Details of ${label}`}>
                          {visibleColumns.map((c, i) =>
                            folds[i] === "essential" ? null : (
                              <div key={c.key} className={`${k.detailItem} ${k[`show-${folds[i]}`]}`}>
                                <dt>{c.header}</dt>
                                <dd>{c.render(row)}</dd>
                              </div>
                            ),
                          )}
                        </dl>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {(paged && sorted.length > 0) || onLoadMore ? (
        <div className={k.tableFoot}>
          <span aria-live="polite">
            {sorted.length === 0
              ? "No rows"
              : `Showing ${formatNumber(from)}–${formatNumber(to)}${all !== null ? ` of ${formatNumber(all)}` : ""}`}
            {/* Sorting happens on the rows this page holds: say so while the server has more. */}
            {sort && onLoadMore && hasMore && sorted.length > 0 ? ` · sorted within the ${formatNumber(sorted.length)} loaded` : ""}
          </span>
          {paged && pages > 1 && (
            <span className={k.pager}>
              <IconButton icon="chevronLeft" label="Previous page" size="sm" variant="secondary" disabled={current === 0} onClick={() => setPage(current - 1)} />
              <span>
                Page {current + 1} of {pages}
              </span>
              <IconButton
                icon="chevronRight"
                label="Next page"
                size="sm"
                variant="secondary"
                disabled={current >= pages - 1}
                onClick={() => setPage(current + 1)}
              />
            </span>
          )}
          {moreError && rows.length > 0 ? (
            <span role="alert" className={k.footError}>
              <Icon name="alert" size={14} />
              {moreError}
            </span>
          ) : null}
          {onLoadMore && hasMore && (
            <Button size="sm" loading={loadingMore} onClick={onLoadMore}>
              {moreError ? "Try again" : "Load more"}
            </Button>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** "Columns": tick the columns to show. */
function ColumnPicker<R>({ columns, hidden, onChange }: { columns: Column<R>[]; hidden: string[]; onChange: (next: string[]) => void }) {
  const { open, setOpen, triggerRef: trigger, panelRef: panel } = usePopover("end");
  const inBrowser = useSyncExternalStore(noSubscription, () => true, () => false);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, setOpen, trigger]);
  return (
    <>
      <Button
        ref={trigger}
        size="sm"
        icon="columns"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
      >
        Columns
      </Button>
      {open &&
        inBrowser &&
        createPortal(
          <div
            ref={panel}
            id={id}
            role="group"
            aria-label="Columns to show"
            className={k.menu}
          >
            {columns.map((c, i) => (
              <label key={c.key} className={k.menuItem}>
                <input
                  type="checkbox"
                  checked={!hidden.includes(c.key)}
                  disabled={i === 0 || c.hideable === false}
                  onChange={(e) => onChange(e.target.checked ? hidden.filter((h) => h !== c.key) : [...hidden, c.key])}
                />
                <span>{c.header}</span>
              </label>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
