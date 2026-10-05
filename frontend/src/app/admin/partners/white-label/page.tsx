"use client";

import { Suspense } from "react";
import { WhiteLabelList } from "@/features/controlplane/WhiteLabelList";

/** Partners → White-label. Suspense: the page reads its filters from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading white-label partners…</p>}>
      <WhiteLabelList />
    </Suspense>
  );
}
