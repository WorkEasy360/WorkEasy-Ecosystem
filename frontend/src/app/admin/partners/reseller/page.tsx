"use client";

import { Suspense } from "react";
import { AllPartnersList } from "@/features/controlplane/PartnerConsole";

/** Partners → Reseller (ADR-062): the All Partners table, listing only the
 * partners whose type is Reseller - the server filters, each partner is under
 * exactly one type. Suspense: the page reads its filters from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading partners…</p>}>
      <AllPartnersList category="reseller" />
    </Suspense>
  );
}
