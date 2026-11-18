"use client";

import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { isDay } from "./DateRangeFilter";

/**
 * A list's filters kept in its address (2026-11-18, admin-ux phase 3): a
 * filtered view survives a reload, can be linked to, and comes back as it was
 * when the person returns from a record (the browser's Back restores the
 * address). One spec per list names each parameter and what it may hold:
 * - `options`: one of these values (a status, a type);
 * - `date`: a `YYYY-MM-DD` day;
 * - otherwise free text (a search), cut to `maxLength` (default 80).
 *
 * A value the spec does not allow (an old link, a hand-edited address) is
 * dropped, never sent to the server, and named in `dropped` so the page can say
 * so. A `from` after its `to` drops both. Other parameters in the address (a
 * tab, a report) are kept as they are.
 */
export type FilterRule = { options?: readonly string[]; date?: boolean; maxLength?: number };
export type FilterSpec = Record<string, FilterRule>;
export type FilterValues<S extends FilterSpec> = { [K in keyof S]: string };

function readFilters<S extends FilterSpec>(spec: S, params: URLSearchParams): { values: FilterValues<S>; dropped: string[] } {
  const values = {} as Record<string, string>;
  const dropped: string[] = [];
  for (const [key, rule] of Object.entries(spec)) {
    const raw = (params.get(key) ?? "").trim();
    let value = "";
    if (raw) {
      if (rule.options && !rule.options.includes(raw)) dropped.push(key);
      else if (rule.date && !isDay(raw)) dropped.push(key);
      else value = raw.slice(0, rule.maxLength ?? 80);
    }
    values[key] = value;
  }
  if ("from" in spec && "to" in spec && values.from && values.to && values.from > values.to) {
    dropped.push("from", "to");
    values.from = "";
    values.to = "";
  }
  return { values: values as FilterValues<S>, dropped };
}

const stampOf = (spec: FilterSpec, params: URLSearchParams) =>
  Object.keys(spec)
    .map((key) => `${key}=${params.get(key) ?? ""}`)
    .join("&");

export function useUrlFilters<S extends FilterSpec>(spec: S) {
  const params = useSearchParams() ?? new URLSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const [state, setState] = useState(() => readFilters(spec, params));
  // `seen`: the address these values were read from or written for. `pending`: the
  // addresses this list wrote that the router has not shown yet. The router updates
  // the address a moment after `replace`, so an address that is still the old one,
  // or one of ours catching up, is not a change; any other is a change from outside
  // (a link, the command palette, Back), and the list follows it.
  const stamp = stampOf(spec, params);
  const [track, setTrack] = useState<{ seen: string; pending: string[] }>({ seen: stamp, pending: [] });
  if (stamp !== track.seen) {
    const ours = track.pending.indexOf(stamp);
    if (ours >= 0) {
      setTrack({ seen: stamp, pending: track.pending.slice(ours + 1) });
    } else {
      setTrack({ seen: stamp, pending: [] });
      setState(readFilters(spec, params));
    }
  }

  function set(next: Partial<FilterValues<S>>) {
    const values = { ...state.values, ...next } as FilterValues<S>;
    setState({ values, dropped: [] });
    const query = new URLSearchParams(params.toString());
    for (const key of Object.keys(spec)) {
      const value = values[key];
      if (value) query.set(key, value);
      else query.delete(key);
    }
    const written = stampOf(spec, query);
    if (written !== track.seen) setTrack((t) => ({ ...t, pending: [...t.pending, written].slice(-10) }));
    const text = query.toString();
    router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
  }

  const reset = () => set(Object.fromEntries(Object.keys(spec).map((key) => [key, ""])) as Partial<FilterValues<S>>);

  return { values: state.values, set, reset, dropped: state.dropped };
}

/** The message for filters dropped from the address, or null. */
export function droppedMessage(dropped: string[]): string | null {
  if (dropped.length === 0) return null;
  return "Some filters in this page's address were not recognised and were left out. The list shows every match for the rest.";
}
