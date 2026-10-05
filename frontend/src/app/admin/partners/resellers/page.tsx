"use client";

import { Suspense } from "react";
import { ResellerList } from "@/features/controlplane/PartnerConsole";

/** Partners → Resellers. Suspense: the page reads its filters from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading resellers…</p>}>
      <ResellerList />
    </Suspense>
  );
}
