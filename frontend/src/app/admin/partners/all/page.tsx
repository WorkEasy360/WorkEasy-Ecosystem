"use client";

import { Suspense } from "react";
import { AllPartnersList } from "@/features/controlplane/PartnerConsole";

/** Partners → All Partners: every partner account, whatever its capabilities.
 * Suspense: the page reads its filters from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading partners…</p>}>
      <AllPartnersList />
    </Suspense>
  );
}
