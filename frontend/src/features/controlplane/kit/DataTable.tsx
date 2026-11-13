"use client";

import { useEffect, useId, useMemo, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/sidebar/icons";
import { Button, IconButton } from "./Button";
import { RowMenu, usePopover, type MenuItem } from "./Menu";
import { EmptyState, Skeleton } from "./Layout";
import { formatNumber } from "./format";
import k from "./Kit.module.css";

const noSubscription = () => () => {};

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
}

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
 * tooltip), numbers right-aligned in tabular figures, a hover state, the row's
 * "⋯" menu at the right, and pagination with "Showing 1–25 of 132".
 *
 * Columns empty in every row are hidden. A row with `rowHref` opens on a
 * click anywhere in it (its first cell also holds a real link, for the keyboard).
 */
export function DataTable<R>({
  caption,
  columns,
  rows,
  rowKey,
  rowHref,
  rowMenu,
  rowLabel,
  loading = false,
  error,
  empty,
  pageSize = 25,
  onLoadMore,
  hasMore = false,
  total,
  selectable,
  columnPicker = false,
  storageKey,
  hideEmptyColumns = true,
  stickyFirst = true,
  maxHeight = true,
  initialSort,
}: {
  /** What the table lists - read by assistive technology, not drawn. */
  caption: string;
  columns: Column<R>[];
  rows: R[];
  rowKey: (row: R) => string;
  rowHref?: (row: R) => string | null | undefined;
  rowMenu?: (row: R) => MenuItem[];
  /** The row's name, for its menu ("Actions for Asha Rao"). */
  rowLabel?: (row: R) => string;
  loading?: boolean;
  error?: string | null;
  /** The empty state (an EmptyState). */
  empty?: React.ReactNode;
  /** Rows per page when every row is loaded (0: no pages). */
  pageSize?: number;
  /** Server pages: a "Load more" button below the rows. */
  onLoadMore?: () => void;
  hasMore?: boolean;
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
}) {
  const router = useRouter();
  const captionId = useId();
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(initialSort ?? null);
  const [page, setPage] = useState(0);
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

  const colSpan = visibleColumns.length + (selectable ? 1 : 0) + (rowMenu ? 1 : 0);

  const toggleSort = (key: string) => {
    setPage(0);
    setSort((s) => (s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }));
  };

  const alignClass = (c: Column<R>) => (c.align === "right" ? k.num : c.align === "center" ? k.center : c.wrap ? k.wrap : undefined);

  if (!loading && !error && rows.length === 0) {
    return <div className={k.tableCard}>{empty ?? <EmptyState icon="search" title="Nothing to show" compact />}</div>;
  }

  return (
    <div className={k.tableCard}>
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
                    className={[alignClass(c), i === 0 && stickyFirst ? k.stickyCol : ""].filter(Boolean).join(" ") || undefined}
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
              {rowMenu && (
                <th scope="col" className={k.menuCol}>
                  <span className="w360-visually-hidden">Actions</span>
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
            {error && rows.length === 0 ? (
              <tr>
                <td colSpan={colSpan}>
                  <p role="alert" className={k.muted}>
                    {error}
                  </p>
                </td>
              </tr>
            ) : null}
            {shown.map((row) => {
              const key = rowKey(row);
              const href = rowHref?.(row) ?? null;
              const selected = selectable?.selected.has(key) ?? false;
              return (
                <tr
                  key={key}
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
                        aria-label={`Select ${rowLabel?.(row) ?? "row"}`}
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
                  {visibleColumns.map((c, i) => {
                    const cls = [alignClass(c), i === 0 && stickyFirst ? k.stickyCol : ""].filter(Boolean).join(" ") || undefined;
                    return i === 0 ? (
                      <th key={c.key} scope="row" className={cls}>
                        {c.render(row)}
                      </th>
                    ) : (
                      <td key={c.key} className={cls}>
                        {c.render(row)}
                      </td>
                    );
                  })}
                  {rowMenu && (
                    <td className={k.menuCol}>
                      <RowMenu label={`Actions for ${rowLabel?.(row) ?? "this row"}`} items={rowMenu(row)} />
                    </td>
                  )}
                </tr>
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
          {onLoadMore && hasMore && (
            <Button size="sm" onClick={onLoadMore}>
              Load more
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
