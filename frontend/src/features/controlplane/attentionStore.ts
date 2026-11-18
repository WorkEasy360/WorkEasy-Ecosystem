"use client";

import { useSyncExternalStore } from "react";
import type { AttentionItem } from "@/lib/api/controlPlane";

/**
 * What the top bar's bell last read from the server (2026-11-18, admin-ux phase 1),
 * so a tab can show the same count without asking again: Billing › Needs attention
 * shows `failed_payments`. Only the bell reads the server; this keeps its answer.
 * `null` until it has one, or when it could not read one - then no count is shown,
 * never a wrong one.
 */

let current: AttentionItem[] | null = null;
const listeners = new Set<() => void>();

export function publishAttention(items: AttentionItem[] | null): void {
  current = items;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The count the bell last read for `key`: 0 when none is waiting, `null` when unknown. */
export function useAttentionCount(key: AttentionItem["key"]): number | null {
  const items = useSyncExternalStore(
    subscribe,
    () => current,
    () => null,
  );
  if (!items) return null;
  return items.find((item) => item.key === key)?.count ?? 0;
}
