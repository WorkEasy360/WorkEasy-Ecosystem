"use client";

import { Suspense } from "react";
import { AllPartnersList } from "@/features/controlplane/PartnerConsole";

/** Partners → White Label (ADR-062): the All Partners table, listing only the
 * partners whose type is White Label - the server filters, each partner is under
 * exactly one type. Suspense: the page reads its filters from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading partners…</p>}>
      <AllPartnersList category="white_label" />
    </Suspense>
  );
}
